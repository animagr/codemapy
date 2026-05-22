from __future__ import annotations

import re
from pathlib import Path

from codemapy.deps.base import DependencyExtractor
from codemapy.models import ImportRef


COMMENT_BLOCK_RE = re.compile(r"/\*.*?\*/", re.DOTALL)
COMMENT_LINE_RE = re.compile(r"//.*")
INCLUDE_RE = re.compile(r"^\s*`include\s+[\"<]([^\">]+)[\">]", re.MULTILINE)
MODULE_DECL_RE = re.compile(r"^\s*module\s+([A-Za-z_][A-Za-z0-9_$]*)\b", re.MULTILINE)
INSTANTIATION_RE = re.compile(
    r"^\s*([A-Za-z_][A-Za-z0-9_$]*)\s*"
    r"(?:#\s*\((?:[^()]|\([^()]*\))*\)\s*)?"
    r"\s+"
    r"([A-Za-z_][A-Za-z0-9_$]*)\s*\(",
    re.MULTILINE,
)

VERILOG_KEYWORDS = {
    "always",
    "and",
    "assign",
    "begin",
    "buf",
    "case",
    "casex",
    "casez",
    "deassign",
    "defparam",
    "else",
    "end",
    "endcase",
    "endmodule",
    "endfunction",
    "endgenerate",
    "endprimitive",
    "endspecify",
    "endtable",
    "endtask",
    "for",
    "force",
    "forever",
    "fork",
    "function",
    "generate",
    "if",
    "initial",
    "inout",
    "input",
    "integer",
    "join",
    "localparam",
    "module",
    "nand",
    "negedge",
    "nor",
    "not",
    "or",
    "output",
    "parameter",
    "posedge",
    "primitive",
    "reg",
    "release",
    "repeat",
    "specify",
    "supply0",
    "supply1",
    "table",
    "task",
    "tri",
    "wand",
    "while",
    "wire",
    "wor",
    "xnor",
    "xor",
}


class VerilogExtractor(DependencyExtractor):
    language = "Verilog"

    def extract(self, path: Path) -> tuple[ImportRef, ...]:
        try:
            source = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            return ()

        stripped = strip_comments(source)
        declarations = set(declared_modules_from_source(stripped))
        refs: list[ImportRef] = []

        for match in INCLUDE_RE.finditer(stripped):
            refs.append(ImportRef(raw=match.group(1), kind="include", line=_line_number(stripped, match.start())))

        for match in INSTANTIATION_RE.finditer(stripped):
            module_name = match.group(1)
            if module_name in declarations or module_name in VERILOG_KEYWORDS:
                continue
            refs.append(ImportRef(raw=module_name, kind="module", line=_line_number(stripped, match.start())))

        unique = {(ref.raw, ref.kind, ref.line): ref for ref in refs}
        return tuple(unique.values())


def declared_modules(path: Path) -> tuple[str, ...]:
    try:
        source = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ()
    return tuple(declared_modules_from_source(strip_comments(source)))


def declared_modules_from_source(source: str) -> tuple[str, ...]:
    return tuple(match.group(1) for match in MODULE_DECL_RE.finditer(source))


def strip_comments(source: str) -> str:
    without_line_comments = COMMENT_LINE_RE.sub("", source)
    return COMMENT_BLOCK_RE.sub(_preserve_newlines, without_line_comments)


def _preserve_newlines(match: re.Match[str]) -> str:
    return "\n" * match.group(0).count("\n")


def _line_number(source: str, position: int) -> int:
    return source.count("\n", 0, position) + 1
