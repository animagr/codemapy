"""State what a map can and cannot see, so its zeros are not read as facts.

codemap's output standard asks every claim to state its bounds. A file in a
language without import analysis has fan-in 0 because nobody looked, not
because nothing uses it; a skipped folder's files are absent, not deleted.
This module reports, per language, whether imports become edges and how
symbols are extracted, plus what the scan skipped and why.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from codemapy.deps.registry import extractor_for_ext
from codemapy.deps.symbols import symbol_source_for_ext
from codemapy.models import Report, SkippedPath
from codemapy.scanner import gitignore_matching
from codemapy.ts import backend


@dataclass(frozen=True)
class LanguageCoverage:
    """What codemapy analyses for one language in this scan.

    ``imports`` is True when the language's imports are resolved to edges;
    ``symbols`` is "ast", "tree-sitter", or None.
    """

    language: str
    files: int
    imports: bool
    symbols: str | None


def language_coverage(report: Report) -> list[LanguageCoverage]:
    """One row per scanned language, most files first."""
    files: Counter[str] = Counter()
    imports: dict[str, bool] = {}
    symbols: dict[str, str | None] = {}
    for file in report.files:
        language = file.language or "Other"
        files[language] += 1
        imports[language] = imports.get(language, False) or extractor_for_ext(file.extension) is not None
        symbols[language] = symbols.get(language) or symbol_source_for_ext(file.extension)
    return [
        LanguageCoverage(language=language, files=count, imports=imports[language], symbols=symbols[language])
        for language, count in sorted(files.items(), key=lambda item: (-item[1], item[0]))
    ]


def skipped_counts(skipped: tuple[SkippedPath, ...]) -> dict[str, dict[str, int]]:
    """Skipped folders and source files, counted by reason."""
    dirs = Counter(item.reason.value for item in skipped if item.is_dir)
    files = Counter(item.reason.value for item in skipped if not item.is_dir)
    return {"dirs": dict(sorted(dirs.items())), "files": dict(sorted(files.items()))}


def coverage_payload(report: Report) -> dict[str, object]:
    """The machine-readable coverage block for context.json."""
    return {
        "languages": [
            {"language": row.language, "files": row.files, "imports": row.imports, "symbols": row.symbols}
            for row in language_coverage(report)
        ],
        "unresolved_imports": len(report.unresolved_imports),
        "skipped": skipped_counts(report.skipped),
        "tree_sitter": backend.AVAILABLE,
        "gitignore": gitignore_matching(),
    }


def coverage_lines(report: Report) -> list[str]:
    """The Markdown body of summary.md's Coverage section."""
    rows = language_coverage(report)
    if not rows:
        return ["- No source files were mapped"]
    lines = [
        "| Language | Files | Imports become edges | Symbols |",
        "|---|---|---|---|",
    ]
    lines.extend(
        f"| {row.language} | {row.files} | {'yes' if row.imports else 'no'} | {row.symbols or 'no'} |"
        for row in rows
    )
    lines.append("")
    blind = [row.language for row in rows if not row.imports]
    if blind:
        lines.append(
            f"- Fan-in and fan-out are 0 for {_join(blind)} files because their imports are not analysed, "
            "not because nothing uses them."
        )
    lines.append(
        f"- Unresolved internal imports: {len(report.unresolved_imports)}"
        + (" (see Unresolved Internal Imports below)" if report.unresolved_imports else "")
    )
    lines.append(f"- Skipped: {_skipped_text(report.skipped)}")
    backend_state = (
        "available" if backend.AVAILABLE else "unavailable (symbols for Python only, imports by regex)"
    )
    lines.append(f"- Tree-sitter backend: {backend_state}. Gitignore matching: {gitignore_matching()}.")
    return lines


def _skipped_text(skipped: tuple[SkippedPath, ...]) -> str:
    counts = skipped_counts(skipped)
    parts = []
    for key, noun in (("dirs", "folder"), ("files", "source file")):
        total = sum(counts[key].values())
        if total:
            reasons = ", ".join(f"{reason} {count}" for reason, count in counts[key].items())
            parts.append(f"{total} {noun}{'s' if total != 1 else ''} ({reasons})")
    return " and ".join(parts) if parts else "nothing"


def _join(names: list[str]) -> str:
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + f" and {names[-1]}"
