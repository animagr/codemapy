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
"""

from __future__ import annotations

import json
import tomllib
from dataclasses import dataclass
from pathlib import Path

from codemapy.models import Report

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
# Test modules are excluded from guard promotion: `unittest.main()` under a
# guard is boilerplate in every test file, they import broadly (so they rank
# high on fan-out) and nothing imports them, which would otherwise let a test
# suite claim every guard slot. Only a containing directory counts as evidence,
# never the filename -- `test_*.py` / `*_test.py` at the root is as often the
# application itself (e.g. an instrument test bench) as it is a test module.
TEST_DIRECTORY_NAMES = {"test", "tests"}


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
    candidates: dict[str, str | None] = {
        file.path: None
        for file in report.files
        if file.path.rsplit("/", 1)[-1].lower() in ENTRY_POINT_BASENAMES
        and len(file.path.split("/")) <= MAX_ENTRY_POINT_DEPTH
    }
    guarded = {module.path: module for module in report.modules if module.has_main_guard}
    for module in report.modules:
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
        if path not in candidates and module.fan_in == 0 and not _is_test_module(path)
    ]
    unimported.sort(key=lambda module: (-module.fan_out, module.path))
    entries.extend(
        EntryPoint(name=module.path, path=module.path, note=MAIN_GUARD_NOTE)
        for module in unimported[:MAX_MAIN_GUARD_ENTRIES]
    )
    return entries[:MAX_ENTRY_POINTS]


def _is_test_module(path: str) -> bool:
    """True if *path* sits under a test directory (see TEST_DIRECTORY_NAMES)."""
    return any(part.lower() in TEST_DIRECTORY_NAMES for part in path.split("/")[:-1])


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
