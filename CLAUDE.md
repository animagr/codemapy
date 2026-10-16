# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

codemapy scans a local project and extracts file-level imports and symbol definitions. From those it builds a dependency graph with cycle detection and prints a terminal tree. It can also write a single-file HTML report and a `.codemapy/` folder of agent artifacts.

It is a smaller Python take on the Go `codemap` (sibling folder `../codemap`). `README.md` has the user-facing feature list, language list, and a table of every CLI option.

## Commands

Run these from this folder in PowerShell. Use `py -3.14`: the bare `python` on this machine is 3.10, and the project requires 3.11 or newer. Without an editable install, put `src` on the path:

```powershell
$env:PYTHONPATH = 'src'
py -3.14 -m unittest discover -s tests              # full suite (about 140 tests)
py -3.14 -m unittest tests.test_core                # one module
py -3.14 -m unittest tests.test_agent_features.StalenessCheckTests.test_check_reports_missing_fresh_and_stale
py -3.14 -m unittest discover -s tests -k cycles    # tests whose name contains "cycles"
py -3.14 -m ruff check src tests                    # lint (no ruff config, default rules)
py -3.14 -m codemapy .                              # terminal tree for this repo
py -3.14 -m codemapy . --artifacts --yes            # rewrite .codemapy/ without prompting
py -3.14 -m codemapy . --check                      # artifact freshness: 0 fresh, 1 stale, 2 missing
py -3.14 -m codemapy.gui                            # Qt GUI (PySide6, falls back to PyQt6)
```

Tests use stdlib `unittest`, not pytest. Most build a throwaway project in a `tempfile` directory and run the real scanner over it.

`tree-sitter`, `tree-sitter-language-pack`, and `pathspec` are declared dependencies, but every use is guarded so codemapy still runs without them. Without them, imports fall back to regex and `ast`, non-Python symbols come back empty, and `.gitignore` matching becomes approximate. If a symbol test or a JS/C import test fails unexpectedly, first check that the backend imports under the interpreter you are running.

## Architecture

Everything flows through frozen dataclasses defined in `models.py`.

1. `cli.main` resolves the targets: one path, several paths, or every subfolder via `--root`. It loads `.codemap.json` (`config.py`) and calls `_map_project` once per project.
2. `scanner.scan_project` walks the tree and filters it through the built-in ignores, nested `.gitignore` files (`pathspec`, or a fnmatch fallback), and config filters. It then sorts files into four groups: source (`LANGUAGES_BY_EXT`), project metadata, docs, and per-extension asset groups. Only source files reach the graph.
   - Every pruned directory, and every dropped file with a source extension, is recorded in `ScanResult.skipped` with a `SkipReason`. Unresolved-import explanations depend on it, so a new skip rule must record itself there.
   - Source files are read once, for LOC and for `generated.is_generated_source`, which sets `FileNode.is_generated`.
3. `graph.build_report` makes one pass over the source files:
   - The extractor chosen by `deps/registry.py` returns `ImportRef(raw, kind, line)`.
   - `deps/symbols.extract_module_facts` collects definitions and the `__main__` guard flag. It runs right after import extraction so the tree-sitter parse is reused.
   - `deps/resolver.ImportResolver.resolve` maps each ref to internal paths. A ref that resolves becomes an `Edge`. One that doesn't goes to `unresolved.UnresolvedClassifier`, using the candidate paths the resolver tried (`last_candidates`, collected by `_first_existing`). It becomes an `UnresolvedImport` if it names a project file, and an `ExternalImport` otherwise.
   - `testfiles.is_test_file` sets `ModuleNode.is_test` from the path and the file's imports, and `test_fan_in` counts importers that are tests.
   - Fan-in and fan-out count distinct neighbours, and `find_cycles` runs an iterative Tarjan SCC.
   - `graph.production_hubs` / `hub_rank_key` are the one hub ranking (non-test fan-in first, tests and generated files left out). `summary.md`, `hubs.json`, and the GUI use them, and `render/html.py` mirrors the rule in JS.
4. The resulting `Report` feeds three outputs:
   - `render/terminal.py`.
   - `render/html.py`: one self-contained HTML string with inline CSS and JS, built directly in Python with no template engine.
   - `artifacts.py`: writes `.codemapy/`, using `entrypoints.py` and `gitinfo.py`.

### Imports and symbols are separate mechanisms (on purpose)

- **Imports**: one `DependencyExtractor` per language (`deps/base.py`).
  - JS/TS and C/C++ use tree-sitter queries (`deps/treesitter.py`) that wrap the regex extractors as their fallback.
  - Python uses `ast`.
  - Rust, Lua, GDScript, C#, Verilog, and Svelte stay on regex deliberately: their resolvers are intricate and regex has proved robust there.
  - A tree-sitter extractor must emit exactly the `kind`/`raw` values of the regex extractor it replaces, because the resolver branches on them.
