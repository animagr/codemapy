"""Answer questions from an existing .codemapy folder, without rescanning.

On a large repo the artifacts are megabytes (codegraph: 4.3 MB symbols.json,
2 MB context.json), too much for an agent to read whole. These queries
return a few lines instead:

- ``find``: rank files against search words, matching path and symbol-name
  tokens with BM25, following codemap's ``find`` (``codemap/find/find.go``).
- ``importers``: the files that import a given file.
- ``tests``: the tests that cover a given file directly.

``symbol_queries`` (``--where``, ``--outline``), ``impact`` (``--impact``,
``--changed``) and ``why`` (``--why``) build on the same ``Artifacts``.
"""

from __future__ import annotations

import json
import math
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from codemapy.artifacts import ARTIFACT_DIR_NAME

BM25_K1 = 1.2
BM25_B = 0.75
# Added when a query word is a file's whole stem or a whole symbol name.
EXACT_MATCH_BONUS = 1.0
# Tests and generated files are still searchable, but rank below real code.
DEMOTED_SCORE_FACTOR = 0.5
MIN_TOKEN_LENGTH = 2
MAX_MATCHED_NAMES = 5
# A file needs this many importers outside the test suite to be called a hub.
HUB_MIN_IMPORTERS = 3

_WORD = re.compile(r"[A-Za-z0-9]+")
_CAMEL_PART = re.compile(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|\d+")


class ArtifactsMissing(Exception):
    """Raised when a project has no readable .codemapy folder."""


class UnknownPath(Exception):
    """Raised when a file argument matches no mapped file, or several."""

    def __init__(self, message: str, candidates: list[str]) -> None:
        super().__init__(message)
        self.candidates = candidates


@dataclass(frozen=True)
class Artifacts:
    """The parts of a .codemapy folder that queries read."""

    root: Path
    modules: dict[str, dict[str, object]]
    edges: list[dict[str, str]]
    symbol_names: dict[str, list[str]]
    git_commit: str | None
    generated_at: str | None
    symbol_tree: dict[str, list[dict[str, object]]] = field(default_factory=dict)
    skipped: list[dict[str, object]] = field(default_factory=list)
    doc_files: frozenset[str] = frozenset()
    metadata_files: frozenset[str] = frozenset()

    @property
    def source_note(self) -> str:
        commit = f"commit {self.git_commit[:12]}" if self.git_commit else "no git commit"
        return f"from {ARTIFACT_DIR_NAME}/ ({commit}, generated {str(self.generated_at)[:19]})"


@dataclass(frozen=True)
class FindHit:
    path: str
    score: float
    matched: tuple[str, ...]
    importers: int
    test_importers: int
    is_test: bool

    @property
    def is_hub(self) -> bool:
        return self.importers - self.test_importers >= HUB_MIN_IMPORTERS


def load_artifacts(root: Path) -> Artifacts:
    """Read context.json, symbols.json and manifest.json from *root*'s .codemapy folder.

    Raises:
        ArtifactsMissing: if context.json is absent or unreadable.
    """
    folder = root / ARTIFACT_DIR_NAME
    try:
        context = json.loads((folder / "context.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ArtifactsMissing(f"No readable artifacts in {folder} (generate them with --artifacts)") from exc
    symbols = _read_optional_json(folder / "symbols.json")
    manifest = _read_optional_json(folder / "manifest.json")
    return Artifacts(
        root=root,
        modules={module["path"]: module for module in context.get("modules", [])},
        edges=context.get("edges", []),
        symbol_names={path: _flatten_names(entries) for path, entries in symbols.get("files", {}).items()},
        git_commit=manifest.get("git_commit"),
        generated_at=manifest.get("generated_at"),
        symbol_tree=symbols.get("files", {}),
        skipped=context.get("skipped", []),
        doc_files=frozenset(item["path"] for item in context.get("doc_files", [])),
        metadata_files=frozenset(item["path"] for item in context.get("metadata_files", [])),
    )


def tokenize(text: str) -> list[str]:
    """Split paths and identifiers into lowercase words: camelCase, snake_case, a/b.c."""
    tokens = []
    for word in _WORD.findall(text):
        tokens.extend(part.lower() for part in _CAMEL_PART.findall(word))
    return [token for token in tokens if len(token) >= MIN_TOKEN_LENGTH]


def find(artifacts: Artifacts, query: str) -> list[FindHit]:
    """Rank mapped files against *query*, best first; files with no match are left out."""
    words = {word.lower() for word in _WORD.findall(query)}
    terms = list(dict.fromkeys(tokenize(query)))
    if not terms:
        return []
    documents = {
        path: Counter(tokenize(path) + [token for name in names for token in tokenize(name)])
        for path, names in ((path, artifacts.symbol_names.get(path, [])) for path in artifacts.modules)
    }
    average_length = sum(sum(doc.values()) for doc in documents.values()) / max(len(documents), 1)
    frequency = Counter(term for doc in documents.values() for term in set(doc) if term in terms)
    hits = []
    for path, doc in documents.items():
        score = _bm25(doc, terms, frequency, len(documents), average_length)
        if score <= 0:
            continue
        names = artifacts.symbol_names.get(path, [])
        stem = Path(path).stem.lower()
        score += EXACT_MATCH_BONUS * sum(
            1 for word in words if word == stem or any(word == name.lower() for name in names)
        )
        module = artifacts.modules[path]
        if module.get("is_test") or module.get("is_generated"):
            score *= DEMOTED_SCORE_FACTOR
        matched = tuple(name for name in names if set(tokenize(name)) & set(terms))[:MAX_MATCHED_NAMES]
        hits.append(
            FindHit(
                path=path,
                score=round(score, 3),
                matched=matched,
                importers=int(module.get("fan_in", 0)),
                test_importers=int(module.get("test_fan_in", 0)),
                is_test=bool(module.get("is_test")),
            )
        )
    return sorted(hits, key=lambda hit: (-hit.score, hit.path))


def resolve_path(artifacts: Artifacts, raw: str, cwd: Path) -> str:
    """Map a file argument to a mapped module path.

    Accepts the project-relative path, a path relative to *cwd* or absolute,
    or a unique trailing part such as ``queries.ts`` or ``db/queries.ts``.

    Raises:
        UnknownPath: if nothing matches, or the trailing part is ambiguous.
    """
    normalized = raw.replace("\\", "/").strip("/")
    if normalized in artifacts.modules:
        return normalized
    try:
        relative = (cwd / raw).resolve().relative_to(artifacts.root.resolve()).as_posix()
    except (OSError, ValueError):
        relative = None
    if relative in artifacts.modules:
        return relative
    lowered = normalized.lower()
    matches = sorted(
        path for path in artifacts.modules if path.lower() == lowered or path.lower().endswith(f"/{lowered}")
    )
    if len(matches) == 1:
        return matches[0]
    if matches:
        raise UnknownPath(f"`{raw}` matches {len(matches)} mapped files; give more of the path", matches)
    stem = Path(normalized).stem.lower()
    similar = sorted(path for path in artifacts.modules if stem and stem in Path(path).stem.lower())
    raise UnknownPath(f"`{raw}` is not a mapped source file", similar)


def importers_of(artifacts: Artifacts, path: str) -> list[tuple[str, str]]:
    """``(importer, import text)`` pairs for *path*, production importers first."""
    pairs = {(edge["source"], edge["raw"]) for edge in artifacts.edges if edge["target"] == path}
    pairs = {pair for pair in pairs if pair[0] != path}
    return sorted(pairs, key=lambda pair: (_is_test(artifacts, pair[0]), pair[0], pair[1]))


def tests_of(artifacts: Artifacts, path: str) -> list[str] | None:
    """Tests covering *path* directly, or None if the artifacts predate ``tested_by``."""
    module = artifacts.modules[path]
    if "tested_by" not in module:
        return None
    return list(module["tested_by"])


def _is_test(artifacts: Artifacts, path: str) -> bool:
    return bool(artifacts.modules.get(path, {}).get("is_test"))


def _bm25(doc: Counter[str], terms: list[str], frequency: Counter[str], total: int, average: float) -> float:
    length = sum(doc.values())
    score = 0.0
    for term in terms:
        count = doc.get(term, 0)
        if not count:
            continue
        idf = math.log(1 + (total - frequency[term] + 0.5) / (frequency[term] + 0.5))
        norm = count + BM25_K1 * (1 - BM25_B + BM25_B * length / max(average, 1e-9))
        score += idf * count * (BM25_K1 + 1) / norm
    return score


def _flatten_names(entries: list[dict[str, object]]) -> list[str]:
    names: list[str] = []
    for entry in entries:
        names.append(str(entry.get("name", "")))
        names.extend(_flatten_names(list(entry.get("children", []))))
    return [name for name in names if name]


def _read_optional_json(path: Path) -> dict[str, object]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


FIND_DEFAULT_LIMIT = 10
LIST_DEFAULT_LIMIT = 50


def format_find(artifacts: Artifacts, query: str, hits: list[FindHit], limit: int) -> list[str]:
    """Human-readable lines for a ``find`` result, stating source and bounds."""
    shown = hits[:limit] if limit else hits
    lines = [f'{len(hits)} files match "{query}" in {artifacts.root} {artifacts.source_note}']
    for rank, hit in enumerate(shown, start=1):
        details = [f"score {hit.score:.2f}"]
        if hit.matched:
            details.append(", ".join(hit.matched))
        details.append(_importer_text(hit.importers, hit.test_importers))
        if hit.is_hub:
            details.append(f"hub ({HUB_MIN_IMPORTERS}+ importers outside tests)")
        if hit.is_test:
            details.append("test file")
        lines.append(f"{rank}. {hit.path} | " + " | ".join(details))
    if len(shown) < len(hits):
        lines.append(f"Showing {len(shown)} of {len(hits)}; raise --limit (0 for all) to see more.")
    lines.append(
        "Ranked by BM25 over path and symbol-name words; an exact file stem or symbol name adds "
        f"{EXACT_MATCH_BONUS:g}; tests and generated files score x{DEMOTED_SCORE_FACTOR:g}."
    )
    return lines


def format_importers(artifacts: Artifacts, path: str, pairs: list[tuple[str, str]], limit: int) -> list[str]:
    sources = sorted({source for source, _ in pairs})
    tests = sum(1 for source in sources if _is_test(artifacts, source))
    lines = [
        f"{path}: {len(sources)} importers ({tests} tests) {artifacts.source_note}",
    ]
    if not pairs:
        lines.append(
            "Nothing imports it. If its language has no import analysis, see Coverage in summary.md."
        )
        return lines
    shown = pairs[:limit] if limit else pairs
    lines.extend(
        f"- {source} ({'test' if _is_test(artifacts, source) else 'production'}, via `{raw}`)"
        for source, raw in shown
    )
    if len(shown) < len(pairs):
        lines.append(f"Showing {len(shown)} of {len(pairs)} import lines; raise --limit (0 for all).")
    return lines


def format_tests(artifacts: Artifacts, path: str, tests: list[str] | None, limit: int) -> list[str]:
    if tests is None:
        return [f"{path}: these artifacts predate test coverage; regenerate them with --artifacts --yes."]
    if artifacts.modules[path].get("is_test"):
        return [f"{path} is itself a test file {artifacts.source_note}"]
    if not tests:
        return [
            f"{path}: no test imports it or is named after it {artifacts.source_note}. "
            "It may still be exercised through the modules that import it (see --importers)."
        ]
    shown = tests[:limit] if limit else tests
    lines = [
        f"{path}: {len(tests)} tests cover it directly (import it or are named after it) {artifacts.source_note}"
    ]
    lines.extend(f"- {test}" for test in shown)
    if len(shown) < len(tests):
        lines.append(f"Showing {len(shown)} of {len(tests)}; raise --limit (0 for all).")
    return lines


def _importer_text(importers: int, tests: int) -> str:
    noun = "importer" if importers == 1 else "importers"
    return f"{importers} {noun} ({tests} tests)" if tests else f"{importers} {noun}"
