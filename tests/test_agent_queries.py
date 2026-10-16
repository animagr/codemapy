"""0.3.0 features: Java imports, inheritance, symbol/impact/why queries, stack,
possibly unused modules, and change-aware staleness."""

from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from codemapy import cli
from codemapy.artifacts import check_artifacts, report_payload, summary_markdown, write_artifacts
from codemapy.config import Config, load_config
from codemapy.deps.java import JavaExtractor
from codemapy.graph import build_report
from codemapy.impact import impact
from codemapy.models import Report
from codemapy.query import load_artifacts
from codemapy.scanner import scan_project
from codemapy.stack import frameworks, read_stack
from codemapy.symbol_queries import base_name, symbol_records, where
from codemapy.ts import backend
from codemapy.unused import possibly_unused
from codemapy.why import why

GIT_AVAILABLE = shutil.which("git") is not None


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


def _section(text: str, heading: str) -> str:
    return text.split(f"## {heading}")[1].split("\n## ")[0]


def _run_cli(*args: str) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = cli.main(list(args))
    return code, out.getvalue(), err.getvalue()


def _git(root: Path, *args: str) -> None:
    identity = ("-c", "user.name=t", "-c", "user.email=t@example.invalid", "-c", "commit.gpgsign=false")
    subprocess.run(["git", "-C", str(root), *identity, *args], check=True, capture_output=True)


JAVA = "src/main/java/com/acme"
JAVA_FILES = {
    f"{JAVA}/core/Engine.java": "package com.acme.core;\n\npublic class Engine extends Base {\n  public static class Part {}\n}\n",
    f"{JAVA}/core/Base.java": "package com.acme.core;\n\npublic abstract class Base {}\n",
    f"{JAVA}/core/Unrelated.java": "package com.acme.core;\n\npublic class Unrelated {}\n",
    f"{JAVA}/ui/Window.java": (
        "package com.acme.ui;\n\n"
        "import java.util.List;\n"
        "import com.acme.core.Engine;\n"
        "import com.acme.core.Engine.Part;\n"
        "import static com.acme.core.Base.helper;\n"
        "import com.acme.missing.Gone;\n\n"
        "public class Window {}\n"
    ),
    f"{JAVA}/ui/Panel.java": "package com.acme.ui;\n\nimport com.acme.core.*;\n\nclass Panel { Engine e; Window w; }\n",
}


