from __future__ import annotations

import posixpath
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from .deps.registry import extractor_for_ext
from .deps.resolver import ImportResolver
from .deps.symbols import extract_module_facts
from .models import (
    Edge,
    ExternalImport,
    FileNode,
    ImportRef,
    ModuleNode,
    Report,
    ScanResult,
    UnresolvedImport,
)
from .testfiles import is_test_file, subject_candidates
from .unresolved import UnresolvedClassifier, unresolved_warnings

# Edge kinds left out of cycle detection. They still count toward fan-in and
# fan-out.
# - `rust-mod`: a `mod child;` declaration paired with the idiomatic
#   `use super::*` / `use crate::...` in the child manufactures a 2-cycle for
#   nearly every module, which cascades into one giant, meaningless
#   strongly-connected component.
# - `java-package`: classes of one Java package use each other without
#   imports, and mutual references inside a package are normal, not a design
#   cycle. Cross-package import cycles are still found.
CYCLE_EXEMPT_EDGE_KINDS = frozenset({"rust-mod", "java-package"})

# Speculative reference kinds. When they resolve they are real edges; when
# they don't they are dropped instead of being recorded as external:
# - `gdscript-ident`: PascalCase identifiers that might name a `class_name`
#   class or an autoload, usually engine builtins when they don't resolve.
# - `java-package`: a file's own package, linked to the same-package classes
#   its source mentions; nothing mentioned means nothing to report.
DROP_IF_UNRESOLVED_KINDS = frozenset({"gdscript-ident", "java-package"})


def build_report(root: Path, files: ScanResult | tuple[FileNode, ...]) -> Report:
    scan = files if isinstance(files, ScanResult) else ScanResult(sources=tuple(files))
    files = scan.sources
    resolver = ImportResolver(root, files)
    classifier = UnresolvedClassifier(
        root, files, scan.skipped, go_modules=resolver.go_modules, java_packages=resolver.java_packages
    )
    imports_by_file: dict[str, tuple[ImportRef, ...]] = {}
    facts_by_file = {}
    edges: list[Edge] = []
    external: list[ExternalImport] = []
    unresolved: list[UnresolvedImport] = []

    for file in files:
        extractor = extractor_for_ext(file.extension)
        imports = extractor.extract(file.absolute_path) if extractor else ()
        # Parsing for symbols right after imports lets the tree-sitter backend
        # reuse the parse tree it just built for this file.
        facts_by_file[file.path] = extract_module_facts(file)
        kept_refs = []
        for ref in imports:
            targets = resolver.resolve(file, ref)
            if targets:
                kept_refs.append(ref)
                for target in targets:
                    edges.append(Edge(source=file.path, target=target, raw=ref.raw, kind=ref.kind))
            elif ref.kind in DROP_IF_UNRESOLVED_KINDS:
                continue
            elif resolver.resolved_to_self:
                kept_refs.append(ref)
            else:
                kept_refs.append(ref)
                miss = classifier.classify(file, ref, resolver.last_candidates)
                if miss is not None:
                    unresolved.append(miss)
                else:
                    external.append(ExternalImport(source=file.path, raw=ref.raw, kind=ref.kind))
        imports_by_file[file.path] = tuple(kept_refs)

    # Fan-in / fan-out count distinct neighbours, not raw edges, so a file that
    # imports the same target on several lines is not over-counted.
    out_neighbours: dict[str, set[str]] = {file.path: set() for file in files}
    in_neighbours: dict[str, set[str]] = {file.path: set() for file in files}
    for edge in edges:
        if edge.source == edge.target:
            continue
        out_neighbours.setdefault(edge.source, set()).add(edge.target)
        in_neighbours.setdefault(edge.target, set()).add(edge.source)

    test_paths = {file.path for file in files if is_test_file(file.path, imports_by_file[file.path])}
    tested_by = _tested_by(edges, test_paths, {file.path for file in files})
    modules = tuple(
        ModuleNode(
            path=file.path,
            language=file.language,
            loc=file.loc,
            size=file.size,
            imports=imports_by_file[file.path],
            fan_in=len(in_neighbours.get(file.path, ())),
            fan_out=len(out_neighbours.get(file.path, ())),
            symbols=facts_by_file[file.path].symbols,
            has_main_guard=facts_by_file[file.path].has_main_guard,
            is_test=file.path in test_paths,
            is_generated=file.is_generated,
            test_fan_in=len(in_neighbours.get(file.path, set()) & test_paths),
            tested_by=tuple(sorted(tested_by.get(file.path, ()))),
        )
        for file in files
    )

    cycles = find_cycles(edges)

    return Report(
        root=root.resolve(),
        files=files,
        modules=modules,
        edges=tuple(edges),
        external_imports=tuple(external),
        warnings=tuple(unresolved_warnings(unresolved)),
        cycles=cycles,
        metadata_files=scan.metadata_files,
        doc_files=scan.doc_files,
        asset_groups=scan.asset_groups,
        unresolved_imports=tuple(unresolved),
        skipped=scan.skipped,
    )


