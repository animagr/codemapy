from __future__ import annotations

import fnmatch
import os
from dataclasses import dataclass
from pathlib import Path

from .config import Config, is_generated_file, is_project_metadata_file, metadata_kind
from .models import AssetGroup, AuxFile, FileNode, ScanResult, TreeNode

try:  # pragma: no cover - exercised indirectly via which matcher is used
    import pathspec as _pathspec
except ImportError:  # graceful degradation to the fnmatch-based fallback
    _pathspec = None  # type: ignore[assignment]


LANGUAGES_BY_EXT = {
    ".py": "Python",
    ".js": "JavaScript",
    ".jsx": "JavaScript",
    ".mjs": "JavaScript",
    ".cjs": "JavaScript",
    ".ts": "TypeScript",
    ".tsx": "TypeScript",
    ".svelte": "Svelte",
    ".v": "Verilog",
    ".vh": "Verilog Header",
    ".sv": "SystemVerilog",
    ".svh": "SystemVerilog Header",
    ".go": "Go",
    ".rs": "Rust",
    ".rb": "Ruby",
    ".java": "Java",
    ".cs": "C#",
    ".cpp": "C++",
    ".cc": "C++",
    ".cxx": "C++",
    ".c": "C",
    ".h": "C Header",
    ".hpp": "C++ Header",
    ".swift": "Swift",
    ".kt": "Kotlin",
    ".kts": "Kotlin",
    ".php": "PHP",
    ".lua": "Lua",
    ".scala": "Scala",
    ".ex": "Elixir",
    ".exs": "Elixir",
    ".gd": "GDScript",
    ".sol": "Solidity",
    ".sh": "Shell",
    ".bash": "Shell",
    ".zsh": "Shell",
}

DOC_EXTENSIONS = {".md", ".markdown", ".rst", ".adoc", ".txt"}

NO_EXTENSION_GROUP = "(no extension)"


def scan_project(root: Path, config: Config) -> ScanResult:
    """Walk *root* once, pruning ignored directories, and classify every file.

    Files are classified as source (mapped languages), documentation, project
    metadata, or unmapped assets. Generated outputs and ignored directories
    are skipped without descending into them.
    """
    root = root.resolve()
    sources: list[FileNode] = []
    metadata: list[AuxFile] = []
    docs: list[AuxFile] = []
    assets: dict[str, list[int]] = {}
    gitignore = _new_gitignore_stack()

    for dirpath, dirnames, filenames in os.walk(root):
        dir_rel = Path(dirpath).relative_to(root).as_posix()
        if dir_rel == ".":
            dir_rel = ""
        if ".gitignore" in filenames:
            gitignore.add(dir_rel, Path(dirpath) / ".gitignore")

        dirnames[:] = sorted(
            name
            for name in dirnames
            if not _dir_ignored(_join_rel(dir_rel, name), name, config, gitignore)
        )

        for name in sorted(filenames):
            rel = _join_rel(dir_rel, name)
            if _file_ignored(rel, name, config, gitignore):
                continue
            _classify(Path(dirpath) / name, rel, config, sources, metadata, docs, assets)

    asset_groups = tuple(
        AssetGroup(extension=extension, count=count, size=size)
        for extension, (count, size) in sorted(assets.items())
    )
    return ScanResult(
        sources=tuple(sorted(sources, key=lambda file: file.path)),
        metadata_files=tuple(sorted(metadata, key=lambda item: item.path)),
        doc_files=tuple(sorted(docs, key=lambda item: item.path)),
        asset_groups=asset_groups,
    )


def build_tree(files: tuple[FileNode, ...]) -> TreeNode:
    root = TreeNode(name="", path="")
    for file in files:
        current = root
        parts = file.path.split("/")
        for index, part in enumerate(parts):
            child_path = "/".join(parts[: index + 1])
            if part not in current.children:
                current.children[part] = TreeNode(name=part, path=child_path)
            current = current.children[part]
        current.file = file
    return root


def _join_rel(base: str, name: str) -> str:
    return f"{base}/{name}" if base else name


def _dir_ignored(rel: str, name: str, config: Config, gitignore: _GitIgnoreStack) -> bool:
    if config.is_ignored_name(name):
        return True
    if gitignore.is_ignored(rel, is_dir=True):
        return True
    return any(pattern and pattern in rel for pattern in config.exclude)


def _file_ignored(rel: str, name: str, config: Config, gitignore: _GitIgnoreStack) -> bool:
    if config.is_ignored_name(name):
        return True
    if gitignore.is_ignored(rel, is_dir=False):
        return True
    return any(pattern and pattern in rel for pattern in config.exclude)


def _classify(
    path: Path,
    rel: str,
    config: Config,
    sources: list[FileNode],
    metadata: list[AuxFile],
    docs: list[AuxFile],
    assets: dict[str, list[int]],
) -> None:
    try:
        stat = path.stat()
    except OSError:
        return

    name = path.name
    ext = path.suffix.lower()
    if is_project_metadata_file(path):
        loc = _count_loc(path) if stat.st_size <= config.max_file_bytes else 0
        metadata.append(AuxFile(path=rel, kind=metadata_kind(name), size=stat.st_size, loc=loc))
        return
    if is_generated_file(path):
        return

    if ext in LANGUAGES_BY_EXT:
        if config.only and ext.lstrip(".") not in config.only:
            return
        if stat.st_size > config.max_file_bytes:
            return
        sources.append(
            FileNode(
                path=rel,
                absolute_path=path,
                size=stat.st_size,
                loc=_count_loc(path),
                extension=ext,
                language=LANGUAGES_BY_EXT[ext],
            )
        )
        return

    if ext in DOC_EXTENSIONS:
        if stat.st_size > config.max_file_bytes:
            return
        docs.append(AuxFile(path=rel, kind="documentation", size=stat.st_size, loc=_count_loc(path)))
        return

    group = assets.setdefault(ext or NO_EXTENSION_GROUP, [0, 0])
    group[0] += 1
    group[1] += stat.st_size


