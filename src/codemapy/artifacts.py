from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from codemapy.config import is_default_ignored_dir_name, is_project_metadata_file
from codemapy.models import ModuleNode, Report
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
        manifest=directory / "manifest.json",
    )

    write_html(report, paths.report)
    paths.context.write_text(_json_dumps(report_payload(report, generated_at)), encoding="utf-8")
    paths.hubs.write_text(_json_dumps(hubs_payload(report)), encoding="utf-8")
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
        "warnings": list(report.warnings),
    }


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
            "manifest": paths.manifest.name,
        },
        "counts": {
            "files": len(report.files),
            "metadata_files": len(metadata_files),
            "loc": report.total_loc,
            "internal_dependencies": len(report.edges),
            "external_references": len(report.external_imports),
        },
        "metadata_files": metadata_files,
        "languages": report.languages,
    }


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