class JavaImportTests(unittest.TestCase):
    def test_extractor_reads_package_and_import_forms(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "Window.java"
            path.write_text(
                "// import fake.Comment;\n" + JAVA_FILES[f"{JAVA}/ui/Window.java"], encoding="utf-8"
            )
            refs = JavaExtractor().extract(path)
        self.assertEqual(
            [
                ("com.acme.ui", "java-package"),
                ("java.util.List", "java-import"),
                ("com.acme.core.Engine", "java-import"),
                ("com.acme.core.Engine.Part", "java-import"),
                ("com.acme.core.Base", "java-import"),
                ("com.acme.missing.Gone", "java-import"),
            ],
            [(ref.raw, ref.kind) for ref in refs],
        )

    def test_imports_wildcards_and_same_package_mentions_resolve(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            report = _report(Path(tmp), JAVA_FILES)
        edges = _edges(report)
        self.assertIn((f"{JAVA}/ui/Window.java", f"{JAVA}/core/Engine.java"), edges)
        self.assertIn((f"{JAVA}/ui/Window.java", f"{JAVA}/core/Base.java"), edges)  # static import
        self.assertIn((f"{JAVA}/ui/Panel.java", f"{JAVA}/core/Engine.java"), edges)  # wildcard, mentioned
        self.assertNotIn(
            (f"{JAVA}/ui/Panel.java", f"{JAVA}/core/Unrelated.java"), edges
        )  # wildcard, unmentioned
        self.assertIn((f"{JAVA}/ui/Panel.java", f"{JAVA}/ui/Window.java"), edges)  # same package, no import
        self.assertIn((f"{JAVA}/core/Engine.java", f"{JAVA}/core/Base.java"), edges)
        self.assertEqual(["java.util.List"], [item.raw for item in report.external_imports])
        self.assertEqual(["com.acme.missing.Gone"], [item.raw for item in report.unresolved_imports])

    def test_import_of_own_nested_class_is_not_reported(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            report = _report(
                Path(tmp),
                {"src/a/Tok.java": "package a;\n\nimport a.Tok.Kind;\n\nclass Tok { enum Kind {} }\n"},
            )
        self.assertEqual(((), ()), (report.unresolved_imports, report.edges))

    def test_lowercase_release_folder_is_source_but_visual_studio_output_is_not(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(
                root,
                {
                    "gui/release/Check.java": "package gui.release;\nclass Check {}\n",
                    "Release/app.c": "int x;\n",
                },
            )
            scan = scan_project(root, Config())
        self.assertEqual(["gui/release/Check.java"], [file.path for file in scan.sources])


class InheritanceTests(unittest.TestCase):
    def test_python_bases_skip_keywords_and_reach_symbols_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            report = _report(root, {"m.py": "import abc\n\nclass A(Base, abc.ABC, metaclass=M):\n    pass\n"})
            paths = write_artifacts(report)
            symbols = json.loads(paths.symbols.read_text(encoding="utf-8"))
        self.assertEqual(["Base", "abc.ABC"], symbols["files"]["m.py"][0]["bases"])

    @unittest.skipUnless(backend.AVAILABLE, "tree-sitter backend not installed")
    def test_tree_sitter_languages_record_extends_and_implements(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            report = _report(Path(tmp), {"w.ts": "class W extends Base implements I, J {}\n", **JAVA_FILES})
        bases = {symbol.name: symbol.bases for module in report.modules for symbol in module.symbols}
        self.assertEqual(("Base", "I", "J"), bases["W"])
        self.assertEqual(("Base",), bases["Engine"])

    def test_base_name_strips_scopes_and_generics(self) -> None:
        self.assertEqual(
            ["ABC", "Widget", "List", "Generic"],
            [base_name(text) for text in ("abc.ABC", "ns::Widget", "List<T>", "Generic[T]")],
        )


class SymbolQueryTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        files = {
            "shapes.py": (
                "class Shape:\n    def area(self):\n        '''Area of the shape.'''\n\n"
                "class Square(Shape):\n    def area(self):\n        pass\n"
            ),
            "app.py": "from shapes import Square\n",
            "tests/test_shapes.py": "import unittest\nfrom shapes import Square\n",
        }
        write_artifacts(_report(self.root, files))
        self.artifacts = load_artifacts(self.root)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_where_matches_names_and_qualified_names(self) -> None:
        self.assertEqual(["Shape.area", "Square.area"], [r.qualified for r in where(self.artifacts, "area")])
        self.assertEqual(["Square.area"], [r.qualified for r in where(self.artifacts, "Square.area")])
        self.assertEqual(["Shape"], [r.qualified for r in where(self.artifacts, "shape")])

    def test_cli_where_lists_bases_and_subclasses(self) -> None:
        code, out, _ = _run_cli(str(self.root), "--where", "Shape")
        self.assertEqual(0, code)
        self.assertIn("- shapes.py:1-3 class Shape", out)
        self.assertIn("extended by: Square (shapes.py:5)", out)

    def test_outline_is_nested_in_source_order(self) -> None:
        records = symbol_records(self.artifacts, "shapes.py")
        self.assertEqual(
            [("Shape", 0), ("area", 1), ("Square", 0), ("area", 1)], [(r.name, r.depth) for r in records]
        )
        code, out, _ = _run_cli(str(self.root), "--outline", "shapes.py")
        self.assertEqual(0, code)
        self.assertIn("  L2-3 def area(self)  # Area of the shape.", out)

    def test_impact_groups_dependents_and_collects_tests(self) -> None:
        result = impact(self.artifacts, ["shapes.py"])
        self.assertEqual({"app.py": 1, "tests/test_shapes.py": 1}, result.distance)
        self.assertEqual(("tests/test_shapes.py",), result.tests)
        code, out, _ = _run_cli(str(self.root), "--impact", "shapes.py")
        self.assertEqual(0, code)
        self.assertIn("shapes.py: 1 production files depend on it, and 1 tests cover", out)

    def test_why_explains_mapped_skipped_doc_and_missing_files(self) -> None:
        _write(self.root, {"README.md": "# x\n", "node_modules/pkg/index.js": "x\n", "data.bin": "x"})
        write_artifacts(build_report(self.root, scan_project(self.root, Config())))
        artifacts = load_artifacts(self.root)
        cases = {
            "shapes.py": "is mapped: Python",
            "README.md": "is documentation",
            "node_modules/pkg/index.js": "`node_modules/` was skipped (a default-ignored name)",
            "data.bin": "`.bin` is a file type codemapy does not map",
            "gone.py": "does not exist",
        }
        for raw, expected in cases.items():
            with self.subTest(raw):
                self.assertIn(expected, why(artifacts, raw, self.root)[0])


@unittest.skipUnless(GIT_AVAILABLE, "git is not installed")
class ChangeAwarenessTests(unittest.TestCase):
    def test_changed_query_and_check_list_changed_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(
                root,
                {
                    "lib.py": "X = 1\n",
                    "app.py": "import lib\n",
                    "tests/test_lib.py": "import unittest\nimport lib\n",
                },
            )
            _git(root, "init", "--quiet")
            _git(root, "add", "--all")
            _git(root, "commit", "--quiet", "-m", "init")
            write_artifacts(build_report(root, scan_project(root, Config())))
            self.assertEqual(0, check_artifacts(root)[0])

            later = os.stat(root / "lib.py").st_mtime + 60
            (root / "lib.py").write_text("X = 2\n", encoding="utf-8")
            os.utime(root / "lib.py", (later, later))
            (root / "new.py").write_text("", encoding="utf-8")
            code, message = check_artifacts(root)
            self.assertEqual(1, code)
            self.assertIn("2 files changed since they were written: lib.py, new.py", message)

            code, out, _ = _run_cli(str(root), "--changed")
            self.assertEqual(0, code)
            self.assertIn("Changed but not in the map (1; new, or not mapped source): new.py", out)
            self.assertIn("- app.py", out)
            self.assertIn("- tests/test_lib.py", out)

            code, out, err = _run_cli(str(root), "--importers", "lib.py")
            self.assertIn("lib.py", err)

    def test_artifacts_from_a_dirty_tree_stay_fresh_until_something_changes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root, {"a.py": "X = 1\n"})
            _git(root, "init", "--quiet")
            _git(root, "add", "--all")
            _git(root, "commit", "--quiet", "-m", "init")
            (root / "a.py").write_text("X = 2\n", encoding="utf-8")
            write_artifacts(build_report(root, scan_project(root, Config())))
            code, message = check_artifacts(root)
        self.assertEqual(0, code, message)
        self.assertIn("no file changed since the artifacts were written", message)


class StackTests(unittest.TestCase):
    def test_manifests_dependencies_and_frameworks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            report = _report(
                root,
                {
                    "pyproject.toml": (
                        '[project]\nname = "bench"\ndependencies = ["PySide6>=6.6", "pyvisa"]\n'
                        '[project.optional-dependencies]\ntest = ["pytest"]\n'
                    ),
                    "web/package.json": '{"name": "ui", "dependencies": {"react": "^18"}, "devDependencies": {"vite": "5"}}',
                    "go.mod": "module m\n\nrequire (\n\tgithub.com/spf13/cobra v1.8.0\n\tgolang.org/x/sys v0.1.0 // indirect\n)\n",
                    "tests/fixtures/package.json": '{"dependencies": {"left-pad": "1"}}',
                    "app.py": "",
                },
            )
            manifests = read_stack(report)
            text = _section(summary_markdown(report), "Stack")
        by_path = {manifest.path: manifest for manifest in manifests}
        self.assertEqual({"pyproject.toml", "web/package.json", "go.mod"}, set(by_path))
        self.assertEqual(("PySide6", "pyvisa"), by_path["pyproject.toml"].dependencies)
        self.assertEqual(("pytest",), by_path["pyproject.toml"].dev_dependencies)
        self.assertEqual(("github.com/spf13/cobra",), by_path["go.mod"].dependencies)
        self.assertIn("PyVISA (instrument control)", frameworks(manifests))
        self.assertIn("- Recognised: PySide6 (Qt GUI), pytest (tests), PyVISA (instrument control)", text)


class PossiblyUnusedTests(unittest.TestCase):
    def test_orphans_are_listed_but_conventions_are_not(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            report = _report(
                Path(tmp),
                {
                    "pkg/__init__.py": "",
                    "pkg/used.py": "",
                    "pkg/orphan.py": "",
                    "app.py": "from pkg import used\n",
                    "tool.py": "if __name__ == '__main__':\n    pass\n",
                    "scripts/once.py": "",
                    "tests/test_x.py": "",
                    "docs/conf.py": "",
                    "native/main.c": "int main(void) { return 0; }\n",
                    "lib/helper.c": "int helper(void) { return 0; }\n",
                },
            )
            unused = [module.path for module in possibly_unused(report).modules]
            payload = report_payload(report)
        self.assertEqual(["pkg/orphan.py"], unused)
        self.assertEqual(["pkg/orphan.py"], payload["possibly_unused"])

    def test_go_files_count_per_package(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            report = _report(
                Path(tmp),
                {
                    "go.mod": "module m\n",
                    "main.go": 'package main\n\nimport "m/used"\n\nfunc main() {}\n',
                    "helpers.go": "package main\n",
                    "used/a.go": "package used\n",
                    "used/b.go": "package used\n",
                    "idle/c.go": "package idle\n",
                },
            )
        self.assertEqual(["idle/c.go"], [module.path for module in possibly_unused(report).modules])

    def test_js_is_left_out_when_aliases_hide_importers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = possibly_unused(
                _report(
                    Path(tmp),
                    {
                        "tsconfig.json": '{"compilerOptions": {"paths": {"@c/*": ["src/c/*"]}}}',
                        "src/c/Card.ts": "export const Card = 1;\n",
                        "src/app.ts": "import { Card } from '@c/Card';\n",
                    },
                )
            )
        self.assertEqual((), result.modules)
        self.assertIn("declares import path aliases", result.left_out[0])


class ContextPayloadTests(unittest.TestCase):
    def test_skipped_list_is_written_for_why(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            report = _report(
                Path(tmp), {"a.py": "", "node_modules/x.js": "", ".gitignore": "secret.py\n", "secret.py": ""}
            )
            skipped = report_payload(report)["skipped"]
        self.assertIn({"path": "node_modules", "reason": "default-ignore", "is_dir": True}, skipped)
        self.assertIn({"path": "secret.py", "reason": "gitignore", "is_dir": False}, skipped)


if __name__ == "__main__":
    unittest.main()
