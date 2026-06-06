"""Optional tree-sitter backend for precise parsing and symbol extraction.

The whole package degrades gracefully: if ``tree_sitter`` and the language
pack are not installed, :data:`AVAILABLE` is ``False`` and every helper returns
empty results so the rest of codemapy keeps working on the regex/``ast`` paths.
"""

from __future__ import annotations

from .backend import (
    AVAILABLE,
    get_language,
    get_parser,
    process_source,
    query_captures,
    query_matches,
)

__all__ = [
    "AVAILABLE",
    "get_language",
    "get_parser",
    "process_source",
    "query_captures",
    "query_matches",
]
