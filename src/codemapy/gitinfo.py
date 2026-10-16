"""Best-effort git metadata used for artifact staleness tracking.

Everything degrades to ``None`` when git is unavailable or the scanned
directory is not a repository, so callers never need to special-case it.
"""

from __future__ import annotations

import subprocess
from collections.abc import Collection
from pathlib import Path, PurePosixPath

GIT_TIMEOUT_SECONDS = 10

# Each ``git status --porcelain`` entry starts with two status letters and a space.
_STATUS_PREFIX_LEN = 3
# Status letters whose entry carries a second path (the rename/copy source).
_TWO_PATH_STATUSES = frozenset("RC")


def head_commit(root: Path) -> str | None:
    """Return the full HEAD commit hash for *root*, or ``None``."""
    output = _run_git(root, "rev-parse", "HEAD")
    return output.strip() if output is not None else None


def is_dirty(root: Path, ignored_dir_names: Collection[str] = ()) -> bool | None:
    """Return whether *root*'s working tree has uncommitted changes, or ``None``.

    Changes under a directory whose name is in *ignored_dir_names* do not count,
    so a tool's own untracked output folder (such as ``.codemapy``) never makes
    the tree look dirty.
    """
    output = _run_git(root, "status", "--porcelain", "-z")
    if output is None:
        return None
    ignored = frozenset(ignored_dir_names)
    return any(ignored.isdisjoint(PurePosixPath(path).parts) for path in _status_paths(output))


def _status_paths(output: str) -> list[str]:
    """Return the path of each entry in ``git status --porcelain -z`` output.

    Entries are NUL-terminated ``XY <path>`` records. A rename or copy entry is
    followed by one extra NUL-terminated field holding its source path, which
    is skipped so it is not misread as an entry of its own.
    """
    paths: list[str] = []
    fields = iter(output.split("\0"))
    for field in fields:
        if len(field) <= _STATUS_PREFIX_LEN:
            continue
        status = field[: _STATUS_PREFIX_LEN - 1]
        paths.append(field[_STATUS_PREFIX_LEN:])
        if _TWO_PATH_STATUSES.intersection(status):
            next(fields, None)
    return paths


def _run_git(root: Path, *args: str) -> str | None:
    """Run git in *root* and return its raw stdout, or ``None`` on any failure."""
    try:
        result = subprocess.run(
            ["git", "-C", str(root), *args],
            capture_output=True,
            # -z output is not quoted, so paths arrive as raw UTF-8 regardless of locale.
            encoding="utf-8",
            errors="replace",
            timeout=GIT_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return result.stdout
