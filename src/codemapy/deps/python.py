from __future__ import annotations

import ast
from pathlib import Path

from codemapy.deps.base import DependencyExtractor
from codemapy.models import ImportRef


class PythonExtractor(DependencyExtractor):
    language = "Python"

    def extract(self, path: Path) -> tuple[ImportRef, ...]:
        try:
            source = path.read_text(encoding="utf-8", errors="ignore")
            tree = ast.parse(source, filename=str(path))
        except (OSError, SyntaxError):
            return ()

        refs: list[ImportRef] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    refs.append(ImportRef(raw=alias.name, kind="import", line=node.lineno))
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                prefix = "." * node.level
                if module:
                    refs.append(ImportRef(raw=prefix + module, kind="from", line=node.lineno))
                else:
                    for alias in node.names:
                        refs.append(ImportRef(raw=prefix + alias.name, kind="from", line=node.lineno))

        return tuple(refs)
