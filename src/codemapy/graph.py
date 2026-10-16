from __future__ import annotations

from collections.abc import Iterable
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
from .testfiles import is_test_file
from .unresolved import UnresolvedClassifier, unresolved_warnings

# Edge kinds that express containment/declaration rather than a true dependency.
# A Rust `mod child;` declaration paired with the idiomatic `use super::*` /
# `use crate::...` in the child manufactures a 2-cycle for nearly every module,
# which cascades into one giant, meaningless strongly-connected component. These
# edges still count toward fan-in/fan-out, but are excluded from cycle analysis.
DECLARATION_EDGE_KINDS = frozenset({"rust-mod"})

# Speculative reference kinds (e.g. GDScript PascalCase identifiers that might
# name a `class_name` class or an autoload). When they resolve they are real
# edges; when they don't they are usually engine builtins, so they are dropped
# instead of being recorded as external references.
DROP_IF_UNRESOLVED_KINDS = frozenset({"gdscript-ident"})


def build_report(root: Path, files: ScanResult | tuple[FileNode, ...]) -> Report:
    scan = files if isinstance(files, ScanResult) else ScanResult(sources=tuple(files))
    files = scan.sources
    resolver = ImportResolver(root, files)
    classifier = UnresolvedClassifier(root, files, scan.skipped)
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
    )


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
        if edge.kind in DECLARATION_EDGE_KINDS:
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
