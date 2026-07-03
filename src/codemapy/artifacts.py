from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from codemapy import gitinfo
from codemapy.config import load_config
from codemapy.entrypoints import entry_points
from codemapy.models import AuxFile, ModuleNode, Report, Symbol
from codemapy.render.html import write_html
from codemapy.scanner import scan_project


ARTIFACT_DIR_NAME = ".codemapy"
ARTIFACT_VERSION = 2


@dataclass(frozen=True)
class ArtifactPaths:
    directory: Path
    report: Path
    context: Path
    summary: Path
    hubs: Path
    symbols: Path
    manifest: Path


def artifact_dir_for(root: Path) -> Path:
    """Return the default artifact directory for a project root."""
    return root.resolve() / ARTIFACT_DIR_NAME


def artifact_dir_exists(root: Path) -> bool:
    """Return True when the default artifact directory already exists."""
    return artifact_dir_for(root).exists()


def write_artifacts(report: Report, output_dir: Path | None = None) -> ArtifactPaths:
    """Write human and agent-readable artifacts for a scanned project.

    Rewriting replaces the ``.codemapy`` folder wholesale, so artifacts left
    behind by older codemapy versions cannot linger. Custom ``output_dir``
    targets are never cleared, only the tool-owned ``.codemapy`` directory.
    """
    directory = (output_dir or report.root / ARTIFACT_DIR_NAME).resolve()
    if directory.name == ARTIFACT_DIR_NAME and directory.exists():
        shutil.rmtree(directory, ignore_errors=True)
    directory.mkdir(parents=True, exist_ok=True)

    generated_at = datetime.now(timezone.utc).isoformat()
    git_commit = gitinfo.head_commit(report.root)
    git_dirty = gitinfo.is_dirty(report.root) if git_commit else None
    paths = ArtifactPaths(
        directory=directory,
        report=directory / "report.html",
        context=directory / "context.json",
        summary=directory / "summary.md",
        hubs=directory / "hubs.json",
        symbols=directory / "symbols.json",
        manifest=directory / "manifest.json",
    )

    write_html(report, paths.report)
    paths.context.write_text(_json_dumps(report_payload(report)), encoding="utf-8")
    paths.hubs.write_text(_json_dumps(hubs_payload(report)), encoding="utf-8")
    paths.symbols.write_text(_json_dumps(symbols_payload(report)), encoding="utf-8")
    paths.summary.write_text(
        summary_markdown(report, generated_at, git_commit=git_commit, git_dirty=git_dirty),
        encoding="utf-8",
    )
    paths.manifest.write_text(
        _json_dumps(manifest_payload(report, paths, generated_at, git_commit=git_commit, git_dirty=git_dirty)),
        encoding="utf-8",
    )
    return paths


def check_artifacts(root: Path) -> tuple[int, str]:
    """Return ``(exit_code, message)``: 0 fresh, 1 stale, 2 missing/unreadable."""
    root = root.resolve()
    manifest_path = artifact_dir_for(root) / "manifest.json"
    if not manifest_path.exists():
        return 2, f"No artifacts found at {manifest_path.parent} (generate them with --artifacts)"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return 2, f"Unreadable manifest: {manifest_path}"

    recorded_commit = manifest.get("git_commit")
    current_commit = gitinfo.head_commit(root)
    if recorded_commit and current_commit:
        if recorded_commit != current_commit:
            return 1, (
                f"Stale: HEAD moved from {recorded_commit[:12]} to {current_commit[:12]} "
                "(refresh with --artifacts --yes)"
            )
        if manifest.get("git_dirty") or gitinfo.is_dirty(root):
            return 1, "Stale: the working tree has uncommitted changes (refresh with --artifacts --yes)"
        return 0, f"Fresh: artifacts match clean HEAD {current_commit[:12]}"
    return _check_by_mtime(root, manifest)


