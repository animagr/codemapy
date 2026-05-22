from __future__ import annotations

from codemapy.deps.base import DependencyExtractor
from codemapy.deps.javascript import JavaScriptExtractor
from codemapy.deps.python import PythonExtractor


_python = PythonExtractor()
_javascript = JavaScriptExtractor()

EXTRACTORS_BY_EXT: dict[str, DependencyExtractor] = {
    ".py": _python,
    ".js": _javascript,
    ".jsx": _javascript,
    ".mjs": _javascript,
    ".cjs": _javascript,
    ".ts": _javascript,
    ".tsx": _javascript,
}


def extractor_for_ext(ext: str) -> DependencyExtractor | None:
    return EXTRACTORS_BY_EXT.get(ext.lower())
