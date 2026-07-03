from __future__ import annotations

from pathlib import Path

from .deps.registry import extractor_for_ext
from .deps.resolver import ImportResolver
from .deps.symbols import extract_symbols
from .models import Edge, ExternalImport, FileNode, ModuleNode, Report, ScanResult

# Edge kinds that express containment/declaration rather than a true dependency.
# A Rust `mod child;` declaration paired with the idiomatic `use super::*` /
# `use crate::...` in the child manufactures a 2-cycle for nearly every module,
# which cascades into one giant, meaningless strongly-connected component. These
# edges still count toward fan-in/fan-out, but are excluded from cycle analysis.
DECLARATION_EDGE_KINDS = frozenset({"rust-mod"})


def build_report(root: Path, files: ScanResult | tuple[FileNode, ...]) -> Report:
    scan = files if isinstance(files, ScanResult) else ScanResult(sources=tuple(files))
    files = scan.sources
    resolver = ImportResolver(root, files)
    imports_by_file = {}
    symbols_by_file = {}
    edges: list[Edge] = []
    external: list[ExternalImport] = []

    for file in files:
        extractor = extractor_for_ext(file.extension)
        imports = extractor.extract(file.absolute_path) if extractor else ()
        imports_by_file[file.path] = imports
        # Extracting symbols right after imports lets the tree-sitter backend
        # reuse the parse tree it just built for this file.
        symbols_by_file[file.path] = extract_symbols(file)
        for ref in imports:
            targets = resolver.resolve(file, ref)
            if targets:
                for target in targets:
                    edges.append(Edge(source=file.path, target=target, raw=ref.raw, kind=ref.kind))
            else:
                external.append(ExternalImport(source=file.path, raw=ref.raw, kind=ref.kind))

    # Fan-in / fan-out count distinct neighbours, not raw edges, so a file that
    # imports the same target on several lines is not over-counted.
    out_neighbours: dict[str, set[str]] = {file.path: set() for file in files}
    in_neighbours: dict[str, set[str]] = {file.path: set() for file in files}
    for edge in edges:
        if edge.source == edge.target:
            continue
        out_neighbours.setdefault(edge.source, set()).add(edge.target)
        in_neighbours.setdefault(edge.target, set()).add(edge.source)

    modules = tuple(
        ModuleNode(
            path=file.path,
            language=file.language,
            loc=file.loc,
            size=file.size,
            imports=imports_by_file[file.path],
            fan_in=len(in_neighbours.get(file.path, ())),
            fan_out=len(out_neighbours.get(file.path, ())),
            symbols=symbols_by_file[file.path],
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
        cycles=cycles,
        metadata_files=scan.metadata_files,
        doc_files=scan.doc_files,
        asset_groups=scan.asset_groups,
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
