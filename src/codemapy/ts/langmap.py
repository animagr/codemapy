"""Map codemapy file extensions to tree-sitter language-pack names.

The grammars wired up here at time of writing are: bash, c, cpp, csharp, go,
java, javascript, lua, python, ruby, rust, shell, tsx, typescript, verilog.
Extensions not listed here have no tree-sitter coverage and fall back to the
regex/``ast`` extractors.
"""

from __future__ import annotations

# Extension -> tree-sitter language-pack name, used for symbol extraction.
PACK_NAME_BY_EXT: dict[str, str] = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".c": "c",
    ".h": "c",
    ".cc": "cpp",
    ".cpp": "cpp",
    ".cxx": "cpp",
    ".hpp": "cpp",
    ".go": "go",
    ".rs": "rust",
    ".rb": "ruby",
    ".java": "java",
    ".cs": "csharp",
    ".lua": "lua",
    ".sh": "bash",
    ".bash": "bash",
    ".zsh": "bash",
    # The pack's "verilog" grammar is the SystemVerilog grammar, which also
    # parses plain Verilog; used for symbol extraction only (imports stay on
    # the regex extractor in deps/verilog.py).
    ".v": "verilog",
    ".vh": "verilog",
    ".sv": "verilog",
    ".svh": "verilog",
}


def pack_name_for_ext(ext: str) -> str | None:
    """Return the language-pack grammar name for *ext* (case-insensitive)."""
    return PACK_NAME_BY_EXT.get(ext.lower())
