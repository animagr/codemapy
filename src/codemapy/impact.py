"""``--impact FILE`` and ``--changed``: what a change can break, and which tests to run.

Following codemap's blast radius, but transitive: every file that imports
the changed files, directly or through other files, grouped by distance.
The tests to run are the affected test files themselves plus the tests
recorded in ``tested_by`` for every changed or affected file, so a Go test
in the same package or a JVM test in a mirrored tree is included even though
it imports nothing.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

from codemapy.query import Artifacts


@dataclass(frozen=True)
class Impact:
    """Dependents by distance from the changed files, and the tests covering them."""

    sources: tuple[str, ...]
    distance: dict[str, int]
    tests: tuple[str, ...]

    def production_at(self, artifacts: Artifacts, distance: int) -> list[str]:
        return sorted(
            path
            for path, hops in self.distance.items()
            if hops == distance and not artifacts.modules.get(path, {}).get("is_test")
        )

    @property
    def max_distance(self) -> int:
        return max(self.distance.values(), default=0)


def impact(artifacts: Artifacts, sources: list[str]) -> Impact:
    """Breadth-first search over reverse import edges from *sources*."""
    importers: dict[str, set[str]] = {}
    for edge in artifacts.edges:
        if edge["source"] != edge["target"]:
            importers.setdefault(edge["target"], set()).add(edge["source"])
    distance: dict[str, int] = {}
    seen = set(sources)
    queue = deque((source, 0) for source in sources)
    while queue:
        path, hops = queue.popleft()
        for importer in sorted(importers.get(path, ())):
            if importer not in seen:
                seen.add(importer)
                distance[importer] = hops + 1
                queue.append((importer, hops + 1))
    tests: set[str] = set()
    for path in [*sources, *distance]:
        module = artifacts.modules.get(path, {})
        if module.get("is_test"):
            tests.add(path)
        tests.update(module.get("tested_by", []))
    return Impact(sources=tuple(sources), distance=distance, tests=tuple(sorted(tests)))


def format_impact(
    artifacts: Artifacts,
    result: Impact,
    limit: int,
    changed: frozenset[str],
    unmapped: list[str] | None = None,
) -> list[str]:
    """Lines for an impact result; *unmapped* lists changed files the map does not hold."""
    affected = [path for path in result.distance if not artifacts.modules.get(path, {}).get("is_test")]
    subject = result.sources[0] if len(result.sources) == 1 else f"{len(result.sources)} changed files"
    lines = [
        f"{subject}: {len(affected)} production files depend on it, and {len(result.tests)} tests cover the "
        f"changed or affected code {artifacts.source_note}"
    ]
    if len(result.sources) > 1:
        lines.append("Changed (mapped): " + ", ".join(result.sources[: limit or None]))
    if unmapped:
        lines.append(
            f"Changed but not in the map ({len(unmapped)}; new, or not mapped source): "
            + ", ".join(unmapped[: limit or None])
        )
    for hops in range(1, result.max_distance + 1):
        paths = result.production_at(artifacts, hops)
        if not paths:
            continue
        label = "Direct importers" if hops == 1 else f"Distance {hops}"
        lines.append(f"{label} ({len(paths)}):")
        lines.extend(_rows(paths, limit, changed))
    if result.tests:
        lines.append(f"Tests to run ({len(result.tests)}):")
        lines.extend(_rows(list(result.tests), limit, changed))
    else:
        lines.append("No test imports the affected files or is named after them.")
    return lines


def _rows(paths: list[str], limit: int, changed: frozenset[str]) -> list[str]:
    shown = paths[:limit] if limit else paths
    rows = [f"- {path}{' [changed]' if path in changed else ''}" for path in shown]
    if len(shown) < len(paths):
        rows.append(f"  ... and {len(paths) - len(shown)} more; raise --limit (0 for all).")
    return rows
