from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from codemapy.config import Config
from codemapy.gui import _summary_text
from codemapy.graph import build_report
from codemapy.render.html import write_html
from codemapy.scanner import scan_project


class CoreTests(unittest.TestCase):
    def test_scans_files_and_builds_python_edges(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "pkg").mkdir()
            (root / "pkg" / "__init__.py").write_text("", encoding="utf-8")
            (root / "pkg" / "a.py").write_text("from . import b\n", encoding="utf-8")
            (root / "pkg" / "b.py").write_text("VALUE = 1\n", encoding="utf-8")

            files = scan_project(root, Config())
            report = build_report(root, files)

        self.assertEqual(len(files), 3)
        self.assertIn(("pkg/a.py", "pkg/b.py"), {(edge.source, edge.target) for edge in report.edges})

    def test_resolves_src_layout_absolute_python_imports(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            package = root / "src" / "codemapy"
            package.mkdir(parents=True)
            (package / "__init__.py").write_text("", encoding="utf-8")
            (package / "models.py").write_text("class Model: pass\n", encoding="utf-8")
            (package / "graph.py").write_text("from codemapy.models import Model\n", encoding="utf-8")

            report = build_report(root, scan_project(root, Config()))

        self.assertIn(
            ("src/codemapy/graph.py", "src/codemapy/models.py"),
            {(edge.source, edge.target) for edge in report.edges},
        )

    def test_gui_summary_text_contains_report_counts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "main.py").write_text("import os\n", encoding="utf-8")
            report = build_report(root, scan_project(root, Config()))
            html_path = write_html(report, root / "report.html")

            summary = _summary_text(report, html_path)

        self.assertIn("Files: 1", summary)
        self.assertIn("External references: 1", summary)
        self.assertIn("report.html", summary)

    def test_cli_json_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "app.py").write_text("import os\n", encoding="utf-8")
            env = os.environ.copy()
            env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")

            result = subprocess.run(
                [sys.executable, "-m", "codemapy", str(root), "--json"],
                check=True,
                capture_output=True,
                text=True,
                env=env,
            )

        payload = json.loads(result.stdout)
        self.assertEqual(payload["root"], str(root.resolve()))
        self.assertEqual(payload["files"][0]["path"], "app.py")


if __name__ == "__main__":
    unittest.main()
