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
from codemapy.models import FileNode, ModuleNode, Report, Symbol
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


class GDScriptResolutionTests(unittest.TestCase):
    def test_class_name_usage_creates_edge_without_extends(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(
                root,
                {
                    "job.gd": "class_name Job\nextends RefCounted\n",
                    "manager.gd": (
                        "extends Node\n"
                        "func start() -> void:\n"
                        "\tvar job := Job.new()\n"
                        "\tprint(Vector2.ZERO)\n"
                    ),
                },
            )
            report = build_report(root, scan_project(root, Config()))

        edges = {(e.source, e.target) for e in report.edges}
        self.assertIn(("manager.gd", "job.gd"), edges)
        # Unresolved engine identifiers must not flood external references.
        externals = {e.raw for e in report.external_imports}
        self.assertNotIn("Vector2", externals)

    def test_autoload_reference_creates_edge(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(
                root,
                {
                    "project.godot": (
                        "config_version=5\n"
                        "\n"
                        "[autoload]\n"
                        "\n"
                        'Global="*res://scripts/global.gd"\n'
                    ),
                    "scripts/global.gd": "extends Node\nvar score := 0\n",
                    "player.gd": (
                        "extends Node\n"
                        "func hit() -> void:\n"
                        "\tGlobal.score += 1\n"
                    ),
                },
            )
            report = build_report(root, scan_project(root, Config()))

        edges = {(e.source, e.target) for e in report.edges}
        self.assertIn(("player.gd", "scripts/global.gd"), edges)


@unittest.skipUnless(backend.AVAILABLE, "tree-sitter backend not installed")
class GDScriptSymbolTests(unittest.TestCase):
    def test_extracts_gdscript_definitions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(
                root,
                {
                    "actor.gd": (
                        "class_name Actor\n"
                        "extends Node\n"
                        "\n"
                        "signal died(cause)\n"
                        "\n"
                        "enum State { IDLE, RUN }\n"
                        "\n"
                        "func take_damage(amount: int) -> void:\n"
                        "\tpass\n"
                        "\n"
                        "class Inner:\n"
                        "\tfunc helper() -> void:\n"
                        "\t\tpass\n"
                    ),
                },
            )
            report = build_report(root, scan_project(root, Config()))

        module = next(m for m in report.modules if m.path == "actor.gd")
        pairs = set()

        def walk(symbols) -> None:
            for symbol in symbols:
                pairs.add((symbol.name, symbol.kind))
                walk(symbol.children)

        walk(module.symbols)
        self.assertIn(("Actor", "class"), pairs)
        self.assertIn(("died", "signal"), pairs)
        self.assertIn(("State", "enum"), pairs)
        self.assertIn(("take_damage", "function"), pairs)
        self.assertIn(("Inner", "class"), pairs)
        self.assertIn(("helper", "function"), pairs)
        # Inner's method is nested under the inner class.
        inner = next(s for s in module.symbols if s.name == "Inner")
        self.assertEqual(["helper"], [c.name for c in inner.children])


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


class EntryPointTests(unittest.TestCase):
    @staticmethod
    def _c_module(path: str, symbols: tuple[Symbol, ...]) -> ModuleNode:
        return ModuleNode(path=path, language="C", loc=10, size=100, symbols=symbols)

    def test_main_definer_detected_from_symbols(self) -> None:
        main_symbol = Symbol(name="main", kind="function", start_line=3, end_line=9)
        report = Report(
            root=Path("/proj"),
            files=(),
            modules=(self._c_module("fw/nested/controller.c", (main_symbol,)),),
            edges=(),
        )
        text = summary_markdown(report, "2026-07-03T00:00:00+00:00")
        self.assertIn("- `fw/nested/controller.c` (defines main())", text)

    def test_main_definer_not_duplicated_with_basename_match(self) -> None:
        main_symbol = Symbol(name="main", kind="function", start_line=1, end_line=2)
        file = FileNode(
            path="main.c",
            absolute_path=Path("/proj/main.c"),
            size=100,
            loc=10,
            extension=".c",
            language="C",
        )
        report = Report(
            root=Path("/proj"),
            files=(file,),
            modules=(self._c_module("main.c", (main_symbol,)),),
            edges=(),
        )
        text = summary_markdown(report, "2026-07-03T00:00:00+00:00")
        section = text.split("## Entry Points")[1].split("##")[0]
        self.assertEqual(1, section.count("- `main.c`"))
        self.assertIn("- `main.c` (defines main())", section)

    def test_main_function_outside_main_languages_is_ignored(self) -> None:
        main_symbol = Symbol(name="main", kind="function", start_line=1, end_line=2)
        module = ModuleNode(path="tools/helper.py", language="Python", loc=10, size=100, symbols=(main_symbol,))
        report = Report(root=Path("/proj"), files=(), modules=(module,), edges=())
        text = summary_markdown(report, "2026-07-03T00:00:00+00:00")
        self.assertNotIn("tools/helper.py", text.split("## Entry Points")[1].split("##")[0])


class MainGuardEntryPointTests(unittest.TestCase):
    @staticmethod
    def _module(path: str, *, guard: bool = True, fan_in: int = 0, fan_out: int = 0) -> ModuleNode:
        return ModuleNode(
            path=path,
            language="Python",
            loc=10,
            size=100,
            fan_in=fan_in,
            fan_out=fan_out,
            has_main_guard=guard,
        )

    @staticmethod
    def _section(report: Report) -> str:
        text = summary_markdown(report, "2026-07-03T00:00:00+00:00")
        return text.split("## Entry Points")[1].split("##")[0]

    def test_guard_detected_from_source(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root, {"tool.py": "def go():\n    pass\n\n\nif __name__ == '__main__':\n    go()\n"})
            report = build_report(root, scan_project(root, Config()))
        self.assertTrue(report.modules[0].has_main_guard)
        self.assertIn("- `tool.py` (__main__ guard)", self._section(report))

    def test_guard_nested_in_a_function_is_not_an_entry_point(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root, {"lib.py": "def go():\n    if __name__ == '__main__':\n        pass\n"})
            report = build_report(root, scan_project(root, Config()))
        self.assertFalse(report.modules[0].has_main_guard)
        self.assertIn("- None detected", self._section(report))

    def test_guard_variants_are_recognised(self) -> None:
        sources = {
            "reversed.py": "if '__main__' == __name__:\n    pass\n",
            "membership.py": "if __name__ in ('__main__', 'x'):\n    pass\n",
            "conjunction.py": "FLAG = True\nif __name__ == '__main__' and FLAG:\n    pass\n",
        }
        for rel, source in sources.items():
            with self.subTest(rel), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                _write(root, {rel: source})
                report = build_report(root, scan_project(root, Config()))
                self.assertTrue(report.modules[0].has_main_guard)

    def test_unrelated_dunder_comparison_is_not_a_guard(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root, {"lib.py": "if __doc__ == '__main__':\n    pass\n"})
            report = build_report(root, scan_project(root, Config()))
        self.assertFalse(report.modules[0].has_main_guard)

    def test_imported_module_with_a_guard_is_not_an_entry_point(self) -> None:
        """A demo block in a library is not a program: something imports it."""
        report = Report(
            root=Path("/proj"),
            files=(),
            modules=(self._module("driver.py", fan_in=3, fan_out=1),),
            edges=(),
        )
        self.assertIn("- None detected", self._section(report))

    def test_guard_entries_are_ranked_by_fan_out(self) -> None:
        report = Report(
            root=Path("/proj"),
            files=(),
            modules=(
                self._module("widget.py", fan_out=1),
                self._module("app.py", fan_out=9),
                self._module("orphan.py", fan_out=0),
            ),
            edges=(),
        )
        section = self._section(report)
        self.assertLess(section.index("app.py"), section.index("widget.py"))
        self.assertLess(section.index("widget.py"), section.index("orphan.py"))

    def test_modules_under_a_test_directory_are_excluded(self) -> None:
        report = Report(
            root=Path("/proj"),
            files=(),
            modules=(
                self._module("tests/test_core.py", fan_out=9),
                self._module("test/helper_check.py", fan_out=8),
                self._module("cli.py", fan_out=1),
            ),
            edges=(),
        )
        section = self._section(report)
        self.assertNotIn("tests/test_core.py", section)
        self.assertNotIn("test/helper_check.py", section)
        self.assertIn("- `cli.py` (__main__ guard)", section)

    def test_test_named_file_at_the_root_is_still_an_entry_point(self) -> None:
        """An instrument test bench is the application, not a test module."""
        report = Report(
            root=Path("/proj"),
            files=(),
            modules=(self._module("11_201_test.py", fan_out=31),),
            edges=(),
        )
        self.assertIn("- `11_201_test.py` (__main__ guard)", self._section(report))

    def test_guard_entries_are_capped(self) -> None:
        modules = tuple(self._module(f"script{index}.py") for index in range(8))
        report = Report(root=Path("/proj"), files=(), modules=modules, edges=())
        section = self._section(report)
        self.assertEqual(3, section.count("(__main__ guard)"))

    def test_guard_entries_never_displace_manifest_scripts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sources = {f"script{index}.py": "if __name__ == '__main__':\n    pass\n" for index in range(8)}
            sources["pyproject.toml"] = '[project]\nname = "p"\n[project.scripts]\nrealcli = "p:main"\n'
            _write(root, sources)
            report = build_report(root, scan_project(root, Config()))
            # Manifest scripts are re-read from disk when entry points are
            # rendered, so stay inside the temporary directory.
            section = self._section(report)
        self.assertIn("- `realcli` -> `p:main` (pyproject.toml script)", section)
        self.assertEqual(3, section.count("(__main__ guard)"))


class HtmlReportTests(unittest.TestCase):
    def test_report_embeds_insights_payload_and_panels(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(
                root,
                {
                    "pkg/__init__.py": "",
                    "pkg/a.py": "import pkg.b\n",
                    "pkg/b.py": "import pkg.a\n",
                },
            )
            report = build_report(root, scan_project(root, Config()))
            html = render_html(report)

        for marker in (
            '"entry_points"',
            '"cycles"',
            '"symbol_count"',
            'id="legend"',
            ">Entry Points<",
            ">Top Hubs<",
            ">Dependency Cycles<",
            ">Insights<",
        ):
            self.assertIn(marker, html)
        # The a<->b import cycle must surface in both the payload and the chip.
        self.assertIn("1 cycles", html)

    def test_symbol_outline_is_capped(self) -> None:
        from codemapy.render.html import MAX_HTML_SYMBOLS_PER_FILE, _report_payload

        symbols = tuple(
            Symbol(name=f"f{i}", kind="function", start_line=i + 1, end_line=i + 1)
            for i in range(MAX_HTML_SYMBOLS_PER_FILE + 10)
        )
        module = ModuleNode(path="big.c", language="C", loc=1, size=1, symbols=symbols)
        report = Report(root=Path("/proj"), files=(), modules=(module,), edges=())
        payload = _report_payload(report)
        entry = payload["modules"][0]
        self.assertEqual(MAX_HTML_SYMBOLS_PER_FILE, len(entry["symbols"]))
        self.assertEqual(10, entry["symbols_omitted"])
        self.assertEqual(MAX_HTML_SYMBOLS_PER_FILE + 10, entry["symbol_count"])


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
