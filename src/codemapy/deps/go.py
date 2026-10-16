"""Regex extraction of Go import paths (the fallback for the tree-sitter extractor)."""

from __future__ import annotations

import re
from pathlib import Path

from codemapy.deps.base import DependencyExtractor
from codemapy.models import ImportRef

GO_IMPORT_KIND = "go-import"

# `import "fmt"` and `import alias "pkg"`, one per line.
_SINGLE_IMPORT = re.compile(r"^[ \t]*import[ \t]+(?:[\w.]+[ \t]+)?[\"`]([^\"`]+)[\"`]", re.MULTILINE)
# `import ( ... )` blocks; each spec inside is `["alias"] "path"`.
_IMPORT_BLOCK = re.compile(r"^[ \t]*import[ \t]*\((.*?)\)", re.MULTILINE | re.DOTALL)
_BLOCK_SPEC = re.compile(r"^[ \t]*(?:[\w.]+[ \t]+)?[\"`]([^\"`]+)[\"`]", re.MULTILINE)


class GoExtractor(DependencyExtractor):
    language = "Go"

    def extract(self, path: Path) -> tuple[ImportRef, ...]:
        try:
            source = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            return ()

        refs: list[ImportRef] = []
        for match in _SINGLE_IMPORT.finditer(source):
            refs.append(
                ImportRef(raw=match.group(1), kind=GO_IMPORT_KIND, line=_line_of(source, match.start(1)))
            )
        for block in _IMPORT_BLOCK.finditer(source):
            offset = block.start(1)
            for spec in _BLOCK_SPEC.finditer(block.group(1)):
                line = _line_of(source, offset + spec.start(1))
                refs.append(ImportRef(raw=spec.group(1), kind=GO_IMPORT_KIND, line=line))
        return tuple(sorted(set(refs), key=lambda ref: (ref.line, ref.raw)))


def _line_of(source: str, offset: int) -> int:
    return source.count("\n", 0, offset) + 1
