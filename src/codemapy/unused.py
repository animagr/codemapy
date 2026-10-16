"""Find source files that nothing in the map imports: candidates for removal.

A file-level version of codegraph's dead-code view. Only languages whose
imports are analysed are considered, since a file in any other language has
no importers by construction. Files that are normally loaded without an
import are left out: entry points and runnable scripts, tests, generated
files, package and crate roots, C/C++ and HDL compile units, framework route
files, and anything under folders of standalone scripts or examples. What
remains is a list to check, not a verdict: a file may still be loaded by
name (plugins, reflection, HTML script tags).
"""

from __future__ import annotations

import json
import posixpath
import re
import tomllib
from dataclasses import dataclass
from pathlib import PurePosixPath

from codemapy.deps.registry import extractor_for_ext
from codemapy.entrypoints import MAIN_FUNCTION_LANGUAGES, entry_points
from codemapy.models import ModuleNode, Report
from codemapy.testfiles import in_test_directory

# Compile units and scene scripts: linked, instantiated or attached, never imported.
ROOT_LANGUAGES = frozenset(
    {"C", "C++", "Verilog", "Verilog Header", "SystemVerilog", "SystemVerilog Header", "GDScript", "C#"}
)
# File names loaded by convention rather than by an import.
CONVENTIONAL_NAMES = frozenset(
    {
        "__init__.py",
        "__main__.py",
        "conftest.py",
        "setup.py",
        "manage.py",
        "lib.rs",
        "main.rs",
        "build.rs",
        "init.lua",
        # Java package and module descriptors.
        "package-info.java",
        "module-info.java",
        # Sphinx and nox configuration.
        "conf.py",
        "noxfile.py",
    }
)
CONVENTIONAL_STEMS = frozenset(
    # index/main/app files, and Next.js app-router files.
    {
        "index",
        "main",
        "app",
        "page",
        "layout",
        "route",
        "loading",
        "error",
        "not-found",
        "template",
        "middleware",
    }
)
# Tool config files (`vite.config.ts`, `astro.config.mjs`) and worker scripts
# (`parse-worker.ts`, started with `new Worker(path)`) are loaded by name.
_LOADED_BY_NAME = re.compile(r"(?:^|[-_.])(?:config|worker)(?:[-_.]|$)", re.IGNORECASE)
# Languages whose files compile as one package per folder (see Go below).
PACKAGE_LANGUAGES = frozenset({"Go"})

# Folders whose files are run directly or kept as samples.
STANDALONE_FOLDERS = frozenset(
    {
        "bench",
        "benches",
        "benchmarks",
        "bin",
        "demo",
        "demos",
        "doc",
        "docs",
        "example",
        "examples",
        "migrations",
        "sample",
        "samples",
        "script",
        "scripts",
        "template",
        "templates",
        "tool",
        "tools",
    }
)


# Languages that are left out when the project resolves imports in ways the
# map cannot see: path aliases, or component files it does not scan.
JS_LANGUAGES = frozenset({"JavaScript", "TypeScript", "Svelte"})
UNMAPPED_COMPONENT_EXTENSIONS = frozenset({".vue", ".astro", ".mdx"})
_ALIAS_CONFIG = re.compile(r'"(?:paths|baseUrl)"\s*:|\balias\s*:', re.IGNORECASE)
_ALIAS_CONFIG_FILES = re.compile(
    r"^(?:tsconfig|jsconfig)[\w.-]*\.json$|^(?:vite|webpack|svelte)\.config\.", re.IGNORECASE
)


@dataclass(frozen=True)
class UnusedResult:
    """Possibly unused modules, and why whole languages were left out."""

    modules: tuple[ModuleNode, ...]
    left_out: tuple[str, ...]


def possibly_unused(report: Report) -> UnusedResult:
    """Modules nothing imports that are not otherwise accounted for, by path."""
    entry_paths = {entry.path for entry in entry_points(report) if entry.path} | _declared_entry_files(report)
    used_packages = _used_packages(report)
    js_reason = _js_blind_spot(report)
    modules = tuple(
        sorted(
            (
                module
                for module in report.modules
                if _unused(module, entry_paths)
                and (
                    module.language not in PACKAGE_LANGUAGES
                    or posixpath.dirname(module.path) not in used_packages
                )
                and not (js_reason and module.language in JS_LANGUAGES)
            ),
            key=lambda module: module.path,
        )
    )
    left_out = (f"JavaScript/TypeScript files are left out: {js_reason}",) if js_reason else ()
    return UnusedResult(modules=modules, left_out=left_out)


def _js_blind_spot(report: Report) -> str | None:
    """Why JS/TS importers may be invisible to the map, or None."""
    if not any(module.language in JS_LANGUAGES for module in report.modules):
        return None
    components = sorted(
        group.extension for group in report.asset_groups if group.extension in UNMAPPED_COMPONENT_EXTENSIONS
    )
    if components:
        return f"{', '.join(components)} files can import them, and those are not mapped"
    for item in report.metadata_files:
        name = item.path.rsplit("/", 1)[-1]
        if not _ALIAS_CONFIG_FILES.match(name) or in_test_directory(item.path):
            continue
        if name.lower().startswith("svelte.config."):
            return f"`{item.path}` sets up import aliases ($lib), which the map does not resolve"
        try:
            text = (report.root / item.path).read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if _ALIAS_CONFIG.search(text):
            return f"`{item.path}` declares import path aliases, which the map does not resolve"
    return None


