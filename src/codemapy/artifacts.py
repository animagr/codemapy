from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from codemapy import gitinfo
from codemapy.config import load_config
from codemapy.entrypoints import entry_points
from codemapy.coverage import coverage_lines, coverage_payload
from codemapy.graph import HubEntry, hub_entries, hub_rank_key, production_hubs
from codemapy.models import AuxFile, ModuleNode, Report, Symbol, UnresolvedImport
from codemapy.render.html import write_html
from codemapy.scanner import scan_project
from codemapy.stack import Manifest, frameworks, read_stack
from codemapy.unresolved import describe_group, group_unresolved, reason_value
from codemapy.unused import possibly_unused


ARTIFACT_DIR_NAME = ".codemapy"
# 3: unresolved_imports split out of external_imports; modules gained
# is_test, is_generated and test_fan_in; hubs rank by non-test fan-in.
ARTIFACT_VERSION = 3


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
    git_dirty = gitinfo.is_dirty(report.root, ignored_dir_names=(ARTIFACT_DIR_NAME,)) if git_commit else None
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
    """Return ``(exit_code, message)``: 0 fresh, 1 stale, 2 missing/unreadable.

    A stale message names the files that changed since the artifacts were
    written (see ``changed_since_artifacts``), so an agent can tell whether
    the files it is working on are affected.
    """
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
        changed = changed_since_artifacts(root, manifest)
        changed_note = f"; {_changed_preview(changed)}" if changed else ""
        if recorded_commit != current_commit:
            return 1, (
                f"Stale: HEAD moved from {recorded_commit[:12]} to {current_commit[:12]}{changed_note} "
                "(refresh with --artifacts --yes)"
            )
        if manifest.get("git_dirty") and changed == []:
            # Mapped from uncommitted edits, and nothing has changed since.
            return 0, (
                f"Fresh: no file changed since the artifacts were written from uncommitted changes on "
                f"{current_commit[:12]}"
            )
        # Artifacts are never committed, so their own folder is not a change.
        if manifest.get("git_dirty") or gitinfo.is_dirty(root, ignored_dir_names=(ARTIFACT_DIR_NAME,)):
            return 1, f"Stale: the working tree has uncommitted changes{changed_note} (refresh with --artifacts --yes)"
        return 0, f"Fresh: artifacts match clean HEAD {current_commit[:12]}"
    return _check_by_mtime(root, manifest)


MAX_CHANGED_PREVIEW = 10


