"""Explain imports that name project files but did not resolve to a mapped one.

An import the resolver cannot place used to land in ``external_imports``,
next to ``os`` and ``react``. That hid real gaps in the map: on codegraph,
109 of its "external" references were ``../src/db/...`` imports of a source
folder the scanner had skipped because ``db`` was a default-ignored name.

An unresolved import is reported as an internal miss when either:

1. One of the resolver's candidate paths exists on disk inside something the
   scan skipped (a pruned directory, or a dropped source file). This is
   direct evidence, and the skip rule is the reason.
2. Its syntax can only name a project file (a relative JS/TS script path, a
   relative C include, a Rust ``mod x;``, a Python relative import or one
   rooted at a top-level project package, a GDScript ``.gd`` path) and none of
   the candidates exists. These are reported as missing.

Everything else stays external. The shape rules are deliberately narrow:
include paths in C and Verilog, Lua ``require`` names and C# namespaces are
routinely satisfied by SDKs and tool settings outside the repo.
"""

from __future__ import annotations

import posixpath
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from codemapy.deps.resolver import C_LANGUAGES, JS_EXTENSIONS
from codemapy.models import FileNode, ImportRef, SkippedPath, SkipReason, UnresolvedImport
from codemapy.scanner import LANGUAGES_BY_EXT

JS_LANGUAGES = frozenset({"JavaScript", "TypeScript", "Svelte"})
RELATIVE_PATH_PREFIXES = ("./", "../")
# Node and bundlers also resolve an extensionless specifier to a JSON file.
JS_DATA_SUFFIXES = (".json", "/index.json")
PYTHON_SOURCE_ROOT = "src"
MISSING_REASON = "missing"

MAX_GROUP_EXAMPLES = 3
MAX_WARNINGS = 5

# Skip rules whose hidden imports are worth a stderr warning: the ones the
# user configured, plus the FPGA tool-output guess. The built-in names
# (vendor/, dist/, node_modules/), .gitignore and build outputs hide
# third-party or built code on purpose, so imports into them are expected;
# they are still listed in summary.md and context.json.
WARN_REASONS = frozenset(
    {
        SkipReason.TOOL_OUTPUT,
        SkipReason.CONFIG_IGNORE,
        SkipReason.EXCLUDE,
        SkipReason.ONLY_FILTER,
        SkipReason.TOO_LARGE,
    }
)

REASON_LABELS = {
    SkipReason.DEFAULT_IGNORE: "a default-ignored name",
    SkipReason.TOOL_OUTPUT: "FPGA tool output: a project file sits beside it",
    SkipReason.CONFIG_IGNORE: "listed in ignore_dirs in .codemap.json",
    SkipReason.GITIGNORE: "matched by .gitignore",
    SkipReason.EXCLUDE: "matched by an exclude pattern",
    SkipReason.ONLY_FILTER: "outside the only filter",
    SkipReason.TOO_LARGE: "over max_file_bytes",
    SkipReason.BUILD_OUTPUT: "named like build output",
}
MISSING_NOTE = "it may be generated at build time, or the import may be stale"


