"""Tree-sitter backed import extractors.

These mirror the ``kind``/``raw`` conventions of the regex extractors they
replace so the existing :mod:`codemapy.deps.resolver` keeps working unchanged.
When the tree-sitter backend is unavailable (or a parse fails) each extractor
delegates to the regex ``fallback`` it was constructed with.
"""

from __future__ import annotations

from pathlib import Path

from codemapy.deps.base import DependencyExtractor
from codemapy.models import ImportRef
from codemapy.ts import backend
from codemapy.ts.langmap import pack_name_for_ext

# JavaScript / TypeScript: static import, re-export, require(...), import(...).
_JS_QUERY = """
(import_statement source: (string (string_fragment) @path))
(export_statement source: (string (string_fragment) @path))
((call_expression
    function: (identifier) @_fn
    arguments: (arguments . (string (string_fragment) @path)))
  (#eq? @_fn "require"))
(call_expression
  function: (import)
  arguments: (arguments . (string (string_fragment) @path)))
"""

# C / C++: #include <system> and #include "local".
_C_QUERY = """
(preproc_include path: (system_lib_string) @system)
(preproc_include path: (string_literal (string_content) @local))
"""


def _read_bytes(path: Path) -> bytes | None:
    try:
        return path.read_bytes()
    except OSError:
        return None


class TreeSitterJsExtractor(DependencyExtractor):
    """Extract JS/TS imports via tree-sitter, falling back to regex."""

    language = "JavaScript"

    def __init__(self, fallback: DependencyExtractor) -> None:
        self._fallback = fallback

    def extract(self, path: Path) -> tuple[ImportRef, ...]:
        pack_name = pack_name_for_ext(path.suffix)
        if not backend.AVAILABLE or pack_name is None:
            return self._fallback.extract(path)
        source = _read_bytes(path)
        if source is None:
            return ()
        captures = backend.query_captures(pack_name, source, _JS_QUERY)
        if not captures and source:
            # A parse that yields nothing on a non-empty file is suspicious;
            # let the regex extractor have a try before giving up.
            return self._fallback.extract(path)
        refs = [
            ImportRef(raw=node.text.decode("utf-8", "ignore"), kind="import", line=node.start_point[0] + 1)
            for capture_name, node in captures
            if capture_name == "path"
        ]
        unique = {(ref.raw, ref.kind, ref.line): ref for ref in refs}
        return tuple(unique.values())


class TreeSitterCExtractor(DependencyExtractor):
    """Extract C/C++ #include directives via tree-sitter, falling back to regex."""

    language = "C"

    def __init__(self, fallback: DependencyExtractor) -> None:
        self._fallback = fallback

    def extract(self, path: Path) -> tuple[ImportRef, ...]:
        pack_name = pack_name_for_ext(path.suffix)
        if not backend.AVAILABLE or pack_name is None:
            return self._fallback.extract(path)
        source = _read_bytes(path)
        if source is None:
            return ()
        captures = backend.query_captures(pack_name, source, _C_QUERY)
        if not captures and source:
            return self._fallback.extract(path)
        refs: list[ImportRef] = []
        for capture_name, node in captures:
            line = node.start_point[0] + 1
            if capture_name == "system":
                raw = node.text.decode("utf-8", "ignore").strip("<>")
                refs.append(ImportRef(raw=raw, kind="system-include", line=line))
            elif capture_name == "local":
                refs.append(ImportRef(raw=node.text.decode("utf-8", "ignore"), kind="include", line=line))
        unique = {(ref.raw, ref.kind, ref.line): ref for ref in refs}
        return tuple(unique.values())
