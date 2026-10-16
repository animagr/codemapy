"""Entry-point detection shared by summary.md and report.html.

Entry points come from four signals: script tables in project manifests
(``pyproject.toml`` / ``package.json``), well-known main-file basenames, files
that define a top-level ``main()`` function in a language where that is the
program entry point (C, C++, Go, Rust), and Python files carrying a top-level
``if __name__ == "__main__":`` guard.

The guard is deliberately the weakest signal. On its own it is near-worthless
evidence: library modules routinely carry one as a manual smoke-test harness,
and in hardware/instrument codebases it is the norm rather than the exception
(12 of 17 and 38 of 43 Python files in two sampled firmware repos). What makes
it discriminating is pairing it with ``fan_in == 0``: a runnable file that
nothing imports is a program, whereas a runnable file that other modules import
is a library with a demo block. Guard-only entries are therefore ranked by
fan-out (the module that pulls in the most of the codebase is the application)
and capped, so they can never crowd out manifest or ``main()`` evidence.

Test and generated files are never promoted by the basename, ``main()`` or
guard signals: a test runner's harness is not the program, and a test suite
would otherwise claim every guard slot. Manifest scripts are taken as written.
"""

from __future__ import annotations

import json
import tomllib
from dataclasses import dataclass
from pathlib import Path

from codemapy.models import Report
from codemapy.testfiles import is_test_file

ENTRY_POINT_BASENAMES = {
    "__main__.py",
    "main.py",
    "app.py",
    "manage.py",
    "launcher.py",
    "run.py",
    "main.c",
    "main.cpp",
    "main.go",
    "main.rs",
    "main.lua",
    "main.gd",
    "program.cs",
    "main.java",
    "index.js",
    "index.ts",
}

# Languages where a top-level function named `main` is the program entry
# point. Java/C# mains are methods nested in a class and their conventional
# file names are covered by ENTRY_POINT_BASENAMES instead.
MAIN_FUNCTION_LANGUAGES = {"C", "C++", "Go", "Rust"}
MAIN_DEFINER_NOTE = "defines main()"
MAIN_GUARD_NOTE = "__main__ guard"

MAX_ENTRY_POINTS = 10
MAX_ENTRY_POINT_DEPTH = 3
# Guard-only candidates are ranked by fan-out and capped well below
# MAX_ENTRY_POINTS: in a repo where every module has a demo block, the tail of
# this list is noise, and the strong signals must keep their slots.
MAX_MAIN_GUARD_ENTRIES = 3

# `index.js` / `index.ts` are usually barrel files that re-export a folder's
# modules (codegraph has a dozen under src/). Only a package-level one is an
# entry point: directly in a package folder (the root, or any folder holding a
# package.json) or in that folder's `src/` or `lib/`.
BARREL_BASENAMES = {"index.js", "index.ts"}
PACKAGE_SOURCE_DIRS = {"src", "lib"}
PACKAGE_MANIFEST = "package.json"


@dataclass(frozen=True)
class EntryPoint:
    """A detected entry point.

    ``name`` is the display text (a script name or file path), ``target`` the
    script target for manifest scripts, ``path`` the project-relative source
    file when the entry corresponds to a scanned file, and ``note`` the
    provenance (e.g. ``"pyproject.toml script"`` or ``"defines main()"``).
    """

    name: str
    target: str | None = None
    path: str | None = None
    note: str | None = None


