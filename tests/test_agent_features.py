from __future__ import annotations

import json
import os
import tempfile
import unittest
import unittest.mock
from pathlib import Path

from codemapy.artifacts import check_artifacts, report_payload, summary_markdown, symbols_payload, write_artifacts
from codemapy.config import Config, load_config
from codemapy.graph import build_report
from codemapy.render.html import render_html
from codemapy.scanner import scan_project
from codemapy.ts import backend

try:
    import pathspec  # noqa: F401

    PATHSPEC_AVAILABLE = True
except ImportError:
    PATHSPEC_AVAILABLE = False


def _write(root: Path, files: dict[str, str | bytes]) -> None:
    for rel, content in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            path.write_bytes(content)
        else:
            path.write_text(content, encoding="utf-8")


class ClassificationTests(unittest.TestCase):
    def test_docs_and_assets_are_classified_not_scanned_as_source(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(
                root,
                {
                    "app.py": "print('hello')\n",
                    "README.md": "# Title\n\nBody.\n",
                    "docs/guide.rst": "Guide\n=====\n",
                    "logo.png": b"\x89PNG\r\n\x1a\n" + b"\x00" * 64,
                    "data.csv": "a,b\n1,2\n",
                },
            )

            scan = scan_project(root, Config())
            report = build_report(root, scan)

        self.assertEqual(["app.py"], [file.path for file in scan.sources])
        self.assertEqual(["README.md", "docs/guide.rst"], [doc.path for doc in scan.doc_files])
        asset_exts = {group.extension for group in scan.asset_groups}
        self.assertEqual({".png", ".csv"}, asset_exts)
        # Assets and docs must not inflate the source LOC total.
        self.assertEqual(report.total_loc, scan.sources[0].loc)

        payload = report_payload(report)
        self.assertEqual(["README.md", "docs/guide.rst"], [d["path"] for d in payload["doc_files"]])
        self.assertEqual({".csv", ".png"}, {a["extension"] for a in payload["assets"]})
        self.assertNotIn("absolute_path", payload["files"][0])

    def test_keep_dirs_reincludes_default_ignored_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(
                root,
                {
                    ".codemap.json": '{"keep_dirs": ["build"]}\n',
                    "build/tool.py": "print('kept')\n",
                    "dist/skip.py": "print('still ignored')\n",
                },
            )

            files = scan_project(root, load_config(root))

        self.assertEqual(["build/tool.py"], [file.path for file in files])


@unittest.skipUnless(PATHSPEC_AVAILABLE, "pathspec not installed")
class GitIgnoreSemanticsTests(unittest.TestCase):
    def test_star_does_not_cross_directory_boundaries(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(
                root,
                {
                    ".gitignore": "src/*.js\n",
                    "src/a.js": "console.log(1)\n",
                    "src/sub/b.js": "console.log(2)\n",
                },
            )

            files = scan_project(root, Config())

        self.assertEqual(["src/sub/b.js"], [file.path for file in files])

    def test_double_star_matches_nested_directories(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(
                root,
                {
                    ".gitignore": "docs/**/gen.py\n",
                    "docs/a/b/gen.py": "print('ignored')\n",
                    "docs/keep.py": "print('visible')\n",
                },
            )

            files = scan_project(root, Config())

        self.assertEqual(["docs/keep.py"], [file.path for file in files])

    def test_fallback_matcher_handles_basic_rules_without_pathspec(self) -> None:
        from codemapy import scanner

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(
                root,
                {
                    ".gitignore": "output/\n*.tmp.py\n!keep.tmp.py\n",
                    "app.py": "print('hello')\n",
                    "note.tmp.py": "print('ignored')\n",
                    "keep.tmp.py": "print('visible')\n",
                    "output/gen.py": "print('ignored')\n",
                },
            )

            with unittest.mock.patch.object(scanner, "_pathspec", None):
                files = scan_project(root, Config())

        self.assertEqual(["app.py", "keep.tmp.py"], [file.path for file in files])


@unittest.skipUnless(backend.AVAILABLE, "tree-sitter backend not installed")
class SymbolNestingTests(unittest.TestCase):
    def test_index_qualifies_methods_with_declaring_class(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(
                root,
                {
                    "widget.ts": (
                        "export class Widget {\n"
                        "  update(): void {}\n"
                        "}\n"
                        "export class Panel {\n"
                        "  update(): void {}\n"
                        "}\n"
                    ),
                },
            )
            report = build_report(root, scan_project(root, Config()))

        payload = symbols_payload(report)
        qualified = {entry["qualified_name"] for entry in payload["index"]["update"]}
        self.assertEqual({"Widget.update", "Panel.update"}, qualified)


class SummaryContentTests(unittest.TestCase):
    def test_summary_contains_overview_entry_points_and_guide(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(
                root,
                {
                    "pyproject.toml": (
                        "[project]\nname = 'demo'\n\n[project.scripts]\ndemo = 'demo.cli:main'\n"
                    ),
                    "src/demo/__init__.py": "",
                    "src/demo/__main__.py": "print('run')\n",
                    "src/demo/core.py": "VALUE = 1\n" * 5,
                    "frontend/package.json": '{"main": "src/index.js"}\n',
                    "README.md": "# demo\n",
                },
            )
            report = build_report(root, scan_project(root, Config()))
            text = summary_markdown(report, "2026-07-03T00:00:00+00:00")

        self.assertIn("## Directory Overview", text)
        self.assertIn("`src/demo/`", text)
        self.assertIn("## Entry Points", text)
        self.assertIn("`demo` -> `demo.cli:main` (pyproject.toml script)", text)
        self.assertIn("`frontend/src/index.js` (frontend/package.json main)", text)
        self.assertIn("`src/demo/__main__.py`", text)
        self.assertIn("## Largest Files", text)
        self.assertIn("## Documentation Files", text)
        self.assertIn("`README.md`", text)
        self.assertIn("## Artifact Guide", text)
        self.assertIn("symbols.json", text)

    def test_rewrite_removes_artifacts_from_older_versions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root, {"app.py": "print('hello')\n"})
            legacy_dir = root / ".codemapy"
            legacy_dir.mkdir()
            (legacy_dir / "legacy.json").write_text("{}", encoding="utf-8")
            report = build_report(root, scan_project(root, Config()))

            paths = write_artifacts(report)

            self.assertFalse((legacy_dir / "legacy.json").exists())
            self.assertTrue(paths.context.exists())

    def test_custom_output_dir_is_not_cleared(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root, {"app.py": "print('hello')\n"})
            out = root / "out"
            out.mkdir()
            (out / "unrelated.txt").write_text("keep me", encoding="utf-8")
            report = build_report(root, scan_project(root, Config()))

            write_artifacts(report, out)

            self.assertTrue((out / "unrelated.txt").exists())

    def test_context_json_has_no_per_file_absolute_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root, {"app.py": "print('hello')\n"})
            report = build_report(root, scan_project(root, Config()))
            paths = write_artifacts(report, Path(tmp) / "out")
            context = json.loads(paths.context.read_text(encoding="utf-8"))

        self.assertNotIn("generated_at", context)
        for entry in context["files"] + context["metadata_files"]:
            self.assertNotIn("absolute_path", entry)


class StalenessCheckTests(unittest.TestCase):
    def test_check_reports_missing_fresh_and_stale(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root, {"app.py": "print('hello')\n"})

            code, message = check_artifacts(root)
            self.assertEqual(2, code)
            self.assertIn("No artifacts", message)

            report = build_report(root, scan_project(root, Config()))
            write_artifacts(report)

            # Temp dirs are not git repositories, so this exercises the
            # mtime fallback path.
            code, message = check_artifacts(root)
            self.assertEqual(0, code, message)

            future = os.stat(root / "app.py").st_mtime + 3600
            os.utime(root / "app.py", (future, future))
            code, message = check_artifacts(root)
            self.assertEqual(1, code, message)
            self.assertIn("app.py", message)


class HtmlEscapingTests(unittest.TestCase):
    def test_report_data_cannot_break_out_of_script_block(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root, {"a.js": 'import x from "x</script><script>alert(1)//";\n'})
            report = build_report(root, scan_project(root, Config()))
            html = render_html(report)

        self.assertNotIn("x</script>", html)
        self.assertIn("x<\\/script>", html)


if __name__ == "__main__":
    unittest.main()
