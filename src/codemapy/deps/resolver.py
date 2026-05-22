from __future__ import annotations

import posixpath
from pathlib import Path

from codemapy.deps.verilog import declared_modules
from codemapy.models import FileNode, ImportRef


JS_EXTENSIONS = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".svelte")
SVELTE_SCRIPT_EXTENSIONS = (".svelte.ts", ".svelte.js")
VERILOG_LANGUAGES = {"Verilog", "Verilog Header", "SystemVerilog", "SystemVerilog Header"}
VERILOG_EXTENSIONS = (".v", ".vh", ".sv", ".svh")
C_LANGUAGES = {"C", "C Header", "C/C++ Header", "C++", "C++ Header"}
C_INCLUDE_DIRS = ("include", "inc", "includes", "Config", "config", "utils", "src")
RUST_ROOT_FILES = ("lib.rs", "main.rs")


class ImportResolver:
    def __init__(self, root: Path, files: tuple[FileNode, ...]) -> None:
        self.root = root.resolve()
        self.paths = {file.path for file in files}
        self.files = files
        self.source_roots = self._detect_source_roots(files)
        self.verilog_modules = self._build_verilog_module_index(files)
        self.rust_crate_roots = self._build_rust_crate_roots(files)

    def resolve(self, source: FileNode, ref: ImportRef) -> str | None:
        if source.language == "Python":
            return self._resolve_python(source, ref.raw)
        if source.language in {"JavaScript", "TypeScript", "Svelte"}:
            return self._resolve_javascript(source, ref.raw)
        if source.language in VERILOG_LANGUAGES:
            return self._resolve_verilog(source, ref)
        if source.language in C_LANGUAGES:
            return self._resolve_c_include(source, ref.raw)
        if source.language == "Rust":
            return self._resolve_rust(source, ref)
        return None

    def _resolve_python(self, source: FileNode, raw: str) -> str | None:
        if raw.startswith("."):
            level = len(raw) - len(raw.lstrip("."))
            remainder = raw[level:]
            base = Path(source.path).parent
            for _ in range(max(level - 1, 0)):
                base = base.parent
            parts = [part for part in remainder.split(".") if part]
            return self._first_existing(self._python_candidates(base, parts))

        parts = [part for part in raw.split(".") if part]
        candidates = []
        for source_root in self.source_roots:
            candidates.extend(self._python_candidates(source_root, parts))
        return self._first_existing(candidates)

    def _python_candidates(self, base: Path, parts: list[str]) -> list[str]:
        joined = base.joinpath(*parts) if parts else base
        candidates = [
            joined.with_suffix(".py").as_posix(),
            joined.joinpath("__init__.py").as_posix(),
        ]
        if parts:
            candidates.append(base.joinpath(parts[0], "__init__.py").as_posix())
        return candidates

    def _resolve_javascript(self, source: FileNode, raw: str) -> str | None:
        if not raw.startswith("."):
            return None
        base = Path(source.path).parent / raw
        candidates: list[str] = []
        if base.suffix:
            candidates.append(base.as_posix())
            if base.suffix == ".svelte":
                candidates.extend(f"{base.as_posix()}{ext}" for ext in (".ts", ".js"))
            elif base.suffix in {".js", ".mjs", ".cjs"}:
                candidates.extend(base.with_suffix(ext).as_posix() for ext in (".ts", ".tsx"))
            elif base.suffix == ".jsx":
                candidates.append(base.with_suffix(".tsx").as_posix())
        else:
            candidates.extend((base.with_suffix(ext).as_posix() for ext in JS_EXTENSIONS))
            candidates.extend(f"{base.as_posix()}{ext}" for ext in SVELTE_SCRIPT_EXTENSIONS)
            candidates.extend((base / f"index{ext}").as_posix() for ext in JS_EXTENSIONS)
        return self._first_existing(candidates)

    def _resolve_verilog(self, source: FileNode, ref: ImportRef) -> str | None:
        if ref.kind == "include":
            return self._resolve_verilog_include(source, ref.raw)
        if ref.kind == "module":
            candidates = self.verilog_modules.get(ref.raw, ())
            return candidates[0] if candidates else None
        return None

    def _resolve_verilog_include(self, source: FileNode, raw: str) -> str | None:
        include_path = Path(raw)
        bases = [Path(source.path).parent, Path()]
        candidates: list[str] = []
        for base in bases:
            candidate = base / include_path
            candidates.append(candidate.as_posix())
            if not candidate.suffix:
                candidates.extend(candidate.with_suffix(ext).as_posix() for ext in VERILOG_EXTENSIONS)
        return self._first_existing(candidates)

    def _resolve_c_include(self, source: FileNode, raw: str) -> str | None:
        include_path = Path(raw)
        source_dir = Path(source.path).parent
        bases = [source_dir, Path()]
        bases.extend(Path(directory) for directory in C_INCLUDE_DIRS)

        candidates = [(base / include_path).as_posix() for base in bases]
        direct_match = self._first_existing(candidates)
        if direct_match:
            return direct_match

        basename_matches = [
            path for path in self.paths if path.lower().endswith(f"/{raw.lower()}") or path.lower() == raw.lower()
        ]
        if basename_matches:
            return sorted(basename_matches, key=lambda path: _c_include_rank(source.path, path))[0]
        return None

    def _resolve_rust(self, source: FileNode, ref: ImportRef) -> str | None:
        if ref.kind == "rust-mod":
            return self._resolve_rust_mod(source, ref.raw)
        if ref.kind == "rust-use":
            return self._resolve_rust_use(source, ref.raw)
        return None

    def _resolve_rust_mod(self, source: FileNode, raw: str) -> str | None:
        source_path = Path(source.path)
        if source_path.name in {"lib.rs", "main.rs", "mod.rs"}:
            base = source_path.parent
        else:
            base = source_path.with_suffix("")
        return self._first_existing(
            [
                (base / raw).with_suffix(".rs").as_posix(),
                (base / raw / "mod.rs").as_posix(),
            ]
        )

    def _resolve_rust_use(self, source: FileNode, raw: str) -> str | None:
        segments = [segment for segment in raw.split("::") if segment]
        if not segments:
            return None

        source_root = _rust_source_root(source.path)
        current_parts = _rust_module_parts(source.path, source_root)
        scope = segments[0]
        if scope == "crate":
            return self._resolve_rust_parts(source_root, segments[1:], source.path)
        if scope == "self":
            return self._resolve_rust_parts(source_root, current_parts + tuple(segments[1:]), source.path)
        if scope == "super":
            remainder = segments
            base_parts = list(current_parts)
            while remainder and remainder[0] == "super":
                if base_parts:
                    base_parts.pop()
                remainder = remainder[1:]
            return self._resolve_rust_parts(source_root, tuple(base_parts + remainder), source.path)

        for crate_root in self.rust_crate_roots.get(scope, ()):
            target = self._resolve_rust_parts(crate_root, segments[1:], source.path)
            if target:
                return target
        return None

    def _resolve_rust_parts(self, source_root: Path, parts: tuple[str, ...], source_path: str) -> str | None:
        candidates: list[str] = []
        clean_parts = tuple(part for part in parts if part and part not in {"self", "*"})
        for length in range(len(clean_parts), 0, -1):
            module_path = source_root.joinpath(*clean_parts[:length])
            candidates.append(module_path.with_suffix(".rs").as_posix())
            candidates.append((module_path / "mod.rs").as_posix())
        for filename in RUST_ROOT_FILES:
            candidates.append((source_root / filename).as_posix())

        target = self._first_existing(candidates)
        if target == source_path:
            return None
        return target

    def _first_existing(self, candidates: list[str]) -> str | None:
        for candidate in candidates:
            normalized = posixpath.normpath(candidate.replace("\\", "/"))
            if normalized.startswith("./"):
                normalized = normalized[2:]
            if normalized in self.paths:
                return normalized
        return None

    def _detect_source_roots(self, files: tuple[FileNode, ...]) -> tuple[Path, ...]:
        roots = [Path()]
        if any(file.path.startswith("src/") for file in files):
            roots.append(Path("src"))
        return tuple(roots)

    def _build_verilog_module_index(self, files: tuple[FileNode, ...]) -> dict[str, tuple[str, ...]]:
        modules: dict[str, list[str]] = {}
        for file in files:
            if file.language not in VERILOG_LANGUAGES:
                continue
            for module_name in declared_modules(file.absolute_path):
                modules.setdefault(module_name, []).append(file.path)
        return {
            module_name: tuple(sorted(paths, key=_verilog_source_rank))
            for module_name, paths in modules.items()
        }

    def _build_rust_crate_roots(self, files: tuple[FileNode, ...]) -> dict[str, tuple[Path, ...]]:
        roots_by_name: dict[str, list[Path]] = {}
        for file in files:
            if file.language != "Rust":
                continue
            source_root = _rust_source_root(file.path)
            if not source_root:
                continue
            crate_name = _rust_crate_name(self.root, source_root)
            roots_by_name.setdefault(crate_name, [])
            if source_root not in roots_by_name[crate_name]:
                roots_by_name[crate_name].append(source_root)
        return {
            crate_name: tuple(sorted(roots, key=lambda path: path.as_posix()))
            for crate_name, roots in roots_by_name.items()
        }