def entry_points(report: Report) -> list[EntryPoint]:
    """Return detected entry points, strongest evidence first, capped at 10."""
    entries: list[EntryPoint] = []
    known_paths = {file.path for file in report.files}

    manifests = [
        item.path
        for item in report.metadata_files
        if item.path.rsplit("/", 1)[-1].lower() in {"pyproject.toml", "package.json"}
        and len(item.path.split("/")) <= MAX_ENTRY_POINT_DEPTH
    ]
    manifests.sort(key=lambda path: (len(path.split("/")), path))
    for rel in manifests:
        prefix = f"{rel.rsplit('/', 1)[0]}/" if "/" in rel else ""
        if rel.endswith("pyproject.toml"):
            entries.extend(_pyproject_scripts(report.root / rel, rel))
        else:
            entries.extend(_package_json_entries(report.root / rel, rel, prefix, known_paths))

    # Well-known basenames near the root, plus any file whose symbols define
    # a top-level main() (allowed at any depth: it is direct evidence, where
    # basename matching is only a naming convention).
    excluded = _test_or_generated_paths(report)
    package_dirs = {""} | {
        _parent_dir(item.path)
        for item in report.metadata_files
        if item.path.rsplit("/", 1)[-1].lower() == PACKAGE_MANIFEST
    }
    candidates: dict[str, str | None] = {
        file.path: None
        for file in report.files
        if _is_entry_basename(file.path, package_dirs) and file.path not in excluded
    }
    guarded = {
        module.path: module
        for module in report.modules
        if module.has_main_guard and module.path not in excluded
    }
    for module in report.modules:
        if module.path in excluded:
            continue
        if module.language in MAIN_FUNCTION_LANGUAGES and any(
            symbol.kind == "function" and symbol.name == "main" for symbol in module.symbols
        ):
            candidates[module.path] = MAIN_DEFINER_NOTE

    # A guard on a file the basenames already caught is corroborating detail,
    # not a second entry, so it only supplies the note.
    for path, note in candidates.items():
        if note is None and path in guarded:
            candidates[path] = MAIN_GUARD_NOTE

    for path in sorted(candidates, key=lambda item: (len(item.split("/")), item)):
        entries.append(EntryPoint(name=path, path=path, note=candidates[path]))

    # Weakest signal, so appended last: runnable Python files that nothing
    # imports, the most connected first.
    unimported = [
        module
        for path, module in guarded.items()
        if path not in candidates and module.fan_in == 0
    ]
    unimported.sort(key=lambda module: (-module.fan_out, module.path))
    entries.extend(
        EntryPoint(name=module.path, path=module.path, note=MAIN_GUARD_NOTE)
        for module in unimported[:MAX_MAIN_GUARD_ENTRIES]
    )
    return entries[:MAX_ENTRY_POINTS]


def _is_entry_basename(path: str, package_dirs: set[str]) -> bool:
    parts = path.split("/")
    basename = parts[-1].lower()
    if basename not in ENTRY_POINT_BASENAMES or len(parts) > MAX_ENTRY_POINT_DEPTH:
        return False
    if basename not in BARREL_BASENAMES:
        return True
    directory = _parent_dir(path)
    if directory in package_dirs:
        return True
    in_source_dir = directory.rsplit("/", 1)[-1].lower() in PACKAGE_SOURCE_DIRS
    return in_source_dir and _parent_dir(directory) in package_dirs


def _parent_dir(path: str) -> str:
    return path.rsplit("/", 1)[0] if "/" in path else ""


def _test_or_generated_paths(report: Report) -> set[str]:
    """Paths that no basename, ``main()`` or guard signal may promote.

    The flags set by ``build_report`` are re-derived from the path and imports
    as well, so hand-built reports are classified the same way.
    """
    excluded = {
        module.path
        for module in report.modules
        if module.is_test or module.is_generated or is_test_file(module.path, module.imports)
    }
    excluded.update(file.path for file in report.files if file.is_generated or is_test_file(file.path))
    return excluded


def _pyproject_scripts(path: Path, rel: str) -> list[EntryPoint]:
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError, UnicodeDecodeError):
        return []
    project = data.get("project")
    if not isinstance(project, dict):
        return []
    entries = []
    for table_name in ("scripts", "gui-scripts"):
        scripts = project.get(table_name)
        if isinstance(scripts, dict):
            entries.extend(
                EntryPoint(name=name, target=str(target), note=f"{rel} script")
                for name, target in sorted(scripts.items())
            )
    return entries


def _package_json_entries(
    path: Path, rel: str, prefix: str, known_paths: set[str]
) -> list[EntryPoint]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return []
    if not isinstance(data, dict):
        return []

    def entry(target: str, note: str) -> EntryPoint:
        name = f"{prefix}{target}"
        return EntryPoint(name=name, path=name if name in known_paths else None, note=note)

    entries = []
    main = data.get("main")
    if isinstance(main, str):
        entries.append(entry(main, f"{rel} main"))
    bin_field = data.get("bin")
    if isinstance(bin_field, str):
        entries.append(entry(bin_field, f"{rel} bin"))
    elif isinstance(bin_field, dict):
        entries.extend(
            entry(target, f"{rel} bin: {name}")
            for name, target in sorted(bin_field.items())
            if isinstance(target, str)
        )
    return entries