def _check_by_mtime(root: Path, manifest: dict[str, object]) -> tuple[int, str]:
    generated_raw = manifest.get("generated_at")
    try:
        generated = datetime.fromisoformat(str(generated_raw)).timestamp()
    except (TypeError, ValueError):
        return 1, "Stale: manifest has no git commit and no usable generated_at timestamp"

    newest: tuple[float, str] | None = None
    for file in scan_project(root, load_config(root)).sources:
        try:
            mtime = file.absolute_path.stat().st_mtime
        except OSError:
            continue
        if newest is None or mtime > newest[0]:
            newest = (mtime, file.path)
    if newest and newest[0] > generated:
        return 1, f"Stale: {newest[1]} was modified after the artifacts were generated"
    return 0, "Fresh: no source file modified since the artifacts were generated"


def report_payload(report: Report) -> dict[str, object]:
    return {
        "version": ARTIFACT_VERSION,
        "root": str(report.root),
        "name": report.name,
        "summary": {
            "files": len(report.files),
            "loc": report.total_loc,
            "size": report.total_size,
            "internal_dependencies": len(report.edges),
            "external_references": len(report.external_imports),
            "symbols": sum(_symbol_count(module.symbols) for module in report.modules),
            "cycles": len(report.cycles),
            "doc_files": len(report.doc_files),
            "languages": report.languages,
        },
        "files": [
            {
                "path": file.path,
                "language": file.language,
                "extension": file.extension,
                "loc": file.loc,
                "size": file.size,
            }
            for file in report.files
        ],
        "metadata_files": [_aux_payload(item) for item in report.metadata_files],
        "doc_files": [_aux_payload(item) for item in report.doc_files],
        "assets": [
            {"extension": group.extension, "count": group.count, "size": group.size}
            for group in report.asset_groups
        ],
        "modules": [
            {
                "path": module.path,
                "language": module.language,
                "loc": module.loc,
                "size": module.size,
                "fan_in": module.fan_in,
                "fan_out": module.fan_out,
                "imports": [
                    {"raw": ref.raw, "kind": ref.kind, "line": ref.line}
                    for ref in module.imports
                ],
                # Full symbol detail lives in symbols.json to avoid duplicating it
                # here (which doubled artifact size on large repos).
                "symbol_count": _symbol_count(module.symbols),
            }
            for module in report.modules
        ],
        "edges": [
            {"source": edge.source, "target": edge.target, "raw": edge.raw, "kind": edge.kind}
            for edge in report.edges
        ],
        "external_imports": [
            {"source": item.source, "raw": item.raw, "kind": item.kind}
            for item in report.external_imports
        ],
        "cycles": [list(cycle) for cycle in report.cycles],
        "warnings": list(report.warnings),
    }


def _aux_payload(item: AuxFile) -> dict[str, object]:
    return {"path": item.path, "kind": item.kind, "size": item.size, "loc": item.loc}


def symbols_payload(report: Report) -> dict[str, object]:
    """A symbol-centric view: per-file definitions plus a flat name index.

    The ``index`` maps each defined name to every place it is defined, so an
    agent can answer "where is X defined?" without scanning the whole tree.
    """
    by_file = {
        module.path: [_symbol_payload(symbol) for symbol in module.symbols]
        for module in report.modules
        if module.symbols
    }

    index: dict[str, list[dict[str, object]]] = {}
    for module in report.modules:
        for symbol in module.symbols:
            for qualified, node in symbol.flatten():
                index.setdefault(node.name, []).append(
                    {
                        "path": module.path,
                        "qualified_name": qualified,
                        "kind": node.kind,
                        "line": node.start_line,
                    }
                )

    return {
        "version": ARTIFACT_VERSION,
        "total_symbols": sum(_symbol_count(module.symbols) for module in report.modules),
        "files": dict(sorted(by_file.items())),
        "index": dict(sorted(index.items())),
    }


def _symbol_payload(symbol: Symbol) -> dict[str, object]:
    payload: dict[str, object] = {
        "name": symbol.name,
        "kind": symbol.kind,
        "start_line": symbol.start_line,
        "end_line": symbol.end_line,
    }
    if symbol.signature:
        payload["signature"] = symbol.signature
    if symbol.doc:
        payload["doc"] = symbol.doc
    if symbol.children:
        payload["children"] = [_symbol_payload(child) for child in symbol.children]
    return payload


