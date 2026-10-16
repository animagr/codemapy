# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

codemapy scans a local project and extracts file-level imports and symbol definitions. From those it builds a dependency graph with cycle detection and prints a terminal tree. It can also write a single-file HTML report and a `.codemapy/` folder of agent artifacts.

It is a smaller Python take on the Go `codemap` (sibling folder `../codemap`). `README.md` has the user-facing feature list, language list, and a table of every CLI option.

## Commands

Run these from this folder in PowerShell. Use `py -3.14`: the bare `python` on this machine is 3.10, and the project requires 3.11 or newer. Without an editable install, put `src` on the path:

```powershell
$env:PYTHONPATH = 'src'
py -3.14 -m unittest discover -s tests              # full suite (about 100 tests)
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
3. `graph.build_report` makes one pass over the source files:
   - The extractor chosen by `deps/registry.py` returns `ImportRef(raw, kind, line)`.
   - `deps/symbols.extract_module_facts` collects definitions and the `__main__` guard flag. It runs right after import extraction so the tree-sitter parse is reused.
   - `deps/resolver.ImportResolver.resolve` maps each ref to internal paths. A ref that resolves becomes an `Edge`; one that doesn't becomes an `ExternalImport`.
   - Fan-in and fan-out count distinct neighbours, and `find_cycles` runs an iterative Tarjan SCC.
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
- C# is many-valued, because a `using` names a namespace rather than a file. Resolution matches the exact namespace (not sub-namespaces) and keeps only files whose declared types the source actually mentions. It also drops namespaces that fan out to more than `CSHARP_NAMESPACE_FANOUT_CAP` files.
- `DECLARATION_EDGE_KINDS` (`rust-mod`) count toward fan-in and fan-out but are excluded from cycle detection. Without that, every `mod x;` + `use super::*` pair would form a fake 2-cycle.
- `DROP_IF_UNRESOLVED_KINDS` (`gdscript-ident`) are speculative refs. They are dropped when they don't resolve, not reported as external.

### Adding a language

Touch whichever layers apply:

- **Scanning**: add it to `scanner.LANGUAGES_BY_EXT`.
- **Imports**: write an extractor in `deps/`, register it in `deps/registry.EXTRACTORS_BY_EXT`, and add a branch in `ImportResolver._resolve_single`.
- **Symbols**: add an entry in `ts/langmap.PACK_NAME_BY_EXT` and a query in `_SYMBOL_QUERIES`.
- **Docs**: update the language section of `README.md`.

## `.codemapy/` artifact contract

Agents read these files directly, and the user's global instructions say to start from `summary.md`, so treat the format as an API:

- `context.json` must not contain per-file absolute paths. A test enforces this.
- Rewriting the folder removes files left by older codemapy versions. A custom output directory is never cleared.
- `manifest.json` records `git_commit` and `git_dirty`. `--check` compares them with HEAD, or with source mtimes outside git.
- Artifacts are never tracked or committed in the scanned repo, and codemapy must not assume they are gitignored there. So both dirty-state calls (`write_artifacts` and `check_artifacts`) pass `ignored_dir_names=(ARTIFACT_DIR_NAME,)` to `gitinfo.is_dirty`. That function parses `git status --porcelain -z` and drops any path with a `.codemapy` component. Keep the two calls in step: if only one ignores the folder, `--check` goes permanently stale again.

## Releases

Bump the version in both `pyproject.toml` and `src/codemapy/__init__.py`; they have drifted apart before. Add an entry to `CHANGELOG.md`, which follows Keep a Changelog and SemVer.
