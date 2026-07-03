from __future__ import annotations

import json
import tempfile
import unittest
import unittest.mock
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


def _flat_kinds(symbols) -> set[tuple[str, str]]:
    """Collect (name, kind) pairs for symbols and all nested children."""
    pairs: set[tuple[str, str]] = set()

    def walk(items) -> None:
        for symbol in items:
            pairs.add((symbol.name, symbol.kind))
            walk(symbol.children)

    walk(symbols)
    return pairs


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


class CSharpDependencyTests(unittest.TestCase):
    def test_using_resolves_to_referenced_namespace_files_and_externals(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _, report = _report(
                tmp,
                {
                    "Core/Engine.cs": "namespace App.Core {\n  public class Engine { }\n}\n",
                    "Core/Loader.cs": "namespace App.Core {\n  public class Loader { }\n}\n",
                    "Ui/Window.cs": (
                        "using System;\n"
                        "using App.Core;\n"
                        "namespace App.Ui {\n"
                        "  public class Window {\n"
                        "    Engine engine;\n"
                        "    Loader loader;\n"
                        "  }\n"
                        "}\n"
                    ),
                },
            )
        edges = {(e.source, e.target) for e in report.edges}
        # `using App.Core;` links to the namespace files whose types are used.
        self.assertIn(("Ui/Window.cs", "Core/Engine.cs"), edges)
        self.assertIn(("Ui/Window.cs", "Core/Loader.cs"), edges)
        # `using System;` matches no internal namespace -> external reference.
        externals = {(e.source, e.raw) for e in report.external_imports}
        self.assertIn(("Ui/Window.cs", "System"), externals)

    def test_using_skips_namespace_files_whose_types_are_not_referenced(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _, report = _report(
                tmp,
                {
                    "Core/Engine.cs": "namespace App.Core {\n  public class Engine { }\n}\n",
                    "Core/Loader.cs": "namespace App.Core {\n  public class Loader { }\n}\n",
                    "Ui/Window.cs": (
                        "using App.Core;\n"
                        "namespace App.Ui {\n"
                        "  public class Window { Engine engine; }\n"
                        "}\n"
                    ),
                },
            )
        edges = {(e.source, e.target) for e in report.edges}
        self.assertIn(("Ui/Window.cs", "Core/Engine.cs"), edges)
        # Loader is never mentioned by Window.cs -> no blanket namespace edge.
        self.assertNotIn(("Ui/Window.cs", "Core/Loader.cs"), edges)

    def test_file_scoped_namespace_and_no_self_edge(self) -> None:
        # File-scoped namespace syntax, and a file importing its own namespace
        # must not produce a self-edge.
        with tempfile.TemporaryDirectory() as tmp:
            _, report = _report(
                tmp,
                {
                    "A.cs": "using App.Core;\nnamespace App.Core;\npublic class A { B b; }\n",
                    "B.cs": "namespace App.Core;\npublic class B { }\n",
                },
            )
        edges = {(e.source, e.target) for e in report.edges}
        self.assertNotIn(("A.cs", "A.cs"), edges)
        self.assertIn(("A.cs", "B.cs"), edges)

    def test_relative_fanout_cap_drops_project_wide_namespace(self) -> None:
        # 12 of 13 C# files share one flat namespace and the client references
        # every one of their types; the relative cap (a third of the C# file
        # count, at least 10) still drops the blanket edges even though the
        # absolute cap (100) is far away.
        files = {
            f"src/F{i}.cs": f"namespace Root {{\n  public class F{i} {{ }}\n}}\n" for i in range(12)
        }
        references = "".join(f"    F{i} f{i};\n" for i in range(12))
        files["client/Client.cs"] = (
            "using Root;\nnamespace Client {\n  public class Client {\n" + references + "  }\n}\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            _, report = _report(tmp, files)
        client_edges = [e for e in report.edges if e.source == "client/Client.cs"]
        self.assertEqual([], client_edges)
        externals = {(e.source, e.raw) for e in report.external_imports}
        self.assertIn(("client/Client.cs", "Root"), externals)

    def test_broad_namespace_fanout_is_capped(self) -> None:
        # A `using` whose namespace resolves to more referenced files than the
        # cap is dropped (and recorded as external) rather than fanning out.
        from codemapy.deps import resolver as resolver_mod

        files = {
            f"god/F{i}.cs": f"namespace Root {{\n  public class F{i} {{ }}\n}}\n" for i in range(3)
        }
        files["client/Client.cs"] = (
            "using Root;\nnamespace Client {\n"
            "  public class Client { F0 a; F1 b; F2 c; }\n"
            "}\n"
        )
        with unittest.mock.patch.object(resolver_mod, "CSHARP_NAMESPACE_FANOUT_CAP", 2):
            with tempfile.TemporaryDirectory() as tmp:
                _, report = _report(tmp, files)
        client_edges = [e for e in report.edges if e.source == "client/Client.cs"]
        self.assertEqual(client_edges, [])
        externals = {(e.source, e.raw) for e in report.external_imports}
        self.assertIn(("client/Client.cs", "Root"), externals)


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

    def test_csharp_symbols_include_types_and_members(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _, report = _report(
                tmp,
                {
                    "Widget.cs": (
                        "namespace DwarfCorp.Tools {\n"
                        "    public interface IThing { void Do(); }\n"
                        "    public class Widget : IThing {\n"
                        "        public Widget(int x) { }\n"
                        "        public void Do() { }\n"
                        "    }\n"
                        "    public struct Point { public int X; }\n"
                        "    public enum Color { Red, Green }\n"
                        "    public record Money(decimal Amount);\n"
                        "    public delegate void Handler(object s);\n"
                        "}\n"
                    ),
                },
            )
        module = next(m for m in report.modules if m.path == "Widget.cs")
        kinds = _flat_kinds(module.symbols)
        self.assertIn(("IThing", "interface"), kinds)
        self.assertIn(("Widget", "class"), kinds)
        self.assertIn(("Widget", "method"), kinds)  # constructor
        self.assertIn(("Do", "method"), kinds)
        self.assertIn(("Point", "struct"), kinds)
        self.assertIn(("Color", "enum"), kinds)
        self.assertIn(("Money", "record"), kinds)
        self.assertIn(("Handler", "delegate"), kinds)
        # Members are nested under their declaring class by span containment.
        widget = next(s for s in module.symbols if s.name == "Widget" and s.kind == "class")
        self.assertIn(("Do", "method"), {(c.name, c.kind) for c in widget.children})

    def test_ruby_symbols_include_methods_and_modules(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _, report = _report(
                tmp,
                {"lib.rb": "class Widget\n  def render; end\nend\nmodule M; end\ndef top; end\n"},
            )
        module = next(m for m in report.modules if m.path == "lib.rb")
        kinds = _flat_kinds(module.symbols)
        self.assertIn(("Widget", "class"), kinds)
        self.assertIn(("render", "method"), kinds)
        self.assertIn(("M", "module"), kinds)
        self.assertIn(("top", "method"), kinds)


if __name__ == "__main__":
    unittest.main()
