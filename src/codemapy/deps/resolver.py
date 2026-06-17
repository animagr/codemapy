from __future__ import annotations

import posixpath
from pathlib import Path

from codemapy.deps.csharp import declared_namespaces
from codemapy.deps.gdscript import declared_classes
from codemapy.deps.verilog import declared_modules
from codemapy.models import FileNode, ImportRef


# A C# `using` names a namespace, which may be declared by many files. Most are
# cohesive (tens of files), but projects often have a flat "god namespace" (e.g.
# the root namespace declared by hundreds of files). Fanning a single `using`
# out to that many targets produces meaningless edges and inflates hub counts,
# so usings whose namespace resolves to more than this many files are dropped.
CSHARP_NAMESPACE_FANOUT_CAP = 100

JS_EXTENSIONS = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".svelte")
SVELTE_SCRIPT_EXTENSIONS = (".svelte.ts", ".svelte.js")
VERILOG_LANGUAGES = {"Verilog", "Verilog Header", "SystemVerilog", "SystemVerilog Header"}
VERILOG_EXTENSIONS = (".v", ".vh", ".sv", ".svh")
C_LANGUAGES = {"C", "C Header", "C/C++ Header", "C++", "C++ Header"}
C_INCLUDE_DIRS = ("include", "inc", "includes", "Config", "config", "utils", "src")
RUST_ROOT_FILES = ("lib.rs", "main.rs")
GODOT_RESOURCE_EXTENSIONS = (".gd", ".tscn", ".scn", ".tres", ".res", ".theme")


