from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path


DEFAULT_IGNORES = {
    ".coverage",
    ".git",
    ".godot",
    ".codemapy",
    ".hg",
    ".cargo",
    ".DS_Store",
    ".env",
    ".gradle",
    ".next",
    ".nuxt",
    ".svn",
    ".codemap",
    ".grammar-build",
    ".idea",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".svelte-kit",
    ".tox",
    ".venv",
    ".vscode",
    ".vite",
    "__pycache__",
    "build",
    "CMakeFiles",
    "codemapy-report.html",
    "coverage",
    "db",
    "DerivedData",
    "Debug",
    "dist",
    "grammars",
    "htmlcov",
    "incremental_db",
    "netlist",
    "node_modules",
    "Pods",
    "Release",
    "simulation",
    "synthesis",
    "target",
    "vendor",
    "venv",
    "Win32",
    "work",
    "x64",
}

DEFAULT_IGNORE_DIR_PREFIXES = (
    "cmake-build-",
    "impl",
)

DEFAULT_IGNORE_DIR_SUFFIXES = (
    ".egg-info",
)

PROJECT_METADATA_FILES = {
    ".eslintrc",
    ".eslintrc.cjs",
    ".eslintrc.js",
    ".eslintrc.json",
    ".eslintrc.yaml",
    ".eslintrc.yml",
    ".prettierrc",
    ".prettierrc.json",
    "bun.lock",
    "bun.lockb",
    "Cargo.lock",
    "Cargo.toml",
    "CMakeCache.txt",
    "CMakeLists.txt",
    "CMakePresets.json",
    "CMakeUserPresets.json",
    "compile_commands.json",
    "composer.lock",
    "constraints.txt",
    "deno.lock",
    "flake.lock",
    "Gemfile.lock",
    "GNUmakefile",
    "go.sum",
    "gradle.lockfile",
    "jsconfig.json",
    "makefile",
    "Makefile",
    "mix.lock",
    "mypy.ini",
    "package-lock.json",
    "package.json",
    "packages.lock.json",
    "Package.resolved",
    "Pipfile.lock",
    "pnpm-lock.yaml",
    "poetry.lock",
    "project.godot",
    "postcss.config.cjs",
    "postcss.config.js",
    "postcss.config.mjs",
    "postcss.config.ts",
    "pubspec.lock",
    "pyproject.toml",
    "pytest.ini",
    "requirements-dev.txt",
    "requirements.txt",
    "rollup.config.js",
    "rollup.config.mjs",
    "rollup.config.ts",
    "setup.cfg",
    "setup.py",
    "svelte.config.js",
    "svelte.config.ts",
    "tailwind.config.cjs",
    "tailwind.config.js",
    "tailwind.config.mjs",
    "tailwind.config.ts",
    "tox.ini",
    "tsconfig.json",
    "tsconfig.node.json",
    "uv.lock",
    "vite.config.js",
    "vite.config.mjs",
    "vite.config.ts",
    "vitest.config.js",
    "vitest.config.mjs",
    "vitest.config.ts",
    "webpack.config.cjs",
    "webpack.config.js",
    "webpack.config.mjs",
    "webpack.config.ts",
    "yarn.lock",
}

PROJECT_METADATA_SUFFIXES = (
    ".qpf",
    ".qsf",
    ".sdc",
    ".sln",
    ".vcxproj",
    ".vcxproj.filters",
    ".vcxproj.user",
    ".xdc",
    ".xpr",
)

GENERATED_FILE_EXTENSIONS = {
    ".a",
    ".bit",
    ".dll",
    ".dylib",
    ".elf",
    ".exe",
    ".fst",
    ".hex",
    ".jed",
    ".lib",
    ".log",
    ".map",
    ".o",
    ".obj",
    ".pof",
    ".pyc",
    ".pyo",
    ".rpt",
    ".sdf",
    ".so",
    ".sof",
    ".vcd",
    ".wlf",
}

GENERATED_FILE_SUFFIXES = (
    ".gd.uid",
    ".import",
    ".css.map",
    ".js.map",
    ".min.js",
    "_prim.v",
)

_DEFAULT_IGNORES_LOWER = {item.lower() for item in DEFAULT_IGNORES}
_PROJECT_METADATA_FILES_LOWER = {item.lower() for item in PROJECT_METADATA_FILES}
_PROJECT_METADATA_SUFFIXES_LOWER = tuple(suffix.lower() for suffix in PROJECT_METADATA_SUFFIXES)
_GENERATED_FILE_EXTENSIONS_LOWER = {suffix.lower() for suffix in GENERATED_FILE_EXTENSIONS}
_GENERATED_FILE_SUFFIXES_LOWER = tuple(suffix.lower() for suffix in GENERATED_FILE_SUFFIXES)


def is_default_ignored_dir_name(name: str) -> bool:
    normalized = name.lower()
    return (
        normalized in _DEFAULT_IGNORES_LOWER
        or any(normalized.startswith(prefix.lower()) for prefix in DEFAULT_IGNORE_DIR_PREFIXES)
        or any(normalized.endswith(suffix.lower()) for suffix in DEFAULT_IGNORE_DIR_SUFFIXES)
    )


def is_project_metadata_file(path: Path) -> bool:
    name = path.name
    normalized = name.lower()
    if normalized.startswith("requirements") and normalized.endswith(".txt"):
        return True
    return normalized in _PROJECT_METADATA_FILES_LOWER or any(
        normalized.endswith(suffix) for suffix in _PROJECT_METADATA_SUFFIXES_LOWER
    )


def is_generated_file(path: Path) -> bool:
    name = path.name.lower()
    suffix = path.suffix.lower()
    return suffix in _GENERATED_FILE_EXTENSIONS_LOWER or any(
        name.endswith(pattern) for pattern in _GENERATED_FILE_SUFFIXES_LOWER
    )


def metadata_kind(name: str) -> str:
    lowered = name.lower()
    if lowered.endswith(".lock") or "lock" in lowered or lowered in {"go.sum", "requirements.txt"}:
        return "dependency-lockfile"
    if lowered.endswith((".toml", ".ini", ".json", ".yaml", ".yml", ".cfg")):
        return "project-config"
    return "project-metadata"


@dataclass(frozen=True)
class Config:
    only: tuple[str, ...] = ()
    exclude: tuple[str, ...] = ()
    ignore_dirs: tuple[str, ...] = tuple(sorted(DEFAULT_IGNORES))
    keep_dirs: tuple[str, ...] = ()
    max_file_bytes: int = 1_000_000

    def is_ignored_name(self, name: str) -> bool:
        """Return True when *name* (a file or directory basename) is ignored.

        ``keep_dirs`` re-includes names that the built-in defaults would drop,
        so projects with a real ``build/`` or ``db/`` source directory can opt
        back in via ``.codemap.json``.
        """
        if name.lower() in {kept.lower() for kept in self.keep_dirs}:
            return False
        return name in self.ignore_dirs or is_default_ignored_dir_name(name)


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
        keep_dirs=tuple(str(item).strip() for item in data.get("keep_dirs", []) if str(item).strip()),
        max_file_bytes=int(data.get("max_file_bytes") or 1_000_000),
    )


def merge_cli_config(config: Config, only: str | None, exclude: str | None) -> Config:
    cli_only = tuple(_clean_exts((only or "").split(",")))
    cli_exclude = tuple(item.strip() for item in (exclude or "").split(",") if item.strip())
    return Config(
        only=cli_only or config.only,
        exclude=config.exclude + cli_exclude,
        ignore_dirs=config.ignore_dirs,
        keep_dirs=config.keep_dirs,
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
