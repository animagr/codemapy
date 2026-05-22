from __future__ import annotations

from codemapy.deps.base import DependencyExtractor
from codemapy.deps.c import CExtractor
from codemapy.deps.javascript import JavaScriptExtractor
from codemapy.deps.python import PythonExtractor
from codemapy.deps.rust import RustExtractor
from codemapy.deps.verilog import VerilogExtractor


_c = CExtractor()
_python = PythonExtractor()
_javascript = JavaScriptExtractor()
_rust = RustExtractor()
_verilog = VerilogExtractor()

EXTRACTORS_BY_EXT: dict[str, DependencyExtractor] = {
    ".c": _c,
    ".h": _c,
    ".cc": _c,
    ".cpp": _c,
    ".cxx": _c,
    ".hpp": _c,
    ".py": _python,
    ".js": _javascript,
    ".jsx": _javascript,
    ".mjs": _javascript,
    ".cjs": _javascript,
    ".ts": _javascript,
    ".tsx": _javascript,
    ".svelte": _javascript,
    ".rs": _rust,
    ".v": _verilog,
    ".vh": _verilog,
    ".sv": _verilog,
    ".svh": _verilog,
}


def extractor_for_ext(ext: str) -> DependencyExtractor | None:
    return EXTRACTORS_BY_EXT.get(ext.lower())
