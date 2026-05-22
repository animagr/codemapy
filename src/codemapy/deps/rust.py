from __future__ import annotations

import re
from pathlib import Path

from codemapy.deps.base import DependencyExtractor
from codemapy.models import ImportRef


COMMENT_BLOCK_RE = re.compile(r"/\*.*?\*/", re.DOTALL)
COMMENT_LINE_RE = re.compile(r"//.*")
MOD_RE = re.compile(r"^\s*(?:pub(?:\([^)]*\))?\s+)?mod\s+([A-Za-z_][A-Za-z0-9_]*)\s*;", re.MULTILINE)
USE_RE = re.compile(r"^\s*(?:pub(?:\([^)]*\))?\s+)?use\s+(.+?);", re.MULTILINE | re.DOTALL)


class RustExtractor(DependencyExtractor):
    language = "Rust"

    def extract(self, path: Path) -> tuple[ImportRef, ...]:
        try:
            source = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            return ()

        searchable = _strip_comments(source)
        refs: list[ImportRef] = []
        for match in MOD_RE.finditer(searchable):
            refs.append(ImportRef(raw=match.group(1), kind="rust-mod", line=_line_number(searchable, match.start())))
        for match in USE_RE.finditer(searchable):
            line = _line_number(searchable, match.start())
            for raw in _use_paths(match.group(1)):
                refs.append(ImportRef(raw=raw, kind="rust-use", line=line))

        unique = {(ref.raw, ref.kind, ref.line): ref for ref in refs}
        return tuple(unique.values())


def _strip_comments(source: str) -> str:
    without_blocks = COMMENT_BLOCK_RE.sub("", source)
    return COMMENT_LINE_RE.sub("", without_blocks)


def _use_paths(statement: str) -> tuple[str, ...]:
    normalized = " ".join(statement.strip().split())
    if not normalized:
        return ()
    normalized = normalized.removeprefix("::")
    if "{" not in normalized:
        return (_clean_use_path(normalized),)

    base, rest = normalized.split("{", 1)
    base = base.rstrip(":").strip()
    entries = _brace_entries(rest)
    paths: list[str] = []
    base_is_scope_only = base in {"crate", "self", "super"} or (base and "::" not in base)
    if base and not base_is_scope_only:
        paths.append(base)
    for entry in entries:
        cleaned = _clean_use_path(entry)
        if not cleaned or cleaned in {"self", "*"}:
            continue
        if base_is_scope_only:
            paths.append(f"{base}::{cleaned}" if base else cleaned)
        elif "::" in cleaned:
            paths.append(f"{base}::{cleaned}")
    return tuple(dict.fromkeys(paths))


def _brace_entries(rest: str) -> tuple[str, ...]:
    depth = 1
    current: list[str] = []
    entries: list[str] = []
    for char in rest:
        if char == "{":
            depth += 1
            current.append(char)
        elif char == "}":
            depth -= 1
            if depth == 0:
                if current:
                    entries.append("".join(current).strip())
                break
            current.append(char)
        elif char == "," and depth == 1:
            entries.append("".join(current).strip())
            current = []
        else:
            current.append(char)
    return tuple(entry for entry in entries if entry)


def _clean_use_path(path: str) -> str:
    cleaned = path.split(" as ", 1)[0].strip()
    if "{" in cleaned:
        cleaned = cleaned.split("{", 1)[0].rstrip(":").strip()
    return cleaned


def _line_number(source: str, position: int) -> int:
    return source.count("\n", 0, position) + 1