def _new_gitignore_stack() -> _GitIgnoreStack:
    if _pathspec is not None:
        return _PathspecGitIgnoreStack()
    return _FallbackGitIgnoreStack()


class _GitIgnoreStack:
    """Interface: gitignore files discovered during the walk, checked per path."""

    def add(self, base: str, path: Path) -> None:
        raise NotImplementedError

    def is_ignored(self, rel: str, is_dir: bool) -> bool:
        raise NotImplementedError


class _PathspecGitIgnoreStack(_GitIgnoreStack):
    """Exact gitignore semantics via ``pathspec.GitIgnoreSpec``.

    Specs are appended in walk order (parents before children), so iterating
    in insertion order applies git's "deeper file wins" precedence.
    """

    def __init__(self) -> None:
        self._specs: list[tuple[str, object]] = []

    def add(self, base: str, path: Path) -> None:
        lines = _read_gitignore_lines(path)
        if not lines:
            return
        try:
            spec = _pathspec.GitIgnoreSpec.from_lines(lines)
        except Exception:  # noqa: BLE001 - unparsable pattern set; skip the file
            return
        self._specs.append((base, spec))

    def is_ignored(self, rel: str, is_dir: bool) -> bool:
        verdict = False
        for base, spec in self._specs:
            sub = _relative_to_gitignore_base(base, rel)
            if not sub:
                continue
            result = spec.check_file(f"{sub}/" if is_dir else sub)
            if result.include is not None:
                verdict = result.include
        return verdict


class _FallbackGitIgnoreStack(_GitIgnoreStack):
    """Approximate gitignore matching via ``fnmatch`` when pathspec is absent.

    Known deviation from git: ``*`` also matches across ``/`` and ``**`` is
    treated like ``*``. Install ``pathspec`` for exact semantics.
    """

    def __init__(self) -> None:
        self._rules: list[GitIgnoreRule] = []

    def add(self, base: str, path: Path) -> None:
        self._rules.extend(_read_gitignore_rules(path, base))

    def is_ignored(self, rel: str, is_dir: bool) -> bool:
        ignored = False
        for rule in self._rules:
            if _gitignore_rule_matches(rule, rel, is_dir):
                ignored = not rule.negated
        return ignored


@dataclass(frozen=True)
class GitIgnoreRule:
    base: str
    pattern: str
    negated: bool
    directory_only: bool
    anchored: bool


def _read_gitignore_lines(path: Path) -> list[str]:
    try:
        return path.read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError:
        return []


def _read_gitignore_rules(path: Path, base: str) -> list[GitIgnoreRule]:
    rules: list[GitIgnoreRule] = []
    for line in _read_gitignore_lines(path):
        pattern = line.strip()
        if not pattern or pattern.startswith("#"):
            continue
        negated = pattern.startswith("!")
        if negated:
            pattern = pattern[1:].strip()
        if not pattern:
            continue

        anchored = pattern.startswith("/")
        pattern = pattern.lstrip("/")
        directory_only = pattern.endswith("/")
        pattern = pattern.rstrip("/")
        if not pattern:
            continue
        rules.append(
            GitIgnoreRule(
                base=base,
                pattern=pattern,
                negated=negated,
                directory_only=directory_only,
                anchored=anchored,
            )
        )
    return rules


def _gitignore_rule_matches(rule: GitIgnoreRule, rel: str, is_dir: bool) -> bool:
    rel_under_base = _relative_to_gitignore_base(rule.base, rel)
    if not rel_under_base:
        return False

    pattern = rule.pattern.replace("\\", "/")
    if rule.directory_only:
        return _matches_directory_pattern(rel_under_base, pattern, rule.anchored, is_dir)
    if rule.anchored or "/" in pattern:
        return fnmatch.fnmatchcase(rel_under_base, pattern)
    return any(fnmatch.fnmatchcase(part, pattern) for part in rel_under_base.split("/"))


def _relative_to_gitignore_base(base: str, rel: str) -> str | None:
    if not base:
        return rel
    prefix = f"{base}/"
    if rel.startswith(prefix):
        return rel[len(prefix) :]
    return None


def _matches_directory_pattern(rel: str, pattern: str, anchored: bool, is_dir: bool) -> bool:
    if anchored or "/" in pattern:
        if is_dir and fnmatch.fnmatchcase(rel, pattern):
            return True
        return rel.startswith(f"{pattern}/") or fnmatch.fnmatchcase(rel, f"{pattern}/*")

    parts = rel.split("/")
    candidates = parts if is_dir else parts[:-1]
    return any(part == pattern or fnmatch.fnmatchcase(part, pattern) for part in candidates)


def _count_loc(path: Path) -> int:
    try:
        count = 0
        with path.open("r", encoding="utf-8", errors="ignore") as handle:
            for line in handle:
                if line.strip():
                    count += 1
        return count
    except OSError:
        return 0