def _tested_by(edges: list[Edge], test_paths: set[str], paths: set[str]) -> dict[str, set[str]]:
    """Map each non-test file to the tests that cover it directly.

    A test covers a file it imports, and the file it is named after
    (``subject_candidates``): Go tests in the same package and JVM tests in a
    mirrored tree never import their subject.
    """
    tested_by: dict[str, set[str]] = {}
    for edge in edges:
        if edge.source in test_paths and edge.target not in test_paths and edge.source != edge.target:
            tested_by.setdefault(edge.target, set()).add(edge.source)
    for test in test_paths:
        for subject in subject_candidates(test):
            if subject in paths and subject not in test_paths:
                tested_by.setdefault(subject, set()).add(test)
                break
    return tested_by


def hub_rank_key(module: ModuleNode) -> tuple[int, int, int, int, str]:
    """Sort key for hubs: production importers first, then all importers, fan-out, size."""
    return (-module.non_test_fan_in, -module.fan_in, -module.fan_out, -module.loc, module.path)


def production_hubs(modules: Iterable[ModuleNode]) -> list[ModuleNode]:
    """Connected modules that are neither tests nor generated, best hubs first.

    Tests are left out because they rank by what they import, not by what
    depends on them, and generated stubs because nobody edits them.
    """
    return sorted(
        (
            module
            for module in modules
            if (module.fan_in or module.fan_out) and not module.is_test and not module.is_generated
        ),
        key=hub_rank_key,
    )


def find_cycles(edges: tuple[Edge, ...] | list[Edge]) -> tuple[tuple[str, ...], ...]:
    """Return circular dependency groups via Tarjan's SCC algorithm.

    Each returned tuple is the sorted set of file paths that form a strongly
    connected component of size > 1 (mutually reachable), plus any direct
    self-imports. The result is sorted for deterministic output.
    """
    adjacency: dict[str, set[str]] = {}
    self_loops: set[str] = set()
    for edge in edges:
        if edge.kind in CYCLE_EXEMPT_EDGE_KINDS:
            continue
        if edge.source == edge.target:
            self_loops.add(edge.source)
            continue
        adjacency.setdefault(edge.source, set()).add(edge.target)
        adjacency.setdefault(edge.target, set())

    cycles = [tuple(sorted(scc)) for scc in _strongly_connected_components(adjacency) if len(scc) > 1]
    cycles.extend((node,) for node in self_loops)
    return tuple(sorted(cycles, key=lambda group: (-len(group), group)))