- **Symbols**:
  - Python uses `ast`, which gives nesting, signatures, and docstrings.
  - Every other language uses the hand-written tree-sitter queries in `_SYMBOL_QUERIES` (`deps/symbols.py`), keyed by pack name from `ts/langmap.py`. A query captures `@def.<kind>` plus `@name`, and nesting is rebuilt from source spans.
  - Don't switch to the language pack's high-level `process()`. It returns unnamed C/C++ definitions and drops Ruby methods.
- **Backend**: `ts/backend.py` is the only module that touches tree-sitter, and every use is gated on `backend.AVAILABLE`. `query_matches` keeps per-match capture grouping, which symbols need; `query_captures` is the flat variant used for imports.

### Resolver and graph quirks

- `ImportResolver.__init__` builds every per-language index up front: source roots, Rust crate roots, Verilog modules, GDScript `class_name` and Godot autoloads, Lua mod roots, and C# namespaces and types.
- `resolve()` returns a tuple. Most languages go through `_resolve_single`, which resolves to one file.
- Python is many-valued for `from X import a, b`. The extractor records the imported names in `ImportRef.names`, and `_resolve_python_import` links `X` itself plus each name that is a submodule file of `X`. Namespace packages (no `__init__.py`) resolve only through their submodules.
- C# is many-valued, because a `using` names a namespace rather than a file. Resolution matches the exact namespace (not sub-namespaces) and keeps only files whose declared types the source actually mentions. It also drops namespaces that fan out to more than `CSHARP_NAMESPACE_FANOUT_CAP` files.
- `DECLARATION_EDGE_KINDS` (`rust-mod`) count toward fan-in and fan-out but are excluded from cycle detection. Without that, every `mod x;` + `use super::*` pair would form a fake 2-cycle.
- `DROP_IF_UNRESOLVED_KINDS` (`gdscript-ident`) are speculative refs. They are dropped when they don't resolve, not reported as external.
- A resolver that builds candidate paths must send them through `_first_existing`; otherwise `last_candidates` stays empty and imports into skipped folders pass as external. C# and the C basename search don't use it, which is fine because neither names a single file.
- `unresolved.py` reports `missing` only for import shapes that can name nothing but a project file. Keep that list narrow: C/Verilog include paths, Lua `require`, and C# namespaces are routinely satisfied outside the repo.
- Only `WARN_REASONS` print stderr warnings. Imports into built-in ignores (`vendor/`, `dist/`), `.gitignore`d paths, and build outputs are expected, so they are listed without a warning.
- Before a release, map real projects with both the previous and the new version in memory and diff the results. For 0.2.0 that caught false "missing" imports (namespace packages, `.d.ts` and `.json` targets) and false "generated" files (scaffold banners, `"""` inside JS) that the unit tests had not.

### Classification rules worth keeping

- `testfiles.py`: for Python and C/C++, a test-like filename alone is not a test. It also needs a test framework import, because the user's instrument test benches (`11_201_test.py`) and firmware self-tests (`flash_test.c`) use those names. `tests/test_agent_features.py` and `tests/test_map_accuracy.py` pin this.
- `config.py`: the FPGA tool-output names (`db`, `work`, `simulation`, `impl*`, ...) are skipped only next to an FPGA project file or a compiled HDL library marker. Don't move them back into `DEFAULT_IGNORES`: as a bare name, `db` hid codegraph's `src/db/` data layer.
- `generated.py`: its banner regexes must stay below line 60 (`HEADER_SCAN_LINES`), or the module classifies itself. A test pins this.

### Adding a language

Touch whichever layers apply:

- **Scanning**: add it to `scanner.LANGUAGES_BY_EXT`.
- **Imports**: write an extractor in `deps/`, register it in `deps/registry.EXTRACTORS_BY_EXT`, and add a branch in `ImportResolver._resolve_single`.
- **Symbols**: add an entry in `ts/langmap.PACK_NAME_BY_EXT` and a query in `_SYMBOL_QUERIES`.
- **Docs**: update the language section of `README.md`.

## `.codemapy/` artifact contract

Agents read these files directly, and the user's global instructions say to start from `summary.md`, so treat the format as an API:

- `context.json` must not contain per-file absolute paths. A test enforces this.
- `ARTIFACT_VERSION` (`artifacts.py`) is bumped when a field changes meaning or moves. Version 3 split `unresolved_imports` out of `external_imports`.
- Rewriting the folder removes files left by older codemapy versions. A custom output directory is never cleared.
- `manifest.json` records `git_commit` and `git_dirty`. `--check` compares them with HEAD, or with source mtimes outside git.
- Artifacts are never tracked or committed in the scanned repo, and codemapy must not assume they are gitignored there. So both dirty-state calls (`write_artifacts` and `check_artifacts`) pass `ignored_dir_names=(ARTIFACT_DIR_NAME,)` to `gitinfo.is_dirty`. That function parses `git status --porcelain -z` and drops any path with a `.codemapy` component. Keep the two calls in step: if only one ignores the folder, `--check` goes permanently stale again.

## Releases

Bump the version in both `pyproject.toml` and `src/codemapy/__init__.py`; they have drifted apart before. Add an entry to `CHANGELOG.md`, which follows Keep a Changelog and SemVer.