def _verilog_source_rank(path: str) -> tuple[int, str]:
    normalized = path.replace("\\", "/").lower()
    generated_score = 0
    if normalized.endswith("_prim.v"):
        generated_score += 10
    if "/impl" in normalized:
        generated_score += 5
    if "netlist" in normalized or "synthesis" in normalized:
        generated_score += 5
    return generated_score, normalized


def _c_include_rank(source_path: str, candidate_path: str) -> tuple[int, str]:
    source_parts = source_path.replace("\\", "/").lower().split("/")
    candidate = candidate_path.replace("\\", "/").lower()
    score = 0
    if source_parts[:-1] and candidate.startswith("/".join(source_parts[:-1]) + "/"):
        score -= 20
    for index, directory in enumerate(C_INCLUDE_DIRS):
        prefix = f"{directory.lower()}/"
        marker = f"/{directory.lower()}/"
        if candidate.startswith(prefix) or marker in candidate:
            score += index
            break
    else:
        score += 50
    return score, candidate


def _rust_source_root(path: str) -> Path:
    rust_path = Path(path)
    parts = rust_path.parts
    if "src" in parts:
        src_index = len(parts) - 1 - tuple(reversed(parts)).index("src")
        return Path(*parts[: src_index + 1])
    return rust_path.parent


def _rust_module_parts(path: str, source_root: Path) -> tuple[str, ...]:
    rust_path = Path(path)
    try:
        relative = rust_path.relative_to(source_root)
    except ValueError:
        relative = rust_path
    if relative.name in RUST_ROOT_FILES and relative.parent == Path("."):
        return ()
    if relative.name == "mod.rs":
        return relative.parent.parts
    return relative.with_suffix("").parts


def _rust_crate_name(root: Path, source_root: Path) -> str:
    if source_root.name == "src":
        crate_dir = source_root.parent
        name = crate_dir.name if crate_dir != Path(".") else root.name
    else:
        name = source_root.name or root.name
    return name.replace("-", "_")
