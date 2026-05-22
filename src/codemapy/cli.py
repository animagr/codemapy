from __future__ import annotations

import argparse
import json
import webbrowser
from pathlib import Path

from .config import load_config, merge_cli_config
from .graph import build_report
from .render.html import write_html
from .render.terminal import render_tree
from .scanner import scan_project


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate a human-readable code map for a local project.")
    parser.add_argument("path", nargs="?", default=".", help="Project path to scan.")
    parser.add_argument("--html", metavar="PATH", help="Write a single-file HTML report.")
    parser.add_argument("--open", action="store_true", help="Open the HTML report after writing it.")
    parser.add_argument("--json", action="store_true", help="Print report data as JSON.")
    parser.add_argument("--only", help="Comma-separated extensions to include, such as py,js,ts.")
    parser.add_argument("--exclude", help="Comma-separated path substrings to exclude.")
    args = parser.parse_args(argv)

    root = Path(args.path).expanduser().resolve()
    if not root.exists() or not root.is_dir():
        parser.error(f"path is not a directory: {root}")

    config = merge_cli_config(load_config(root), args.only, args.exclude)
    files = scan_project(root, config)
    report = build_report(root, files)

    if args.json:
        print(_report_json(report))
    else:
        print(render_tree(report))

    if args.html or args.open:
        output = Path(args.html) if args.html else root / "codemapy-report.html"
        html_path = write_html(report, output)
        print(f"\nHTML report: {html_path}")
        if args.open:
            webbrowser.open(html_path.as_uri())

    return 0


def _report_json(report) -> str:
    payload = {
        "root": str(report.root),
        "files": [file.__dict__ | {"absolute_path": str(file.absolute_path)} for file in report.files],
        "modules": [
            {
                "path": module.path,
                "language": module.language,
                "loc": module.loc,
                "size": module.size,
                "fan_in": module.fan_in,
                "fan_out": module.fan_out,
                "imports": [ref.__dict__ for ref in module.imports],
            }
            for module in report.modules
        ],
        "edges": [edge.__dict__ for edge in report.edges],
        "external_imports": [item.__dict__ for item in report.external_imports],
    }
    return json.dumps(payload, indent=2, sort_keys=True)
