from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class FileNode:
    path: str
    absolute_path: Path
    size: int
    loc: int
    extension: str
    language: str


@dataclass(frozen=True)
class ImportRef:
    raw: str
    kind: str
    line: int


@dataclass(frozen=True)
class Symbol:
    """A definition found in a source file (function, class, method, ...)."""

    name: str
    kind: str
    start_line: int
    end_line: int
    signature: str | None = None
    doc: str | None = None
    children: tuple["Symbol", ...] = ()

    def flatten(self, prefix: str = "") -> "list[tuple[str, Symbol]]":
        """Yield ``(qualified_name, symbol)`` pairs for self and descendants."""
        qualified = f"{prefix}.{self.name}" if prefix else self.name
        pairs: list[tuple[str, Symbol]] = [(qualified, self)]
        for child in self.children:
            pairs.extend(child.flatten(qualified))
        return pairs


@dataclass(frozen=True)
class ModuleNode:
    path: str
    language: str
    loc: int
    size: int
    imports: tuple[ImportRef, ...] = ()
    fan_in: int = 0
    fan_out: int = 0
    symbols: tuple[Symbol, ...] = ()


@dataclass(frozen=True)
class Edge:
    source: str
    target: str
    raw: str
    kind: str


@dataclass(frozen=True)
class ExternalImport:
    source: str
    raw: str
    kind: str


@dataclass(frozen=True)
class Report:
    root: Path
    files: tuple[FileNode, ...]
    modules: tuple[ModuleNode, ...]
    edges: tuple[Edge, ...]
    external_imports: tuple[ExternalImport, ...] = ()
    warnings: tuple[str, ...] = ()
    cycles: tuple[tuple[str, ...], ...] = ()

    @property
    def name(self) -> str:
        return self.root.name or str(self.root)

    @property
    def total_loc(self) -> int:
        return sum(file.loc for file in self.files)

    @property
    def total_size(self) -> int:
        return sum(file.size for file in self.files)

    @property
    def languages(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for file in self.files:
            key = file.language or "Other"
            counts[key] = counts.get(key, 0) + 1
        return dict(sorted(counts.items(), key=lambda item: (-item[1], item[0])))


@dataclass
class TreeNode:
    name: str
    path: str
    children: dict[str, "TreeNode"] = field(default_factory=dict)
    file: FileNode | None = None

    @property
    def is_file(self) -> bool:
        return self.file is not None
