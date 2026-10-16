from __future__ import annotations

import posixpath
import re
from pathlib import Path

from codemapy.deps.csharp import declared_namespaces, declared_types
from codemapy.deps.gdscript import declared_classes
from codemapy.deps.java import JAVA_PACKAGE_KIND, WILDCARD_SUFFIX, declared_package
from codemapy.deps.verilog import declared_modules
from codemapy.models import FileNode, ImportRef


# A C# `using` names a namespace, which may be declared by many files. Most are
# cohesive (tens of files), but projects often have a flat "god namespace" (e.g.
# the root namespace declared by hundreds of files). Fanning a single `using`
# out to that many targets produces meaningless edges and inflates hub counts,
# so usings whose namespace resolves to more than a cap are dropped. The cap is
# relative to project size (a 38-file namespace in a 100-file mod is just as
# god-like as a 300-file namespace in a large solution), bounded below so small
# cohesive projects keep their edges and above by this absolute limit.
CSHARP_NAMESPACE_FANOUT_CAP = 100
CSHARP_NAMESPACE_MIN_FANOUT_CAP = 10
CSHARP_NAMESPACE_FANOUT_DIVISOR = 3

JS_EXTENSIONS = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".svelte")
SVELTE_SCRIPT_EXTENSIONS = (".svelte.ts", ".svelte.js")
TS_DECLARATION_SUFFIX = ".d.ts"
# Emitted JS suffix -> (TypeScript source suffixes, declaration file suffix).
TS_EMITTED_SUFFIXES = {
    ".js": ((".ts", ".tsx"), ".d.ts"),
    ".mjs": ((".mts", ".ts", ".tsx"), ".d.mts"),
    ".cjs": ((".cts", ".ts", ".tsx"), ".d.cts"),
}
VERILOG_LANGUAGES = {"Verilog", "Verilog Header", "SystemVerilog", "SystemVerilog Header"}
VERILOG_EXTENSIONS = (".v", ".vh", ".sv", ".svh")
C_LANGUAGES = {"C", "C Header", "C/C++ Header", "C++", "C++ Header"}
C_INCLUDE_DIRS = ("include", "inc", "includes", "Config", "config", "utils", "src")
GO_MOD = "go.mod"
GO_TEST_SUFFIX = "_test.go"
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
        self.godot_autoloads = self._build_godot_autoloads()
        self.lua_mod_roots = self._build_lua_mod_roots(files)
        self.csharp_namespaces = self._build_csharp_namespace_index(files)
        self.csharp_types = self._build_csharp_type_index(files)
        self._type_token_cache: dict[str, frozenset[str]] = {}
        self.go_modules = self._build_go_modules(files)
        self.go_packages = self._build_go_packages(files)
        self.java_classes, self.java_packages, self.java_roots = self._build_java_index(files)
        self.last_candidates: list[str] = []
        self.resolved_to_self = False

    def resolve(self, source: FileNode, ref: ImportRef) -> tuple[str, ...]:
        """Resolve *ref* to zero or more internal target files.

        Most languages map an import to a single file; C# `using` directives
        name a namespace that several files share, so resolution is many-valued.
        Afterwards ``last_candidates`` holds the normalized paths this call
        tried, which explains a failed resolution (see ``codemapy.unresolved``),
        and ``resolved_to_self`` is True when the import names the importing file
        itself (a Java import of its own nested class): resolved, but no edge.
        """
        self.last_candidates = []
        self.resolved_to_self = False
        if source.language == "C#":
            return self._resolve_csharp(source, ref)
        if source.language == "Python":
            return self._resolve_python_import(source, ref)
        if source.language == "Go":
            return self._resolve_go(source, ref)
        if source.language == "Java":
            return self._resolve_java(source, ref)
        target = self._resolve_single(source, ref)
        return (target,) if target else ()

    def _resolve_go(self, source: FileNode, ref: ImportRef) -> tuple[str, ...]:
        """Resolve a Go import to every non-test file of the imported package.

        A Go import names a package (a folder), and all of its files compile
        together, so each one is a dependency. The folder comes from the
        longest matching ``module`` path among the repo's ``go.mod`` files, or
        from a relative path. Anything else (the standard library, other
        modules) is external.
        """
        if ref.raw.startswith(("./", "../")):
            directory = _posix_dir(posixpath.join(posixpath.dirname(source.path), ref.raw))
        else:
            directory = self._go_package_dir(ref.raw)
            if directory is None:
                return ()
        targets = tuple(path for path in self.go_packages.get(directory, ()) if path != source.path)
        if not targets:
            # Nothing mapped: list what is on disk so a skipped folder can be named.
            self.last_candidates.extend(self._go_files_on_disk(directory))
        return targets

    def _go_package_dir(self, import_path: str) -> str | None:
        matches = [
            module
            for module in self.go_modules
            if import_path == module or import_path.startswith(f"{module}/")
        ]
        if not matches:
            return None
        module = max(matches, key=len)
        remainder = import_path[len(module) :].lstrip("/")
        return _posix_dir(posixpath.join(self.go_modules[module], remainder))

    def _go_files_on_disk(self, directory: str) -> list[str]:
        try:
            names = sorted(entry.name for entry in (self.root / directory).iterdir() if entry.is_file())
        except OSError:
            return []
        return [
            posixpath.join(directory, name)
            for name in names
            if name.endswith(".go") and not name.endswith(GO_TEST_SUFFIX)
        ]

    def _resolve_single(self, source: FileNode, ref: ImportRef) -> str | None:
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
        # its sub-namespaces, so match the namespace name exactly. Within the
        # namespace, only files whose declared types the source actually
        # mentions count as dependencies - otherwise every `using` of a shared
        # namespace blankets edges to all of its files and drowns real hubs.
        candidates = tuple(path for path in self.csharp_namespaces.get(ref.raw, ()) if path != source.path)
        if not candidates:
            return ()
        tokens = self._type_tokens(source)
        targets = tuple(
            path
            for path in candidates
            if not tokens.isdisjoint(self.csharp_types.get(path, frozenset()))
        )
        if not targets or len(targets) > self._csharp_fanout_cap():
            return ()
        return targets

    def _csharp_fanout_cap(self) -> int:
        csharp_files = sum(1 for file in self.files if file.language == "C#")
        relative = max(CSHARP_NAMESPACE_MIN_FANOUT_CAP, csharp_files // CSHARP_NAMESPACE_FANOUT_DIVISOR)
        return min(CSHARP_NAMESPACE_FANOUT_CAP, relative)

    def _type_tokens(self, source: FileNode) -> frozenset[str]:
        """PascalCase-ish identifiers appearing in *source* (C# and Java), cached per file."""
        cached = self._type_token_cache.get(source.path)
        if cached is None:
            try:
                text = source.absolute_path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                text = ""
            cached = frozenset(re.findall(r"\b[A-Z][A-Za-z0-9_]*", text))
            self._type_token_cache[source.path] = cached
        return cached

    def _resolve_java(self, source: FileNode, ref: ImportRef) -> tuple[str, ...]:
        """Resolve a Java import, or the file's own package, to class files.

        ``import a.b.C`` (or nested ``a.b.C.Inner``) maps through the
        fully-qualified-name index; ``import a.b.*`` and the file's own
        package (``java-package``) map to the package's classes that the
        source mentions by name, as C# ``using`` resolution does.
        """
        if ref.kind == JAVA_PACKAGE_KIND or ref.raw.endswith(WILDCARD_SUFFIX):
            package = ref.raw.removesuffix(WILDCARD_SUFFIX)
            tokens = self._type_tokens(source)
            return tuple(
                path
                for path in self.java_packages.get(package, ())
                if path != source.path and Path(path).stem in tokens
            )
        parts = ref.raw.split(".")
        for length in range(len(parts), 0, -1):
            target = self.java_classes.get(".".join(parts[:length]))
            if target == source.path:
                self.resolved_to_self = True
                return ()
            if target:
                return (target,)
        # Not mapped: the paths it would have under each source root, so a
        # skipped folder can be named.
        relative = "/".join(parts) + ".java"
        self.last_candidates.extend(posixpath.join(root, relative) if root else relative for root in self.java_roots)
        return ()

    def _build_java_index(
        self, files: tuple[FileNode, ...]
    ) -> tuple[dict[str, str], dict[str, tuple[str, ...]], tuple[str, ...]]:
        """Fully-qualified class name -> file, package -> files, and source roots.

        A source root is the folder above a file's package path
        (``src/main/java`` for ``src/main/java/a/b/C.java`` in package
        ``a.b``). The class name is the file name, Java's rule for public
        top-level classes.
        """
        classes: dict[str, str] = {}
        packages: dict[str, list[str]] = {}
        roots: set[str] = set()
        for file in files:
            if file.language != "Java":
                continue
            package = declared_package(file.absolute_path) or ""
            stem = Path(file.path).stem
            classes.setdefault(f"{package}.{stem}" if package else stem, file.path)
            packages.setdefault(package, []).append(file.path)
            folder = _posix_dir(posixpath.dirname(file.path))
            package_path = package.replace(".", "/")
            if not package_path:
                roots.add(folder)
            elif folder == package_path:
                roots.add("")
            elif folder.endswith(f"/{package_path}"):
                roots.add(folder[: -len(package_path) - 1])
        return (
            classes,
            {package: tuple(sorted(paths)) for package, paths in packages.items()},
            tuple(sorted(roots)),
        )

    def _build_csharp_type_index(self, files: tuple[FileNode, ...]) -> dict[str, frozenset[str]]:
        return {
            file.path: declared_types(file.absolute_path)
            for file in files
            if file.language == "C#"
        }

    def _resolve_python_import(self, source: FileNode, ref: ImportRef) -> tuple[str, ...]:
        """Resolve a Python import to the module, plus any imported submodules.

        ``from pkg import sub`` runs ``pkg/__init__.py`` and then loads
        ``pkg/sub.py``, so both are dependencies. In a namespace package (a
        folder without ``__init__.py``) the submodule is the only file.
        Imported names that are not submodules (functions, classes) add
        nothing.
        """
        targets: list[str] = []
        module = self._resolve_python(source, ref.raw)
        if module:
            targets.append(module)
        for name in ref.names:
            if name == "*":
                continue
            submodule = self._resolve_python_submodule(source, ref.raw, name)
            if submodule and submodule != source.path and submodule not in targets:
                targets.append(submodule)
        return tuple(targets)

    def _resolve_python(self, source: FileNode, raw: str) -> str | None:
        bases, parts = self._python_bases(source, raw)
        candidates: list[str] = []
        for base in bases:
            candidates.extend(self._python_candidates(base, parts))
        return self._first_existing(candidates)

    def _resolve_python_submodule(self, source: FileNode, raw: str, name: str) -> str | None:
        bases, parts = self._python_bases(source, raw)
        candidates: list[str] = []
        for base in bases:
            package = base.joinpath(*parts)
            candidates.append((package / f"{name}.py").as_posix())
            candidates.append((package / name / "__init__.py").as_posix())
        return self._first_existing(candidates)

    def _python_bases(self, source: FileNode, raw: str) -> tuple[list[Path], list[str]]:
        """The folders a Python module path is looked up from, and its dotted parts."""
        if raw.startswith("."):
            level = len(raw) - len(raw.lstrip("."))
            base = Path(source.path).parent
            for _ in range(max(level - 1, 0)):
                base = base.parent
            return [base], [part for part in raw[level:].split(".") if part]
        return list(self.source_roots), [part for part in raw.split(".") if part]

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
            elif base.suffix in TS_EMITTED_SUFFIXES:
                # An ESM specifier names the emitted file; TypeScript maps it
                # back to the source or to a hand-written declaration file.
                source_ext, declaration = TS_EMITTED_SUFFIXES[base.suffix]
                candidates.extend(base.with_suffix(ext).as_posix() for ext in source_ext)
                candidates.append(base.with_suffix(declaration).as_posix())
            elif base.suffix == ".jsx":
                candidates.append(base.with_suffix(".tsx").as_posix())
        # A dot in the last segment is not always an extension: `./app.component`
        # names `app.component.ts`, so anything but a script suffix also gets
        # the extension and index probes.
        if base.suffix not in JS_EXTENSIONS:
            if base.name:
                candidates.extend(f"{base.as_posix()}{ext}" for ext in JS_EXTENSIONS)
                candidates.extend(f"{base.as_posix()}{ext}" for ext in SVELTE_SCRIPT_EXTENSIONS)
            candidates.extend((base / f"index{ext}").as_posix() for ext in JS_EXTENSIONS)
            # TypeScript falls back to a declaration file when no source exists.
            if base.name:
                candidates.append(f"{base.as_posix()}{TS_DECLARATION_SUFFIX}")
            candidates.append((base / f"index{TS_DECLARATION_SUFFIX}").as_posix())
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
        bases = [source_path.parent]
        if source_path.name not in {"lib.rs", "main.rs", "mod.rs"}:
            # `foo.rs` declares its children in `foo/`. The sibling fallback
            # covers crate roots renamed in Cargo.toml (`[lib] path =
            # "src/tree_sitter_cli.rs"`), which declare modules beside
            # themselves; compiling code only ever has one of the two.
            bases.insert(0, source_path.with_suffix(""))
        candidates: list[str] = []
        for base in bases:
            candidates.append((base / raw).with_suffix(".rs").as_posix())
            candidates.append((base / raw / "mod.rs").as_posix())
        return self._first_existing(candidates)

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
        if ref.kind == "gdscript-ident":
            return self._resolve_gdscript_ident(source, ref.raw)
        return None

    def _resolve_gdscript_ident(self, source: FileNode, name: str) -> str | None:
        """Resolve a PascalCase identifier to a `class_name` file or autoload."""
        candidates = self.gdscript_classes.get(name, ())
        if candidates:
            target = candidates[0]
            return target if target != source.path else None
        for godot_root in self._godot_roots_for_source(source):
            res_path = self.godot_autoloads.get(godot_root, {}).get(name)
            if res_path:
                return self._resolve_gdscript_path(source, res_path)
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
            self.last_candidates.append(normalized)
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

    def _build_go_modules(self, files: tuple[FileNode, ...]) -> dict[str, str]:
        """Module path -> module root folder, for every go.mod above a Go file.

        A repo can hold several modules (codemap keeps fixture modules under
        testdata/), so each Go file's folder and its ancestors are checked.
        Nearer roots win when two go.mod files declare the same module path.
        """
        folders: set[str] = set()
        for file in files:
            if file.language != "Go":
                continue
            parts = Path(file.path).parent.parts
            folders.update("/".join(parts[:depth]) for depth in range(len(parts) + 1))
        modules: dict[str, str] = {}
        for folder in sorted(folders, key=lambda item: (-item.count("/"), item)):
            module = _go_module_path(self.root / folder / GO_MOD)
            if module:
                modules.setdefault(module, folder)
        return modules

    def _build_go_packages(self, files: tuple[FileNode, ...]) -> dict[str, tuple[str, ...]]:
        packages: dict[str, list[str]] = {}
        for file in files:
            if file.language == "Go" and not file.path.endswith(GO_TEST_SUFFIX):
                packages.setdefault(_posix_dir(posixpath.dirname(file.path)), []).append(file.path)
        return {folder: tuple(sorted(paths)) for folder, paths in packages.items()}

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

    def _build_godot_autoloads(self) -> dict[Path, dict[str, str]]:
        """Per Godot project root: autoload singleton name -> resource path."""
        autoloads: dict[Path, dict[str, str]] = {}
        for godot_root in self.godot_roots:
            entries = _parse_godot_autoloads(self.root / godot_root / "project.godot")
            if entries:
                autoloads[godot_root] = entries
        return autoloads

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


def _posix_dir(path: str) -> str:
    """Normalize a project-relative folder path; the root is ``""``."""
    normalized = posixpath.normpath(path) if path else ""
    return "" if normalized == "." else normalized


def _go_module_path(go_mod: Path) -> str | None:
    """The ``module`` path declared in a go.mod file, or None."""
    try:
        lines = go_mod.read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError:
        return None
    for line in lines:
        stripped = line.split("//", 1)[0].strip()
        if stripped.startswith("module "):
            return stripped.removeprefix("module ").strip().strip('"`') or None
    return None


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


def _parse_godot_autoloads(path: Path) -> dict[str, str]:
    """Read the ``[autoload]`` section of a ``project.godot`` file.

    Entries look like ``Global="*res://scripts/global.gd"`` - the leading
    ``*`` marks an enabled singleton and is not part of the path.
    """
    try:
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError:
        return {}

    entries: dict[str, str] = {}
    in_autoload = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("["):
            in_autoload = stripped == "[autoload]"
            continue
        if not in_autoload or "=" not in stripped:
            continue
        name, _, value = stripped.partition("=")
        resource = value.strip().strip('"').lstrip("*")
        if name.strip() and resource:
            entries[name.strip()] = resource
    return entries


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
