# codemapy

`codemapy` is a small Python code map generator. It scans a local project, extracts file-level imports, builds a dependency graph, prints a compact terminal tree, and can write a single-file HTML report.

![License](https://img.shields.io/badge/license-MIT-green.svg)
![GitHub last commit](https://img.shields.io/github/last-commit/animagr/codemapy/main)
![GitHub commit activity](https://img.shields.io/github/commit-activity/m/animagr/codemapy/main)
![GitHub Issues](https://img.shields.io/github/issues/animagr/codemapy)

This is a fresh implementation inspired by the Go `codemap/` project, with a deliberately smaller v1 scope:

- local paths only
- no daemon, hooks, MCP server, remote clone, or diff mode
- Python imports parsed with `ast`
- JavaScript and TypeScript imports parsed with lightweight regexes
- HTML report generated with embedded CSS/JS and no runtime Python dependencies

## Languages Currently Supported

`codemapy` currently supports two levels of language handling:

- **Dependency graph support:** Python (`.py`), JavaScript (`.js`, `.jsx`, `.mjs`, `.cjs`), TypeScript (`.ts`, `.tsx`), Svelte (`.svelte`), Rust (`.rs`), C/C++ includes (`.c`, `.h`, `.cc`, `.cpp`, `.cxx`, `.hpp`), and basic Verilog/SystemVerilog (`.v`, `.vh`, `.sv`, `.svh`)
- **File scanning, LOC counts, tree, and treemap labels:** Python, JavaScript, TypeScript, Svelte, Verilog, SystemVerilog, Go, Rust, Ruby, Java, C#, C, C++, Swift, Kotlin, PHP, Lua, Scala, Elixir, Solidity, and shell scripts

Planned dependency graph support: Lua and Bash.

## Usage

```bash
python -m codemapy <path>
python -m codemapy <path> --artifacts
python -m codemapy <path> --artifacts --yes
python -m codemapy <path> --json
python -m codemapy <path> --open
```

From a checkout without installing:

```bash
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
- `context.json` - full machine-readable scan data for AI agents, including project metadata files such as lockfiles and config files
- `summary.md` - compact project summary for agent context
- `hubs.json` - fan-in/fan-out sorted dependency data
- `manifest.json` - artifact metadata and counts

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
