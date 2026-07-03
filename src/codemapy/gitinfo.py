"""Best-effort git metadata used for artifact staleness tracking.

Everything degrades to ``None`` when git is unavailable or the scanned
directory is not a repository, so callers never need to special-case it.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

GIT_TIMEOUT_SECONDS = 10


def head_commit(root: Path) -> str | None:
    """Return the full HEAD commit hash for *root*, or ``None``."""
    return _run_git(root, "rev-parse", "HEAD")


def is_dirty(root: Path) -> bool | None:
    """Return whether *root*'s working tree has uncommitted changes, or ``None``."""
    output = _run_git(root, "status", "--porcelain")
    if output is None:
        return None
    return bool(output.strip())


def _run_git(root: Path, *args: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), *args],
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip()
