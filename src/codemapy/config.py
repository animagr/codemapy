from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path


DEFAULT_IGNORES = {
    ".git",
    ".hg",
    ".svn",
    ".codemap",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".tox",
    ".venv",
    "__pycache__",
    "build",
    "codemapy-report.html",
    "dist",
    "htmlcov",
    "node_modules",
    "target",
    "venv",
}


@dataclass(frozen=True)
class Config:
    only: tuple[str, ...] = ()
    exclude: tuple[str, ...] = ()
    ignore_dirs: tuple[str, ...] = tuple(sorted(DEFAULT_IGNORES))
    max_file_bytes: int = 1_000_000


def load_config(root: Path) -> Config:
    path = root / ".codemap.json"
    if not path.exists():
        return Config()

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return Config()

    return Config(
        only=tuple(_clean_exts(data.get("only", []))),
        exclude=tuple(str(item) for item in data.get("exclude", []) if str(item).strip()),
        ignore_dirs=tuple(sorted(DEFAULT_IGNORES | set(data.get("ignore_dirs", [])))),
        max_file_bytes=int(data.get("max_file_bytes") or 1_000_000),
    )


def merge_cli_config(config: Config, only: str | None, exclude: str | None) -> Config:
    cli_only = tuple(_clean_exts((only or "").split(",")))
    cli_exclude = tuple(item.strip() for item in (exclude or "").split(",") if item.strip())
    return Config(
        only=cli_only or config.only,
        exclude=config.exclude + cli_exclude,
        ignore_dirs=config.ignore_dirs,
        max_file_bytes=config.max_file_bytes,
    )


def _clean_exts(values: object) -> list[str]:
    if not isinstance(values, list):
        values = list(values) if isinstance(values, tuple) else []
    result = []
    for value in values:
        ext = str(value).strip().lower().lstrip(".")
        if ext:
            result.append(ext)
    return result
