from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from codemapy.config import is_default_ignored_dir_name, is_project_metadata_file
from codemapy.models import ModuleNode, Report, Symbol
from codemapy.render.html import write_html


ARTIFACT_DIR_NAME = ".codemapy"
ARTIFACT_VERSION = 1


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
    """Write human and agent-readable artifacts for a scanned project."""
    directory = (output_dir or report.root / ARTIFACT_DIR_NAME).resolve()
    directory.mkdir(parents=True, exist_ok=True)

    generated_at = datetime.now(timezone.utc).isoformat()
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
    paths.context.write_text(_json_dumps(report_payload(report, generated_at)), encoding="utf-8")
    paths.hubs.write_text(_json_dumps(hubs_payload(report)), encoding="utf-8")
    paths.symbols.write_text(_json_dumps(symbols_payload(report)), encoding="utf-8")
    paths.summary.write_text(summary_markdown(report, generated_at), encoding="utf-8")
    paths.manifest.write_text(_json_dumps(manifest_payload(report, paths, generated_at)), encoding="utf-8")
    return paths


def report_payload(report: Report, generated_at: str | None = None) -> dict[str, object]:
    metadata_files = metadata_files_payload(report.root)
    return {
        "version": ARTIFACT_VERSION,
        "generated_at": generated_at,
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
            "languages": report.languages,
        },
        "files": [
            {
                "path": file.path,
                "absolute_path": str(file.absolute_path),
                "language": file.language,
                "extension": file.extension,
                "loc": file.loc,
                "size": file.size,
            }
            for file in report.files
        ],
        "metadata_files": metadata_files,
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


def manifest_payload(report: Report, paths: ArtifactPaths, generated_at: str) -> dict[str, object]:
    metadata_files = metadata_files_payload(report.root)
    return {
        "version": ARTIFACT_VERSION,
        "generated_at": generated_at,
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
            "metadata_files": len(metadata_files),
            "loc": report.total_loc,
            "internal_dependencies": len(report.edges),
            "external_references": len(report.external_imports),
            "symbols": sum(_symbol_count(module.symbols) for module in report.modules),
            "cycles": len(report.cycles),
        },
        "metadata_files": metadata_files,
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


def summary_markdown(report: Report, generated_at: str | None = None) -> str:
    lines = [
        "# codemapy summary",
        "",
        f"- Root: `{report.root}`",
        f"- Generated: `{generated_at or ''}`",
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

    if report.languages:
        lines.extend(f"- {language}: {count} files" for language, count in report.languages.items())
    else:
        lines.append("- None detected")

    lines.extend(["", "## Top Hubs", ""])
    hubs = [module for module in _ranked_modules(report) if module.fan_in or module.fan_out][:10]
    if hubs:
        lines.extend(
            f"- `{module.path}`: fan-in {module.fan_in}, fan-out {module.fan_out}, {module.loc} loc"
            for module in hubs
        )
    else:
        lines.append("- No internal dependency edges detected")

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

    lines.extend(["", "## Project Metadata Files", ""])
    metadata_files = metadata_files_payload(report.root)
    if metadata_files:
        lines.extend(
            f"- `{item['path']}`: {item['kind']}, {item['loc']} loc, {item['size']} bytes"
            for item in metadata_files
        )
    else:
        lines.append("- None")

    return "\n".join(lines) + "\n"


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


def metadata_files_payload(root: Path) -> list[dict[str, object]]:
    root = root.resolve()
    files: list[dict[str, object]] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if not is_project_metadata_file(path):
            continue
        rel = path.relative_to(root).as_posix()
        if _is_in_ignored_dir(path, root):
            continue
        try:
            stat = path.stat()
        except OSError:
            continue
        files.append(
            {
                "path": rel,
                "absolute_path": str(path),
                "kind": _metadata_kind(path.name),
                "size": stat.st_size,
                "loc": _count_loc(path),
            }
        )
    return files


def _metadata_kind(name: str) -> str:
    lowered = name.lower()
    if lowered.endswith(".lock") or "lock" in lowered or lowered in {"go.sum", "requirements.txt"}:
        return "dependency-lockfile"
    if lowered.endswith((".toml", ".ini", ".json", ".yaml", ".yml", ".cfg")):
        return "project-config"
    return "project-metadata"


def _is_in_ignored_dir(path: Path, root: Path) -> bool:
    parts = path.relative_to(root).parts[:-1]
    return any(is_default_ignored_dir_name(part) for part in parts)


def _count_loc(path: Path) -> int:
    try:
        count = 0
        with path.open("r", encoding="utf-8", errors="ignore") as handle:
            for line in handle:
                if line.strip():
                    count += 1
        return count
    except OSError:
        return 0


def _json_dumps(payload: dict[str, object]) -> str:
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"
