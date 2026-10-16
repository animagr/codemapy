"""Declared dependencies and recognised frameworks, read from project manifests.

An agent orients faster knowing "PySide6 + pytest" or "React + Vite + Vitest"
before opening any code. This reads the manifests the scan already lists
(``pyproject.toml``, ``requirements*.txt``, ``package.json``, ``Cargo.toml``,
``go.mod``) near the root, skipping ones inside test fixtures, and matches
dependency names against a short table of well-known frameworks and tools,
in the spirit of Understand-Anything's framework registry. No dependency
is installed or resolved; only names are read.
"""

from __future__ import annotations

import json
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

from codemapy.models import Report
from codemapy.testfiles import in_test_directory

MAX_MANIFEST_DEPTH = 3
_REQUIREMENT_NAME = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)")
_GO_REQUIRE_LINE = re.compile(r"^\s*([^\s()]+)\s+v[^\s]+(?P<indirect>\s*//\s*indirect)?")

# Dependency name (lowercase) -> label. Well-known frameworks and tools only;
# everything else is still listed as a plain dependency.
FRAMEWORKS: dict[str, str] = {
    # Python
    "django": "Django (web)",
    "flask": "Flask (web)",
    "fastapi": "FastAPI (web)",
    "pyside6": "PySide6 (Qt GUI)",
    "pyqt5": "PyQt5 (Qt GUI)",
    "pyqt6": "PyQt6 (Qt GUI)",
    "numpy": "NumPy",
    "scipy": "SciPy",
    "pandas": "pandas",
    "matplotlib": "Matplotlib (plots)",
    "sympy": "SymPy",
    "pytest": "pytest (tests)",
    "sqlalchemy": "SQLAlchemy (database)",
    "pydantic": "Pydantic",
    "click": "Click (CLI)",
    "typer": "Typer (CLI)",
    "pyvisa": "PyVISA (instrument control)",
    "pyserial": "pySerial (serial ports)",
    "nidaqmx": "NI-DAQmx",
    "torch": "PyTorch",
    "tensorflow": "TensorFlow",
    "scikit-learn": "scikit-learn",
    # JavaScript / TypeScript
    "react": "React (UI)",
    "vue": "Vue (UI)",
    "svelte": "Svelte (UI)",
    "@sveltejs/kit": "SvelteKit",
    "next": "Next.js",
    "@angular/core": "Angular",
    "vite": "Vite (build)",
    "webpack": "webpack (build)",
    "typescript": "TypeScript",
    "jest": "Jest (tests)",
    "vitest": "Vitest (tests)",
    "mocha": "Mocha (tests)",
    "@playwright/test": "Playwright (tests)",
    "cypress": "Cypress (tests)",
    "express": "Express (web server)",
    "fastify": "Fastify (web server)",
    "electron": "Electron (desktop)",
    "tailwindcss": "Tailwind CSS",
    "three": "three.js (3D)",
    "d3": "D3 (charts)",
    # Rust
    "tokio": "Tokio (async)",
    "serde": "Serde",
    "clap": "clap (CLI)",
    "axum": "axum (web)",
    "bevy": "Bevy (game engine)",
    "wasm-bindgen": "wasm-bindgen (WebAssembly)",
    "tauri": "Tauri (desktop)",
    # Go
    "github.com/spf13/cobra": "Cobra (CLI)",
    "github.com/gin-gonic/gin": "Gin (web)",
    "google.golang.org/grpc": "gRPC",
}


@dataclass(frozen=True)
class Manifest:
    """One manifest's declared dependencies (names only)."""

    path: str
    name: str | None
    dependencies: tuple[str, ...]
    dev_dependencies: tuple[str, ...]


def read_stack(report: Report) -> list[Manifest]:
    """Manifests near the root (not inside test folders), shallowest first."""
    manifests = []
    for item in sorted(report.metadata_files, key=lambda item: (item.path.count("/"), item.path)):
        if item.path.count("/") >= MAX_MANIFEST_DEPTH or in_test_directory(item.path):
            continue
        manifest = _read_manifest(report.root / item.path, item.path)
        if manifest is not None and (manifest.dependencies or manifest.dev_dependencies):
            manifests.append(manifest)
    return manifests


