"""Test coverage per file (tested_by), the Coverage section, and artifact queries."""

from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from codemapy import cli
from codemapy.artifacts import report_payload, summary_markdown, write_artifacts
from codemapy.config import load_config
from codemapy.graph import build_report
from codemapy.models import Report
from codemapy.query import UnknownPath, find, load_artifacts, resolve_path, tests_of, tokenize
from codemapy.scanner import scan_project
from codemapy.testfiles import subject_candidates


def _write(root: Path, files: dict[str, str]) -> None:
    for rel, content in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


def _report(root: Path, files: dict[str, str]) -> Report:
    _write(root, files)
    return build_report(root, scan_project(root, load_config(root)))


def _section(text: str, heading: str) -> str:
    return text.split(f"## {heading}")[1].split("\n## ")[0]


PROJECT = {
    "app/core.py": "class ImportResolver:\n    def resolve(self):\n        pass\n",
    "app/util.py": "def format_path():\n    pass\n",
    "app/cli.py": "from app import core, util\n",
    "app/other.py": "from app import core\n",
    "tests/test_core.py": "import unittest\nfrom app import core\n",
    "web/widget.ts": "export const widget = 1;\n",
    "web/__tests__/widget.test.ts": "import { widget } from '../widget';\n",
    "notes.sh": "echo hi\n",
}


def _run_cli(*args: str) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = cli.main(list(args))
    return code, out.getvalue(), err.getvalue()


class TestedByTests(unittest.TestCase):
    def test_subject_candidates_follow_naming_conventions(self) -> None:
        self.assertEqual(["pkg/foo.go"], subject_candidates("pkg/foo_test.go"))
        self.assertIn("src/a.ts", subject_candidates("src/__tests__/a.test.ts"))
        self.assertIn("src/main/java/a/Foo.java", subject_candidates("src/test/java/a/FooTest.java"))
        self.assertIn("app/models/user.rb", subject_candidates("spec/models/user_spec.rb"))
        self.assertEqual([], subject_candidates("lib/thing.rb"))

    def test_tests_cover_what_they_import_and_what_they_are_named_after(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            report = _report(Path(tmp), PROJECT)
        modules = {module.path: module for module in report.modules}
        self.assertEqual(("tests/test_core.py",), modules["app/core.py"].tested_by)
        self.assertEqual(("web/__tests__/widget.test.ts",), modules["web/widget.ts"].tested_by)
        self.assertEqual((), modules["app/util.py"].tested_by)

    def test_go_test_in_the_same_package_covers_its_sibling(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            report = _report(
                Path(tmp),
                {
                    "go.mod": "module app\n",
                    "calc/calc.go": "package calc\n",
                    "calc/calc_test.go": "package calc\n",
                },
            )
        modules = {module.path: module for module in report.modules}
        self.assertEqual(("calc/calc_test.go",), modules["calc/calc.go"].tested_by)

    def test_summary_reports_direct_coverage_and_untested_hubs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            report = _report(Path(tmp), PROJECT)
            section = _section(summary_markdown(report), "Test Coverage")
        self.assertIn("2 of 6 production modules (33%) have a direct test", section)
        self.assertIn("- Hubs without a direct test:", section)
        self.assertIn("`app/util.py`", section)
        self.assertNotIn("`app/core.py`", section)


class CoverageSectionTests(unittest.TestCase):
    def test_summary_table_and_blind_language_note(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            report = _report(Path(tmp), {**PROJECT, ".gitignore": "build.log\n"})
            section = _section(summary_markdown(report), "Coverage")
        self.assertIn("| Python | 5 | yes | ast |", section)
        self.assertIn("| Shell | 1 | no |", section)
        self.assertIn(
            "Fan-in and fan-out are 0 for Shell files because their imports are not analysed", section
        )
        self.assertIn("- Unresolved internal imports: 0", section)

    def test_context_json_coverage_block_counts_skips_by_reason(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            report = _report(
                Path(tmp), {**PROJECT, "dist/bundle.js": "x\n", ".gitignore": "secret.py\n", "secret.py": ""}
            )
            coverage = report_payload(report)["coverage"]
        self.assertEqual({"default-ignore": 1}, coverage["skipped"]["dirs"])
        self.assertEqual({"gitignore": 1}, coverage["skipped"]["files"])
        languages = {row["language"]: row for row in coverage["languages"]}
        self.assertTrue(languages["TypeScript"]["imports"])
        self.assertFalse(languages["Shell"]["imports"])


class QueryTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        write_artifacts(_report(self.root, PROJECT))
        self.artifacts = load_artifacts(self.root)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_tokenize_splits_camel_snake_and_paths(self) -> None:
        self.assertEqual(
            ["import", "resolver", "src", "db", "queries", "ts"], tokenize("ImportResolver src/db/queries.ts")
        )

    def test_find_ranks_symbol_and_path_matches_and_demotes_tests(self) -> None:
        hits = find(self.artifacts, "import resolver")
        self.assertEqual("app/core.py", hits[0].path)
        self.assertIn("ImportResolver", hits[0].matched)
        self.assertEqual([], find(self.artifacts, "zzz"))

    def test_resolve_path_accepts_unique_suffixes_and_reports_ambiguity(self) -> None:
        self.assertEqual("app/core.py", resolve_path(self.artifacts, "core.py", self.root))
        self.assertEqual(
            "app/core.py", resolve_path(self.artifacts, str(self.root / "app" / "core.py"), Path.cwd())
        )
        with self.assertRaises(UnknownPath) as raised:
            resolve_path(self.artifacts, "nope.py", self.root)
        self.assertEqual([], raised.exception.candidates)

    def test_tests_of_reads_tested_by_and_flags_old_artifacts(self) -> None:
        self.assertEqual(["tests/test_core.py"], tests_of(self.artifacts, "app/core.py"))
        context = json.loads((self.root / ".codemapy/context.json").read_text(encoding="utf-8"))
        for module in context["modules"]:
            module.pop("tested_by")
        (self.root / ".codemapy/context.json").write_text(json.dumps(context), encoding="utf-8")
        self.assertIsNone(tests_of(load_artifacts(self.root), "app/core.py"))

    def test_cli_importers_lists_production_before_tests(self) -> None:
        code, out, _ = _run_cli(str(self.root), "--importers", "core.py")
        self.assertEqual(0, code)
        lines = out.splitlines()
        self.assertTrue(lines[0].startswith("app/core.py: 3 importers (1 tests) from .codemapy/"))
        self.assertEqual(
            ["- app/cli.py", "- app/other.py", "- tests/test_core.py"],
            [line.split(" (")[0] for line in lines[1:]],
        )

    def test_cli_find_json_and_limits(self) -> None:
        code, out, _ = _run_cli(str(self.root), "--find", "core", "--json", "--limit", "1")
        self.assertEqual(0, code)
        payload = json.loads(out)
        self.assertEqual(["app/core.py"], [hit["path"] for hit in payload])

    def test_cli_query_errors(self) -> None:
        code, _, err = _run_cli(str(self.root), "--tests", "missing.py")
        self.assertEqual(2, code)
        self.assertIn("is not a mapped source file", err)
        with tempfile.TemporaryDirectory() as empty:
            code, _, err = _run_cli(empty, "--find", "x")
        self.assertEqual(2, code)
        self.assertIn("No artifacts found", err)


if __name__ == "__main__":
    unittest.main()
