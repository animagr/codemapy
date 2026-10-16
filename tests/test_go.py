"""Go import extraction, go.mod-based resolution, and package hub rows."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from codemapy.artifacts import summary_markdown
from codemapy.config import load_config
from codemapy.deps.go import GoExtractor
from codemapy.deps.treesitter import TreeSitterGoExtractor
from codemapy.graph import build_report, hub_entries
from codemapy.models import Report
from codemapy.scanner import scan_project
from codemapy.ts import backend

IMPORT_BLOCK = 'package main\n\nimport (\n\t"fmt"\n\tcfg "app/config"\n\t_ "app/plugins"\n)\n'


def _write(root: Path, files: dict[str, str]) -> None:
    for rel, content in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


def _report(root: Path, files: dict[str, str]) -> Report:
    _write(root, files)
    return build_report(root, scan_project(root, load_config(root)))


def _edges(report: Report) -> set[tuple[str, str]]:
    return {(edge.source, edge.target) for edge in report.edges}


APP = {
    "go.mod": "module app\n\ngo 1.24\n",
    "main.go": 'package main\n\nimport (\n\t"fmt"\n\t"app/config"\n)\n\nfunc main() { fmt.Println(config.Name) }\n',
    "config/config.go": "package config\n\nconst Name = 1\n",
    "config/load.go": 'package config\n\nimport "app/util"\n\nvar _ = util.X\n',
    "config/config_test.go": 'package config\n\nimport "testing"\n\nfunc TestX(t *testing.T) {}\n',
    "util/util.go": "package util\n\nconst X = 1\n",
}


class GoExtractionTests(unittest.TestCase):
    def test_regex_extractor_reads_single_lines_and_blocks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "main.go"
            path.write_text(IMPORT_BLOCK + 'import "os"\n', encoding="utf-8")
            refs = GoExtractor().extract(path)
        self.assertEqual(["fmt", "app/config", "app/plugins", "os"], [ref.raw for ref in refs])
        self.assertEqual({"go-import"}, {ref.kind for ref in refs})
        self.assertEqual(4, refs[0].line)

    @unittest.skipUnless(backend.AVAILABLE, "tree-sitter backend not installed")
    def test_tree_sitter_extractor_matches_the_regex_one(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "main.go"
            path.write_text(IMPORT_BLOCK, encoding="utf-8")
            tree_refs = TreeSitterGoExtractor(fallback=GoExtractor()).extract(path)
            regex_refs = GoExtractor().extract(path)
        self.assertEqual(
            {(r.raw, r.kind, r.line) for r in regex_refs}, {(r.raw, r.kind, r.line) for r in tree_refs}
        )


class GoResolutionTests(unittest.TestCase):
    def test_import_links_every_non_test_file_of_the_package(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            report = _report(Path(tmp), APP)
        self.assertIn(("main.go", "config/config.go"), _edges(report))
        self.assertIn(("main.go", "config/load.go"), _edges(report))
        self.assertNotIn(("main.go", "config/config_test.go"), _edges(report))
        self.assertIn(("config/load.go", "util/util.go"), _edges(report))
        self.assertEqual({"fmt", "testing"}, {item.raw for item in report.external_imports})
        self.assertEqual((), report.unresolved_imports)

    def test_nested_module_resolves_against_its_own_go_mod(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            report = _report(
                Path(tmp),
                {
                    **APP,
                    "testdata/fixture/go.mod": "module fixture\n",
                    "testdata/fixture/main.go": 'package main\n\nimport "fixture/lib"\n',
                    "testdata/fixture/lib/lib.go": "package lib\n",
                },
            )
        self.assertIn(("testdata/fixture/main.go", "testdata/fixture/lib/lib.go"), _edges(report))

    def test_relative_import_and_missing_project_package(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            report = _report(
                Path(tmp),
                {**APP, "cmd/tool.go": 'package main\n\nimport (\n\t"../util"\n\t"app/gone"\n)\n'},
            )
        self.assertIn(("cmd/tool.go", "util/util.go"), _edges(report))
        self.assertEqual([("app/gone", None)], [(u.raw, u.reason) for u in report.unresolved_imports])


class GoPackageHubTests(unittest.TestCase):
    def test_a_package_is_one_hub_row_counting_outside_importers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            report = _report(
                Path(tmp),
                {**APP, "cmd/a.go": 'package main\n\nimport "app/config"\n'},
            )
            text = summary_markdown(report)
        rows = {hub.label: hub for hub in hub_entries(report)}
        self.assertEqual((2, 2, 1), (rows["config/"].files, rows["config/"].fan_in, rows["config/"].fan_out))
        self.assertIn("- `config/` (package, 2 files): fan-in 2", text)
        self.assertNotIn("`config/config.go`", text.split("## Top Hubs")[1].split("\n## ")[0])


if __name__ == "__main__":
    unittest.main()
