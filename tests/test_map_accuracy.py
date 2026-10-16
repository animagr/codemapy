"""Tests for map accuracy: skipped and unresolved imports, test and generated
file classification, hub ranking, and entry-point filtering."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from codemapy.artifacts import hubs_payload, report_payload, summary_markdown, write_artifacts
from codemapy.config import Config, load_config
from codemapy.generated import (
    has_generated_header,
    has_generated_path,
    is_generated_source,
    is_minified_script,
)
from codemapy.graph import build_report, production_hubs
from codemapy.models import ImportRef, Report, SkipReason
from codemapy.scanner import scan_project
from codemapy.testfiles import is_test_file


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


class ToolOutputDirTests(unittest.TestCase):
    def test_db_folder_without_fpga_project_is_mapped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            report = _report(
                root,
                {
                    "src/app.ts": "import { query } from './db/queries';\n",
                    "src/db/queries.ts": "export const query = 1;\n",
                },
            )
        self.assertIn("src/db/queries.ts", [file.path for file in report.files])
        self.assertIn(("src/app.ts", "src/db/queries.ts"), _edges(report))
        self.assertEqual((), report.unresolved_imports)

    def test_quartus_db_folder_beside_project_file_is_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(
                root,
                {
                    "top.qpf": "PROJECT_REVISION = top\n",
                    "top.v": "module top(); endmodule\n",
                    "db/top.v": "x\n",
                },
            )
            scan = scan_project(root, Config())
        self.assertEqual(["top.v"], [file.path for file in scan.sources])
        self.assertIn(("db", SkipReason.TOOL_OUTPUT), {(item.path, item.reason) for item in scan.skipped})

    def test_vivado_impl_run_two_levels_below_project_is_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(
                root,
                {
                    "proj/proj.xpr": "<Project/>\n",
                    "proj/src/top.v": "module top(); endmodule\n",
                    "proj/proj.runs/impl_1/top_routed.v": "module top(); endmodule\n",
                },
            )
            scan = scan_project(root, Config())
        self.assertEqual(["proj/src/top.v"], [file.path for file in scan.sources])

    def test_modelsim_work_library_is_skipped_by_its_marker(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(
                root,
                {
                    "sim/tb.v": "module tb(); endmodule\n",
                    "sim/work/_info": "m255\n",
                    "sim/work/cache.v": "x\n",
                },
            )
            scan = scan_project(root, Config())
        self.assertEqual(["sim/tb.v"], [file.path for file in scan.sources])

    def test_plain_impl_and_work_folders_are_mapped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root, {"src/impl/engine.rs": "pub fn run() {}\n", "work/notes.py": "VALUE = 1\n"})
            scan = scan_project(root, Config())
        self.assertEqual(["src/impl/engine.rs", "work/notes.py"], [file.path for file in scan.sources])

    def test_files_named_like_tool_output_folders_are_mapped(self) -> None:
        """0.1.9 applied the `impl` folder prefix to file names and dropped Implicant.java."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(
                root, {"top.qpf": "x\n", "model/Implicant.java": "class Implicant {}\n", "src/impl.rs": ""}
            )
            scan = scan_project(root, Config())
        self.assertEqual(["model/Implicant.java", "src/impl.rs"], [file.path for file in scan.sources])

    def test_keep_dirs_overrides_tool_output_detection(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(
                root,
                {
                    ".codemap.json": '{"keep_dirs": ["db"]}\n',
                    "top.qpf": "x\n",
                    "db/top.v": "module top(); endmodule\n",
                },
            )
            scan = scan_project(root, load_config(root))
        self.assertEqual(["db/top.v"], [file.path for file in scan.sources])


class UnresolvedImportTests(unittest.TestCase):
    def test_import_into_default_ignored_dir_is_unresolved_not_external(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            report = _report(
                root, {"src/app.js": "import lib from '../dist/lib';\n", "dist/lib.js": "export default 1;\n"}
            )
        self.assertEqual((), report.external_imports)
        [item] = report.unresolved_imports
        self.assertEqual((SkipReason.DEFAULT_IGNORE, "dist/"), (item.reason, item.hidden_by))
        # Built-in names hide build output and vendored code on purpose: listed, not warned.
        self.assertEqual((), report.warnings)
        self.assertIn(
            'add "dist" to keep_dirs', _section(summary_markdown(report), "Unresolved Internal Imports")
        )

    def test_vendored_stylesheet_import_is_not_an_unresolved_edge(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            report = _report(
                root,
                {
                    "src/setup.js": "import '../vendor/ui.min.css';\nimport '../vendor/ui';\n",
                    "vendor/ui.min.css": "a{}\n",
                    "vendor/ui.js": "export {};\n",
                },
            )
        self.assertEqual(["../vendor/ui"], [item.raw for item in report.unresolved_imports])

    def test_import_into_config_ignored_dir_points_at_ignore_dirs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            report = _report(
                root,
                {
                    ".codemap.json": '{"ignore_dirs": ["gen"]}\n',
                    "app.ts": "import { api } from './gen/api';\n",
                    "gen/api.ts": "export const api = 1;\n",
                },
            )
            text = summary_markdown(report)
        [item] = report.unresolved_imports
        self.assertEqual(SkipReason.CONFIG_IGNORE, item.reason)
        self.assertIn('remove "gen" from ignore_dirs', _section(text, "Unresolved Internal Imports"))
        self.assertEqual(1, len(report.warnings))
        self.assertIn("point", report.warnings[0])

    def test_import_of_gitignored_file_names_the_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            report = _report(
                root,
                {
                    ".gitignore": "pkg/secret.py\n",
                    "pkg/__init__.py": "",
                    "pkg/main.py": "from . import secret\n",
                    "pkg/secret.py": "TOKEN = 1\n",
                },
            )
        [item] = report.unresolved_imports
        self.assertEqual((SkipReason.GITIGNORE, "pkg/secret.py"), (item.reason, item.hidden_by))

    def test_missing_relative_script_import_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            report = _report(root, {"src/app.ts": "import { gone } from './absent';\n"})
            text = summary_markdown(report)
        [item] = report.unresolved_imports
        self.assertIsNone(item.reason)
        self.assertIn("No matching file: 1 import from 1 file", _section(text, "Unresolved Internal Imports"))
        self.assertEqual((), report.warnings)

    def test_asset_and_package_imports_stay_external(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            report = _report(
                root,
                {
                    "app.ts": (
                        "import './styles.css';\nimport data from './data.json';\nimport React from 'react';\n"
                    )
                },
            )
        self.assertEqual((), report.unresolved_imports)
        self.assertEqual(
            {"./styles.css", "./data.json", "react"}, {item.raw for item in report.external_imports}
        )

    def test_extensionless_import_of_a_json_file_is_not_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            report = _report(
                root,
                {
                    "public/main.js": "import circuit from './defaultCircuit';\n",
                    "public/defaultCircuit.json": "{}\n",
                },
            )
        self.assertEqual((), report.unresolved_imports)

    def test_python_from_dot_import_of_a_package_attribute_is_not_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            report = _report(root, {"pkg/__init__.py": "helper = 1\n", "pkg/a.py": "from . import helper\n"})
        self.assertEqual((), report.unresolved_imports)

    def test_python_missing_submodule_of_a_project_module_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            report = _report(root, {"utils.py": "VALUE = 1\n", "main.py": "import utils.extra\nimport os\n"})
        self.assertEqual(["utils.extra"], [item.raw for item in report.unresolved_imports])
        self.assertEqual(["os"], [item.raw for item in report.external_imports])

    def test_python_namespace_package_import_is_not_missing(self) -> None:
        """A folder of modules without __init__.py is importable (PEP 420)."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            report = _report(
                root,
                {
                    "lab_utils/gui_utils.py": "VALUE = 1\n",
                    "lab_utils/hostname.py": "VALUE = 2\n",
                    "test_ctrl.py": "from lab_utils import gui_utils\nfrom lab_utils.hostname import VALUE\n",
                },
            )
        self.assertEqual((), report.unresolved_imports)
        self.assertEqual(
            {("test_ctrl.py", "lab_utils/gui_utils.py"), ("test_ctrl.py", "lab_utils/hostname.py")},
            _edges(report),
        )


class PythonSubmoduleImportTests(unittest.TestCase):
    def test_from_package_import_links_the_package_and_its_submodules(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            report = _report(
                root,
                {
                    "pkg/__init__.py": "helper = 1\n",
                    "pkg/sub.py": "VALUE = 1\n",
                    "pkg/nested/__init__.py": "",
                    "app.py": "from pkg import sub, nested, helper\n",
                },
            )
        self.assertEqual(
            {("app.py", "pkg/__init__.py"), ("app.py", "pkg/sub.py"), ("app.py", "pkg/nested/__init__.py")},
            _edges(report),
        )
        self.assertEqual((), report.external_imports)

    def test_relative_from_import_links_submodules(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            report = _report(
                root,
                {"pkg/__init__.py": "", "pkg/a.py": "from .inner import mod\n", "pkg/inner/mod.py": ""},
            )
        self.assertEqual({("pkg/a.py", "pkg/inner/mod.py")}, _edges(report))

    def test_names_from_a_module_file_or_outside_the_project_add_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            report = _report(
                root,
                {
                    "utils.py": "def helper():\n    pass\n",
                    "app.py": "from utils import helper\nfrom os import path\nfrom pkg import *\n",
                    "pkg/__init__.py": "",
                    "pkg/star.py": "",
                },
            )
        self.assertEqual({("app.py", "utils.py"), ("app.py", "pkg/__init__.py")}, _edges(report))
        self.assertEqual(["os"], [item.raw for item in report.external_imports])

    def test_stdlib_import_named_like_a_skipped_dir_stays_external(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            report = _report(root, {"setup_env.py": "import venv\n", "venv/lib/site.py": "x = 1\n"})
        self.assertEqual((), report.unresolved_imports)
        self.assertEqual(["venv"], [item.raw for item in report.external_imports])

    def test_context_json_records_reasons_and_counts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            report = _report(
                root,
                {
                    "src/app.js": "import a from '../dist/lib';\nimport b from './nope';\n",
                    "dist/lib.js": "export default 1;\n",
                },
            )
            payload = report_payload(report)
        self.assertEqual(2, payload["summary"]["unresolved_imports"])
        reasons = {(item["raw"], item["reason"], item["hidden_by"]) for item in payload["unresolved_imports"]}
        self.assertEqual({("../dist/lib", "default-ignore", "dist/"), ("./nope", "missing", None)}, reasons)
        self.assertEqual(json.loads(json.dumps(payload))["unresolved_imports"], payload["unresolved_imports"])


class ResolverFixTests(unittest.TestCase):
    def test_renamed_rust_crate_root_declares_modules_beside_itself(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            report = _report(
                root, {"cli/src/tree_cli.rs": "mod fuzz;\n", "cli/src/fuzz.rs": "pub fn go() {}\n"}
            )
        self.assertIn(("cli/src/tree_cli.rs", "cli/src/fuzz.rs"), _edges(report))
        self.assertEqual((), report.unresolved_imports)

    def test_rust_child_module_directory_still_wins_over_a_sibling(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            report = _report(
                root,
                {
                    "src/lib.rs": "mod foo;\n",
                    "src/foo.rs": "mod bar;\n",
                    "src/foo/bar.rs": "",
                    "src/bar.rs": "",
                },
            )
        self.assertIn(("src/foo.rs", "src/foo/bar.rs"), _edges(report))
        self.assertNotIn(("src/foo.rs", "src/bar.rs"), _edges(report))

    def test_dotted_typescript_module_name_resolves(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            report = _report(
                root,
                {
                    "main.ts": "import { App } from './app.component';\n",
                    "app.component.ts": "export class App {}\n",
                },
            )
        self.assertIn(("main.ts", "app.component.ts"), _edges(report))

    def test_typescript_declaration_file_resolves(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            report = _report(
                root,
                {
                    "src/x.ts": "import { api } from '../lib/api';\n",
                    "lib/api.d.ts": "export declare const api: number;\n",
                },
            )
        self.assertIn(("src/x.ts", "lib/api.d.ts"), _edges(report))

    def test_esm_js_specifier_resolves_to_a_declaration_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            report = _report(
                root,
                {
                    "src/queues.ts": "import { Job } from './job.js';\n",
                    "src/job.d.ts": "export type Job = {};\n",
                },
            )
        self.assertIn(("src/queues.ts", "src/job.d.ts"), _edges(report))
        self.assertEqual((), report.unresolved_imports)


class TestFileClassificationTests(unittest.TestCase):
    def test_test_directories(self) -> None:
        for path in (
            "tests/x.py",
            "src/__tests__/a.ts",
            "app/jvmTest/Foo.kt",
            "Foo.Tests/Bar.cs",
            "testdata/x.go",
            "rtl/testbench/tb.v",
        ):
            with self.subTest(path):
                self.assertTrue(is_test_file(path))
        for path in ("contest/x.py", "latest/a.ts", "src/testing.py", "Test.py"):
            with self.subTest(path):
                self.assertFalse(is_test_file(path))

    def test_test_filenames_with_their_own_evidence(self) -> None:
        for path in (
            "pkg/a_test.go",
            "src/a.test.ts",
            "src/a.spec.tsx",
            "FooTest.java",
            "lib/foo_spec.rb",
            "rtl/tb_top.v",
            "rtl/top_tb.sv",
        ):
            with self.subTest(path):
                self.assertTrue(is_test_file(path))
        self.assertFalse(is_test_file("Contest.java"))

    def test_python_test_names_need_a_framework_import(self) -> None:
        unittest_ref = ImportRef(raw="unittest", kind="import", line=1)
        pyside_ref = ImportRef(raw="PySide6.QtWidgets", kind="from", line=1)
        self.assertFalse(is_test_file("11_201_test.py", (pyside_ref,)))
        self.assertFalse(is_test_file("test_bench.py"))
        self.assertTrue(is_test_file("test_core.py", (unittest_ref,)))
        self.assertTrue(is_test_file("conftest.py"))

    def test_c_test_names_need_a_framework_header(self) -> None:
        self.assertFalse(is_test_file("fw/flash_test.c", (ImportRef(raw="flash.h", kind="local", line=1),)))
        self.assertTrue(
            is_test_file("src/parser_test.cc", (ImportRef(raw="gtest/gtest.h", kind="system", line=1),))
        )

    def test_report_flags_tests_and_counts_their_imports(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            report = _report(
                root,
                {
                    "core.py": "VALUE = 1\n",
                    "app.py": "import core\n",
                    "tests/test_a.py": "import core\n",
                    "tests/test_b.py": "import core\n",
                },
            )
        modules = {module.path: module for module in report.modules}
        self.assertTrue(modules["tests/test_a.py"].is_test)
        self.assertFalse(modules["app.py"].is_test)
        self.assertEqual(
            (3, 2, 1),
            (modules["core.py"].fan_in, modules["core.py"].test_fan_in, modules["core.py"].non_test_fan_in),
        )


class GeneratedFileTests(unittest.TestCase):
    def test_comment_banners_are_detected(self) -> None:
        banners = (
            "// Code generated by protoc-gen-go. DO NOT EDIT.\npackage pb\n",
            "/**\n * @generated by relay-compiler\n */\nexport {};\n",
            "// <auto-generated />\nnamespace X {}\n",
            "# This file was automatically generated by SWIG. Do not edit.\n",
        )
        for content in banners:
            with self.subTest(content.splitlines()[0]):
                self.assertTrue(has_generated_header(content))

    def test_banner_outside_a_comment_or_the_header_window_is_ignored(self) -> None:
        self.assertFalse(has_generated_header('BANNER = "// Code generated by tool. DO NOT EDIT."\n'))
        late = "x = 1\n" * 70 + "# Code generated by tool. DO NOT EDIT.\n"
        self.assertFalse(has_generated_header(late))

    def test_block_comments_are_tracked_per_language(self) -> None:
        """A JS test embedding Python source must not read its docstring as a banner."""
        embedded = (
            'const expected = `\ndef y():\n    """\n    Function \'y\' automatically generated by App\n'
        )
        self.assertFalse(has_generated_header(embedded, "tests/codegen.spec.mjs"))
        docstring = '"""\nThis module was automatically generated by makeclasses.py\n"""\nimport os\n'
        self.assertTrue(has_generated_header(docstring, "pkg/classes.py"))

    def test_scaffold_banners_are_not_generated(self) -> None:
        """Gradle `init`, `rspec --init` and `rspec:install` write templates the project then owns."""
        gradle = "/*\n * This file was generated by the Gradle 'init' task.\n */\nrootProject.name = 'x'\n"
        rspec = "# This file was generated by the `rspec --init` command.\nRSpec.configure {}\n"
        rails = (
            "# This file was generated by the `rails generate rspec:install` command. Conventionally, all\n"
        )
        self.assertFalse(has_generated_header(gradle, "settings.gradle.kts"))
        self.assertFalse(has_generated_header(rspec, "spec/spec_helper.rb"))
        self.assertFalse(has_generated_header(rails, "spec/spec_helper.rb"))
        self.assertTrue(has_generated_header("# This file was generated by gen.py\n", "x.py"))

    def test_codegen_filenames_and_minified_scripts(self) -> None:
        self.assertTrue(has_generated_path("proto/user_pb2.py"))
        self.assertTrue(has_generated_path("api/user.pb.go"))
        self.assertFalse(has_generated_path("src/user.py"))
        bundle = "!function(){" + "a();b(c,d);{e}" * 400 + "}();\n"
        self.assertTrue(is_minified_script("site/bundle.js", bundle))
        self.assertFalse(is_minified_script("site/app.js", "const a = 1;\n" * 400))

    def test_detector_does_not_classify_its_own_source(self) -> None:
        source = Path(__file__).resolve().parents[1] / "src" / "codemapy" / "generated.py"
        self.assertFalse(is_generated_source("src/codemapy/generated.py", source.read_text(encoding="utf-8")))

    def test_generated_files_keep_their_edges_but_leave_rankings(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            report = _report(
                root,
                {
                    "api_pb2.py": "VALUE = 1\n",
                    "client.py": "import api_pb2\n",
                    "server.py": "import api_pb2\n",
                    "tools/main.py": "# Code generated by gen.py. DO NOT EDIT.\nprint('x')\n",
                },
            )
            text = summary_markdown(report)
        modules = {module.path: module for module in report.modules}
        self.assertTrue(modules["api_pb2.py"].is_generated)
        self.assertIn(("client.py", "api_pb2.py"), _edges(report))
        self.assertNotIn("api_pb2.py", [module.path for module in production_hubs(report.modules)])
        self.assertIn("`api_pb2.py`", _section(text, "Generated Files"))
        self.assertNotIn("tools/main.py", _section(text, "Entry Points"))


class HubRankingTests(unittest.TestCase):
    FILES = {
        "core.py": "VALUE = 1\n",
        "util.py": "VALUE = 2\n",
        "app.py": "import core\nimport util\n",
        "cli.py": "import util\n",
        "tests/test_a.py": "import core\n",
        "tests/test_b.py": "import core\n",
        "tests/test_c.py": "import core\n",
    }

    def test_hubs_rank_by_non_test_importers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            report = _report(root, self.FILES)
            hubs = _section(summary_markdown(report), "Top Hubs")
        self.assertLess(hubs.index("`util.py`"), hubs.index("`core.py`"))
        self.assertIn("- `core.py`: fan-in 4 (3 from tests)", hubs)
        self.assertNotIn("tests/", hubs)

    def test_hubs_json_top_lists_leave_out_tests(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            payload = hubs_payload(_report(root, self.FILES))
        self.assertEqual(["util.py", "core.py"], [item["path"] for item in payload["top_fan_in"]])
        self.assertTrue(all(not item["is_test"] for item in payload["top_fan_out"]))
        self.assertIn("tests/test_a.py", [item["path"] for item in payload["hubs"]])

    def test_edges_only_between_tests_are_not_called_absent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            report = _report(
                root, {"tests/helper.py": "VALUE = 1\n", "tests/test_a.py": "from . import helper\n"}
            )
            hubs = _section(summary_markdown(report), "Top Hubs")
        self.assertIn("None: the only internal dependency edge is between test or generated files", hubs)


class BarrelEntryPointTests(unittest.TestCase):
    def test_only_package_level_index_files_are_entry_points(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            report = _report(
                root,
                {
                    "index.ts": "export {};\n",
                    "src/index.ts": "export {};\n",
                    "lib/index.js": "module.exports = {};\n",
                    "src/context/index.ts": "export {};\n",
                },
            )
            section = _section(summary_markdown(report), "Entry Points")
        for path in ("`index.ts`", "`src/index.ts`", "`lib/index.js`"):
            self.assertIn(path, section)
        self.assertNotIn("src/context/index.ts", section)

    def test_index_files_of_nested_packages_are_entry_points(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            report = _report(
                root,
                {
                    "plugin/package.json": '{"name": "plugin"}\n',
                    "plugin/src/index.ts": "export {};\n",
                    "web/index.ts": "export {};\n",
                    "tools/src/index.ts": "export {};\n",
                },
            )
            section = _section(summary_markdown(report), "Entry Points")
        self.assertIn("`plugin/src/index.ts`", section)
        self.assertNotIn("web/index.ts", section)
        self.assertNotIn("tools/src/index.ts", section)

    def test_main_files_under_test_directories_are_not_entry_points(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            report = _report(
                root, {"main.py": "print('app')\n", "tests/fixtures/main.py": "print('fixture')\n"}
            )
            section = _section(summary_markdown(report), "Entry Points")
        self.assertIn("`main.py`", section)
        self.assertNotIn("tests/fixtures/main.py", section)


class ArtifactVersionTests(unittest.TestCase):
    def test_written_context_carries_new_module_flags(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            report = _report(
                root, {"app.py": "import core\n", "core.py": "", "tests/test_app.py": "import app\n"}
            )
            paths = write_artifacts(report)
            context = json.loads(paths.context.read_text(encoding="utf-8"))
        self.assertEqual(3, context["version"])
        modules = {module["path"]: module for module in context["modules"]}
        self.assertEqual(
            {"is_test": True, "is_generated": False, "test_fan_in": 0},
            {key: modules["tests/test_app.py"][key] for key in ("is_test", "is_generated", "test_fan_in")},
        )
        self.assertEqual(1, modules["app.py"]["test_fan_in"])
        self.assertEqual(1, context["summary"]["test_files"])


if __name__ == "__main__":
    unittest.main()
