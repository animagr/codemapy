from __future__ import annotations

import fnmatch
from dataclasses import dataclass
from pathlib import Path

from .config import Config, is_default_ignored_dir_name, is_generated_file, is_project_metadata_file
from .models import FileNode, TreeNode


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
    ".sol": "Solidity",
    ".sh": "Shell",
    ".bash": "Shell",
    ".zsh": "Shell",
}


def scan_project(root: Path, config: Config) -> tuple[FileNode, ...]:
    root = root.resolve()
    files: list[FileNode] = []
    gitignore = GitIgnoreMatcher.from_root(root)

    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        if _is_ignored(path, root, rel, config, gitignore):
            continue

        try:
            stat = path.stat()
        except OSError:
            continue
        if stat.st_size > config.max_file_bytes:
            continue

        ext = path.suffix.lower()
        if config.only and ext.lstrip(".") not in config.only:
            continue

        language = LANGUAGES_BY_EXT.get(ext, "")
        loc = _count_loc(path)
        files.append(
            FileNode(
                path=rel,
                absolute_path=path,
                size=stat.st_size,
                loc=loc,
                extension=ext,
                language=language,
            )
        )

    return tuple(files)


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


def _is_ignored(path: Path, root: Path, rel: str, config: Config, gitignore: GitIgnoreMatcher) -> bool:
    names = path.relative_to(root).parts
    if any(part in config.ignore_dirs or is_default_ignored_dir_name(part) for part in names):
        return True
    if gitignore.matches(rel):
        return True
    if is_project_metadata_file(path) or is_generated_file(path):
        return True
    return any(pattern and pattern in rel for pattern in config.exclude)


@dataclass(frozen=True)
class GitIgnoreRule:
    base: str
    pattern: str
    negated: bool
    directory_only: bool
    anchored: bool


@dataclass(frozen=True)
class GitIgnoreMatcher:
    rules: tuple[GitIgnoreRule, ...]

    @classmethod
    def from_root(cls, root: Path) -> GitIgnoreMatcher:
        rules: list[GitIgnoreRule] = []
        for path in sorted(root.rglob(".gitignore")):
            if not path.is_file():
                continue
            base = path.parent.relative_to(root).as_posix()
            if base == ".":
                base = ""
            rules.extend(_read_gitignore_rules(path, base))
        rules.sort(key=lambda rule: (len(Path(rule.base).parts) if rule.base else 0, rule.base))
        return cls(tuple(rules))

    def matches(self, rel: str) -> bool:
        ignored = False
        for rule in self.rules:
            if _gitignore_rule_matches(rule, rel):
                ignored = not rule.negated
        return ignored


def _read_gitignore_rules(path: Path, base: str) -> list[GitIgnoreRule]:
    try:
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError:
        return []

    rules: list[GitIgnoreRule] = []
    for line in lines:
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


def _gitignore_rule_matches(rule: GitIgnoreRule, rel: str) -> bool:
    rel_under_base = _relative_to_gitignore_base(rule.base, rel)
    if rel_under_base is None:
        return False

    pattern = rule.pattern.replace("\\", "/")
    if rule.directory_only:
        return _matches_directory_pattern(rel_under_base, pattern, rule.anchored)
    if rule.anchored or "/" in pattern:
        return fnmatch.fnmatchcase(rel_under_base, pattern)
    return any(fnmatch.fnmatchcase(part, pattern) for part in rel_under_base.split("/"))


def _relative_to_gitignore_base(base: str, rel: str) -> str | None:
    if not base:
        return rel
    if rel == base:
        return ""
    prefix = f"{base}/"
    if rel.startswith(prefix):
        return rel[len(prefix) :]
    return None


def _matches_directory_pattern(rel: str, pattern: str, anchored: bool) -> bool:
    if anchored or "/" in pattern:
        return rel == pattern or rel.startswith(f"{pattern}/") or fnmatch.fnmatchcase(rel, f"{pattern}/*")

    parts = rel.split("/")
    return any(part == pattern or fnmatch.fnmatchcase(part, pattern) for part in parts[:-1])


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
