# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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

[0.1.4]: https://github.com/animagr/codemapy/compare/v0.1.3...v0.1.4
[0.1.3]: https://github.com/animagr/codemapy/compare/v0.1.2...v0.1.3
[0.1.2]: https://github.com/animagr/codemapy/compare/v0.1.1...v0.1.2
[0.1.1]: https://github.com/animagr/codemapy/compare/v0.1.0...v0.1.1
