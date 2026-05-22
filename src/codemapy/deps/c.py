from __future__ import annotations

import re
from pathlib import Path

from codemapy.deps.base import DependencyExtractor
from codemapy.models import ImportRef


INCLUDE_RE = re.compile(r"^\s*#\s*include\s*([<\"])([^>\"]+)[>\"]", re.MULTILINE)


class CExtractor(DependencyExtractor):
    language = "C"

    def extract(self, path: Path) -> tuple[ImportRef, ...]:
        try:
            source = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            return ()

        refs: list[ImportRef] = []
        for match in INCLUDE_RE.finditer(source):
            delimiter = match.group(1)
            kind = "system-include" if delimiter == "<" else "include"
            refs.append(ImportRef(raw=match.group(2), kind=kind, line=_line_number(source, match.start())))

        unique = {(ref.raw, ref.kind, ref.line): ref for ref in refs}
        return tuple(unique.values())


def _line_number(source: str, position: int) -> int:
    return source.count("\n", 0, position) + 1
