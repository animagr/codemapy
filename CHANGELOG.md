# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.5] - 2026-07-03

### Added

- Added a `--check` CLI flag that reports whether existing `.codemapy` artifacts are stale (exit 0 fresh, 1 stale, 2 missing). Freshness is judged against the git commit and dirty state recorded in `manifest.json` (now included at generation time), falling back to source-file mtimes outside git repositories.
- Added new `summary.md` sections aimed at agent context: a directory overview (file counts, LOC, dominant language per top directory), detected entry points (`[project.scripts]` from `pyproject.toml`, `main`/`bin` from `package.json`, and well-known main files), the largest files by LOC, documentation files, unmapped file groups, and an artifact guide describing what each `.codemapy` file contains.
- Added file classification during scanning: documentation files (`.md`, `.rst`, `.adoc`, `.txt`, ...) and unmapped assets (images, data files, binaries) are now listed separately (`doc_files` and `assets` in `context.json`) instead of being counted as source files, so LOC totals and the treemap only reflect mapped source languages.
- Added a `keep_dirs` key to `.codemap.json` that re-includes directory names the built-in defaults would ignore (e.g. a real `build/` or `db/` source directory).
- Added nesting for tree-sitter symbols by span containment, so methods are recorded under their declaring class across all tree-sitter languages and the `symbols.json` index now maps names to qualified entries such as `Widget.update` instead of bare method names.
- Entry-point detection also reads `pyproject.toml` and `package.json` manifests in subdirectories (e.g. `frontend/package.json`), and recognises `launcher.py` and `run.py`.
- Entry-point detection now also reports any C, C++, Go, or Rust file whose extracted symbols define a top-level `main()` function, annotated as `(defines main())`. This catches firmware-style entry points that live in files not named `main.c` (e.g. a NIOS II app's `fw_cy_nimbus.c`) and works at any directory depth, unlike the basename heuristics.
- Added Verilog/SystemVerilog symbol extraction via the tree-sitter language pack: `.v`, `.vh`, `.sv`, and `.svh` files now record their modules, functions, and tasks with line spans. Functions and tasks nest under their declaring module, so the `symbols.json` index records qualified names such as `counter.next_value`. Dependency extraction (`` `include ``/instantiations) is unchanged.

### Removed

- Rewriting artifacts now replaces the `.codemapy` folder wholesale, so files left behind by older codemapy versions are removed instead of lingering. Custom `--html`/output locations are never cleared.

### Changed

- Rewrote the project walk as a single pruned `os.walk` pass: ignored directories (`.git`, `node_modules`, `target`, ...) are no longer descended into, `.gitignore` files are discovered during the same walk, and project metadata is collected once instead of via three extra full-tree walks per artifact write. Large repositories scan dramatically faster.
- Gitignore matching now uses `pathspec` (new runtime dependency) for exact git semantics, including `**` patterns and `*` not crossing directory boundaries. The previous fnmatch-based matcher remains as a fallback when `pathspec` is not installed.
- The tree-sitter backend now caches parse trees so import extraction and symbol extraction of the same file parse it once instead of twice.
- `context.json` no longer records per-file absolute paths or a `generated_at` timestamp (the root is recorded once and the timestamp lives in `manifest.json`), making artifacts diff-stable and machine-portable. The artifact schema version is now 2.
- Reworked the human `report.html` viewer: a new Insights panel lists entry points, top hubs, and dependency cycles, and clicking any file (in the tree, treemap, or graph) opens a details view with its language, LOC, size, fan-in/out, internal dependencies in both directions, external references, and a symbol outline. The header gains symbol and cycle count chips, a language color legend sits below it, treemap tiles and graph nodes have hover tooltips, and the chart palette was replaced with a colorblind-safe one (the old blue/violet pair was indistinguishable under deuteranopia).

### Fixed

- Fixed a script-injection hole in `report.html`: scanned content containing `</script>` (e.g. inside an import string) could break out of the embedded data block and execute. The payload is now escaped.
- `--open` now opens the existing `report.html` when the artifact rewrite prompt is declined, instead of silently doing nothing.
- `requirements*.txt` variants such as `requirements-lock.txt` are now classified as dependency lockfiles instead of documentation.
- C# `using` resolution is now filtered by referenced types: a namespace file only becomes a dependency of an importer that actually mentions one of the types it declares, instead of every `using` blanketing edges to all files sharing the namespace. On a 103-file RimWorld mod this cut internal edges from 1440 to 267, replaced a uniform fan-in-38 hub list with a real ranking, and collapsed a false 56-file "cycle" to the actual 3-file one. The fan-out cap is also relative to project size now (one third of the C# file count, bounded to 10-100).
- `.csproj`, `.fsproj`, `.vbproj`, `.props`, `.targets`, and `mkdocs.yml` are now classified as project metadata instead of unmapped assets.

## [0.1.4] - 2026-06-17

### Added

- Added C# (`.cs`) symbol extraction via the tree-sitter language pack. Each scanned C# file now records its classes, interfaces, structs, records, enums, delegates, methods, and constructors (with line spans), so they appear in `symbols.json` and the symbol name index.
- Added C# (`.cs`) dependency graph support. `using` directives are resolved against a project-wide index of declared namespaces (`namespace X.Y.Z`, block or file-scoped), so a `using` fans out to every file that declares the imported namespace; unmatched namespaces (e.g. `System.*`, NuGet packages) are recorded as external references. A `using` whose namespace resolves to more files than a fan-out cap (default 100) is dropped, so a flat root "god namespace" does not create meaningless edges or inflate hub counts.

## [0.1.3] - 2026-06-06

### Added

- Added a tree-sitter backend (`tree-sitter` + `tree-sitter-language-pack`) for precise parsing, enabled by default as declared runtime dependencies. It degrades gracefully: when unavailable, extraction falls back to the regex/`ast` paths.
- Added symbol-level extraction. Each scanned file now records the functions, classes, methods, and other definitions it contains (with line spans, and — for Python — full signatures and docstrings). Python uses `ast`; the other tree-sitter-covered languages (JavaScript, TypeScript, Go, Rust, Java, Ruby, C, C++, Lua, Bash) use the language pack.
- Added a `symbols.json` artifact: per-file definitions plus a flat name index (`name -> [{path, qualified_name, kind, line}]`) so an agent can answer "where is X defined?" without scanning the whole tree.
- Added circular-dependency detection (Tarjan's strongly-connected-components algorithm). Cycles are reported in `context.json`, `manifest.json`, and a new "Dependency Cycles" section in `summary.md`.
- Added a "Symbols by Kind" overview and symbol/cycle counts to `summary.md` and the artifact manifest.

### Changed

- JavaScript/TypeScript and C/C++ import extraction now use tree-sitter when available, fixing multi-line imports and comment edge cases that the regex extractors mishandled. The regex extractors remain as fallbacks.
- Fan-in/fan-out now counts distinct neighbouring files, so importing the same target on multiple lines no longer inflates the score.
- Symbol extraction for non-Python languages now uses dedicated tree-sitter definition queries instead of the language pack's high-level structure output, which returned no names for C/C++ and omitted methods/modules for Ruby.
- Cycle detection now ignores declaration/containment edges (e.g. Rust `mod`), which previously merged most of a Rust crate into one giant false cycle.
- `summary.md` now summarises large dependency cycles (directories + a few examples) instead of listing every member inline.
- `context.json` no longer duplicates full symbol detail (kept only in `symbols.json`); it records a `symbol_count` per module instead, roughly halving the file on large repos.
- `run-gui.bat` now installs the tree-sitter backend on first launch if it is missing, so the GUI uses it without a separate setup step.

### Fixed

- `manifest.json` now reports each artifact's byte size and adds a note for large artifacts, pointing agents to `summary.md`, `hubs.json`, and the `symbols.json` index instead of loading multi-megabyte files.
- Fixed C/C++ system includes (e.g. `#include <endian.h>`) resolving to the including file itself, which produced a spurious self-import cycle.

## [0.1.2] - 2026-05-22

### Added

- Added GDScript (`.gd`) scanning and dependency graph support for Godot projects.
- Added GDScript resolution for `class_name` inheritance and `res://` resource paths used by `extends`, `preload()`, and `load()`.
- Added Lua (`.lua`) dependency graph support for `require()`, `dofile()`, `loadfile()`, and Luanti modpath loading patterns.

### Changed

- Ignored common Godot cache and generated files, including `.godot/`, `.gd.uid`, and `.import` files.
- Updated `run-gui.bat` to pause when the GUI exits with an error so console diagnostics remain visible.

### Fixed

- Fixed JavaScript and TypeScript current-directory imports such as `from "."` crashing during dependency resolution.
- Fixed Lua directory-style file references such as `dofile(".")` crashing during dependency resolution.

## [0.1.1] - 2026-05-22

### Added

- Added `.codemapy/` artifact generation with `report.html`, `context.json`, `summary.md`, `hubs.json`, and `manifest.json`.
- Added CLI overwrite confirmation for existing `.codemapy/` artifacts, plus `--yes` for non-interactive rewrites.
- Added GUI artifact generation so selecting a project writes `.codemapy/` directly into that project.
- Added dependency graph support for Verilog and SystemVerilog files (`.v`, `.vh`, `.sv`, `.svh`).
- Added dependency graph support for C/C++ include relationships (`.c`, `.h`, `.cc`, `.cpp`, `.cxx`, `.hpp`).
- Added dependency graph support for Rust modules and internal `use` paths (`.rs`).
- Added Svelte support for scanning and JavaScript/TypeScript dependency resolution.
- Added root and nested `.gitignore` support when scanning projects, including directory rules, globs, anchored paths, and negated patterns.
- Added README documentation for supported languages and agent artifacts.

### Changed

- Improved JavaScript and TypeScript relative import resolution, including parent-directory imports and source mappings for `.js` imports that resolve to `.ts` or `.tsx` files.
- Improved Verilog module resolution to prefer source files over generated primitive or implementation outputs.
- Updated the GUI workflow to remove the separate HTML save location and focus on project-level artifact generation.
- Excluded `.codemapy/` from future scans.
- Excluded common dependency, config, and project metadata files from the HTML source map while preserving them in agent artifacts.
- Ignored generated outputs, caches, vendor folders, editor folders, platform build artifacts, and language build artifacts for Python, C/C++, Verilog/SystemVerilog, JavaScript/TypeScript, Svelte, Rust, Go, and Apple projects.

### Fixed

- Suppressed scanned-project Python `SyntaxWarning` messages during import extraction.
- Fixed dependency graph resolution for nested include-style paths across supported languages.

[0.1.5]: https://github.com/animagr/codemapy/compare/v0.1.4...v0.1.5
[0.1.4]: https://github.com/animagr/codemapy/compare/v0.1.3...v0.1.4
[0.1.3]: https://github.com/animagr/codemapy/compare/v0.1.2...v0.1.3
[0.1.2]: https://github.com/animagr/codemapy/compare/v0.1.1...v0.1.2
[0.1.1]: https://github.com/animagr/codemapy/compare/v0.1.0...v0.1.1
