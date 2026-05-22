from __future__ import annotations

import re
from pathlib import Path

from codemapy.deps.base import DependencyExtractor
from codemapy.models import ImportRef


IMPORT_PATTERNS = (
    re.compile(r"^\s*import\s+(?:.+?\s+from\s+)?[\"']([^\"']+)[\"']", re.MULTILINE),
    re.compile(r"^\s*export\s+.+?\s+from\s+[\"']([^\"']+)[\"']", re.MULTILINE),
    re.compile(r"\brequire\(\s*[\"']([^\"']+)[\"']\s*\)"),
    re.compile(r"\bimport\(\s*[\"']([^\"']+)[\"']\s*\)"),
)


class JavaScriptExtractor(DependencyExtractor):
    language = "JavaScript"

    def extract(self, path: Path) -> tuple[ImportRef, ...]:
        try:
            source = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            return ()

        refs: list[ImportRef] = []
        for pattern in IMPORT_PATTERNS:
            for match in pattern.finditer(source):
                line = source.count("\n", 0, match.start()) + 1
                refs.append(ImportRef(raw=match.group(1), kind="import", line=line))

        unique = {(ref.raw, ref.kind, ref.line): ref for ref in refs}
        return tuple(unique.values())