def _used_packages(report: Report) -> set[str]:
    """Go package folders that something imports, or that hold a main() program.

    Every file of a Go package compiles together, so a file is unused only
    when its whole package is.
    """
    used = set()
    for module in report.modules:
        if module.language in PACKAGE_LANGUAGES and (module.fan_in or _defines_main(module)):
            used.add(posixpath.dirname(module.path))
    return used


def _unused(module: ModuleNode, entry_paths: set[str]) -> bool:
    if module.fan_in or module.is_test or module.is_generated or module.has_main_guard:
        return False
    if module.path in entry_paths or module.language in ROOT_LANGUAGES:
        return False
    path = PurePosixPath(module.path)
    if extractor_for_ext(path.suffix) is None:
        return False
    if path.name in CONVENTIONAL_NAMES or path.stem.lower() in CONVENTIONAL_STEMS:
        return False
    if path.name.startswith("+") or path.name.endswith(".d.ts"):
        return False  # SvelteKit route files, TypeScript declarations
    if _LOADED_BY_NAME.search(path.stem):
        return False
    if any(part.lower() in STANDALONE_FOLDERS for part in path.parts[:-1]):
        return False
    return not _defines_main(module)


def _defines_main(module: ModuleNode) -> bool:
    """A program entry: main() in C/C++/Go/Rust, or a main method in Java."""
    if module.language in MAIN_FUNCTION_LANGUAGES:
        return any(symbol.kind == "function" and symbol.name == "main" for symbol in module.symbols)
    if module.language == "Java":
        return any(
            child.name == "main"
            for symbol in module.symbols
            for child in symbol.children
            if child.kind == "method"
        )
    return False


def _declared_entry_files(report: Report) -> set[str]:
    """Files that manifests name as programs or libraries, at any depth.

    ``package.json`` ``main`` / ``module`` / ``bin``; ``Cargo.toml`` crate
    roots (``[lib] path``, ``[[bin]]``, ``[[bench]]``, ...) and ``src/bin/``
    binaries; ``pyproject.toml`` ``[project.scripts]`` modules.
    """
    declared: set[str] = set()
    for item in report.metadata_files:
        name = item.path.rsplit("/", 1)[-1].lower()
        folder = posixpath.dirname(item.path)
        try:
            text = (report.root / item.path).read_text(encoding="utf-8", errors="ignore")
            if name == "package.json":
                declared.update(_join(folder, target) for target in _package_targets(json.loads(text)))
            elif name == "cargo.toml":
                declared.update(_join(folder, target) for target in _cargo_targets(tomllib.loads(text)))
            elif name == "pyproject.toml":
                declared.update(_script_modules(report, folder, tomllib.loads(text)))
        except (OSError, json.JSONDecodeError, tomllib.TOMLDecodeError, AttributeError, TypeError):
            continue
    declared.update(
        module.path
        for module in report.modules
        if module.language == "Rust" and "/src/bin/" in f"/{module.path}"
    )
    return declared


def _package_targets(data: dict[str, object]) -> list[str]:
    targets = [data[key] for key in ("main", "module") if isinstance(data.get(key), str)]
    bin_field = data.get("bin")
    if isinstance(bin_field, str):
        targets.append(bin_field)
    elif isinstance(bin_field, dict):
        targets.extend(value for value in bin_field.values() if isinstance(value, str))
    return targets


def _cargo_targets(data: dict[str, object]) -> list[str]:
    targets = []
    lib = data.get("lib")
    if isinstance(lib, dict) and isinstance(lib.get("path"), str):
        targets.append(lib["path"])
    for section in ("bin", "bench", "example", "test"):
        entries = data.get(section)
        if isinstance(entries, list):
            targets.extend(
                entry["path"]
                for entry in entries
                if isinstance(entry, dict) and isinstance(entry.get("path"), str)
            )
    return targets


def _script_modules(report: Report, folder: str, data: dict[str, object]) -> set[str]:
    project = data.get("project")
    scripts = project.get("scripts") if isinstance(project, dict) else None
    if not isinstance(scripts, dict):
        return set()
    paths = {module.path for module in report.modules}
    found = set()
    for target in scripts.values():
        module = str(target).split(":", 1)[0].strip().replace(".", "/")
        for base in (folder, _join(folder, "src")):
            for candidate in (f"{module}.py", f"{module}/__init__.py"):
                path = _join(base, candidate)
                if path in paths:
                    found.add(path)
    return found


def _join(folder: str, target: str) -> str:
    return (
        posixpath.normpath(posixpath.join(folder, target.removeprefix("./")))
        if folder
        else posixpath.normpath(target)
    )
