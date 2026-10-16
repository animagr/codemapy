"""``--why FILE``: why a file is, or is not, in the map.

Follows codemap's explanation of unindexed files: the first matching reason
wins, checked in order: mapped; dropped or inside a skipped folder (with
the rule and the .codemap.json change that would map it); missing on disk;
a folder; documentation or project metadata; an extension codemapy does not
map; otherwise added or changed after the artifacts were written.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from codemapy.models import SkipReason
from codemapy.query import Artifacts
from codemapy.scanner import DOC_EXTENSIONS, LANGUAGES_BY_EXT
from codemapy.unresolved import REASON_LABELS, skip_hint


def why(artifacts: Artifacts, raw: str, cwd: Path) -> list[str]:
    """Explanation lines for the file or folder *raw*."""
    path = _project_path(artifacts.root, raw, cwd)
    if path is None:
        return [f"{raw} is outside the mapped project {artifacts.root}"]
    module = artifacts.modules.get(path)
    if module is not None:
        return [_mapped_line(path, module) + f" {artifacts.source_note}"]
    skipped = _skip_record(artifacts, path)
    if skipped is not None:
        reason = SkipReason(skipped["reason"])
        hidden_by = f"{skipped['path']}/" if skipped["is_dir"] else str(skipped["path"])
        where = "it" if hidden_by.rstrip("/") == path else f"`{hidden_by}`"
        line = f"{path} is not mapped: {where} was skipped ({REASON_LABELS[reason]})"
        hint = skip_hint(reason, hidden_by)
        return [f"{line}. {hint}" if hint else line]
    absolute = artifacts.root / path
    if not absolute.exists():
        return [f"{path} does not exist under {artifacts.root}"]
    if absolute.is_dir():
        mapped = sum(1 for module_path in artifacts.modules if module_path.startswith(f"{path}/"))
        return [f"{path}/ is a folder holding {mapped} mapped source files {artifacts.source_note}"]
    if path in artifacts.doc_files:
        return [f"{path} is documentation: listed in doc_files, not mapped as source"]
    if path in artifacts.metadata_files:
        return [
            f"{path} is project metadata (a manifest, lockfile or config): listed in metadata_files, not mapped"
        ]
    suffix = absolute.suffix.lower()
    if suffix not in LANGUAGES_BY_EXT:
        kind = "documentation" if suffix in DOC_EXTENSIONS else "a file type codemapy does not map"
        return [f"{path} is not mapped: `{suffix or '(no extension)'}` is {kind}"]
    return [_late_line(artifacts, path, absolute)]


def _project_path(root: Path, raw: str, cwd: Path) -> str | None:
    """*raw* as a project-relative POSIX path, or None if it lies outside *root*.

    A relative *raw* is tried against the current directory, then the project
    root; the first that exists wins, and the root reading is used otherwise.
    """
    root = root.resolve()
    candidates = [Path(raw)] if Path(raw).is_absolute() else [cwd / raw, root / raw]
    chosen = next((candidate for candidate in candidates if candidate.exists()), candidates[-1])
    try:
        relative = chosen.resolve().relative_to(root).as_posix()
    except (OSError, ValueError):
        return None
    return "" if relative == "." else relative


def _skip_record(artifacts: Artifacts, path: str) -> dict[str, object] | None:
    """The skipped file itself, or its shallowest skipped ancestor folder."""
    records = {str(item["path"]): item for item in artifacts.skipped}
    if path in records:
        return records[path]
    parts = path.split("/")
    for depth in range(1, len(parts)):
        record = records.get("/".join(parts[:depth]))
        if record is not None and record.get("is_dir"):
            return record
    return None


def _mapped_line(path: str, module: dict[str, object]) -> str:
    flags = [
        flag
        for flag, on in (("test file", module.get("is_test")), ("generated", module.get("is_generated")))
        if on
    ]
    extra = f" ({', '.join(flags)})" if flags else ""
    return (
        f"{path} is mapped{extra}: {module.get('language')}, {module.get('loc')} loc, "
        f"fan-in {module.get('fan_in')}, fan-out {module.get('fan_out')}"
    )


def _late_line(artifacts: Artifacts, path: str, absolute: Path) -> str:
    try:
        generated = datetime.fromisoformat(str(artifacts.generated_at)).timestamp()
        newer = absolute.stat().st_mtime > generated
    except (TypeError, ValueError, OSError):
        newer = False
    if newer:
        return f"{path} was added or changed after the artifacts were written; refresh with --artifacts --yes"
    return (
        f"{path} is not in the map and no skip rule was recorded for it; it may have been added after the "
        "artifacts were written, or be over max_file_bytes in artifacts from an older codemapy"
    )