class UnresolvedClassifier:
    """Decide whether a failed import is an internal miss or truly external."""

    def __init__(self, root: Path, files: tuple[FileNode, ...], skipped: tuple[SkippedPath, ...]) -> None:
        self.root = root.resolve()
        self._paths = {file.path for file in files}
        self._skipped_dirs = {item.path: item for item in skipped if item.is_dir}
        self._skipped_files = {item.path: item for item in skipped if not item.is_dir}
        self._python_top_level = _python_top_level_names(files)
        self._python_dirs = _python_dirs(files)
        self._exists_cache: dict[str, bool] = {}

    def classify(
        self, source: FileNode, ref: ImportRef, candidates: Iterable[str]
    ) -> UnresolvedImport | None:
        """Return an UnresolvedImport, or None if *ref* should stay external.

        *candidates* are the normalized paths the resolver tried for *ref*.
        """
        candidates = list(candidates)
        hidden = self._hidden_target(candidates)
        if hidden is not None:
            hidden_by = f"{hidden.path}/" if hidden.is_dir else hidden.path
            return UnresolvedImport(
                source=source.path,
                raw=ref.raw,
                kind=ref.kind,
                line=ref.line,
                reason=hidden.reason,
                hidden_by=hidden_by,
            )
        if self._names_project_file(source, ref) and not any(
            self._exists(path) for path in candidates + self._data_candidates(source, ref)
        ):
            return UnresolvedImport(source=source.path, raw=ref.raw, kind=ref.kind, line=ref.line)
        return None

    def _data_candidates(self, source: FileNode, ref: ImportRef) -> list[str]:
        """JSON files an extensionless JS/TS specifier may name (``./defaultCircuit``)."""
        if source.language not in JS_LANGUAGES:
            return []
        target = posixpath.normpath(posixpath.join(posixpath.dirname(source.path), ref.raw))
        return [f"{target}{suffix}" for suffix in JS_DATA_SUFFIXES]

    def _hidden_target(self, candidates: list[str]) -> SkippedPath | None:
        for candidate in candidates:
            # Only source files stand for edges; `./vendor/x.min.css` does not.
            if posixpath.splitext(candidate)[1].lower() not in LANGUAGES_BY_EXT:
                continue
            skipped_file = self._skipped_files.get(candidate)
            if skipped_file is not None:
                return skipped_file
            skipped_dir = self._skipped_ancestor(candidate)
            if skipped_dir is not None and self._exists(candidate):
                return skipped_dir
        return None

    def _skipped_ancestor(self, candidate: str) -> SkippedPath | None:
        parts = candidate.split("/")[:-1]
        for depth in range(1, len(parts) + 1):
            skipped = self._skipped_dirs.get("/".join(parts[:depth]))
            if skipped is not None:
                return skipped
        return None

    def _exists(self, candidate: str) -> bool:
        cached = self._exists_cache.get(candidate)
        if cached is None:
            cached = not candidate.startswith("../") and (self.root / candidate).is_file()
            self._exists_cache[candidate] = cached
        return cached

    def _names_project_file(self, source: FileNode, ref: ImportRef) -> bool:
        raw = ref.raw
        if source.language == "Python":
            return self._python_names_project_module(source, raw)
        if source.language in JS_LANGUAGES:
            # Only script-shaped paths: a missing `./data.json` or `./app.css`
            # never stood for a code edge in the first place.
            relative = raw.startswith(RELATIVE_PATH_PREFIXES) or raw in {".", ".."}
            suffix = posixpath.splitext(raw.rstrip("/"))[1]
            return relative and (suffix == "" or suffix in JS_EXTENSIONS)
        if source.language in C_LANGUAGES:
            return raw.startswith(RELATIVE_PATH_PREFIXES)
        if source.language == "Rust":
            return ref.kind == "rust-mod"
        if source.language == "GDScript":
            return ref.kind == "gdscript-path" and raw.endswith(".gd")
        return False

    def _python_names_project_module(self, source: FileNode, raw: str) -> bool:
        module = raw.lstrip(".")
        level = len(raw) - len(module)
        parts = [part for part in module.split(".") if part]
        if level == 0:
            if not parts or parts[0] not in self._python_top_level:
                return False
            bases = ["", PYTHON_SOURCE_ROOT]
        else:
            base = posixpath.dirname(source.path)
            for _ in range(level - 1):
                base = posixpath.dirname(base)
            # `from . import name` and `from .name import x` both arrive as
            # ".name". The first may name an attribute of the package rather
            # than a submodule, so a single name under a package with an
            # __init__.py is not evidence of a missing file.
            if len(parts) <= 1 and posixpath.join(base, "__init__.py") in self._paths:
                return False
            bases = [base]
        # A folder of Python files without an __init__.py is a namespace
        # package (PEP 420), so importing it by name is valid.
        return not any(posixpath.join(base, *parts) in self._python_dirs for base in bases)


def _python_dirs(files: tuple[FileNode, ...]) -> frozenset[str]:
    """Every directory that holds a Python file, at any depth below it."""
    dirs: set[str] = set()
    for file in files:
        if file.language != "Python":
            continue
        parts = file.path.split("/")[:-1]
        dirs.update("/".join(parts[:depth]) for depth in range(1, len(parts) + 1))
    return frozenset(dirs)


