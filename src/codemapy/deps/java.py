"""Regex extraction of Java imports and package declarations.

Java imports are single lines (``import a.b.C;``), so a regex is reliable once
comments are blanked out. Besides one reference per import, the extractor
emits one ``java-package`` reference for the file's own package: classes of
the same package are used without any import, and the resolver links them by
the class names the source mentions.
"""

from __future__ import annotations

import re
from pathlib import Path

from codemapy.deps.base import DependencyExtractor
from codemapy.models import ImportRef

JAVA_IMPORT_KIND = "java-import"
JAVA_PACKAGE_KIND = "java-package"
WILDCARD_SUFFIX = ".*"

_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)
_LINE_COMMENT = re.compile(r"//[^\n]*")
_PACKAGE = re.compile(r"^[ \t]*package[ \t]+([\w.]+)[ \t]*;", re.MULTILINE)
_IMPORT = re.compile(r"^[ \t]*import[ \t]+(static[ \t]+)?([\w.]+?)(\.\*)?[ \t]*;", re.MULTILINE)


class JavaExtractor(DependencyExtractor):
    language = "Java"

    def extract(self, path: Path) -> tuple[ImportRef, ...]:
        source = _read_code(path)
        if source is None:
            return ()
        refs: list[ImportRef] = []
        package = _PACKAGE.search(source)
        if package:
            refs.append(
                ImportRef(
                    raw=package.group(1), kind=JAVA_PACKAGE_KIND, line=_line_of(source, package.start(1))
                )
            )
        for match in _IMPORT.finditer(source):
            is_static, name, wildcard = match.group(1), match.group(2), match.group(3)
            if is_static and not wildcard:
                # `import static a.b.C.member;` depends on class a.b.C.
                name = name.rpartition(".")[0] or name
            elif wildcard and not is_static:
                name = f"{name}{WILDCARD_SUFFIX}"
            refs.append(ImportRef(raw=name, kind=JAVA_IMPORT_KIND, line=_line_of(source, match.start(2))))
        unique = {(ref.raw, ref.kind): ref for ref in refs}
        return tuple(unique.values())


def declared_package(path: Path) -> str | None:
    """The ``package`` a Java file declares, or None for the default package."""
    source = _read_code(path)
    match = _PACKAGE.search(source) if source else None
    return match.group(1) if match else None


def _read_code(path: Path) -> str | None:
    """Read *path* with comments blanked, keeping line numbers intact."""
    try:
        source = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None
    source = _BLOCK_COMMENT.sub(lambda match: "\n" * match.group(0).count("\n"), source)
    return _LINE_COMMENT.sub("", source)


def _line_of(source: str, offset: int) -> int:
    return source.count("\n", 0, offset) + 1
