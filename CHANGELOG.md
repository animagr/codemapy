# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.3.0] - 2026-10-16

### Added

- `--impact FILE` lists every file that depends on FILE, directly or through other files, grouped by distance, and the tests to run: every affected test file plus the tests recorded in `tested_by` for each changed or affected file. `--changed [REF]` does the same for every file `git diff` reports since REF (default: uncommitted changes against HEAD) and names changed files the map does not hold. This follows codemap's blast radius, made transitive.
- `--where NAME` shows where a name (or `Class.member`) is defined: file, line range, kind, signature, the first docstring line, what it extends, and the classes that extend it. `--outline FILE` lists a file's definitions in source order with line ranges, signatures and docstring first lines, so an agent can see a file's structure without opening it.
- Inheritance: symbols record `bases`, what a class extends or implements, in `symbols.json`. Python reads them from `ast` (keyword arguments such as `metaclass=` are left out); JavaScript, TypeScript, Java, C#, C++ and Ruby read them from new tree-sitter queries.
- `--why FILE` explains why a file is or is not in the map: mapped (with its numbers), skipped (the rule, and the `.codemap.json` change that would map it), documentation or project metadata, an unmapped file type, missing, or added after the artifacts were written. `context.json` now lists the `skipped` folders and source files with their reasons.
- A **Stack** section in `summary.md` (and `stack` in `context.json`): the declared dependencies of `pyproject.toml`, `requirements*.txt`, `package.json`, `Cargo.toml` and `go.mod` near the root, skipping manifests inside test fixtures, plus the well-known frameworks and tools among them (`PySide6 (Qt GUI)`, `PyVISA (instrument control)`, `React (UI)`, `Vitest (tests)`, ...).
- A **Possibly Unused Modules** section in `summary.md` (and `possibly_unused` in `context.json`): files nothing in the map imports, limited to languages whose imports are analysed. Files loaded without an import are left out: entry points and runnable scripts, tests, generated files, files a manifest names (`package.json` `main`/`bin`, Cargo crate roots and `src/bin/`, `[project.scripts]`), package and crate roots, C/C++ and HDL compile units, config and worker files, framework route files, Java `package-info.java`, and folders of scripts, examples, docs or templates. A Go file counts only when its whole package is unused. JavaScript/TypeScript is left out, with the reason stated, when the project declares import path aliases or holds `.vue`, `.astro` or `.mdx` files.
- Java import resolution. `import a.b.C` (and nested `a.b.C.Inner`, `import static`) resolves through a fully-qualified-name index built from `package` declarations, and `import a.b.*` links the package's classes the file mentions. Classes of the same package, which need no import, link by the class names the source mentions, the token filter C# `using` resolution already uses. Same-package edges are left out of cycle detection. An import inside the project's namespace that matches no file is reported as unresolved (logisim-evolution's build-generated `BuildInfo`). Digital went from 0 to 7,346 internal edges, logisim-evolution from 0 to 10,833.

### Changed

- `--check` names the files changed since the artifacts were written (up to 10): ones git reports changed since the recorded commit and modified after the artifacts were generated, or deleted. Artifacts written from a dirty working tree now read as fresh until something changes again, instead of always stale. Queries print the same note and mark rows for changed files `[changed]`.
- `Debug`, `Release` and `Win32` are skipped only with that exact capitalisation (Visual Studio's output folders). Lowercase `release` and `debug` are common source package names: Digital's update checker in `gui/release/` was missing from its map.

## [0.2.1] - 2026-10-16

### Added

- Go import resolution. A new extractor (tree-sitter, with a regex fallback) reads `import` lines, and the resolver maps an import to a package folder through the module paths declared in the repo's `go.mod` files (nested modules included, the longest module path wins), or through a relative path. Every non-test file of the imported package becomes a dependency, because a Go package compiles as one unit; imports outside the repo's modules (the standard library, other modules) stay external. codemap went from 22 internal edges to 2,163. `go.mod` and `go.work` are listed as project metadata.
- Hub lists show a Go package as one row (`scanner/ (package, 18 files)`) counting importers from outside the package, instead of one identical row per file. `summary.md`, `report.html`, and the GUI use the shared ranking, and `hubs.json` gains `top_hubs` in that form.
- Per-file test coverage: each module in `context.json` records `tested_by`, the test files that import it or are named after it (`foo_test.go` -> `foo.go`, `__tests__/foo.test.ts` -> `foo.ts`, `src/test/.../FooTest.java` -> `src/main/.../Foo.java`, `spec/x_spec.rb` -> `app/x.rb`). A new **Test Coverage** section in `summary.md` gives the share of production modules with a direct test and lists the top hubs without one, and `report.html` shows each file's tests.
- A **Coverage** section in `summary.md` (and a `coverage` block in `context.json`) states what the map can and cannot see: per language, the file count, whether imports become edges, and how symbols are extracted; that a 0 fan-in in an unanalysed language means "not analysed", not "unused"; what the scan skipped, counted by reason; and whether the tree-sitter backend and exact `.gitignore` matching were available.
- Query mode, answering from the existing artifacts without rescanning: `--find WORDS` ranks files by BM25 over path and symbol-name words (an exact stem or symbol name adds a bonus; tests and generated files count half) and lists matching symbols, importer counts and hub status; `--importers FILE` lists the files that import FILE, production first; `--tests FILE` lists the tests covering FILE. FILE may be a unique trailing part such as `queries.ts`. `--limit N` bounds the rows and `--json` gives machine-readable output. Each answer names the commit and time the artifacts came from, a stale folder adds a note on stderr, and a missing one exits with code 2.

## [0.2.0] - 2026-10-16

### Added

- Imports that name a project file but do not resolve are reported as unresolved internal imports instead of being listed with the external references. Each records why its target is missing from the map: the skip rule that hid it (`default-ignore`, `tool-output`, `config-ignore`, `gitignore`, `exclude`, `only-filter`, `too-large`, `build-output`) with the skipped folder or file in `hidden_by`, or `missing` when no matching file exists. `context.json` lists them under `unresolved_imports`; `summary.md` groups them by cause in a new **Unresolved Internal Imports** section with examples and the `.codemap.json` change that would map the target; `report.html` shows them in the insights panel and each file's details; and imports hidden by the user's own configuration or the FPGA tool-output rule print a warning on stderr (imports into `vendor/`, `dist/`, `.gitignore`d or build-output paths are listed but not warned about). Detection is precision-first: either a source file named by the import exists inside a skipped path, or the import's syntax can only name a project file (relative JS/TS script paths, relative C/C++ includes, Rust `mod x;`, Python relative imports and imports rooted at a top-level project module, GDScript `.gd` paths) and no matching file exists, counting a `.json` file for an extensionless JS/TS path and a Python namespace package folder as matches.
- Test files are classified and flagged `is_test` in `context.json` and `hubs.json`, from a test directory (`tests/`, `__tests__/`, `spec/`, `testdata/`, `fixtures/`, `jvmTest/`, `Foo.Tests/`, ...), a filename reserved for tests (`_test.go`, `.test.ts` / `.spec.ts`, `FooTest.java`, `foo_spec.rb`, HDL testbenches `tb_*.v` / `*_tb.sv`, ...), or, for Python and C/C++, a test-like filename together with a test framework import. The name alone is not enough there, because a `*_test.py` is often an instrument test-bench application and a `flash_test.c` a firmware self-test. Each module also records `test_fan_in`, the importers that are tests, and `summary.md` reports the test file count and LOC.
- Generated source files are detected from a code generator's filename (`_pb2.py`, `.pb.go`, `.g.cs`, ...), a generator banner on a comment line in the first 60 lines (`Code generated ... DO NOT EDIT`, `@generated`, `<auto-generated>`, ...), or minified or bundled JavaScript, ported from codegraph. Block comments are tracked per language (so Python docstrings embedded in a JS test are not read as banners), and banners from one-time scaffolding steps (Gradle `init`, `rspec --init`, `rails generate rspec:install`) do not count. They stay in the graph so imports of them resolve, are flagged `is_generated`, and are listed in a new **Generated Files** section of `summary.md`.

### Changed

- Hubs are ranked by importers that are not tests, and test and generated modules are left out of the top hub lists in `summary.md`, `report.html`, `hubs.json` (`top_fan_in`, `top_fan_out`), and the GUI. `hubs.json` `hubs` still lists every connected module, flagged. On codegraph, `src/index.ts` (487 importers, 457 of them tests) moved from first to sixth.
- The FPGA tool-output folder names `db`, `incremental_db`, `netlist`, `simulation`, `synthesis`, `work`, and `impl*` are skipped only when an FPGA project file (`.qpf`, `.qsf`, `.qsys`, `.xpr`, `.mpf`, `.ldf`, `.prjx`, `modelsim.ini`) sits in the folder's parent or grandparent, or the folder holds a compiled HDL library marker (`_info`, `_lib.qdb`). Skipping them everywhere silently dropped real source: codegraph's `src/db/` data layer (8 files, 6,878 LOC) was missing from its map. `--root` subfolders follow the same rule, and `keep_dirs` still overrides it.
- Entry-point detection no longer lists nested `index.js` / `index.ts` barrel files: only a package's own index counts (directly in the root or a folder holding a `package.json`, or in that folder's `src/` or `lib/`), and a manifest `main` / `bin` still names any file. On codegraph, six `src/*/index.ts` barrels left the list and the `ui/`, `telemetry-dashboard/`, and `telemetry-worker/` package entries joined it.
- When every internal dependency edge is between test or generated files, Top Hubs says so instead of claiming no edges were detected. Test and generated files are never promoted by the basename, `main()`, or `__main__`-guard signals; the guard's test-directory check now uses the shared test classifier.
- The artifact format version is now 3: `context.json` gained `unresolved_imports` and the per-module `is_test`, `is_generated`, and `test_fan_in` fields, and `external_imports` no longer contains imports of project files.

### Fixed

- Rust crate roots renamed in `Cargo.toml` (`[lib] path = "src/tree_sitter_cli.rs"`) now resolve their `mod x;` declarations to the sibling module files. tree-sitter gained 34 module edges.
- Relative JS/TS imports of dotted module names (`./app.component` for `app.component.ts`) and of declaration files (`../lib/api` for `lib/api.d.ts`, and ESM specifiers such as `./job.js` for `job.d.ts`) now resolve.
- Source files whose names began with `impl` (`Implicant.java`, a Rust `impl.rs`) were dropped from every scan, because the `impl` folder prefix was also matched against file names. The FPGA folder names now apply to folders only.
- Python `from package import submodule` now links the submodule file (`package/submodule.py` or `package/submodule/__init__.py`) as well as the package's `__init__.py`. Before, only `__init__.py` was linked, and a namespace package (a folder without `__init__.py`) produced no edge at all, so `from lab_utils import gui_utils` left `gui_utils.py` unconnected. Imported names that are not submodules add nothing. This only adds edges; on codemapy itself it recovered `from codemapy import gitinfo`.

## [0.1.9] - 2026-10-16

### Fixed

- `--check` reported **stale** in every git repository that does not ignore `.codemapy/`, because the untracked artifact folder itself counted as an uncommitted change. Changes under any `.codemapy/` folder are now ignored both when the dirty state is recorded in `manifest.json` and when `--check` compares it, so artifacts never need to be committed or gitignored to read as fresh. Git paths are now decoded as UTF-8 regardless of the Windows locale.

## [0.1.8] - 2026-10-12

### Added

- The CLI accepts several project paths (`codemapy A B C --artifacts`) and maps each one in turn.
- Added a repeatable `--root DIR` option that maps every immediate subfolder of `DIR` as its own project. Hidden folders and names the scanner ignores by default (`node_modules`, `build`, `venv`, ...) are skipped and listed on stderr, and a `keep_dirs` entry in the root's `.codemap.json` brings one back. `--root` combines with plain paths, and a folder named twice is mapped once.
- When mapping more than one project, each one gets a `=== [n/N] <path> ===` header and the run ends with a `Mapped X of N projects.` line. A project that fails is reported with its traceback and the rest still run; the exit code is 1 if any failed. `--json` prints a single JSON array of reports, and `--check` prints one line per project and exits with the worst result (0 fresh, 1 stale, 2 missing).

### Changed

- `--html` is rejected when more than one project is being mapped, since it writes a single file. Use `--artifacts` to get a `report.html` per project.

### Fixed

- `codemapy.__version__` reported `0.1.4`; it now matches the package version.

## [0.1.7] - 2026-08-14

### Added

- Entry-point detection now recognises Python files with a top-level `if __name__ == "__main__":` guard, annotated as `(__main__ guard)`, and `context.json` records the flag per module as `has_main_guard`. The guard alone is weak evidence — library modules routinely carry one as a manual smoke-test harness, and in two sampled instrument-firmware repos 12 of 17 and 38 of 43 Python files had one — so a guarded file is only promoted to an entry point when nothing imports it (`fan_in == 0`), guard-only entries are ranked by fan-out so the module that pulls in the most of the codebase comes first, and at most 3 of them are listed so they cannot crowd out manifest or `main()` evidence. Modules under a `test/` or `tests/` directory are excluded from guard promotion, since `unittest.main()` under a guard is boilerplate in every test file and a suite would otherwise claim every slot; the filename is never used as that evidence, because a root-level `test_*.py` / `*_test.py` is as often the application itself as it is a test module. A PySide6 test-bench repo whose real entry point is `11_201_test.py` (matching no known basename) went from `None detected` to that file ranked first.

## [0.1.6] - 2026-07-03

### Added

- Added GDScript symbol extraction via the tree-sitter language pack: `.gd` files now record their `class_name` classes, inner classes, functions, signals, and enums with line spans. Pure-GDScript projects previously reported zero symbols.
- Added GDScript dependency edges for `class_name` usage and autoload singletons: a PascalCase identifier such as `Job.new()` or `Global.score` now resolves against the project's `class_name` index and the `[autoload]` section of `project.godot` (per nested Godot project root). Identifiers that resolve to nothing (engine builtins like `Vector2`) are dropped rather than recorded as external references. Godot projects that previously showed zero internal dependencies now map their real structure (e.g. a 26-script colony sim went from 0 to 66 edges with `Cell.gd`/`Grid.gd` correctly ranked as hubs).

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

[0.3.0]: https://github.com/animagr/codemapy/compare/v0.2.1...v0.3.0
[0.2.1]: https://github.com/animagr/codemapy/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/animagr/codemapy/compare/v0.1.9...v0.2.0
[0.1.9]: https://github.com/animagr/codemapy/compare/v0.1.8...v0.1.9
[0.1.8]: https://github.com/animagr/codemapy/compare/v0.1.7...v0.1.8
[0.1.7]: https://github.com/animagr/codemapy/compare/v0.1.6...v0.1.7
[0.1.6]: https://github.com/animagr/codemapy/compare/v0.1.5...v0.1.6
[0.1.5]: https://github.com/animagr/codemapy/compare/v0.1.4...v0.1.5
[0.1.4]: https://github.com/animagr/codemapy/compare/v0.1.3...v0.1.4
[0.1.3]: https://github.com/animagr/codemapy/compare/v0.1.2...v0.1.3
[0.1.2]: https://github.com/animagr/codemapy/compare/v0.1.1...v0.1.2
[0.1.1]: https://github.com/animagr/codemapy/compare/v0.1.0...v0.1.1
