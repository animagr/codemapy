from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
import warnings
from pathlib import Path

from codemapy.artifacts import artifact_dir_for, write_artifacts
from codemapy.config import Config
from codemapy.gui import _summary_text
from codemapy.graph import build_report
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

    def test_python_extractor_suppresses_invalid_escape_warnings(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "mathy.py").write_text(
                '"""Return :math:`x \\sqrt{2}`"""\nimport os\n',
                encoding="utf-8",
            )

            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always", SyntaxWarning)
                report = build_report(root, scan_project(root, Config()))

        syntax_warnings = [warning for warning in caught if issubclass(warning.category, SyntaxWarning)]
        self.assertEqual([], syntax_warnings)
        self.assertEqual(1, len(report.external_imports))

    def test_builds_verilog_module_edges(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "top.v").write_text(
                "module top();\n"
                "  spi_slave s_s(.clk(clk));\n"
                "endmodule\n",
                encoding="utf-8",
            )
            (root / "spi_slave.v").write_text(
                "module spi_slave(input clk);\n"
                "endmodule\n",
                encoding="utf-8",
            )

            report = build_report(root, scan_project(root, Config()))

        self.assertIn(
            ("top.v", "spi_slave.v"),
            {(edge.source, edge.target) for edge in report.edges},
        )

    def test_builds_verilog_parameterized_module_edges(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "top.v").write_text(
                "module top();\n"
                "  fifo #(.WIDTH(8)) fifo0(.clk(clk));\n"
                "endmodule\n",
                encoding="utf-8",
            )
            (root / "fifo.v").write_text("module fifo(input clk);\nendmodule\n", encoding="utf-8")

            report = build_report(root, scan_project(root, Config()))

        self.assertIn(("top.v", "fifo.v"), {(edge.source, edge.target) for edge in report.edges})

    def test_resolves_verilog_includes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "defs.vh").write_text("`define WIDTH 8\n", encoding="utf-8")
            (root / "top.v").write_text(
                "`include \"defs.vh\"\n"
                "module top();\n"
                "endmodule\n",
                encoding="utf-8",
            )

            report = build_report(root, scan_project(root, Config()))

        self.assertIn(("top.v", "defs.vh"), {(edge.source, edge.target) for edge in report.edges})

    def test_resolves_javascript_parent_directory_imports(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cells = root / "src" / "cells"
            parser = root / "src" / "parser"
            cells.mkdir(parents=True)
            parser.mkdir(parents=True)
            (cells / "DataTableCell.svelte.ts").write_text(
                "import type { Unit } from '../parser/types';\n",
                encoding="utf-8",
            )
            (parser / "types.ts").write_text("export type Unit = string;\n", encoding="utf-8")

            report = build_report(root, scan_project(root, Config()))

        self.assertIn(
            ("src/cells/DataTableCell.svelte.ts", "src/parser/types.ts"),
            {(edge.source, edge.target) for edge in report.edges},
        )

    def test_resolves_svelte_component_imports(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = root / "src"
            src.mkdir()
            (src / "App.svelte").write_text(
                "<script>\n"
                "import MathField from './MathField.svelte';\n"
                "</script>\n",
                encoding="utf-8",
            )
            (src / "MathField.svelte").write_text("<script>export let value = '';</script>\n", encoding="utf-8")

            report = build_report(root, scan_project(root, Config()))

        self.assertIn(
            ("src/App.svelte", "src/MathField.svelte"),
            {(edge.source, edge.target) for edge in report.edges},
        )

    def test_resolves_svelte_sidecar_script_imports(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cells = root / "src" / "cells"
            cells.mkdir(parents=True)
            (cells / "MathCell.svelte.ts").write_text(
                "import MathField from './MathField.svelte';\n",
                encoding="utf-8",
            )
            (cells / "MathField.svelte.ts").write_text("export const tag = 'math';\n", encoding="utf-8")

            report = build_report(root, scan_project(root, Config()))

        self.assertIn(
            ("src/cells/MathCell.svelte.ts", "src/cells/MathField.svelte.ts"),
            {(edge.source, edge.target) for edge in report.edges},
        )

    def test_resolves_javascript_current_directory_imports(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "main.ts").write_text("import { helper } from '.';\n", encoding="utf-8")
            (root / "index.ts").write_text("export const helper = 1;\n", encoding="utf-8")

            report = build_report(root, scan_project(root, Config()))

        self.assertIn(("main.ts", "index.ts"), {(edge.source, edge.target) for edge in report.edges})

    def test_scans_gdscript_and_resolves_godot_resource_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            game = root / "game"
            src = game / "src"
            src.mkdir(parents=True)
            (game / "project.godot").write_text("config_version=5\n", encoding="utf-8")
            (src / "actor.gd").write_text(
                "extends Node\n"
                "const Job = preload(\"res://src/job.gd\")\n",
                encoding="utf-8",
            )
            (src / "job.gd").write_text("class_name Job\nextends RefCounted\n", encoding="utf-8")

            files = scan_project(root, Config())
            report = build_report(root, files)

        languages = {file.path: file.language for file in files}
        self.assertEqual("GDScript", languages["game/src/actor.gd"])
        self.assertIn(
            ("game/src/actor.gd", "game/src/job.gd"),
            {(edge.source, edge.target) for edge in report.edges},
        )

    def test_resolves_gdscript_class_name_extends(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "base.gd").write_text("class_name BaseJob\nextends RefCounted\n", encoding="utf-8")
            (root / "child.gd").write_text("extends BaseJob\n", encoding="utf-8")

            report = build_report(root, scan_project(root, Config()))

        self.assertIn(("child.gd", "base.gd"), {(edge.source, edge.target) for edge in report.edges})

    def test_resolves_lua_require_imports(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pkg = root / "pkg"
            pkg.mkdir()
            (root / "main.lua").write_text('local helper = require("pkg.helper")\n', encoding="utf-8")
            (pkg / "helper.lua").write_text("return {}\n", encoding="utf-8")

            report = build_report(root, scan_project(root, Config()))

        self.assertIn(("main.lua", "pkg/helper.lua"), {(edge.source, edge.target) for edge in report.edges})

    def test_resolves_luanti_modpath_dofile_imports(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            mod = root / "mods" / "dmobs"
            mobs = mod / "mobs"
            mobs.mkdir(parents=True)
            (mod / "mod.conf").write_text("name = dmobs\n", encoding="utf-8")
            (mod / "init.lua").write_text(
                'local dpath = core.get_modpath("dmobs") .. "/"\n'
                'dofile(dpath .. "mobs/pig.lua")\n',
                encoding="utf-8",
            )
            (mobs / "pig.lua").write_text("return {}\n", encoding="utf-8")

            report = build_report(root, scan_project(root, Config()))

        self.assertIn(
            ("mods/dmobs/init.lua", "mods/dmobs/mobs/pig.lua"),
            {(edge.source, edge.target) for edge in report.edges},
        )

    def test_resolves_luanti_current_modname_dofile_imports(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "mod.conf").write_text("name = mobs_fish\n", encoding="utf-8")
            (root / "init.lua").write_text(
                'local MP = core.get_modpath(core.get_current_modname()) .. "/"\n'
                'dofile(MP .. "spawn.lua")\n',
                encoding="utf-8",
            )
            (root / "spawn.lua").write_text("return {}\n", encoding="utf-8")

            report = build_report(root, scan_project(root, Config()))

        self.assertIn(("init.lua", "spawn.lua"), {(edge.source, edge.target) for edge in report.edges})

    def test_lua_directory_file_import_does_not_crash(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "main.lua").write_text('dofile(".")\n', encoding="utf-8")
            (root / "init.lua").write_text("return {}\n", encoding="utf-8")

            report = build_report(root, scan_project(root, Config()))

        self.assertIn(("main.lua", "init.lua"), {(edge.source, edge.target) for edge in report.edges})

    def test_resolves_typescript_source_from_javascript_import_suffix(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = root / "src"
            src.mkdir()
            (src / "LatexParser.ts").write_text(
                "import Visitor from './LatexParserVisitor.js';\n",
                encoding="utf-8",
            )
            (src / "LatexParserVisitor.ts").write_text("export default class Visitor {}\n", encoding="utf-8")

            report = build_report(root, scan_project(root, Config()))

        self.assertIn(
            ("src/LatexParser.ts", "src/LatexParserVisitor.ts"),
            {(edge.source, edge.target) for edge in report.edges},
        )

    def test_resolves_c_same_directory_include(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "main.c").write_text('#include "main.h"\nint main(void) { return 0; }\n', encoding="utf-8")
            (root / "main.h").write_text("#pragma once\n", encoding="utf-8")

            report = build_report(root, scan_project(root, Config()))

        self.assertIn(("main.c", "main.h"), {(edge.source, edge.target) for edge in report.edges})

    def test_resolves_c_include_directory_header(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = root / "src"
            include = root / "include"
            src.mkdir()
            include.mkdir()
            (src / "adc_basic.c").write_text("#include <adc_basic.h>\n", encoding="utf-8")
            (include / "adc_basic.h").write_text("#pragma once\n", encoding="utf-8")

            report = build_report(root, scan_project(root, Config()))

        self.assertIn(
            ("src/adc_basic.c", "include/adc_basic.h"),
            {(edge.source, edge.target) for edge in report.edges},
        )

    def test_resolves_c_config_header(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            include = root / "include"
            config = root / "Config"
            include.mkdir()
            config.mkdir()
            (include / "driver_init.h").write_text("#include <clock_config.h>\n", encoding="utf-8")
            (config / "clock_config.h").write_text("#pragma once\n", encoding="utf-8")

            report = build_report(root, scan_project(root, Config()))

        self.assertIn(
            ("include/driver_init.h", "Config/clock_config.h"),
            {(edge.source, edge.target) for edge in report.edges},
        )

    def test_resolves_c_nested_utility_include(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            utils = root / "utils"
            assembler = utils / "assembler"
            assembler.mkdir(parents=True)
            (utils / "assembler.h").write_text('#include "assembler/gas.h"\n', encoding="utf-8")
            (assembler / "gas.h").write_text("#pragma once\n", encoding="utf-8")

            report = build_report(root, scan_project(root, Config()))

        self.assertIn(
            ("utils/assembler.h", "utils/assembler/gas.h"),
            {(edge.source, edge.target) for edge in report.edges},
        )

    def test_resolves_rust_mod_declarations(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = root / "src"
            src.mkdir()
            (src / "lib.rs").write_text("pub mod scanner;\n", encoding="utf-8")
            (src / "scanner.rs").write_text("pub fn scan() {}\n", encoding="utf-8")

            report = build_report(root, scan_project(root, Config()))

        self.assertIn(("src/lib.rs", "src/scanner.rs"), {(edge.source, edge.target) for edge in report.edges})

    def test_resolves_rust_nested_mod_declarations(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            scanner = root / "src" / "scanner"
            scanner.mkdir(parents=True)
            (root / "src" / "lib.rs").write_text("pub mod scanner;\n", encoding="utf-8")
            (scanner / "mod.rs").write_text("mod walk;\n", encoding="utf-8")
            (scanner / "walk.rs").write_text("pub fn walk() {}\n", encoding="utf-8")

            report = build_report(root, scan_project(root, Config()))

        self.assertIn(
            ("src/scanner/mod.rs", "src/scanner/walk.rs"),
            {(edge.source, edge.target) for edge in report.edges},
        )

    def test_resolves_rust_crate_use_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            editor = root / "src" / "editor"
            editor.mkdir(parents=True)
            (root / "src" / "lib.rs").write_text("pub mod editor;\n", encoding="utf-8")
            (editor / "mod.rs").write_text("pub mod cursor;\n", encoding="utf-8")
            (editor / "cursor.rs").write_text("pub struct Cursor;\n", encoding="utf-8")
            (root / "src" / "latex.rs").write_text(
                "use crate::editor::cursor::Cursor;\n",
                encoding="utf-8",
            )

            report = build_report(root, scan_project(root, Config()))

        self.assertIn(
            ("src/latex.rs", "src/editor/cursor.rs"),
            {(edge.source, edge.target) for edge in report.edges},
        )

    def test_resolves_rust_brace_use_once_per_module(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            treemap = root / "src" / "treemap"
            treemap.mkdir(parents=True)
            (root / "src" / "lib.rs").write_text("pub mod treemap;\npub mod gui;\n", encoding="utf-8")
            (treemap / "mod.rs").write_text("pub struct Rect;\npub struct Tile;\n", encoding="utf-8")
            (root / "src" / "gui.rs").write_text(
                "use crate::treemap::{Rect, Tile};\n",
                encoding="utf-8",
            )

            report = build_report(root, scan_project(root, Config()))

        edges = [(edge.source, edge.target) for edge in report.edges]
        self.assertEqual(1, edges.count(("src/gui.rs", "src/treemap/mod.rs")))

    def test_resolves_rust_super_use_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            editor = root / "src" / "editor"
            editor.mkdir(parents=True)
            (root / "src" / "lib.rs").write_text("pub mod editor;\n", encoding="utf-8")
            (editor / "mod.rs").write_text("pub mod cursor;\npub mod input;\n", encoding="utf-8")
            (editor / "cursor.rs").write_text("pub struct Cursor;\n", encoding="utf-8")
            (editor / "input.rs").write_text("use super::cursor::Cursor;\n", encoding="utf-8")

            report = build_report(root, scan_project(root, Config()))

        self.assertIn(
            ("src/editor/input.rs", "src/editor/cursor.rs"),
            {(edge.source, edge.target) for edge in report.edges},
        )

    def test_resolves_rust_workspace_crate_use_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            parser = root / "crates" / "ratex-parser" / "src"
            layout = root / "crates" / "ratex-layout" / "src"
            parser.mkdir(parents=True)
            layout.mkdir(parents=True)
            (parser / "lib.rs").write_text("pub mod parser;\n", encoding="utf-8")
            (parser / "parser.rs").write_text("pub fn parse() {}\n", encoding="utf-8")
            (layout / "lib.rs").write_text(
                "use ratex_parser::parser::parse;\n",
                encoding="utf-8",
            )

            report = build_report(root, scan_project(root, Config()))

        self.assertIn(
            ("crates/ratex-layout/src/lib.rs", "crates/ratex-parser/src/parser.rs"),
            {(edge.source, edge.target) for edge in report.edges},
        )

    def test_prefers_source_verilog_over_generated_primitive_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            generated = root / "impl1"
            generated.mkdir()
            (root / "top.v").write_text(
                "module top();\n"
                "  spi_slave s_s(.clk(clk));\n"
                "endmodule\n",
                encoding="utf-8",
            )
            (root / "spi_slave.v").write_text("module spi_slave();\nendmodule\n", encoding="utf-8")
            (generated / "spi_slave_prim.v").write_text(
                "module spi_slave();\nendmodule\n",
                encoding="utf-8",
            )

            report = build_report(root, scan_project(root, Config()))

        self.assertIn(("top.v", "spi_slave.v"), {(edge.source, edge.target) for edge in report.edges})
        self.assertNotIn(
            ("top.v", "impl1/spi_slave_prim.v"),
            {(edge.source, edge.target) for edge in report.edges},
        )

    def test_gui_summary_text_contains_report_counts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "main.py").write_text("import os\n", encoding="utf-8")
            report = build_report(root, scan_project(root, Config()))
            artifact_paths = write_artifacts(report)

            summary = _summary_text(report, artifact_paths)

        self.assertIn("Files: 1", summary)
        self.assertIn("External references: 1", summary)
        self.assertIn("context.json", summary)

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
        self.assertEqual(payload["summary"]["files"], 1)

    def test_writes_agent_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "pkg").mkdir()
            (root / "pkg" / "__init__.py").write_text("", encoding="utf-8")
            (root / "pkg" / "a.py").write_text("from . import b\n", encoding="utf-8")
            (root / "pkg" / "b.py").write_text("VALUE = 1\n", encoding="utf-8")
            (root / "Cargo.lock").write_text("# generated by Cargo\n", encoding="utf-8")
            report = build_report(root, scan_project(root, Config()))

            paths = write_artifacts(report)

            self.assertTrue(paths.report.exists())
            self.assertTrue(paths.context.exists())
            self.assertTrue(paths.summary.exists())
            self.assertTrue(paths.hubs.exists())
            self.assertTrue(paths.manifest.exists())
            context = json.loads(paths.context.read_text(encoding="utf-8"))
            self.assertEqual(context["summary"]["files"], 3)
            self.assertEqual(["Cargo.lock"], [item["path"] for item in context["metadata_files"]])
            self.assertIn(("pkg/a.py", "pkg/b.py"), {(edge["source"], edge["target"]) for edge in context["edges"]})
            self.assertIn("pkg/b.py", paths.summary.read_text(encoding="utf-8"))
            self.assertIn("Cargo.lock", paths.summary.read_text(encoding="utf-8"))

    def test_dependency_metadata_files_are_not_scanned_as_source(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "src").mkdir()
            (root / "src" / "lib.rs").write_text("pub fn lib() {}\n", encoding="utf-8")
            (root / "Cargo.lock").write_text("# cargo lock\n", encoding="utf-8")
            (root / "package-lock.json").write_text('{"lockfileVersion": 3}\n', encoding="utf-8")
            (root / "pyproject.toml").write_text("[project]\nname = 'demo'\n", encoding="utf-8")
            (root / "setup.py").write_text("from setuptools import setup\n", encoding="utf-8")
            (root / "vite.config.ts").write_text("export default {}\n", encoding="utf-8")
            (root / "CMakeLists.txt").write_text("project(demo)\n", encoding="utf-8")
            (root / "firmware.qsf").write_text("set_global_assignment\n", encoding="utf-8")
            (root / "demo.vcxproj").write_text("<Project />\n", encoding="utf-8")

            files = scan_project(root, Config())
            report = build_report(root, files)
            paths = write_artifacts(report)
            context = json.loads(paths.context.read_text(encoding="utf-8"))

        self.assertEqual(["src/lib.rs"], [file.path for file in files])
        self.assertEqual(
            [
                "CMakeLists.txt",
                "Cargo.lock",
                "demo.vcxproj",
                "firmware.qsf",
                "package-lock.json",
                "pyproject.toml",
                "setup.py",
                "vite.config.ts",
            ],
            sorted(item["path"] for item in context["metadata_files"]),
        )

    def test_generated_outputs_are_not_scanned_as_source_or_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = root / "src"
            src.mkdir()
            (src / "main.py").write_text("print('hello')\n", encoding="utf-8")
            (root / "module.pyc").write_text("compiled\n", encoding="utf-8")
            (root / "app.min.js").write_text("console.log('built')\n", encoding="utf-8")
            (root / "trace.vcd").write_text("$date\n", encoding="utf-8")
            (root / "top_prim.v").write_text("module top(); endmodule\n", encoding="utf-8")
            (root / "build").mkdir()
            (root / "build" / "generated.py").write_text("print('built')\n", encoding="utf-8")
            (root / "cmake-build-debug").mkdir()
            (root / "cmake-build-debug" / "main.c").write_text("int main(void) { return 0; }\n", encoding="utf-8")
            (root / "pkg.egg-info").mkdir()
            (root / "pkg.egg-info" / "SOURCES.txt").write_text("src/main.py\n", encoding="utf-8")
            for directory in (
                ".cargo",
                ".gradle",
                ".grammar-build",
                ".idea",
                ".vscode",
                "DerivedData",
                "Pods",
                "grammars",
                "vendor",
            ):
                ignored_dir = root / directory
                ignored_dir.mkdir()
                (ignored_dir / "ignored.py").write_text("print('ignored')\n", encoding="utf-8")
            (root / ".DS_Store").write_text("ignored\n", encoding="utf-8")
            (root / ".env").write_text("TOKEN=ignored\n", encoding="utf-8")

            files = scan_project(root, Config())
            report = build_report(root, files)
            paths = write_artifacts(report)
            context = json.loads(paths.context.read_text(encoding="utf-8"))

        self.assertEqual(["src/main.py"], [file.path for file in files])
        self.assertEqual([], context["metadata_files"])

    def test_scanner_ignores_codemapy_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "app.py").write_text("print('hello')\n", encoding="utf-8")
            artifact_dir = artifact_dir_for(root)
            artifact_dir.mkdir()
            (artifact_dir / "context.json").write_text("{}", encoding="utf-8")

            files = scan_project(root, Config())

        self.assertEqual([file.path for file in files], ["app.py"])

    def test_scanner_honors_root_gitignore(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".gitignore").write_text(
                "/bundled-tools/\n"
                ".claude/settings.local.json\n"
                "*.scratch\n"
                "!keep.scratch\n",
                encoding="utf-8",
            )
            (root / ".claude").mkdir()
            (root / ".claude" / "settings.local.json").write_text("{}", encoding="utf-8")
            (root / "bundled-tools").mkdir()
            (root / "bundled-tools" / "tool.py").write_text("print('ignored')\n", encoding="utf-8")
            (root / "app.py").write_text("print('hello')\n", encoding="utf-8")
            (root / "notes.scratch").write_text("ignored\n", encoding="utf-8")
            (root / "keep.scratch").write_text("visible\n", encoding="utf-8")

            files = scan_project(root, Config())

        self.assertEqual([".gitignore", "app.py", "keep.scratch"], [file.path for file in files])

    def test_scanner_honors_nested_gitignore(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = root / "ProjectA"
            project.mkdir()
            (project / ".gitignore").write_text("output/\n*.cache\n", encoding="utf-8")
            (project / "app.py").write_text("print('hello')\n", encoding="utf-8")
            (project / "data.cache").write_text("ignored\n", encoding="utf-8")
            (project / "output").mkdir()
            (project / "output" / "generated.py").write_text("print('ignored')\n", encoding="utf-8")
            (root / "ProjectB").mkdir()
            (root / "ProjectB" / "data.cache").write_text("visible\n", encoding="utf-8")

            files = scan_project(root, Config())

        self.assertEqual(
            ["ProjectA/.gitignore", "ProjectA/app.py", "ProjectB/data.cache"],
            [file.path for file in files],
        )

    def test_cli_declines_to_rewrite_existing_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "app.py").write_text("import os\n", encoding="utf-8")
            artifact_dir = artifact_dir_for(root)
            artifact_dir.mkdir()
            context_path = artifact_dir / "context.json"
            context_path.write_text('{"old": true}\n', encoding="utf-8")
            env = os.environ.copy()
            env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")

            result = subprocess.run(
                [sys.executable, "-m", "codemapy", str(root), "--artifacts"],
                input="n\n",
                check=True,
                capture_output=True,
                text=True,
                env=env,
            )

            self.assertIn("Artifacts unchanged", result.stdout)
            self.assertEqual(json.loads(context_path.read_text(encoding="utf-8")), {"old": True})

    def test_cli_confirms_rewrite_existing_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "app.py").write_text("import os\n", encoding="utf-8")
            artifact_dir = artifact_dir_for(root)
            artifact_dir.mkdir()
            context_path = artifact_dir / "context.json"
            context_path.write_text('{"old": true}\n', encoding="utf-8")
            env = os.environ.copy()
            env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")

            subprocess.run(
                [sys.executable, "-m", "codemapy", str(root), "--artifacts"],
                input="yes\n",
                check=True,
                capture_output=True,
                text=True,
                env=env,
            )

            payload = json.loads(context_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["summary"]["files"], 1)
            self.assertNotIn("old", payload)


if __name__ == "__main__":
    unittest.main()