def _symbol_count(symbols: tuple[Symbol, ...]) -> int:
    return sum(1 + _symbol_count(symbol.children) for symbol in symbols)


def hubs_payload(report: Report) -> dict[str, object]:
    hubs = sorted(
        report.modules,
        key=lambda module: (-module.fan_in, -module.fan_out, -module.loc, module.path),
    )
    active = [module for module in hubs if module.fan_in or module.fan_out]
    return {
        "version": ARTIFACT_VERSION,
        "hubs": [_module_payload(module) for module in active],
        "top_fan_in": [_module_payload(module) for module in hubs if module.fan_in][:20],
        "top_fan_out": [
            _module_payload(module)
            for module in sorted(
                report.modules,
                key=lambda module: (-module.fan_out, -module.fan_in, -module.loc, module.path),
            )
            if module.fan_out
        ][:20],
    }


def manifest_payload(
    report: Report,
    paths: ArtifactPaths,
    generated_at: str,
    git_commit: str | None = None,
    git_dirty: bool | None = None,
) -> dict[str, object]:
    return {
        "version": ARTIFACT_VERSION,
        "generated_at": generated_at,
        "git_commit": git_commit,
        "git_dirty": git_dirty,
        "root": str(report.root),
        "artifact_dir": str(paths.directory),
        "files": {
            "report": paths.report.name,
            "context": paths.context.name,
            "summary": paths.summary.name,
            "hubs": paths.hubs.name,
            "symbols": paths.symbols.name,
            "manifest": paths.manifest.name,
        },
        "counts": {
            "files": len(report.files),
            "metadata_files": len(report.metadata_files),
            "doc_files": len(report.doc_files),
            "loc": report.total_loc,
            "internal_dependencies": len(report.edges),
            "external_references": len(report.external_imports),
            "symbols": sum(_symbol_count(module.symbols) for module in report.modules),
            "cycles": len(report.cycles),
        },
        "metadata_files": [_aux_payload(item) for item in report.metadata_files],
        "languages": report.languages,
        "artifact_bytes": _artifact_sizes(paths),
        "notes": artifact_notes(paths),
    }


LARGE_ARTIFACT_BYTES = 5 * 1024 * 1024  # 5 MB


def _artifact_sizes(paths: ArtifactPaths) -> dict[str, int]:
    sizes: dict[str, int] = {}
    for path in (paths.report, paths.context, paths.summary, paths.hubs, paths.symbols):
        try:
            sizes[path.name] = path.stat().st_size
        except OSError:
            continue
    return sizes


def artifact_notes(paths: ArtifactPaths) -> list[str]:
    notes: list[str] = []
    sizes = _artifact_sizes(paths)
    large = sorted(name for name, size in sizes.items() if size > LARGE_ARTIFACT_BYTES)
    if large:
        notes.append(
            "Large artifacts (" + ", ".join(large) + "). For agent context prefer "
            "summary.md, hubs.json, and the symbols.json `index` over loading the full files."
        )
    return notes


