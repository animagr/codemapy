from __future__ import annotations

import re
from pathlib import Path

from codemapy.deps.base import DependencyExtractor
from codemapy.models import ImportRef

COMMENT_BLOCK_RE = re.compile(r"/\*.*?\*/", re.DOTALL)
COMMENT_LINE_RE = re.compile(r"//.*")
# `using X.Y.Z;` and `using static X.Y.Z;` (also tolerating a leading C# 10
# `global` modifier). Namespace aliases (`using Foo = Bar;`) and using
# statements/declarations (`using (var x = ...)`, `using var x = ...;`) are not
# matched: the captured identifier must be followed directly by `;`.
USING_RE = re.compile(
    r"^\s*(?:global\s+)?using\s+(?:static\s+)?([A-Za-z_][A-Za-z0-9_.]*)\s*;",
    re.MULTILINE,
)
# `namespace X.Y.Z` for both block (`namespace X { ... }`) and file-scoped
# (`namespace X;`) declarations.
NAMESPACE_RE = re.compile(r"^\s*namespace\s+([A-Za-z_][A-Za-z0-9_.]*)", re.MULTILINE)
# Type declarations: classes/interfaces/structs/enums (also matches the name in
# `record class X` / `record struct X`), plain positional records, and delegates.
TYPE_DECL_RE = re.compile(r"\b(?:class|interface|struct|enum)\s+([A-Za-z_]\w*)")
RECORD_DECL_RE = re.compile(r"\brecord\s+(?:class\s+|struct\s+)?([A-Za-z_]\w*)")
DELEGATE_DECL_RE = re.compile(r"\bdelegate\b[^;(=]*?([A-Za-z_]\w*)\s*\(")


class CSharpExtractor(DependencyExtractor):
    language = "C#"

    def extract(self, path: Path) -> tuple[ImportRef, ...]:
        try:
            source = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            return ()

        searchable = _strip_comments(source)
        refs: list[ImportRef] = []
        for match in USING_RE.finditer(searchable):
            refs.append(
                ImportRef(
                    raw=match.group(1),
                    kind="csharp-using",
                    line=_line_number(searchable, match.start()),
                )
            )
        unique = {(ref.raw, ref.kind, ref.line): ref for ref in refs}
        return tuple(unique.values())


def declared_namespaces(path: Path) -> tuple[str, ...]:
    """Return the namespaces declared in *path* (a C# source file).

    A single file may declare more than one namespace; each distinct name is
    returned once, in first-seen order.
    """
    try:
        source = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ()
    searchable = _strip_comments(source)
    return tuple(dict.fromkeys(match.group(1) for match in NAMESPACE_RE.finditer(searchable)))


def declared_types(path: Path) -> frozenset[str]:
    """Return the names of the top-level-visible types declared in *path*.

    Used to filter C# `using` resolution: a namespace file is only a real
    dependency of an importer that actually references one of its types.
    """
    try:
        source = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return frozenset()
    searchable = _strip_comments(source)
    names: set[str] = set()
    for pattern in (TYPE_DECL_RE, RECORD_DECL_RE, DELEGATE_DECL_RE):
        names.update(match.group(1) for match in pattern.finditer(searchable))
    return frozenset(names)


def _strip_comments(source: str) -> str:
    without_blocks = COMMENT_BLOCK_RE.sub("", source)
    return COMMENT_LINE_RE.sub("", without_blocks)


def _line_number(source: str, position: int) -> int:
    return source.count("\n", 0, position) + 1