def frameworks(manifests: list[Manifest]) -> list[str]:
    """Labels of the recognised frameworks and tools, in table order."""
    names = {
        name.lower() for manifest in manifests for name in manifest.dependencies + manifest.dev_dependencies
    }
    return [label for name, label in FRAMEWORKS.items() if name in names]


def _read_manifest(path: Path, rel: str) -> Manifest | None:
    name = rel.rsplit("/", 1)[-1].lower()
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None
    try:
        if name == "pyproject.toml":
            return _pyproject(rel, tomllib.loads(text))
        if name.startswith("requirements") and name.endswith(".txt"):
            names = _requirement_names(text.splitlines())
            is_dev = any(marker in name for marker in ("dev", "test", "doc"))
            return Manifest(rel, None, () if is_dev else names, names if is_dev else ())
        if name == "package.json":
            data = json.loads(text)
            return Manifest(
                rel, data.get("name"), _keys(data.get("dependencies")), _keys(data.get("devDependencies"))
            )
        if name == "cargo.toml":
            return _cargo(rel, tomllib.loads(text))
        if name == "go.mod":
            return _go_mod(rel, text)
    except (tomllib.TOMLDecodeError, json.JSONDecodeError, AttributeError, TypeError):
        return None
    return None


def _pyproject(rel: str, data: dict[str, object]) -> Manifest:
    project = data.get("project") if isinstance(data.get("project"), dict) else {}
    dependencies = list(_requirement_names(project.get("dependencies", [])))
    dev = [
        name
        for group in _dict(project.get("optional-dependencies")).values()
        for name in _requirement_names(group)
    ]
    dev += [
        name for group in _dict(data.get("dependency-groups")).values() for name in _requirement_names(group)
    ]
    poetry = _dict(_dict(data.get("tool")).get("poetry"))
    dependencies += [key for key in _dict(poetry.get("dependencies")) if key.lower() != "python"]
    for group in _dict(poetry.get("group")).values():
        dev += list(_dict(_dict(group).get("dependencies")))
    return Manifest(rel, project.get("name") or poetry.get("name"), _unique(dependencies), _unique(dev))


def _cargo(rel: str, data: dict[str, object]) -> Manifest:
    dependencies = list(_dict(data.get("dependencies"))) + list(
        _dict(_dict(data.get("workspace")).get("dependencies"))
    )
    dev = list(_dict(data.get("dev-dependencies"))) + list(_dict(data.get("build-dependencies")))
    return Manifest(rel, _dict(data.get("package")).get("name"), _unique(dependencies), _unique(dev))


def _go_mod(rel: str, text: str) -> Manifest:
    module = None
    dependencies = []
    in_block = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("module "):
            module = stripped.removeprefix("module ").strip()
        elif stripped.startswith("require ("):
            in_block = True
        elif in_block and stripped.startswith(")"):
            in_block = False
        elif in_block or stripped.startswith("require "):
            match = _GO_REQUIRE_LINE.match(stripped.removeprefix("require "))
            # Indirect requirements are pulled in by other modules, not declared.
            if match and not match.group("indirect"):
                dependencies.append(match.group(1))
    return Manifest(rel, module, _unique(dependencies), ())


def _requirement_names(lines: object) -> tuple[str, ...]:
    names = []
    for line in lines if isinstance(lines, list) else []:
        text = str(line).strip()
        if not text or text.startswith(("#", "-")):
            continue
        match = _REQUIREMENT_NAME.match(text)
        if match:
            names.append(match.group(1))
    return _unique(names)


def _keys(value: object) -> tuple[str, ...]:
    return _unique(list(value)) if isinstance(value, dict) else ()


def _dict(value: object) -> dict:
    return value if isinstance(value, dict) else {}


def _unique(names: list[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(names))
