from __future__ import annotations

import argparse
import json
import sys
import traceback
import webbrowser
from dataclasses import asdict
from pathlib import Path

from .artifacts import (
    ArtifactPaths,
    artifact_dir_for,
    artifact_notes,
    check_artifacts,
    report_payload,
    write_artifacts,
)
from .config import load_config, merge_cli_config
from .graph import build_report
from .models import Report
from .query import (
    FIND_DEFAULT_LIMIT,
    LIST_DEFAULT_LIMIT,
    ArtifactsMissing,
    UnknownPath,
    find,
    format_find,
    format_importers,
    format_tests,
    importers_of,
    load_artifacts,
    resolve_path,
    tests_of,
)
from .render.html import write_html
from .render.terminal import render_tree
from .scanner import scan_project


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    try:
        targets = resolve_targets(args.paths, args.root or [])
    except ValueError as exc:
        parser.error(str(exc))
    batch = len(targets) > 1 or bool(args.root)
    if batch and args.html:
        parser.error("--html writes a single file; use --artifacts to map several projects")
    querying = args.find is not None or args.importers is not None or args.tests is not None
    if querying and batch:
        parser.error("--find, --importers and --tests read one project's artifacts; give a single path")
    if args.limit is not None and args.limit < 0:
        parser.error("--limit must be 0 (no limit) or more")

    if args.check:
        return _check_all(targets, batch)
    if querying:
        return _query(targets[0], args)

    json_batch: list[dict[str, object]] | None = [] if batch and args.json else None
    failures: list[Path] = []
    for index, root in enumerate(targets, start=1):
        if batch and not args.json:
            print(f"\n=== [{index}/{len(targets)}] {root} ===")
        try:
            _map_project(root, args, json_batch)
        except Exception:
            if not batch:
                raise
            print(f"\nFailed to map {root}:", file=sys.stderr)
            traceback.print_exc()
            failures.append(root)

    if json_batch is not None:
        print(json.dumps(json_batch, indent=2, sort_keys=True))
    if batch:
        _print_batch_summary(len(targets), failures, quiet=args.json)
    return 1 if failures else 0


def resolve_targets(paths: list[str], roots: list[str]) -> list[Path]:
    """Return the project directories to map, in order and without duplicates.

    Each entry in *paths* is one project. Each entry in *roots* contributes its
    immediate subfolders as separate projects, skipping hidden folders and names
    the root's scan configuration would ignore (``node_modules``, ``build``, ...).
    With neither given, the current directory is mapped.

    Raises:
        ValueError: if a path or root is not a directory, or a root has no
            subfolders left to map.
    """
    targets: list[Path] = []
    for raw in paths or ([] if roots else ["."]):
        targets.append(_existing_dir(raw))
    for raw in roots:
        root = _existing_dir(raw)
        projects, skipped = _subfolders(root)
        if skipped:
            print(f"Skipping in {root}: {', '.join(skipped)}", file=sys.stderr)
        if not projects:
            raise ValueError(f"no subfolders to map in {root}")
        targets.extend(projects)
    return list(dict.fromkeys(targets))


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate a human-readable code map for a local project.")
    parser.add_argument(
        "paths",
        nargs="*",
        metavar="path",
        help="Project path(s) to scan; give several to map each one in turn (default: current directory).",
    )
    parser.add_argument(
        "--root",
        action="append",
        metavar="DIR",
        help="Map every immediate subfolder of DIR as its own project. Repeatable.",
    )
    parser.add_argument("--html", metavar="PATH", help="Write a single-file HTML report (single project only).")
    parser.add_argument("--artifacts", action="store_true", help="Write .codemapy agent artifacts.")
    parser.add_argument("--yes", action="store_true", help="Overwrite existing .codemapy artifacts without prompting.")
    parser.add_argument("--open", action="store_true", help="Open the HTML report after writing it.")
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print report data as JSON (an array of reports when mapping several projects).",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Check whether existing .codemapy artifacts are stale (exit 0 fresh, 1 stale, 2 missing).",
    )
    parser.add_argument("--only", help="Comma-separated extensions to include, such as py,js,ts.")
    parser.add_argument("--exclude", help="Comma-separated path substrings to exclude.")
    queries = parser.add_mutually_exclusive_group()
    queries.add_argument(
        "--find",
        metavar="WORDS",
        help="Rank files by path and symbol names matching WORDS, from existing artifacts (no rescan).",
    )
    queries.add_argument(
        "--importers", metavar="FILE", help="List the files that import FILE, from existing artifacts."
    )
    queries.add_argument(
        "--tests", metavar="FILE", help="List the tests that import FILE or are named after it."
    )
    parser.add_argument(
        "--limit",
        type=int,
        metavar="N",
        help=f"Rows to show for a query (default {FIND_DEFAULT_LIMIT} for --find, "
        f"{LIST_DEFAULT_LIMIT} otherwise; 0 for all).",
    )
    return parser


def _existing_dir(raw: str) -> Path:
    path = Path(raw).expanduser().resolve()
    if not path.is_dir():
        raise ValueError(f"path is not a directory: {path}")
    return path


