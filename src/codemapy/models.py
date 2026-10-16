from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path


@dataclass(frozen=True)
class FileNode:
    """A scanned source file.

    ``is_generated`` marks machine-written code (a ``DO NOT EDIT`` banner, a
    codegen filename such as ``_pb2.py``, or a minified script). Such files
    stay in the graph so imports of them still resolve, but they are kept out
    of hub and entry-point rankings.
    """

    path: str
    absolute_path: Path
    size: int
    loc: int
    extension: str
    language: str
    is_generated: bool = False


class SkipReason(StrEnum):
    """Why the scanner left a file or directory out of the map."""

    DEFAULT_IGNORE = "default-ignore"
    TOOL_OUTPUT = "tool-output"
    CONFIG_IGNORE = "config-ignore"
    GITIGNORE = "gitignore"
    EXCLUDE = "exclude"
    ONLY_FILTER = "only-filter"
    TOO_LARGE = "too-large"
    BUILD_OUTPUT = "build-output"


@dataclass(frozen=True)
class SkippedPath:
    """A directory the scanner pruned, or a source-language file it dropped."""

    path: str
    reason: SkipReason
    is_dir: bool


@dataclass(frozen=True)
class ImportRef:
    """One import statement's target.

    ``names`` lists what a Python ``from X import a, b`` brings in, so the
    resolver can link ``a`` and ``b`` when they are submodules of ``X``.
    Other extractors leave it empty.
    """

    raw: str
    kind: str
    line: int
    names: tuple[str, ...] = ()


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
class ModuleFacts:
    """What parsing one source file yields beyond its import list.

    ``has_main_guard`` is Python-only: it records a top-level
    ``if __name__ == "__main__":`` block, which marks the file as directly
    runnable and feeds entry-point detection.
    """

    symbols: tuple[Symbol, ...] = ()
    has_main_guard: bool = False


@dataclass(frozen=True)
class AuxFile:
    """A non-source file worth listing for agents (metadata or documentation)."""

    path: str
    kind: str
    size: int
    loc: int


@dataclass(frozen=True)
class AssetGroup:
    """Aggregate of scanned-but-unmapped files sharing an extension."""

    extension: str
    count: int
    size: int


@dataclass(frozen=True)
class ScanResult:
    """Everything a project walk found, classified.

    Iterating a ``ScanResult`` yields its source files, so callers that only
    care about sources can treat it like the plain tuple ``scan_project``
    used to return.
    """

    sources: tuple[FileNode, ...]
    metadata_files: tuple[AuxFile, ...] = ()
    doc_files: tuple[AuxFile, ...] = ()
    asset_groups: tuple[AssetGroup, ...] = ()
    skipped: tuple[SkippedPath, ...] = ()

    def __iter__(self):
        return iter(self.sources)

    def __len__(self) -> int:
        return len(self.sources)


@dataclass(frozen=True)
class ModuleNode:
    """One source file in the dependency graph.

    ``fan_in`` counts every distinct importer; ``test_fan_in`` is the part of
    it that comes from test files, so ``non_test_fan_in`` is what production
    code depends on. Hubs are ranked on the latter: a module that only the
    test suite imports is not a hub of the program.

    ``tested_by`` lists the test files that cover this module directly: they
    import it, or they are named after it (``foo_test.go`` for ``foo.go``).
    """

    path: str
    language: str
    loc: int
    size: int
    imports: tuple[ImportRef, ...] = ()
    fan_in: int = 0
    fan_out: int = 0
    symbols: tuple[Symbol, ...] = ()
    has_main_guard: bool = False
    is_test: bool = False
    is_generated: bool = False
    test_fan_in: int = 0
    tested_by: tuple[str, ...] = ()

    @property
    def non_test_fan_in(self) -> int:
        return self.fan_in - self.test_fan_in


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
class UnresolvedImport:
    """An import that names a project file but did not resolve to a mapped one.

    ``reason`` is why the target is missing from the map: the skip rule that
    hid it (with ``hidden_by`` naming the skipped directory or file), or
    ``None`` when no matching file exists at all.
    """

    source: str
    raw: str
    kind: str
    line: int
    reason: SkipReason | None = None
    hidden_by: str | None = None


@dataclass(frozen=True)
class Report:
    root: Path
    files: tuple[FileNode, ...]
    modules: tuple[ModuleNode, ...]
    edges: tuple[Edge, ...]
    external_imports: tuple[ExternalImport, ...] = ()
    warnings: tuple[str, ...] = ()
    cycles: tuple[tuple[str, ...], ...] = ()
    metadata_files: tuple[AuxFile, ...] = ()
    doc_files: tuple[AuxFile, ...] = ()
    asset_groups: tuple[AssetGroup, ...] = ()
    unresolved_imports: tuple[UnresolvedImport, ...] = ()
    skipped: tuple[SkippedPath, ...] = ()

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
