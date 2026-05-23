from __future__ import annotations

import re
from pathlib import Path

from codemapy.deps.base import DependencyExtractor
from codemapy.models import ImportRef


COMMENT_BLOCK_RE = re.compile(r"--\[(=*)\[.*?\]\1\]", re.DOTALL)
COMMENT_LINE_RE = re.compile(r"--.*")
REQUIRE_RE = re.compile(r"\brequire\s*(?:\(\s*)?[\"']([^\"']+)[\"']\s*\)?")
FILE_CALL_RE = re.compile(r"\b(?:dofile|loadfile)\s*\(\s*([^)]+?)\s*\)")
MODPATH_ASSIGN_RE = re.compile(
    r"\b(?:local\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*"
    r"(?:minetest|core)\.get_modpath\(\s*(?:[\"']([^\"']+)[\"']|(?:minetest|core)\.get_current_modname\(\s*\))\s*\)"
)
STRING_RE = re.compile(r"[\"']([^\"']+)[\"']")
IDENTIFIER_RE = re.compile(r"\b[A-Za-z_][A-Za-z0-9_]*\b")


class LuaExtractor(DependencyExtractor):
    language = "Lua"

    def extract(self, path: Path) -> tuple[ImportRef, ...]:
        try:
            source = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            return ()

        searchable = strip_comments(source)
        modpath_vars = _modpath_vars(searchable)
        refs: list[ImportRef] = []

        for match in REQUIRE_RE.finditer(searchable):
            refs.append(ImportRef(raw=match.group(1), kind="lua-require", line=_line_number(searchable, match.start())))

        for match in FILE_CALL_RE.finditer(searchable):
            expr = match.group(1)
            line = _line_number(searchable, match.start())
            ref = _file_call_ref(expr, modpath_vars, line)
            if ref:
                refs.append(ref)

        unique = {(ref.raw, ref.kind, ref.line): ref for ref in refs}
        return tuple(unique.values())


def strip_comments(source: str) -> str:
    without_blocks = COMMENT_BLOCK_RE.sub(_preserve_newlines, source)
    return COMMENT_LINE_RE.sub("", without_blocks)


def _modpath_vars(source: str) -> dict[str, str]:
    vars_by_name: dict[str, str] = {}
    for match in MODPATH_ASSIGN_RE.finditer(source):
        var_name = match.group(1)
        mod_name = match.group(2) or "."
        vars_by_name[var_name] = mod_name
    return vars_by_name


def _file_call_ref(expr: str, modpath_vars: dict[str, str], line: int) -> ImportRef | None:
    strings = STRING_RE.findall(expr)
    if not strings:
        return None

    identifiers = IDENTIFIER_RE.findall(expr)
    for identifier in identifiers:
        if identifier in modpath_vars:
            suffix = "".join(strings).lstrip("/")
            if suffix:
                return ImportRef(raw=f"{modpath_vars[identifier]}:{suffix}", kind="lua-modpath", line=line)

    if len(strings) == 1:
        return ImportRef(raw=strings[0], kind="lua-file", line=line)
    return None


def _preserve_newlines(match: re.Match[str]) -> str:
    return "\n" * match.group(0).count("\n")


def _line_number(source: str, position: int) -> int:
    return source.count("\n", 0, position) + 1