def _subfolders(root: Path) -> tuple[list[Path], list[str]]:
    """Split *root*'s child directories into ``(projects, skipped names)``."""
    config = load_config(root)
    projects: list[Path] = []
    skipped: list[str] = []
    for child in sorted(root.iterdir(), key=lambda item: item.name.lower()):
        if not child.is_dir():
            continue
        if child.name.startswith(".") or config.dir_skip_reason(child) is not None:
            skipped.append(child.name)
        else:
            projects.append(child.resolve())
    return projects, skipped


def _check_all(targets: list[Path], batch: bool) -> int:
    """Report artifact freshness per target; the exit code is the worst result."""
    worst = 0
    for root in targets:
        code, message = check_artifacts(root)
        print(f"{root}: {message}" if batch else message)
        worst = max(worst, code)
    return worst


def _query(root: Path, args: argparse.Namespace) -> int:
    """Answer --find / --importers / --tests from root's artifacts: 0 answered, 2 cannot."""
    code, message = check_artifacts(root)
    if code == 2:
        print(message, file=sys.stderr)
        return 2
    if code == 1:
        print(f"Note: {message}", file=sys.stderr)
    try:
        artifacts = load_artifacts(root)
    except ArtifactsMissing as exc:
        print(exc, file=sys.stderr)
        return 2

    if args.find is not None:
        limit = FIND_DEFAULT_LIMIT if args.limit is None else args.limit
        hits = find(artifacts, args.find)
        if args.json:
            print(json.dumps([{**asdict(hit), "is_hub": hit.is_hub} for hit in hits[: limit or None]], indent=2))
        else:
            print("\n".join(format_find(artifacts, args.find, hits, limit)))
        return 0

    limit = LIST_DEFAULT_LIMIT if args.limit is None else args.limit
    try:
        path = resolve_path(artifacts, args.importers or args.tests, Path.cwd())
    except UnknownPath as exc:
        print(exc, file=sys.stderr)
        for candidate in exc.candidates[:LIST_DEFAULT_LIMIT]:
            print(f"  {candidate}", file=sys.stderr)
        return 2
    if args.importers is not None:
        pairs = importers_of(artifacts, path)
        if args.json:
            print(json.dumps({"path": path, "importers": [{"source": s, "raw": r} for s, r in pairs]}, indent=2))
        else:
            print("\n".join(format_importers(artifacts, path, pairs, limit)))
        return 0
    tests = tests_of(artifacts, path)
    if args.json:
        print(json.dumps({"path": path, "tested_by": tests}, indent=2))
    else:
        print("\n".join(format_tests(artifacts, path, tests, limit)))
    return 0


def _map_project(root: Path, args: argparse.Namespace, json_batch: list[dict[str, object]] | None) -> None:
    """Scan one project, print it, and write the requested outputs.

    When *json_batch* is a list the JSON payload is collected there instead of
    printed, so a multi-project run emits a single JSON array.
    """
    config = merge_cli_config(load_config(root), args.only, args.exclude)
    report = build_report(root, scan_project(root, config))
    # stderr, so --json output on stdout stays parseable.
    for warning in report.warnings:
        print(f"Warning: {warning}", file=sys.stderr)

    if json_batch is not None:
        json_batch.append(report_payload(report))
    elif args.json:
        print(_report_json(report))
    else:
        print(render_tree(report))

    _write_outputs(root, report, args)


def _write_outputs(root: Path, report: Report, args: argparse.Namespace) -> None:
    artifact_paths: ArtifactPaths | None = None
    existing_report: Path | None = None
    if args.artifacts or (args.open and not args.html):
        artifact_dir = artifact_dir_for(root)
        if artifact_dir.exists() and not args.yes and not _confirm_rewrite(artifact_dir):
            print(f"\nArtifacts unchanged: {artifact_dir}")
            if (artifact_dir / "report.html").exists():
                existing_report = artifact_dir / "report.html"
        else:
            artifact_paths = write_artifacts(report)
            print(f"\nArtifacts: {artifact_paths.directory}")
            for note in artifact_notes(artifact_paths):
                print(f"Note: {note}")

    html_path: Path | None = None
    if args.html:
        html_path = write_html(report, Path(args.html))
    elif artifact_paths:
        html_path = artifact_paths.report
    elif existing_report:
        html_path = existing_report
    if html_path:
        print(f"\nHTML report: {html_path}")
        if args.open:
            webbrowser.open(html_path.as_uri())


def _print_batch_summary(total: int, failures: list[Path], quiet: bool) -> None:
    if not quiet:
        print(f"\nMapped {total - len(failures)} of {total} projects.")
    if failures:
        print("Failed:", file=sys.stderr)
        for root in failures:
            print(f"  {root}", file=sys.stderr)


def _report_json(report: Report) -> str:
    return json.dumps(report_payload(report), indent=2, sort_keys=True)


def _confirm_rewrite(artifact_dir: Path) -> bool:
    prompt = (
        f"{artifact_dir} already exists. "
        "Replace its contents with fresh artifacts for the current codebase? [y/N] "
    )
    try:
        answer = input(prompt)
    except EOFError:
        return False
    return answer.strip().lower() in {"y", "yes"}
