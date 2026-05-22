from __future__ import annotations

from pathlib import Path

from .config import Config
from .models import FileNode, TreeNode


LANGUAGES_BY_EXT = {
    ".py": "Python",
    ".js": "JavaScript",
    ".jsx": "JavaScript",
    ".mjs": "JavaScript",
    ".cjs": "JavaScript",
    ".ts": "TypeScript",
    ".tsx": "TypeScript",
    ".go": "Go",
    ".rs": "Rust",
    ".rb": "Ruby",
    ".java": "Java",
    ".cs": "C#",
    ".cpp": "C++",
    ".cc": "C++",
    ".cxx": "C++",
    ".c": "C",
    ".h": "C/C++ Header",
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

    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        if _is_ignored(path, root, rel, config):
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


def _is_ignored(path: Path, root: Path, rel: str, config: Config) -> bool:
    names = path.relative_to(root).parts
    if any(part in config.ignore_dirs for part in names):
        return True
    return any(pattern and pattern in rel for pattern in config.exclude)


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
