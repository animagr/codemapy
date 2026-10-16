# codemapy

`codemapy` is a small Python code map generator. It scans a local project, extracts file-level imports and code symbols (functions, classes, methods), builds a dependency graph, detects circular dependencies, prints a compact terminal tree, and writes both a single-file HTML report and machine-readable artifacts for AI agents.

![License](https://img.shields.io/badge/license-MIT-green.svg)
![GitHub last commit](https://img.shields.io/github/last-commit/animagr/codemapy/main)
![GitHub commit activity](https://img.shields.io/github/commit-activity/m/animagr/codemapy/main)
![GitHub Issues](https://img.shields.io/github/issues/animagr/codemapy)

This is a fresh implementation inspired by the Go `codemap/` project, with a deliberately smaller scope:

- local paths only
- no daemon, hooks, MCP server, remote clone, or diff mode
- Python imports and symbols parsed with `ast`
- JavaScript, TypeScript, and C/C++ imports parsed with tree-sitter (regex fallback)
- symbol-level definitions (functions, classes, methods, ...) extracted across tree-sitter-covered languages
- circular-dependency detection via strongly-connected components
- test and generated files classified, so hubs and entry points describe the program rather than its test suite or codegen output
- imports that name a project file but miss the map are reported with the reason, instead of passing as external references
- per-file test coverage (which tests import or are named after each file) and a coverage statement saying what each language's numbers can and cannot show
- `--find`, `--importers`, and `--tests` queries that answer from the written artifacts without rescanning
- HTML report generated with embedded CSS/JS

## Parsing Backend

`codemapy` uses [tree-sitter](https://tree-sitter.github.io/) (via `tree-sitter-language-pack`) for
precise import parsing and symbol extraction. The backend is **enabled by default** — it is a declared
runtime dependency, so a normal install pulls it in and codemapy uses it automatically. The tool also
degrades gracefully: if the backend is unavailable, import extraction falls back to regexes and Python
`ast`, and symbol extraction is limited to Python.

`.gitignore` handling uses [pathspec](https://pypi.org/project/pathspec/) (also a declared runtime
dependency) for exact git semantics, including `**` patterns. Without it, codemapy falls back to an
approximate fnmatch-based matcher.

## Installation

First-time setup (installs codemapy plus the tree-sitter backend):

```bash
python -m pip install -e .
```

Add the optional Qt GUI:

```bash
python -m pip install -e .[gui]
```

Requires Python 3.11+.

## Languages Currently Supported

`codemapy` supports three levels of language handling:

- **Dependency graph support** (imports resolved to internal edges): Python (`.py`, including `from package import submodule` and namespace packages), JavaScript (`.js`, `.jsx`, `.mjs`, `.cjs`), TypeScript (`.ts`, `.tsx`), Svelte (`.svelte`), GDScript (`.gd`, including `class_name` usage and `project.godot` autoload singletons), Lua (`.lua`), Rust (`.rs`), C# (`.cs`, via `using`/namespace resolution filtered by referenced type names), C/C++ includes (`.c`, `.h`, `.cc`, `.cpp`, `.cxx`, `.hpp`), Go (`.go`, via the module paths in the repo's `go.mod` files; an import links every non-test file of the package), and basic Verilog/SystemVerilog (`.v`, `.vh`, `.sv`, `.svh`)
- **Symbol extraction** (functions, classes, methods, structs, etc. recorded in `symbols.json`, counted per file in `context.json`): Python (`ast`, with signatures and docstrings), and via tree-sitter — JavaScript, TypeScript, Go, Rust, Java, C#, Ruby, C, C++, Lua, GDScript (classes, functions, signals, enums), Verilog/SystemVerilog (modules, functions, tasks), and Bash/shell scripts. Definitions are nested by span, so methods carry their declaring class and the symbol index records qualified names such as `Widget.update`.
- **File scanning, LOC counts, tree, and treemap labels:** Python, JavaScript, TypeScript, Svelte, GDScript, Verilog, SystemVerilog, Go, Rust, Ruby, Java, C#, C, C++, Swift, Kotlin, PHP, Lua, Scala, Elixir, Solidity, and shell scripts

Files outside these languages are not counted as source: documentation (`.md`, `.rst`, `.adoc`, `.txt`, ...) is listed separately in the artifacts, and remaining files (images, data, binaries) are summarised per extension so they never inflate LOC totals or the treemap.

Planned dependency graph support: Bash, Java, and Ruby (these currently have symbol extraction but not import-resolved dependency edges).

## Usage

```bash
py -3.14 -m codemapy <path>
py -3.14 -m codemapy <path> --artifacts
py -3.14 -m codemapy <path> --artifacts --yes
py -3.14 -m codemapy <path> --json
py -3.14 -m codemapy <path> --open
py -3.14 -m codemapy <path> --check
py -3.14 -m codemapy <path1> <path2> <path3> --artifacts
py -3.14 -m codemapy --root <dir> --artifacts --yes
py -3.14 -m codemapy <path> --find "import resolver"
py -3.14 -m codemapy <path> --importers src/db/queries.ts
py -3.14 -m codemapy <path> --tests queries.ts
```

With no options, codemapy scans the project and prints a compact file tree to the terminal. It writes nothing to disk.

### Options

| Option | What it does |
| --- | --- |
| `path ...` | Project folder(s) to map. Give several to map each one in turn. If neither a path nor `--root` is given, the current directory is mapped. |
| `--root DIR` | Map every immediate subfolder of `DIR` as its own project. Repeatable, and combines with plain paths. See [Mapping several projects](#mapping-several-projects). |
| `--artifacts` | Write a `.codemapy/` folder into each mapped project: `report.html`, `context.json`, `summary.md`, `hubs.json`, `symbols.json`, and `manifest.json` (see [Agent Artifacts](#agent-artifacts)). If the folder already exists, codemapy asks before replacing it. Any answer other than `y`/`yes` leaves the existing folder unchanged. |
| `--yes` | Replace an existing `.codemapy/` folder without asking. It only has an effect alongside `--artifacts`, or `--open` without `--html`. Use it in scripts and multi-project runs: without `--yes`, a run that cannot answer the prompt (no interactive input) keeps the old artifacts. |
| `--open` | Open the HTML report in your browser when the run finishes. With `--html PATH` it opens that file. Otherwise it writes `.codemapy/` just as `--artifacts` does, then opens `.codemapy/report.html`. If you decline the rewrite prompt, the existing report is opened. |
| `--html PATH` | Write a standalone single-file HTML report to `PATH`, outside `.codemapy/`. It works for a single project only, so it is rejected with several paths or `--root`. |
| `--json` | Print the full report data as JSON instead of the terminal tree. When several projects are mapped, it prints one JSON array. |
| `--check` | Report whether each project's existing `.codemapy/` artifacts are stale, without scanning or writing anything. Exit code 0 means fresh, 1 stale, 2 missing. Every option other than the paths and `--root` is ignored. |
| `--only EXTS` | Comma-separated file extensions to include, such as `py,js,ts`. The leading dot is optional. Replaces the `only` list from `.codemap.json`. |
| `--exclude TEXTS` | Comma-separated text to exclude: any file or folder whose relative path contains one of the entries is skipped, for example `tests/,generated`. Adds to the `exclude` list from `.codemap.json`. |
| `--find WORDS` | Rank the project's files against WORDS, matching path and symbol-name words (BM25), and print the top ones with their matching symbols and importer counts. Reads the existing `.codemapy/` artifacts; nothing is rescanned. See [Querying the Artifacts](#querying-the-artifacts). |
| `--importers FILE` | List the files that import FILE, production importers first, from the existing artifacts. FILE can be the project-relative path, a path relative to the current directory, or a unique trailing part such as `queries.ts`. |
| `--tests FILE` | List the test files that cover FILE directly: they import it or are named after it. |
| `--limit N` | Rows a query prints (default 10 for `--find`, 50 otherwise; 0 for all). |
| `-h`, `--help` | Show the option summary and exit. |

### Mapping several projects

Pass several paths to map each one in turn, or `--root <dir>` to map every immediate subfolder of `<dir>` as its own project (repeat `--root` for more parents; it combines with plain paths, and duplicates are mapped once). Hidden folders and names the scanner ignores by default (`node_modules`, `build`, `venv`, ...) are skipped and listed on stderr; a `keep_dirs` entry in the root's `.codemap.json` brings one back.

With more than one project, each gets a `=== [n/N] <path> ===` header, a failure in one project is reported and the rest still run (exit code 1 if any failed), `--json` prints a single JSON array, and `--check` prints one line per project and exits with the worst result. `--html` writes a single file, so it is rejected; use `--artifacts` instead. Existing `.codemapy/` folders are confirmed one by one unless `--yes` is given.

`--check` reports whether existing `.codemapy` artifacts are stale without rewriting them: exit code 0 means fresh, 1 stale, 2 missing. In a git repository the check compares the commit (and dirty state) recorded in `manifest.json` against HEAD; elsewhere it falls back to comparing source file modification times. codemapy's own `.codemapy/` folders never count as uncommitted changes, so the artifacts don't need to be committed or added to `.gitignore` to read as fresh.

From a checkout without installing the package, add the backend dependencies first so tree-sitter
parsing and exact gitignore matching are available (otherwise codemapy falls back to regex/`ast`
and fnmatch):

```bash
python -m pip install tree-sitter tree-sitter-language-pack pathspec
$env:PYTHONPATH = "src"   # PowerShell
python -m codemapy .
```

### Configuration

An optional `.codemap.json` in the project root tunes the scan:

```json
{
  "only": ["py", "ts"],
  "exclude": ["third_party/"],
  "ignore_dirs": ["fixtures"],
  "keep_dirs": ["build"],
  "max_file_bytes": 1000000
}
```

`keep_dirs` re-includes directory names that the built-in defaults would ignore (useful when a project has a real `build/` or `dist/` source directory).

The folder names FPGA toolchains use for their output (`db`, `incremental_db`, `netlist`, `simulation`, `synthesis`, `work`, and `impl*`) are skipped only when they look like tool output: an FPGA project file (`.qpf`, `.qsf`, `.qsys`, `.xpr`, `.mpf`, `.ldf`, `.prjx`, or `modelsim.ini`) sits in the folder's parent or grandparent, or the folder holds a compiled HDL library marker (`_info`, `_lib.qdb`). Elsewhere they are ordinary source folders, such as a `src/db/` data layer or a Rust `impl/` module. `keep_dirs` overrides the check as well.

## Agent Artifacts

Write a `.codemapy/` folder into the scanned project:

```bash
python -m codemapy <path> --artifacts
```

Generated files:

- `report.html` - human-readable file tree, treemap, dependency graph, and an insights panel with entry points, top hubs, dependency cycles, unresolved internal imports, and per-file details (dependencies, external references, unresolved imports, symbol outline)
- `context.json` - full machine-readable scan data for AI agents, including imports, per-file symbol counts, a `has_main_guard` flag marking directly runnable Python files, `is_test` / `is_generated` flags, `test_fan_in` (importers that are tests), and `tested_by` (the tests covering it) per module, dependency edges, unresolved internal imports with their reasons, circular-dependency groups, documentation files, asset summaries, project metadata files such as lockfiles and config files, and a `coverage` block (full symbol detail lives in `symbols.json`)
- `summary.md` - compact project briefing for agent context: languages, test and generated file counts, coverage (what each language's numbers can show, and what was skipped), a directory overview, entry points, top hubs (a Go package is one row), direct test coverage and untested hubs, largest files, dependency cycles, unresolved internal imports grouped by cause, generated files, symbols by kind, external references, documentation and metadata files, and a guide to the other artifacts
- `hubs.json` - every connected module, the top production hubs ranked by non-test fan-in and by fan-out, and `top_hubs` with each Go package as one row
- `symbols.json` - per-file definitions plus a flat name index (`name -> [{path, qualified_name, kind, line}]`) so an agent can locate "where is X defined?" directly
- `manifest.json` - artifact metadata, counts, per-file byte sizes, and the git commit/dirty state at generation time (used by `--check` for staleness)

The `.codemapy/` folder is ignored by future scans.
Common project metadata files, such as `Cargo.lock`, `package.json`, `pyproject.toml`, `CMakeLists.txt`, `tsconfig.json`, and project constraint/config files, are kept out of the source treemap and dependency graph but listed in agent artifacts.
Generated outputs and cache/build artifacts are ignored entirely.
If `.codemapy/` already exists, codemapy asks before rewriting it. Use `--yes` to rewrite without prompting.

### Entry-Point Detection

`summary.md` and the report's insights panel list detected entry points, from four signals in descending order of strength:

1. **Manifest scripts** - `[project.scripts]` / `[project.gui-scripts]` in `pyproject.toml`, and `main` / `bin` in `package.json`, including manifests up to three directories deep.
2. **Well-known basenames** near the root - `main.py`, `app.py`, `manage.py`, `index.ts`, `main.rs`, and similar. `index.js` / `index.ts` count only as a package's own index: directly in the root or a folder holding a `package.json`, or in that folder's `src/` or `lib/`. Others are usually barrel files that re-export a folder.
3. **A top-level `main()`** in C, C++, Go, or Rust, annotated `(defines main())`. This works at any depth, so it catches firmware entry points in oddly named files.
4. **A top-level `if __name__ == "__main__":` guard** in Python, annotated `(__main__ guard)`.

The guard is the weakest signal and is filtered accordingly: library modules very often carry one as a manual smoke-test harness, so a guarded file is only promoted to an entry point when **nothing imports it** (fan-in 0). Guard-only entries are ranked by fan-out — the module pulling in the most of the codebase is the application, not the demo block — and at most three are listed, so they can never crowd out the stronger signals. A guard on a file that another signal already found just adds the annotation.

Test and generated files (see below) are never promoted by signals 2-4.

### Test and Generated Files

Test files stay in the map but are flagged `is_test`, because a test suite imports broadly and nothing imports it: counted like the program, it inflates every hub's fan-in and crowds the fan-out and entry-point lists. Hubs are therefore ranked by importers that are not tests, and `summary.md` shows the test share, for example `fan-in 487 (457 from tests)`. A file is a test when:

- it sits in a test directory (`tests/`, `test/`, `__tests__/`, `spec/`, `testdata/`, `fixtures/`, `e2e/`, `testbench/`, or a case-sensitive `...Test/`, `...Tests/`, `...Spec/` such as `jvmTest/` or `Foo.Tests/`), or
- its name is reserved for tests (`_test.go`, `.test.ts` / `.spec.ts`, `FooTest.java`, `FooTests.cs`, `foo_spec.rb`, HDL testbenches `tb_*.v` / `*_tb.sv`, ...), or
- for Python and C/C++, its name is test-like (`test_*.py`, `*_test.py`, `*_test.c`, `*_unittest.cc`) **and** it imports a test framework (`pytest` / `unittest`, gtest, Catch2, Unity, CppUTest, ...). In instrument and firmware code a `*_test.py` is often the test-bench application itself and a `flash_test.c` a production self-test, so the name alone is not enough. `conftest.py` always counts.

Generated files are flagged `is_generated` and listed in `summary.md`. They stay in the graph, so imports of them still resolve, but they are left out of hubs and entry points. Detection (ported from codegraph) uses three signals: a code generator's filename (`_pb2.py`, `.pb.go`, `.g.cs`, `OuterClass.java`, ...), a generator banner on a comment line in the first 60 lines (`Code generated ... DO NOT EDIT`, `@generated`, `<auto-generated>`, ...), or a minified or bundled JavaScript file. A banner from a one-time scaffolding step (Gradle `init`, `rspec --init`, `rails generate rspec:install`) does not count, because the project owns and edits that file afterwards.

### Querying the Artifacts

On a large repo, `context.json` and `symbols.json` run to megabytes, too much for an agent to read whole. Three queries answer from them instead, in a few lines and without rescanning:

```bash
py -3.14 -m codemapy <path> --find "import resolver"      # which files are about this?
py -3.14 -m codemapy <path> --importers resolver.py       # who imports this file?
py -3.14 -m codemapy <path> --tests resolver.py           # which tests cover it directly?
```

`--find` ranks files with BM25 over the words in their path and symbol names (`ImportResolver` counts as `import` and `resolver`). An exact file stem or symbol name scores a bonus, and tests and generated files count half. Each hit lists its matching symbols, its importers, and whether it is a hub (3 or more importers outside the tests). Every answer names the artifacts it came from (git commit and generation time). If they are stale, a note says so on stderr, and if they are missing the query exits with code 2. Add `--json` for machine-readable output.

### Test Coverage

Each module records `tested_by`: the test files that cover it directly. A test covers a file it imports, and the file it is named after: `foo_test.go` covers `foo.go` (Go tests in the same package never import it), `__tests__/foo.test.ts` covers `foo.ts`, `src/test/java/a/FooTest.java` covers `src/main/java/a/Foo.java`, and `spec/models/user_spec.rb` covers `app/models/user.rb`. `summary.md` reports the share of production modules with a direct test and lists the top hubs that have none. A module that is only reached through other modules does not count as covered.

### Coverage

`summary.md` opens with a Coverage section that states what the numbers can show. For each language it gives the file count, whether imports become edges, and how symbols are extracted. It warns that fan-in and fan-out are 0 for a language without import analysis because nobody looked, not because nothing uses those files. It also counts what the scan skipped, by reason, and says whether the tree-sitter backend and exact `.gitignore` matching were available. `context.json` carries the same facts under `coverage`.

### Unresolved Internal Imports

An import that names a project file but does not resolve to a mapped one is reported as unresolved, not as an external reference. Each one records why its target is missing from the map:

- **A skip rule hid it.** The target exists on disk inside something the scan skipped, and the reason names the rule: `default-ignore`, `tool-output`, `config-ignore`, `gitignore`, `exclude`, `only-filter`, `too-large`, or `build-output`. `hidden_by` names the skipped folder or file. When your own configuration or the FPGA tool-output rule hid the target (`config-ignore`, `exclude`, `only-filter`, `too-large`, `tool-output`), a warning also prints on stderr with the `.codemap.json` change that would map it. The built-in names (`vendor/`, `dist/`, `node_modules/`, ...), `.gitignore`, and build outputs hide third-party or built code on purpose, so imports into them are listed but not warned about. Only source files count: an import of `../vendor/ui.min.css` is not an unresolved edge.
- **No file exists** (`missing`). Only imports whose syntax can name nothing but a project file count here: relative JS/TS script paths, relative C/C++ includes, Rust `mod x;`, Python relative imports and imports rooted at a top-level project module, and GDScript `.gd` paths. An extensionless JS/TS path that names a `.json` file, and a Python folder of modules without `__init__.py` (a namespace package), are not missing. The target may be generated at build time, or the import may be stale.

`summary.md` groups them by cause with examples, `context.json` lists each one under `unresolved_imports`, and `report.html` shows them in the insights panel and in each file's details.

To write a one-off standalone HTML file outside the artifact folder:

```bash
python -m codemapy <path> --html report.html
```

## Qt GUI

Install a Qt binding, then launch the small directory-picker app:

```bash
python -m pip install -e .[gui]
python -m codemapy.gui
```

On Windows from the source checkout:

```bat
run-gui.bat
```

The app lets you select a project directory and click **Scan**. It writes `.codemapy/` into that project and asks before rewriting an existing `.codemapy/` folder.

## Development

```bash
python -m unittest discover -s tests
python -m codemapy . --artifacts
```
