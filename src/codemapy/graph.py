from __future__ import annotations

from pathlib import Path

from .deps.registry import extractor_for_ext
from .deps.resolver import ImportResolver
from .models import Edge, ExternalImport, FileNode, ModuleNode, Report


def build_report(root: Path, files: tuple[FileNode, ...]) -> Report:
    resolver = ImportResolver(root, files)
    imports_by_file = {}
    edges: list[Edge] = []
    external: list[ExternalImport] = []

    for file in files:
        extractor = extractor_for_ext(file.extension)
        imports = extractor.extract(file.absolute_path) if extractor else ()
        imports_by_file[file.path] = imports
        for ref in imports:
            target = resolver.resolve(file, ref)
            if target:
                edges.append(Edge(source=file.path, target=target, raw=ref.raw, kind=ref.kind))
            else:
                external.append(ExternalImport(source=file.path, raw=ref.raw, kind=ref.kind))

    fan_in = {file.path: 0 for file in files}
    fan_out = {file.path: 0 for file in files}
    for edge in edges:
        fan_out[edge.source] = fan_out.get(edge.source, 0) + 1
        fan_in[edge.target] = fan_in.get(edge.target, 0) + 1

    modules = tuple(
        ModuleNode(
            path=file.path,
            language=file.language,
            loc=file.loc,
            size=file.size,
            imports=imports_by_file[file.path],
            fan_in=fan_in[file.path],
            fan_out=fan_out[file.path],
        )
        for file in files
    )

    return Report(root=root.resolve(), files=files, modules=modules, edges=tuple(edges), external_imports=tuple(external))