def _strongly_connected_components(adjacency: dict[str, set[str]]) -> list[list[str]]:
    """Iterative Tarjan's algorithm (recursion-free for large graphs)."""
    index_of: dict[str, int] = {}
    low_link: dict[str, int] = {}
    on_stack: set[str] = set()
    stack: list[str] = []
    components: list[list[str]] = []
    counter = 0

    for start in adjacency:
        if start in index_of:
            continue
        # work_stack holds (node, iterator over its successors)
        work_stack: list[tuple[str, list[str]]] = [(start, sorted(adjacency[start]))]
        index_of[start] = low_link[start] = counter
        counter += 1
        stack.append(start)
        on_stack.add(start)

        while work_stack:
            node, successors = work_stack[-1]
            advanced = False
            while successors:
                successor = successors.pop(0)
                if successor not in index_of:
                    index_of[successor] = low_link[successor] = counter
                    counter += 1
                    stack.append(successor)
                    on_stack.add(successor)
                    work_stack.append((successor, sorted(adjacency.get(successor, ()))))
                    advanced = True
                    break
                if successor in on_stack:
                    low_link[node] = min(low_link[node], index_of[successor])
            if advanced:
                continue

            # All successors processed; finalise this node.
            work_stack.pop()
            if work_stack:
                parent = work_stack[-1][0]
                low_link[parent] = min(low_link[parent], low_link[node])
            if low_link[node] == index_of[node]:
                component: list[str] = []
                while True:
                    member = stack.pop()
                    on_stack.discard(member)
                    component.append(member)
                    if member == node:
                        break
                components.append(component)

    return components


# Languages whose imports name a package folder rather than a file. Every
# file of such a package gets the same importers, so hub lists show the
# package once instead of each of its files.
PACKAGE_LANGUAGES = frozenset({"Go"})


@dataclass(frozen=True)
class HubEntry:
    """One row of a hub list: a single file, or a whole package folder.

    ``path`` is set for a single file; a package row has ``label`` ending in
    "/" and counts importers from outside the package only. ``tested`` says
    whether a test covers the file (or any file of the package) directly.
    """

    label: str
    path: str | None
    files: int
    fan_in: int
    test_fan_in: int
    fan_out: int
    loc: int
    tested: bool = False

    @property
    def non_test_fan_in(self) -> int:
        return self.fan_in - self.test_fan_in


def hub_entries(report: Report) -> list[HubEntry]:
    """Production hubs, best first, with package-language files grouped by folder."""
    candidates = production_hubs(report.modules)
    entries = [
        HubEntry(
            label=module.path,
            path=module.path,
            files=1,
            fan_in=module.fan_in,
            test_fan_in=module.test_fan_in,
            fan_out=module.fan_out,
            loc=module.loc,
            tested=bool(module.tested_by),
        )
        for module in candidates
        if module.language not in PACKAGE_LANGUAGES
    ]
    entries.extend(_package_entries(report, [m for m in candidates if m.language in PACKAGE_LANGUAGES]))
    return sorted(
        entries,
        key=lambda entry: (-entry.non_test_fan_in, -entry.fan_in, -entry.fan_out, -entry.loc, entry.label),
    )


def _package_entries(report: Report, modules: list[ModuleNode]) -> list[HubEntry]:
    folder_of = {module.path: posixpath.dirname(module.path) for module in modules}
    if not folder_of:
        return []
    test_paths = {module.path for module in report.modules if module.is_test}
    package_paths = {module.path for module in report.modules if module.language in PACKAGE_LANGUAGES}
    importers: dict[str, set[str]] = {}
    imported: dict[str, set[str]] = {}
    for edge in report.edges:
        target_folder = folder_of.get(edge.target)
        if target_folder is not None and posixpath.dirname(edge.source) != target_folder:
            importers.setdefault(target_folder, set()).add(edge.source)
        source_folder = folder_of.get(edge.source)
        if source_folder is not None and posixpath.dirname(edge.target) != source_folder:
            # An imported package counts once, however many files it has.
            target = posixpath.dirname(edge.target) + "/" if edge.target in package_paths else edge.target
            imported.setdefault(source_folder, set()).add(target)
    members: dict[str, list[ModuleNode]] = {}
    for module in modules:
        members.setdefault(folder_of[module.path], []).append(module)
    entries = []
    for folder, group in members.items():
        sources = importers.get(folder, set())
        entries.append(
            HubEntry(
                label=f"{folder}/" if folder else "./",
                path=group[0].path if len(group) == 1 else None,
                files=len(group),
                fan_in=len(sources),
                test_fan_in=len(sources & test_paths),
                fan_out=len(imported.get(folder, set())),
                loc=sum(module.loc for module in group),
                tested=any(module.tested_by for module in group),
            )
        )
    return entries
