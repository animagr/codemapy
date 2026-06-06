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
- HTML report generated with embedded CSS/JS

## Parsing Backend

`codemapy` uses [tree-sitter](https://tree-sitter.github.io/) (via `tree-sitter-language-pack`) for
precise import parsing and symbol extraction. The backend is **enabled by default** — it is a declared
runtime dependency, so a normal install pulls it in and codemapy uses it automatically. The tool also
degrades gracefully: if the backend is unavailable, import extraction falls back to regexes and Python
`ast`, and symbol extraction is limited to Python.

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

- **Dependency graph support** (imports resolved to internal edges): Python (`.py`), JavaScript (`.js`, `.jsx`, `.mjs`, `.cjs`), TypeScript (`.ts`, `.tsx`), Svelte (`.svelte`), GDScript (`.gd`), Lua (`.lua`), Rust (`.rs`), C/C++ includes (`.c`, `.h`, `.cc`, `.cpp`, `.cxx`, `.hpp`), and basic Verilog/SystemVerilog (`.v`, `.vh`, `.sv`, `.svh`)
- **Symbol extraction** (functions, classes, methods, structs, etc. recorded in `symbols.json`, counted per file in `context.json`): Python (`ast`, with signatures and docstrings), and via tree-sitter — JavaScript, TypeScript, Go, Rust, Java, Ruby, C, C++, Lua, and Bash/shell scripts
- **File scanning, LOC counts, tree, and treemap labels:** Python, JavaScript, TypeScript, Svelte, GDScript, Verilog, SystemVerilog, Go, Rust, Ruby, Java, C#, C, C++, Swift, Kotlin, PHP, Lua, Scala, Elixir, Solidity, and shell scripts

Planned dependency graph support: Bash, Go, Java, and Ruby (these currently have symbol extraction but not import-resolved dependency edges).

## Usage

```bash
python -m codemapy <path>
python -m codemapy <path> --artifacts
python -m codemapy <path> --artifacts --yes
python -m codemapy <path> --json
python -m codemapy <path> --open
```

From a checkout without installing the package, add the backend dependencies first so tree-sitter
parsing is available (otherwise codemapy falls back to regex/`ast`):

```bash
python -m pip install tree-sitter tree-sitter-language-pack
$env:PYTHONPATH = "src"   # PowerShell
python -m codemapy .
```

## Agent Artifacts

Write a `.codemapy/` folder into the scanned project:

```bash
python -m codemapy <path> --artifacts
```

Generated files:

- `report.html` - human-readable file tree, treemap, and dependency graph
- `context.json` - full machine-readable scan data for AI agents, including imports, per-file symbol counts, dependency edges, circular-dependency groups, and project metadata files such as lockfiles and config files (full symbol detail lives in `symbols.json`)
- `summary.md` - compact project summary for agent context (languages, top hubs, dependency cycles, symbols by kind, external references)
- `hubs.json` - fan-in/fan-out sorted dependency data
- `symbols.json` - per-file definitions plus a flat name index (`name -> [{path, qualified_name, kind, line}]`) so an agent can locate "where is X defined?" directly
- `manifest.json` - artifact metadata, counts, and per-file byte sizes (with a note when artifacts are large)

The `.codemapy/` folder is ignored by future scans.
Common project metadata files, such as `Cargo.lock`, `package.json`, `pyproject.toml`, `CMakeLists.txt`, `tsconfig.json`, and project constraint/config files, are kept out of the source treemap and dependency graph but listed in agent artifacts.
Generated outputs and cache/build artifacts are ignored entirely.
If `.codemapy/` already exists, codemapy asks before rewriting it. Use `--yes` to rewrite without prompting.

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
