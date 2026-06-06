"""Thin, cached wrapper around tree-sitter and ``tree_sitter_language_pack``.

Everything here is import-safe: if the optional dependencies are missing the
module still imports, exposes ``AVAILABLE = False`` and returns empty results.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

try:  # pragma: no cover - exercised indirectly via AVAILABLE
    import tree_sitter as _tree_sitter
    import tree_sitter_language_pack as _pack

    AVAILABLE = True
except Exception:  # noqa: BLE001 - any import failure means "no backend"
    _tree_sitter = None  # type: ignore[assignment]
    _pack = None  # type: ignore[assignment]
    AVAILABLE = False


@lru_cache(maxsize=None)
def get_language(name: str) -> Any | None:
    """Return the tree-sitter ``Language`` for *name*, or ``None``."""
    if not AVAILABLE:
        return None
    try:
        return _pack.get_language(name)
    except Exception:  # noqa: BLE001 - language not bundled / failed to load
        return None


@lru_cache(maxsize=None)
def get_parser(name: str) -> Any | None:
    """Return a raw ``tree_sitter.Parser`` configured for *name*."""
    language = get_language(name)
    if language is None:
        return None
    try:
        return _tree_sitter.Parser(language)
    except Exception:  # noqa: BLE001
        return None


@lru_cache(maxsize=None)
def _get_query(name: str, query_source: str) -> Any | None:
    language = get_language(name)
    if language is None:
        return None
    try:
        return _tree_sitter.Query(language, query_source)
    except Exception:  # noqa: BLE001 - bad query for this grammar version
        return None


def query_captures(name: str, source: bytes, query_source: str) -> list[tuple[str, Any]]:
    """Run *query_source* against *source* parsed as language *name*.

    Returns a flat list of ``(capture_name, node)`` pairs. Empty when the
    backend, grammar, or query is unavailable.
    """
    parser = get_parser(name)
    query = _get_query(name, query_source)
    if parser is None or query is None:
        return []
    try:
        tree = parser.parse(source)
        cursor = _tree_sitter.QueryCursor(query)
        captures: list[tuple[str, Any]] = []
        for _match_id, capture_map in cursor.matches(tree.root_node):
            for capture_name, nodes in capture_map.items():
                for node in nodes:
                    captures.append((capture_name, node))
        return captures
    except Exception:  # noqa: BLE001
        return []


def query_matches(name: str, source: bytes, query_source: str) -> list[dict[str, list[Any]]]:
    """Run *query_source* against *source*, preserving per-match grouping.

    Returns a list of ``{capture_name: [nodes]}`` dicts, one per match, so
    callers can correlate captures that belong to the same match (e.g. a
    definition node and its name). Empty when anything is unavailable.
    """
    parser = get_parser(name)
    query = _get_query(name, query_source)
    if parser is None or query is None:
        return []
    try:
        tree = parser.parse(source)
        cursor = _tree_sitter.QueryCursor(query)
        return [dict(capture_map) for _match_id, capture_map in cursor.matches(tree.root_node)]
    except Exception:  # noqa: BLE001
        return []


def process_source(source: str, language: str, **flags: bool) -> Any | None:
    """Call the language pack's high-level ``process`` for *language*.

    ``flags`` are forwarded to ``ProcessConfig`` (e.g. ``structure=True``).
    Returns the ``ProcessResult`` or ``None`` if unavailable / on error.
    """
    if not AVAILABLE:
        return None
    try:
        config = _pack.ProcessConfig(language=language, **flags)
        return _pack.process(source, config)
    except Exception:  # noqa: BLE001 - unsupported language or parse failure
        return None
