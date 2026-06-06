from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from codemapy.artifacts import report_payload, summary_markdown, symbols_payload, write_artifacts
from codemapy.config import Config
from codemapy.deps.registry import extractor_for_ext
from codemapy.graph import build_report, find_cycles
from codemapy.models import Edge, Report
from codemapy.scanner import scan_project
from codemapy.ts import backend


def _report(tmp: str, files: dict[str, str]):
    root = Path(tmp)
    for rel, content in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return root, build_report(root, scan_project(root, Config()))


class CycleTests(unittest.TestCase):
    def test_find_cycles_detects_scc_and_self_loops(self) -> None:
        edges = [
            Edge("a.py", "b.py", "b", "import"),
            Edge("b.py", "c.py", "c", "import"),
            Edge("c.py", "a.py", "a", "import"),
            Edge("x.py", "x.py", "x", "import"),
            Edge("d.py", "e.py", "e", "import"),  # acyclic, must not appear
        ]
        cycles = find_cycles(edges)
        self.assertIn(("a.py", "b.py", "c.py"), cycles)
        self.assertIn(("x.py",), cycles)
        self.assertTrue(all("d.py" not in cycle for cycle in cycles))

    def test_python_import_cycle_surfaces_in_report(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _, report = _report(
                tmp,
                {
                    "pkg/__init__.py": "",
                    "pkg/a.py": "import pkg.b\n",
                    "pkg/b.py": "import pkg.a\n",
                },
            )
        self.assertTrue(any(set(cycle) == {"pkg/a.py", "pkg/b.py"} for cycle in report.cycles))


    def test_mod_declaration_edges_excluded_from_cycles(self) -> None:
        # lib.rs declares `mod child;` and child does `use super::*;`. That pairing
        # must NOT be reported as a circular dependency.
        edges = [
            Edge("lib.rs", "child.rs", "child", "rust-mod"),
            Edge("child.rs", "lib.rs", "super::*", "rust-use"),
        ]
        self.assertEqual(find_cycles(edges), ())

    def test_c_system_include_does_not_self_resolve(self) -> None:
        # A header that does `#include <endian.h>` must not resolve to itself
        # (which previously produced a spurious self-import cycle).
        with tempfile.TemporaryDirectory() as tmp:
            _, report = _report(tmp, {"portable/endian.h": "#include <endian.h>\n"})
        self_edges = [e for e in report.edges if e.source == e.target]
        self.assertEqual(self_edges, [])
        self.assertEqual(report.cycles, ())

    def test_real_use_cycle_still_detected(self) -> None:
        edges = [
            Edge("a.rs", "b.rs", "crate::b", "rust-use"),
            Edge("b.rs", "a.rs", "crate::a", "rust-use"),
        ]
        self.assertIn(("a.rs", "b.rs"), find_cycles(edges))


class FanCountTests(unittest.TestCase):
    def test_duplicate_imports_count_target_once(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _, report = _report(
                tmp,
                {
                    "pkg/__init__.py": "",
                    "pkg/b.py": "VALUE = 1\n",
                    # Imports the same target on two lines.
                    "pkg/a.py": "from pkg import b\nfrom pkg.b import VALUE\n",
                },
            )
        b = next(m for m in report.modules if m.path == "pkg/b.py")
        self.assertEqual(b.fan_in, 1)


class PythonSymbolTests(unittest.TestCase):
    def test_extracts_signatures_docstrings_and_nesting(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _, report = _report(
                tmp,
                {
                    "m.py": (
                        "def top(a: int, b: str = 'x') -> bool:\n"
                        "    '''Top doc.'''\n"
                        "    return True\n"
                        "\n"
                        "class C:\n"
                        "    '''Class doc.'''\n"
                        "    def method(self, n: int) -> int:\n"
                        "        return n\n"
                    ),
                },
            )
        module = next(m for m in report.modules if m.path == "m.py")
        by_name = {s.name: s for s in module.symbols}
        self.assertEqual(by_name["top"].kind, "function")
        self.assertEqual(by_name["top"].signature, "def top(a: int, b: str='x') -> bool")
        self.assertEqual(by_name["top"].doc, "Top doc.")
        cls = by_name["C"]
        self.assertEqual(cls.kind, "class")
        self.assertEqual(cls.doc, "Class doc.")
        self.assertEqual([c.name for c in cls.children], ["method"])
        self.assertEqual(cls.children[0].kind, "method")

    def test_symbols_payload_builds_name_index(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _, report = _report(tmp, {"m.py": "def alpha():\n    pass\n"})
        payload = symbols_payload(report)
        self.assertIn("alpha", payload["index"])
        entry = payload["index"]["alpha"][0]
        self.assertEqual(entry["path"], "m.py")
        self.assertEqual(entry["kind"], "function")

    def test_symbols_json_artifact_written(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, report = _report(tmp, {"m.py": "def alpha():\n    pass\n"})
            paths = write_artifacts(report, Path(tmp) / "out")
            data = json.loads(paths.symbols.read_text(encoding="utf-8"))
            self.assertGreaterEqual(data["total_symbols"], 1)
            self.assertIn("m.py", data["files"])


class ArtifactShapeTests(unittest.TestCase):
    def test_context_json_uses_symbol_count_not_full_symbols(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _, report = _report(tmp, {"m.py": "def alpha():\n    pass\nclass C:\n    def m(self): pass\n"})
        payload = report_payload(report)
        module = next(m for m in payload["modules"] if m["path"] == "m.py")
        self.assertNotIn("symbols", module)
        self.assertEqual(module["symbol_count"], 3)  # alpha, C, C.m

    def test_large_cycle_is_summarised(self) -> None:
        big = tuple(f"pkg/sub/mod_{i}.py" for i in range(30))
        report = Report(
            root=Path("/tmp/x"),
            files=(),
            modules=(),
            edges=(),
            cycles=(big,),
        )
        text = summary_markdown(report, "2026-06-06")
        self.assertIn("30 files across", text)
        self.assertIn("large dependency cycle", text)
        # Must not dump all 30 members inline.
        self.assertNotIn("mod_29.py", text)


@unittest.skipUnless(backend.AVAILABLE, "tree-sitter backend not installed")
class TreeSitterImportTests(unittest.TestCase):
    def test_javascript_multiline_and_dynamic_imports(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "a.js"
            path.write_text(
                'import {\n  one,\n  two,\n} from "./b";\n'
                'const c = require("./c");\n'
                'const d = import("./d.js");\n',
                encoding="utf-8",
            )
            refs = extractor_for_ext(".js").extract(path)
        raws = {ref.raw for ref in refs}
        self.assertEqual(raws, {"./b", "./c", "./d.js"})

    def test_c_distinguishes_system_and_local_includes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "a.c"
            path.write_text('#include <stdio.h>\n#include "local.h"\n', encoding="utf-8")
            refs = extractor_for_ext(".c").extract(path)
        by_raw = {ref.raw: ref.kind for ref in refs}
        self.assertEqual(by_raw["stdio.h"], "system-include")
        self.assertEqual(by_raw["local.h"], "include")

    def test_go_symbols_extracted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _, report = _report(
                tmp,
                {"main.go": "package main\nfunc Foo() {}\ntype Bar struct{}\nfunc (b Bar) M() {}\n"},
            )
        module = next(m for m in report.modules if m.path == "main.go")
        kinds = {(s.name, s.kind) for s in module.symbols}
        self.assertIn(("Foo", "function"), kinds)
        self.assertIn(("Bar", "type"), kinds)
        self.assertIn(("M", "method"), kinds)

    def test_c_symbols_have_names(self) -> None:
        # The language pack's process() returns name=None for C; our queries don't.
        with tempfile.TemporaryDirectory() as tmp:
            _, report = _report(
                tmp,
                {"main.c": "int add(int a){ return a; }\nstruct Pt { int x; };\n"},
            )
        module = next(m for m in report.modules if m.path == "main.c")
        kinds = {(s.name, s.kind) for s in module.symbols}
        self.assertIn(("add", "function"), kinds)
        self.assertIn(("Pt", "struct"), kinds)

    def test_ruby_symbols_include_methods_and_modules(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _, report = _report(
                tmp,
                {"lib.rb": "class Widget\n  def render; end\nend\nmodule M; end\ndef top; end\n"},
            )
        module = next(m for m in report.modules if m.path == "lib.rb")
        kinds = {(s.name, s.kind) for s in module.symbols}
        self.assertIn(("Widget", "class"), kinds)
        self.assertIn(("render", "method"), kinds)
        self.assertIn(("M", "module"), kinds)
        self.assertIn(("top", "method"), kinds)


if __name__ == "__main__":
    unittest.main()
