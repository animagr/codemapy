from __future__ import annotations

import argparse
import json
import webbrowser
from pathlib import Path

from .artifacts import ArtifactPaths, artifact_dir_for, artifact_notes, report_payload, write_artifacts
from .config import load_config, merge_cli_config
from .graph import build_report
from .render.html import write_html
from .render.terminal import render_tree
from .scanner import scan_project


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate a human-readable code map for a local project.")
    parser.add_argument("path", nargs="?", default=".", help="Project path to scan.")
    parser.add_argument("--html", metavar="PATH", help="Write a single-file HTML report.")
    parser.add_argument("--artifacts", action="store_true", help="Write .codemapy agent artifacts.")
    parser.add_argument("--yes", action="store_true", help="Overwrite existing .codemapy artifacts without prompting.")
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

    artifact_paths: ArtifactPaths | None = None
    if args.artifacts or (args.open and not args.html):
        artifact_dir = artifact_dir_for(root)
        if artifact_dir.exists() and not args.yes and not _confirm_rewrite(artifact_dir):
            print(f"\nArtifacts unchanged: {artifact_dir}")
        else:
            artifact_paths = write_artifacts(report)
            print(f"\nArtifacts: {artifact_paths.directory}")
            for note in artifact_notes(artifact_paths):
                print(f"Note: {note}")

    if args.html or (args.open and artifact_paths):
        if args.html:
            html_path = write_html(report, Path(args.html))
        elif artifact_paths:
            html_path = artifact_paths.report
        print(f"\nHTML report: {html_path}")
        if args.open:
            webbrowser.open(html_path.as_uri())

    return 0


def _report_json(report) -> str:
    return json.dumps(report_payload(report), indent=2, sort_keys=True)


def _confirm_rewrite(artifact_dir: Path) -> bool:
    prompt = (
        f"{artifact_dir} already exists. "
        "Rewrite .codemapy artifacts against the current codebase? [y/N] "
    )
    try:
        answer = input(prompt)
    except EOFError:
        return False
    return answer.strip().lower() in {"y", "yes"}