def summary_markdown(
    report: Report,
    generated_at: str | None = None,
    git_commit: str | None = None,
    git_dirty: bool | None = None,
) -> str:
    lines = [
        "# codemapy summary",
        "",
        f"- Root: `{report.root}`",
        f"- Generated: `{generated_at or ''}`",
    ]
    if git_commit:
        dirty_note = " (dirty working tree)" if git_dirty else ""
        lines.append(f"- Git commit: `{git_commit[:12]}`{dirty_note}")
    lines.extend(
        [
            f"- Files: {len(report.files)}",
            f"- LOC: {report.total_loc}",
            f"- Internal dependencies: {len(report.edges)}",
            f"- External references: {len(report.external_imports)}",
            f"- Symbols: {sum(_symbol_count(module.symbols) for module in report.modules)}",
            f"- Dependency cycles: {len(report.cycles)}",
            "",
            "## Languages",
            "",
        ]
    )

    if report.languages:
        lines.extend(
            f"- {language}: {_count_files(count)}" for language, count in report.languages.items()
        )
    else:
        lines.append("- None detected")

    lines.extend(["", "## Directory Overview", ""])
    lines.extend(_directory_overview(report))

    lines.extend(["", "## Entry Points", ""])
    entry_points = _entry_points(report)
    lines.extend(entry_points if entry_points else ["- None detected"])

    lines.extend(["", "## Top Hubs", ""])
    hubs = [module for module in _ranked_modules(report) if module.fan_in or module.fan_out][:10]
    if hubs:
        lines.extend(
            f"- `{module.path}`: fan-in {module.fan_in}, fan-out {module.fan_out}, {module.loc} loc"
            for module in hubs
        )
    else:
        lines.append("- No internal dependency edges detected")

    lines.extend(["", "## Largest Files", ""])
    largest = sorted(report.files, key=lambda file: (-file.loc, file.path))[:10]
    if largest:
        lines.extend(f"- `{file.path}`: {file.loc} loc" for file in largest)
    else:
        lines.append("- None")

    lines.extend(["", "## Dependency Cycles", ""])
    if report.cycles:
        for cycle in report.cycles[:20]:
            lines.append(_format_cycle(cycle))
        if len(report.cycles) > 20:
            lines.append(f"- ... and {len(report.cycles) - 20} more")
    else:
        lines.append("- None detected")

    lines.extend(["", "## Symbols by Kind", ""])
    kind_counts = _symbol_kind_counts(report)
    if kind_counts:
        lines.extend(f"- {kind}: {count}" for kind, count in kind_counts)
    else:
        lines.append("- None detected")

    lines.extend(["", "## External References", ""])
    external_counts = _external_reference_counts(report)
    if external_counts:
        lines.extend(f"- `{name}`: {count}" for name, count in external_counts[:20])
    else:
        lines.append("- None")

    lines.extend(["", "## Documentation Files", ""])
    lines.extend(_doc_file_lines(report))

    lines.extend(["", "## Project Metadata Files", ""])
    if report.metadata_files:
        lines.extend(
            f"- `{item.path}`: {item.kind}, {item.loc} loc, {item.size} bytes"
            for item in report.metadata_files
        )
    else:
        lines.append("- None")

    lines.extend(["", "## Other Files", ""])
    lines.extend(_asset_lines(report))

    lines.extend(
        [
            "",
            "## Artifact Guide",
            "",
            "- `context.json`: full scan data - files, imports, dependency edges, cycles, per-file symbol counts",
            "- `symbols.json`: per-file definitions plus an `index` mapping each defined name to its locations",
            "- `hubs.json`: modules ranked by fan-in / fan-out",
            "- `manifest.json`: generation metadata, artifact byte sizes, and `git_commit` for staleness checks",
            "- `report.html`: visual file tree, treemap, dependency graph, and insights "
            "(entry points, hubs, cycles, per-file symbols) for humans",
        ]
    )

    return "\n".join(lines) + "\n"


MAX_OVERVIEW_DIRS = 15


def _directory_overview(report: Report) -> list[str]:
    """Group source files by top directory: count, LOC, and dominant language."""
    if not report.files:
        return ["- None"]

    depth = 1
    top_level = {_group_key(file.path, 1) for file in report.files}
    if len(top_level) == 1 and "." not in top_level:
        # Everything lives under a single directory (e.g. `src/`); show one
        # more level so the overview says something useful.
        depth = 2

    groups: dict[str, tuple[int, int, dict[str, int]]] = {}
    for file in report.files:
        key = _group_key(file.path, depth)
        count, loc, languages = groups.setdefault(key, (0, 0, {}))
        languages[file.language or "Other"] = languages.get(file.language or "Other", 0) + 1
        groups[key] = (count + 1, loc + file.loc, languages)

    ranked = sorted(groups.items(), key=lambda item: (-item[1][1], item[0]))
    lines = []
    for key, (count, loc, languages) in ranked[:MAX_OVERVIEW_DIRS]:
        dominant = max(sorted(languages), key=lambda lang: languages[lang])
        lines.append(f"- `{key}`: {_count_files(count)}, {loc} loc ({dominant})")
    if len(ranked) > MAX_OVERVIEW_DIRS:
        lines.append(f"- ... and {len(ranked) - MAX_OVERVIEW_DIRS} more directories")
    return lines