def _python_top_level_names(files: tuple[FileNode, ...]) -> frozenset[str]:
    """Top-level Python packages and modules, at the root and under ``src/``."""
    names: set[str] = set()
    for file in files:
        if file.language != "Python":
            continue
        parts = file.path.split("/")
        if parts[0] == PYTHON_SOURCE_ROOT and len(parts) > 1:
            parts = parts[1:]
        names.add(parts[0].removesuffix(".py"))
    return frozenset(names)


@dataclass(frozen=True)
class UnresolvedGroup:
    """Unresolved imports sharing a cause: one skipped path, or no file at all."""

    reason: SkipReason | None
    hidden_by: str | None
    items: tuple[UnresolvedImport, ...]

    @property
    def source_count(self) -> int:
        return len({item.source for item in self.items})


def group_unresolved(items: Iterable[UnresolvedImport]) -> list[UnresolvedGroup]:
    """Group by cause: hidden targets first (largest first), missing files last."""
    groups: dict[tuple[SkipReason | None, str | None], list[UnresolvedImport]] = {}
    for item in items:
        groups.setdefault((item.reason, item.hidden_by), []).append(item)
    ordered = sorted(
        groups.items(),
        key=lambda entry: (entry[0][0] is None, -len(entry[1]), entry[0][1] or ""),
    )
    return [
        UnresolvedGroup(reason=reason, hidden_by=hidden_by, items=tuple(members))
        for (reason, hidden_by), members in ordered
    ]


def reason_value(item: UnresolvedImport) -> str:
    """The JSON value for an item's reason: a SkipReason value or ``missing``."""
    return item.reason.value if item.reason is not None else MISSING_REASON


def describe_group(group: UnresolvedGroup) -> str:
    """One Markdown line explaining *group*, with examples and what to do."""
    count = _count_imports(len(group.items))
    sources = _count_files(group.source_count)
    examples = ", ".join(
        f"`{item.source}:{item.line}` -> `{item.raw}`" for item in group.items[:MAX_GROUP_EXAMPLES]
    )
    if group.reason is None:
        return f"No matching file: {count} from {sources}, e.g. {examples} ({MISSING_NOTE})"
    line = (
        f"`{group.hidden_by}` was skipped ({REASON_LABELS[group.reason]}): "
        f"{count} from {sources}, e.g. {examples}"
    )
    hint = _hint(group)
    return f"{line}. {hint}" if hint else line


def unresolved_warnings(items: Iterable[UnresolvedImport]) -> list[str]:
    """Short warnings for imports hidden by a WARN_REASONS rule; others are only listed."""
    hidden = [group for group in group_unresolved(items) if group.reason in WARN_REASONS]
    warnings = []
    for group in hidden[:MAX_WARNINGS]:
        verb = "points" if len(group.items) == 1 else "point"
        line = (
            f"{_count_imports(len(group.items))} {verb} into `{group.hidden_by}`, "
            f"which was skipped ({REASON_LABELS[group.reason]})"
        )
        hint = _hint(group)
        warnings.append(f"{line}. {hint}" if hint else line)
    if len(hidden) > MAX_WARNINGS:
        warnings.append(f"... and {len(hidden) - MAX_WARNINGS} more skipped paths that imports point into")
    return warnings


def _hint(group: UnresolvedGroup) -> str:
    if group.hidden_by is None or not group.hidden_by.endswith("/"):
        return ""
    name = group.hidden_by.rstrip("/").rsplit("/", 1)[-1]
    if group.reason in {SkipReason.DEFAULT_IGNORE, SkipReason.TOOL_OUTPUT}:
        return f'If it holds real source, add "{name}" to keep_dirs in .codemap.json'
    if group.reason is SkipReason.CONFIG_IGNORE:
        return f'To map it, remove "{name}" from ignore_dirs in .codemap.json'
    return ""


def _count_imports(count: int) -> str:
    return f"{count} import" if count == 1 else f"{count} imports"


def _count_files(count: int) -> str:
    return f"{count} file" if count == 1 else f"{count} files"
