"""``--where NAME`` and ``--outline FILE``: definitions without opening files.

Both read ``symbols.json``. ``--where`` finds exact definitions of a name
(``resolve``, or qualified ``ImportResolver.resolve``) with their location,
signature, bases, and the classes that extend them (codegraph's type
hierarchy, one level each way). ``--outline`` lists a file's definitions in
source order with line ranges and signatures.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from codemapy.query import Artifacts

MAX_DOC_CHARS = 100
# A base as written (`abc.ABC`, `ns::Widget`, `List<T>`, `Generic[T]`) -> `ABC`, `Widget`, `List`, `Generic`.
_GENERIC_START = re.compile(r"[<\[(]")
_SCOPE_SEPARATOR = re.compile(r"\.|::")


@dataclass(frozen=True)
class SymbolRecord:
    """One definition from symbols.json, flattened with its qualified name and depth."""

    path: str
    qualified: str
    name: str
    kind: str
    start: int
    end: int
    signature: str | None
    doc: str | None
    bases: tuple[str, ...]
    depth: int


def symbol_records(artifacts: Artifacts, path: str | None = None) -> list[SymbolRecord]:
    """Every definition (or *path*'s), in source order, nested ones after their parent."""
    paths = [path] if path is not None else sorted(artifacts.symbol_tree)
    records: list[SymbolRecord] = []
    for file_path in paths:
        _flatten(file_path, artifacts.symbol_tree.get(file_path, []), "", 0, records)
    return records


def where(artifacts: Artifacts, name: str) -> list[SymbolRecord]:
    """Definitions named *name*, or whose qualified name is or ends with it.

    Exact case first; if nothing matches, case-insensitive.
    """
    records = symbol_records(artifacts)
    for fold in (False, True):
        target = name.lower() if fold else name
        matches = [
            record
            for record in records
            if target in {_fold(record.name, fold), _fold(record.qualified, fold)}
            or _fold(record.qualified, fold).endswith(f".{target}")
        ]
        if matches:
            return matches
    return []


def subclasses(artifacts: Artifacts) -> dict[str, list[SymbolRecord]]:
    """Base simple name -> the definitions that extend or implement it."""
    found: dict[str, list[SymbolRecord]] = {}
    for record in symbol_records(artifacts):
        for base in record.bases:
            found.setdefault(base_name(base), []).append(record)
    return found


def base_name(base: str) -> str:
    """The plain type name in a base as written: ``abc.ABC`` -> ``ABC``, ``List<T>`` -> ``List``."""
    head = _GENERIC_START.split(base, maxsplit=1)[0].strip()
    return _SCOPE_SEPARATOR.split(head)[-1]


def format_where(
    artifacts: Artifacts, name: str, records: list[SymbolRecord], limit: int, changed: frozenset[str]
) -> list[str]:
    if not records:
        return [f'`{name}` is not defined in any mapped file {artifacts.source_note}. Try --find "{name}".']
    children = subclasses(artifacts)
    shown = records[:limit] if limit else records
    noun = "definition" if len(records) == 1 else "definitions"
    lines = [f"{len(records)} {noun} of `{name}` {artifacts.source_note}"]
    for record in shown:
        lines.append(
            f"- {record.path}:{record.start}-{record.end}{_changed(record.path, changed)} {_heading(record)}"
        )
        if record.bases:
            lines.append(f"  extends: {', '.join(record.bases)}")
        extenders = children.get(record.name, [])
        if extenders:
            listed = ", ".join(
                f"{child.qualified} ({child.path}:{child.start})" for child in extenders[: limit or None]
            )
            lines.append(f"  extended by: {listed}")
        if record.doc:
            lines.append(f"  doc: {_first_line(record.doc)}")
    if len(shown) < len(records):
        lines.append(f"Showing {len(shown)} of {len(records)}; raise --limit (0 for all).")
    return lines


def format_outline(
    artifacts: Artifacts, path: str, records: list[SymbolRecord], limit: int, changed: frozenset[str]
) -> list[str]:
    module = artifacts.modules[path]
    header = (
        f"{path}{_changed(path, changed)}: {module.get('language')}, {module.get('loc')} loc, "
        f"{len(records)} definitions {artifacts.source_note}"
    )
    if not records:
        return [
            header,
            "No definitions extracted (see Coverage in summary.md for which languages have symbols).",
        ]
    shown = records[:limit] if limit else records
    lines = [header]
    for record in shown:
        doc = f"  # {_first_line(record.doc)}" if record.doc else ""
        lines.append(f"{'  ' * record.depth}L{record.start}-{record.end} {_heading(record)}{doc}")
    if len(shown) < len(records):
        lines.append(f"Showing {len(shown)} of {len(records)} definitions; raise --limit (0 for all).")
    return lines


def _heading(record: SymbolRecord) -> str:
    if record.signature:
        return record.signature
    bases = f"({', '.join(record.bases)})" if record.bases else ""
    return f"{record.kind} {record.qualified if record.depth == 0 else record.name}{bases}"


def _flatten(
    path: str, entries: list[dict[str, object]], prefix: str, depth: int, out: list[SymbolRecord]
) -> None:
    for entry in entries:
        name = str(entry.get("name", ""))
        qualified = f"{prefix}.{name}" if prefix else name
        out.append(
            SymbolRecord(
                path=path,
                qualified=qualified,
                name=name,
                kind=str(entry.get("kind", "")),
                start=int(entry.get("start_line", 0)),
                end=int(entry.get("end_line", 0)),
                signature=entry.get("signature"),
                doc=entry.get("doc"),
                bases=tuple(entry.get("bases", ())),
                depth=depth,
            )
        )
        _flatten(path, list(entry.get("children", [])), qualified, depth + 1, out)


def _first_line(text: str) -> str:
    line = text.strip().splitlines()[0] if text.strip() else ""
    return line if len(line) <= MAX_DOC_CHARS else line[: MAX_DOC_CHARS - 3] + "..."


def _fold(text: str, fold: bool) -> str:
    return text.lower() if fold else text


def _changed(path: str, changed: frozenset[str]) -> str:
    return " [changed]" if path in changed else ""