def _group_key(path: str, depth: int) -> str:
    parts = path.split("/")
    if len(parts) <= depth:
        return "/".join(parts[:-1]) + "/" if len(parts) > 1 else "."
    return "/".join(parts[:depth]) + "/"


def _entry_points(report: Report) -> list[str]:
    lines = []
    for entry in entry_points(report):
        line = f"- `{entry.name}`"
        if entry.target:
            line += f" -> `{entry.target}`"
        if entry.note:
            line += f" ({entry.note})"
        lines.append(line)
    return lines


MAX_DOC_FILES = 15
MAX_ASSET_GROUPS = 10


def _doc_file_lines(report: Report) -> list[str]:
    if not report.doc_files:
        return ["- None"]
    docs = sorted(report.doc_files, key=lambda item: (len(item.path.split("/")), item.path))
    lines = [f"- `{item.path}` ({item.loc} loc)" for item in docs[:MAX_DOC_FILES]]
    if len(docs) > MAX_DOC_FILES:
        lines.append(f"- ... and {len(docs) - MAX_DOC_FILES} more")
    return lines


def _asset_lines(report: Report) -> list[str]:
    if not report.asset_groups:
        return ["- None"]
    groups = sorted(report.asset_groups, key=lambda group: (-group.size, group.extension))
    lines = [
        f"- `{group.extension}`: {_count_files(group.count)}, {group.size} bytes"
        for group in groups[:MAX_ASSET_GROUPS]
    ]
    if len(groups) > MAX_ASSET_GROUPS:
        lines.append(f"- ... and {len(groups) - MAX_ASSET_GROUPS} more extensions")
    return lines


def _ranked_modules(report: Report) -> list[ModuleNode]:
    return sorted(
        report.modules,
        key=lambda module: (-module.fan_in, -module.fan_out, -module.loc, module.path),
    )


LARGE_CYCLE_THRESHOLD = 8


def _format_cycle(cycle: tuple[str, ...]) -> str:
    if len(cycle) == 1:
        return f"- self-import: `{cycle[0]}`"
    if len(cycle) <= LARGE_CYCLE_THRESHOLD:
        return f"- {len(cycle)} files: " + " <-> ".join(f"`{path}`" for path in cycle)

    # Summarise large cycles: which directories, and a few example members.
    directories = sorted({_dir_of(path) for path in cycle})
    dir_text = ", ".join(f"`{directory}`" for directory in directories[:5])
    if len(directories) > 5:
        dir_text += f", +{len(directories) - 5} more"
    examples = ", ".join(f"`{path}`" for path in cycle[:4])
    return (
        f"- {len(cycle)} files across {dir_text} "
        f"(large dependency cycle; may stem from a re-export/barrel hub); e.g. {examples}, ..."
    )


def _dir_of(path: str) -> str:
    head, _, _ = path.rpartition("/")
    return head or "."


def _count_files(count: int) -> str:
    return f"{count} file" if count == 1 else f"{count} files"


def _symbol_kind_counts(report: Report) -> list[tuple[str, int]]:
    counts: dict[str, int] = {}

    def tally(symbols: tuple[Symbol, ...]) -> None:
        for symbol in symbols:
            counts[symbol.kind] = counts.get(symbol.kind, 0) + 1
            tally(symbol.children)

    for module in report.modules:
        tally(module.symbols)
    return sorted(counts.items(), key=lambda item: (-item[1], item[0]))


def _external_reference_counts(report: Report) -> list[tuple[str, int]]:
    counts: dict[str, int] = {}
    for item in report.external_imports:
        counts[item.raw] = counts.get(item.raw, 0) + 1
    return sorted(counts.items(), key=lambda item: (-item[1], item[0]))


def _module_payload(module: ModuleNode) -> dict[str, object]:
    return {
        "path": module.path,
        "language": module.language,
        "loc": module.loc,
        "size": module.size,
        "fan_in": module.fan_in,
        "fan_out": module.fan_out,
    }


def _json_dumps(payload: dict[str, object]) -> str:
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"