class ImportResolver:
    def __init__(self, root: Path, files: tuple[FileNode, ...]) -> None:
        self.root = root.resolve()
        self.paths = {file.path for file in files}
        self.files = files
        self.source_roots = self._detect_source_roots(files)
        self.verilog_modules = self._build_verilog_module_index(files)
        self.rust_crate_roots = self._build_rust_crate_roots(files)
        self.gdscript_classes = self._build_gdscript_class_index(files)
        self.godot_roots = self._build_godot_roots(files)
        self.lua_mod_roots = self._build_lua_mod_roots(files)
        self.csharp_namespaces = self._build_csharp_namespace_index(files)

    def resolve(self, source: FileNode, ref: ImportRef) -> tuple[str, ...]:
        """Resolve *ref* to zero or more internal target files.

        Most languages map an import to a single file; C# `using` directives
        name a namespace that several files share, so resolution is many-valued.
        """
        if source.language == "C#":
            return self._resolve_csharp(source, ref)
        target = self._resolve_single(source, ref)
        return (target,) if target else ()

    def _resolve_single(self, source: FileNode, ref: ImportRef) -> str | None:
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
        if source.language == "GDScript":
            return self._resolve_gdscript(source, ref)
        if source.language == "Lua":
            return self._resolve_lua(source, ref)
        return None

    def _resolve_csharp(self, source: FileNode, ref: ImportRef) -> tuple[str, ...]:
        if ref.kind != "csharp-using":
            return ()
        # A `using` imports the types declared *directly* in a namespace, not in
        # its sub-namespaces, so match the namespace name exactly.
        targets = tuple(path for path in self.csharp_namespaces.get(ref.raw, ()) if path != source.path)
        if not targets or len(targets) > CSHARP_NAMESPACE_FANOUT_CAP:
            return ()
        return targets

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
            if base.name:
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
        if direct_match and direct_match != source.path:
            return direct_match

        basename_matches = [
            path
            for path in self.paths
            if path != source.path
            and (path.lower().endswith(f"/{raw.lower()}") or path.lower() == raw.lower())
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

    def _resolve_gdscript(self, source: FileNode, ref: ImportRef) -> str | None:
        if ref.kind == "gdscript-class":
            candidates = self.gdscript_classes.get(ref.raw, ())
            return candidates[0] if candidates else None
        if ref.kind == "gdscript-path":
            return self._resolve_gdscript_path(source, ref.raw)
        return None

    def _resolve_gdscript_path(self, source: FileNode, raw: str) -> str | None:
        if raw.startswith(("uid://", "user://")):
            return None

        candidates: list[str] = []
        if raw.startswith("res://"):
            resource_path = Path(raw.removeprefix("res://"))
            for godot_root in self._godot_roots_for_source(source):
                candidates.append((godot_root / resource_path).as_posix())
            candidates.append(resource_path.as_posix())
        else:
            base = Path(source.path).parent / raw
            candidates.append(base.as_posix())

        expanded: list[str] = []
        for candidate in candidates:
            path = Path(candidate)
            expanded.append(path.as_posix())
            if not path.suffix:
                expanded.extend(path.with_suffix(ext).as_posix() for ext in GODOT_RESOURCE_EXTENSIONS)
        target = self._first_existing(expanded)
        if target == source.path:
            return None
        return target

    def _resolve_lua(self, source: FileNode, ref: ImportRef) -> str | None:
        if ref.kind == "lua-require":
            return self._resolve_lua_require(source, ref.raw)
        if ref.kind == "lua-file":
            return self._resolve_lua_file(source, ref.raw)
        if ref.kind == "lua-modpath":
            return self._resolve_lua_modpath(source, ref.raw)
        return None

    def _resolve_lua_require(self, source: FileNode, raw: str) -> str | None:
        normalized = raw.replace(".", "/")
        bases = [Path(source.path).parent, Path()]
        candidates: list[str] = []
        for base in bases:
            module_path = base / normalized
            candidates.append(module_path.with_suffix(".lua").as_posix())
            candidates.append((module_path / "init.lua").as_posix())
        return self._first_existing(candidates)

    def _resolve_lua_file(self, source: FileNode, raw: str) -> str | None:
        include_path = Path(raw.lstrip("/"))
        candidates = [
            (Path(source.path).parent / include_path).as_posix(),
            include_path.as_posix(),
        ]
        return self._first_existing(_lua_file_candidates(candidates))

    def _resolve_lua_modpath(self, source: FileNode, raw: str) -> str | None:
        mod_name, _, suffix = raw.partition(":")
        if not suffix:
            return None
        roots = [self._current_lua_mod_root(source)] if mod_name == "." else list(self.lua_mod_roots.get(mod_name, ()))
        candidates = [(root / suffix).as_posix() for root in roots if root is not None]
        return self._first_existing(_lua_file_candidates(candidates))

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

    def _build_gdscript_class_index(self, files: tuple[FileNode, ...]) -> dict[str, tuple[str, ...]]:
        classes: dict[str, list[str]] = {}
        for file in files:
            if file.language != "GDScript":
                continue
            for class_name in declared_classes(file.absolute_path):
                classes.setdefault(class_name, []).append(file.path)
        return {class_name: tuple(sorted(paths)) for class_name, paths in classes.items()}

    def _build_csharp_namespace_index(self, files: tuple[FileNode, ...]) -> dict[str, tuple[str, ...]]:
        namespaces: dict[str, list[str]] = {}
        for file in files:
            if file.language != "C#":
                continue
            for namespace in declared_namespaces(file.absolute_path):
                namespaces.setdefault(namespace, []).append(file.path)
        return {namespace: tuple(sorted(paths)) for namespace, paths in namespaces.items()}

    def _build_godot_roots(self, files: tuple[FileNode, ...]) -> tuple[Path, ...]:
        roots: set[Path] = set()
        for file in files:
            if Path(file.path).name == "project.godot":
                roots.add(Path(file.path).parent)
            if file.language != "GDScript":
                continue
            current = file.absolute_path.parent.resolve()
            while _path_is_relative_to(current, self.root):
                if (current / "project.godot").exists():
                    roots.add(current.relative_to(self.root))
                    break
                if current == self.root:
                    break
                current = current.parent

        if not roots:
            roots.add(Path())
        return tuple(sorted(roots, key=lambda path: path.as_posix()))

    def _godot_roots_for_source(self, source: FileNode) -> tuple[Path, ...]:
        source_path = Path(source.path)
        matches = [root for root in self.godot_roots if root == Path() or _path_is_relative_to(source_path, root)]
        return tuple(sorted(matches, key=lambda path: len(path.parts), reverse=True)) or (Path(),)

    def _build_lua_mod_roots(self, files: tuple[FileNode, ...]) -> dict[str, tuple[Path, ...]]:
        roots: dict[str, list[Path]] = {}
        for file in files:
            path = Path(file.path)
            if path.name == "mod.conf":
                mod_name = _lua_mod_conf_name(file.absolute_path) or path.parent.name
                roots.setdefault(mod_name, []).append(path.parent)
            elif file.language == "Lua" and path.name == "init.lua":
                roots.setdefault(path.parent.name, []).append(path.parent)
        return {
            mod_name: tuple(sorted(dict.fromkeys(paths), key=lambda path: path.as_posix()))
            for mod_name, paths in roots.items()
        }

    def _current_lua_mod_root(self, source: FileNode) -> Path | None:
        source_path = Path(source.path)
        for roots in self.lua_mod_roots.values():
            for root in sorted(roots, key=lambda path: len(path.parts), reverse=True):
                if _path_is_relative_to(source_path, root):
                    return root
        return source_path.parent


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


def _lua_file_candidates(paths: list[str]) -> list[str]:
    candidates: list[str] = []
    for raw_path in paths:
        path = Path(raw_path)
        candidates.append(path.as_posix())
        if not path.suffix:
            if path.name:
                candidates.append(path.with_suffix(".lua").as_posix())
            candidates.append((path / "init.lua").as_posix())
    return candidates


def _lua_mod_conf_name(path: Path) -> str | None:
    try:
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            key, sep, value = line.partition("=")
            if sep and key.strip() == "name":
                name = value.strip()
                return name or None
    except OSError:
        return None
    return None


def _path_is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True
