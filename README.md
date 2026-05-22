# codemapy

`codemapy` is a small Python-first code map generator. It scans a local project, extracts file-level imports, builds a dependency graph, prints a compact terminal tree, and can write a single-file HTML report.

This is a fresh implementation inspired by the Go `codemap/` project, with a deliberately smaller v1 scope:

- local paths only
- no daemon, hooks, MCP server, remote clone, or diff mode
- Python imports parsed with `ast`
- JavaScript and TypeScript imports parsed with lightweight regexes
- HTML report generated with embedded CSS/JS and no runtime Python dependencies

## Usage

```bash
python -m codemapy <path>
python -m codemapy <path> --html report.html
python -m codemapy <path> --json
python -m codemapy <path> --open
```

From a checkout without installing:

```bash
$env:PYTHONPATH = "src"   # PowerShell
python -m codemapy .
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

The app lets you select a directory, choose where to save the generated HTML report, and click **Scan**.

## Development

```bash
python -m unittest discover -s tests
python -m codemapy . --html report.html
```