def changed_since_artifacts(root: Path, manifest: dict[str, object] | None = None) -> list[str] | None:
    """Files changed since the artifacts in *root* were written, or None if unknown.

    Uses git when the manifest recorded a commit: a file counts when git
    reports it changed since that commit and it was modified after the
    artifacts were generated, or deleted. Edits already present when a dirty
    tree was mapped are therefore not counted. Outside git, mapped files
    modified after generation are reported instead.
    """
    root = root.resolve()
    if manifest is None:
        try:
            manifest = json.loads((artifact_dir_for(root) / "manifest.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
    try:
        generated = datetime.fromisoformat(str(manifest.get("generated_at"))).timestamp()
    except (TypeError, ValueError):
        return None
    commit = manifest.get("git_commit")
    if commit and gitinfo.head_commit(root):
        candidates = gitinfo.changed_files(root, since=str(commit), ignored_dir_names=(ARTIFACT_DIR_NAME,))
        if candidates is None:
            return None
        return [path for path in candidates if _modified_after(root / path, generated)]
    try:
        context = json.loads((artifact_dir_for(root) / "context.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    mapped = [str(item["path"]) for item in context.get("files", [])]
    return [path for path in mapped if _modified_after(root / path, generated)]


def _modified_after(path: Path, timestamp: float) -> bool:
    try:
        return path.stat().st_mtime > timestamp
    except OSError:
        return True  # deleted since


def _changed_preview(paths: list[str]) -> str:
    noun = "file" if len(paths) == 1 else "files"
    shown = ", ".join(paths[:MAX_CHANGED_PREVIEW])
    more = f" and {len(paths) - MAX_CHANGED_PREVIEW} more" if len(paths) > MAX_CHANGED_PREVIEW else ""
    return f"{len(paths)} {noun} changed since they were written: {shown}{more}"


def _check_by_mtime(root: Path, manifest: dict[str, object]) -> tuple[int, str]:
    generated_raw = manifest.get("generated_at")
    try:
        generated = datetime.fromisoformat(str(generated_raw)).timestamp()
    except (TypeError, ValueError):
        return 1, "Stale: manifest has no git commit and no usable generated_at timestamp"

    newer = sorted(
        file.path
        for file in scan_project(root, load_config(root)).sources
        if _modified_after(file.absolute_path, generated)
    )
    if newer:
        return 1, f"Stale: {_changed_preview(newer)} (refresh with --artifacts --yes)"
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
            "unresolved_imports": len(report.unresolved_imports),
            "test_files": len(_test_modules(report)),
            "generated_files": len(_generated_modules(report)),
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
                # Python only: the file has a top-level `__main__` guard, so it
                # can be run directly. Paired with fan_in == 0 this is what
                # promotes a file to an entry point.
                "has_main_guard": module.has_main_guard,
                "is_test": module.is_test,
                "is_generated": module.is_generated,
                # Importers that are test files; fan_in - test_fan_in is what
                # production code depends on, and what hubs are ranked by.
                "test_fan_in": module.test_fan_in,
                # Tests that import this module or are named after it.
                "tested_by": list(module.tested_by),
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
        "unresolved_imports": [_unresolved_payload(item) for item in report.unresolved_imports],
        "cycles": [list(cycle) for cycle in report.cycles],
        "coverage": coverage_payload(report),
        "stack": _stack_payload(read_stack(report)),
        # Files nothing imports that are not entry points, tests, generated or
        # loaded by convention (see codemapy.unused): candidates, not verdicts.
        "possibly_unused": [module.path for module in possibly_unused(report).modules],
        # Pruned folders and dropped source files, with the reason (answers --why).
        "skipped": [
            {"path": item.path, "reason": item.reason.value, "is_dir": item.is_dir} for item in report.skipped
        ],
        "warnings": list(report.warnings),
    }


def _unresolved_payload(item: UnresolvedImport) -> dict[str, object]:
    return {
        "source": item.source,
        "raw": item.raw,
        "kind": item.kind,
        "line": item.line,
        "reason": reason_value(item),
        "hidden_by": item.hidden_by,
    }


def _test_modules(report: Report) -> list[ModuleNode]:
    return [module for module in report.modules if module.is_test]


def _generated_modules(report: Report) -> list[ModuleNode]:
    return [module for module in report.modules if module.is_generated]


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
    if symbol.bases:
        payload["bases"] = list(symbol.bases)
    if symbol.children:
        payload["children"] = [_symbol_payload(child) for child in symbol.children]
    return payload


def _symbol_count(symbols: tuple[Symbol, ...]) -> int:
    return sum(1 + _symbol_count(symbol.children) for symbol in symbols)


MAX_TOP_HUBS = 20


def hubs_payload(report: Report) -> dict[str, object]:
    """Every connected module, plus the top production hubs by fan-in and fan-out.

    ``hubs`` keeps test and generated modules (flagged) for completeness; the
    ``top_*`` lists leave them out and rank by non-test fan-in.
    """
    active = sorted(
        (module for module in report.modules if module.fan_in or module.fan_out), key=hub_rank_key
    )
    production = production_hubs(report.modules)
    return {
        "version": ARTIFACT_VERSION,
        "hubs": [_module_payload(module) for module in active],
        "top_fan_in": [
            _module_payload(module) for module in production if module.non_test_fan_in
        ][:MAX_TOP_HUBS],
        "top_fan_out": [
            _module_payload(module)
            for module in sorted(
                production,
                key=lambda module: (-module.fan_out, -module.non_test_fan_in, -module.loc, module.path),
            )
            if module.fan_out
        ][:MAX_TOP_HUBS],
        # Like top_fan_in, but a Go package is one row rather than one row per file.
        "top_hubs": [_hub_payload(hub) for hub in hub_entries(report)[:MAX_TOP_HUBS]],
    }


def _hub_payload(hub: HubEntry) -> dict[str, object]:
    return {
        "label": hub.label,
        "path": hub.path,
        "files": hub.files,
        "fan_in": hub.fan_in,
        "test_fan_in": hub.test_fan_in,
        "fan_out": hub.fan_out,
        "loc": hub.loc,
        "tested": hub.tested,
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
            "unresolved_imports": len(report.unresolved_imports),
            "test_files": len(_test_modules(report)),
            "generated_files": len(_generated_modules(report)),
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
    tests = _test_modules(report)
    generated = _generated_modules(report)
    lines.extend(
        [
            f"- Files: {len(report.files)}",
            f"- LOC: {report.total_loc}",
            f"- Test files: {len(tests)} ({sum(module.loc for module in tests)} loc)",
            f"- Generated files: {len(generated)} ({sum(module.loc for module in generated)} loc)",
            f"- Internal dependencies: {len(report.edges)}",
            f"- External references: {len(report.external_imports)}",
            f"- Unresolved internal imports: {len(report.unresolved_imports)}",
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

    lines.extend(["", "## Coverage", ""])
    lines.extend(coverage_lines(report))

    lines.extend(["", "## Stack", ""])
    lines.extend(_stack_lines(read_stack(report)))

    lines.extend(["", "## Directory Overview", ""])
    lines.extend(_directory_overview(report))

    lines.extend(["", "## Entry Points", ""])
    entry_points = _entry_points(report)
    lines.extend(entry_points if entry_points else ["- None detected"])

    lines.extend(["", "## Top Hubs", ""])
    lines.extend(_hub_lines(report))

    lines.extend(["", "## Test Coverage", ""])
    lines.extend(_test_coverage_lines(report))

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

    lines.extend(["", "## Possibly Unused Modules", ""])
    lines.extend(_unused_lines(report))

    lines.extend(["", "## Unresolved Internal Imports", ""])
    lines.extend(_unresolved_lines(report))

    if generated:
        lines.extend(["", "## Generated Files", ""])
        lines.extend(_generated_lines(generated))

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
            "- `context.json`: full scan data - files, imports, dependency edges, unresolved internal imports "
            "with their reasons, cycles, per-file symbol counts, test / generated flags, `tested_by` "
            "(the tests covering each module), and a `coverage` block",
            "- `symbols.json`: per-file definitions plus an `index` mapping each defined name to its locations",
            "- `hubs.json`: modules ranked by non-test fan-in and by fan-out, plus `top_hubs` with Go "
            "packages grouped",
            "- Query without reading these files: `codemapy --find WORDS`, `--where NAME`, `--outline FILE`, "
            "`--importers FILE`, `--tests FILE`, `--impact FILE`, `--changed`, `--why FILE`",
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


MAX_SUMMARY_HUBS = 10
MAX_UNRESOLVED_GROUPS = 10
MAX_GENERATED_FILES = 15


def _hub_lines(report: Report) -> list[str]:
    hubs = hub_entries(report)[:MAX_SUMMARY_HUBS]
    if not hubs and report.edges:
        edges = "the only internal dependency edge is" if len(report.edges) == 1 else (
            f"all {len(report.edges)} internal dependency edges are"
        )
        return [f"- None: {edges} between test or generated files"]
    if not hubs:
        return ["- No internal dependency edges detected"]
    return [f"- {_hub_text(hub)}" for hub in hubs]


def _hub_text(hub: HubEntry) -> str:
    tests_note = f" ({hub.test_fan_in} from tests)" if hub.test_fan_in else ""
    package_note = f" (package, {_count_files(hub.files)})" if hub.path is None or hub.label.endswith("/") else ""
    return (
        f"`{hub.label}`{package_note}: fan-in {hub.fan_in}{tests_note}, "
        f"fan-out {hub.fan_out}, {hub.loc} loc"
    )


MAX_UNTESTED_HUBS = 10


def _test_coverage_lines(report: Report) -> list[str]:
    """Direct test coverage of production modules, and the hubs that lack it."""
    production = [module for module in report.modules if not module.is_test and not module.is_generated]
    if not any(module.is_test for module in report.modules):
        return ["- No test files detected, so no module has a direct test"]
    covered = sum(1 for module in production if module.tested_by)
    share = round(100 * covered / len(production)) if production else 0
    lines = [
        f"- {covered} of {len(production)} production modules ({share}%) have a direct test: "
        "a test file imports them or is named after them (`foo_test.go`, `foo.test.ts`, "
        "`src/test/.../FooTest.java`). A module reached only through other modules is not counted.",
    ]
    untested = [hub for hub in hub_entries(report) if not hub.tested][:MAX_UNTESTED_HUBS]
    if untested:
        lines.append("- Hubs without a direct test:")
        lines.extend(f"  - {_hub_text(hub)}" for hub in untested)
    return lines


def _unresolved_lines(report: Report) -> list[str]:
    if not report.unresolved_imports:
        return ["- None: every import that names a project file resolved to a mapped one"]
    groups = group_unresolved(report.unresolved_imports)
    lines = [
        f"{_count_imports(len(report.unresolved_imports))} name a project file but did not resolve "
        "to a mapped source file, so the edges they stand for are missing from this map.",
        "",
    ]
    lines.extend(f"- {describe_group(group)}" for group in groups[:MAX_UNRESOLVED_GROUPS])
    if len(groups) > MAX_UNRESOLVED_GROUPS:
        lines.append(f"- ... and {len(groups) - MAX_UNRESOLVED_GROUPS} more causes (see `context.json`)")
    return lines


def _generated_lines(generated: list[ModuleNode]) -> list[str]:
    ordered = sorted(generated, key=lambda module: module.path)
    lines = [f"- `{module.path}` ({module.loc} loc)" for module in ordered[:MAX_GENERATED_FILES]]
    if len(ordered) > MAX_GENERATED_FILES:
        lines.append(f"- ... and {len(ordered) - MAX_GENERATED_FILES} more")
    return lines


def _count_imports(count: int) -> str:
    return f"{count} import" if count == 1 else f"{count} imports"


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
        "test_fan_in": module.test_fan_in,
        "is_test": module.is_test,
        "is_generated": module.is_generated,
    }


def _json_dumps(payload: dict[str, object]) -> str:
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"


MAX_STACK_NAMES = 15
MAX_UNUSED_LINES = 15


def _stack_payload(manifests: list[Manifest]) -> dict[str, object]:
    return {
        "manifests": [
            {
                "path": manifest.path,
                "name": manifest.name,
                "dependencies": list(manifest.dependencies),
                "dev_dependencies": list(manifest.dev_dependencies),
            }
            for manifest in manifests
        ],
        "frameworks": frameworks(manifests),
    }


def _stack_lines(manifests: list[Manifest]) -> list[str]:
    if not manifests:
        return ["- No dependency manifests found near the root"]
    lines = []
    for manifest in manifests:
        name = f" ({manifest.name})" if manifest.name else ""
        parts = []
        if manifest.dependencies:
            parts.append(f"{len(manifest.dependencies)} dependencies: {_names(manifest.dependencies)}")
        if manifest.dev_dependencies:
            parts.append(f"{len(manifest.dev_dependencies)} dev/optional: {_names(manifest.dev_dependencies)}")
        lines.append(f"- `{manifest.path}`{name}: " + "; ".join(parts))
    recognised = frameworks(manifests)
    if recognised:
        lines.append(f"- Recognised: {', '.join(recognised)}")
    return lines


def _names(names: tuple[str, ...]) -> str:
    shown = ", ".join(names[:MAX_STACK_NAMES])
    return f"{shown}, ... and {len(names) - MAX_STACK_NAMES} more" if len(names) > MAX_STACK_NAMES else shown


def _unused_lines(report: Report) -> list[str]:
    result = possibly_unused(report)
    unused = result.modules
    notes = [f"- {note}" for note in result.left_out]
    if not unused:
        return ["- None found among files whose language has import analysis", *notes]
    lines = [*notes, *([""] if notes else []),
        f"{_count_files(len(unused))} that nothing in the map imports and that are not entry points, tests, "
        "generated, or loaded by convention. Check before deleting: a file can still be loaded by name "
        "(plugins, reflection, HTML script tags, build steps).",
        "",
    ]
    lines.extend(f"- `{module.path}` ({module.loc} loc)" for module in unused[:MAX_UNUSED_LINES])
    if len(unused) > MAX_UNUSED_LINES:
        lines.append(f"- ... and {len(unused) - MAX_UNUSED_LINES} more (see `possibly_unused` in `context.json`)")
    return lines
