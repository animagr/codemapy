from __future__ import annotations

from codemapy.deps.base import DependencyExtractor
from codemapy.deps.c import CExtractor
from codemapy.deps.csharp import CSharpExtractor
from codemapy.deps.gdscript import GDScriptExtractor
from codemapy.deps.javascript import JavaScriptExtractor
from codemapy.deps.lua import LuaExtractor
from codemapy.deps.python import PythonExtractor
from codemapy.deps.rust import RustExtractor
from codemapy.deps.treesitter import TreeSitterCExtractor, TreeSitterJsExtractor
from codemapy.deps.verilog import VerilogExtractor


_c_regex = CExtractor()
_javascript_regex = JavaScriptExtractor()

_c = TreeSitterCExtractor(fallback=_c_regex)
_javascript = TreeSitterJsExtractor(fallback=_javascript_regex)
_python = PythonExtractor()
_csharp = CSharpExtractor()
_gdscript = GDScriptExtractor()
_lua = LuaExtractor()
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
    ".cs": _csharp,
    ".js": _javascript,
    ".jsx": _javascript,
    ".mjs": _javascript,
    ".cjs": _javascript,
    ".ts": _javascript,
    ".tsx": _javascript,
    ".svelte": _javascript_regex,
    ".gd": _gdscript,
    ".lua": _lua,
    ".rs": _rust,
    ".v": _verilog,
    ".vh": _verilog,
    ".sv": _verilog,
    ".svh": _verilog,
}


def extractor_for_ext(ext: str) -> DependencyExtractor | None:
    return EXTRACTORS_BY_EXT.get(ext.lower())
