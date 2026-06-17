"""Symbol (definition) extraction for the agent-readable code map.

Python uses the stdlib ``ast`` so we get accurate signatures, docstrings, and
nesting. Every other tree-sitter-covered language uses dedicated definition
queries (see :data:`_SYMBOL_QUERIES`) which capture each definition node along
with its name; this is far more reliable than the language pack's high-level
structure output, which omits names for C/C++ and members for Ruby.
"""

from __future__ import annotations

import ast
import warnings
from pathlib import Path

from codemapy.models import FileNode, Symbol
from codemapy.ts import backend
from codemapy.ts.langmap import pack_name_for_ext

# Each pattern captures the name as @name and the whole definition node as a
# @def.<kind> capture. The kind is read from the capture name suffix.
_SYMBOL_QUERIES: dict[str, str] = {
    "javascript": """
        (function_declaration name: (identifier) @name) @def.function
        (generator_function_declaration name: (identifier) @name) @def.function
        (class_declaration name: (identifier) @name) @def.class
        (method_definition name: (property_identifier) @name) @def.method
        (variable_declarator
            name: (identifier) @name
            value: [(arrow_function) (function_expression)]) @def.function
    """,
    "typescript": """
        (function_declaration name: (identifier) @name) @def.function
        (class_declaration name: (type_identifier) @name) @def.class
        (abstract_class_declaration name: (type_identifier) @name) @def.class
        (interface_declaration name: (type_identifier) @name) @def.interface
        (enum_declaration name: (identifier) @name) @def.enum
        (type_alias_declaration name: (type_identifier) @name) @def.type
        (method_definition name: (property_identifier) @name) @def.method
        (method_signature name: (property_identifier) @name) @def.method
        (variable_declarator
            name: (identifier) @name
            value: [(arrow_function) (function_expression)]) @def.function
    """,
    "go": """
        (function_declaration name: (identifier) @name) @def.function
        (method_declaration name: (field_identifier) @name) @def.method
        (type_declaration (type_spec name: (type_identifier) @name)) @def.type
        (const_spec name: (identifier) @name) @def.constant
    """,
    "rust": """
        (function_item name: (identifier) @name) @def.function
        (struct_item name: (type_identifier) @name) @def.struct
        (enum_item name: (type_identifier) @name) @def.enum
        (trait_item name: (type_identifier) @name) @def.trait
        (impl_item type: (type_identifier) @name) @def.impl
        (mod_item name: (identifier) @name) @def.module
        (macro_definition name: (identifier) @name) @def.macro
    """,
    "c": """
        (function_definition
            declarator: (function_declarator declarator: (identifier) @name)) @def.function
        (struct_specifier name: (type_identifier) @name) @def.struct
        (enum_specifier name: (type_identifier) @name) @def.enum
        (type_definition declarator: (type_identifier) @name) @def.type
    """,
    "cpp": """
        (function_definition
            declarator: (function_declarator
                declarator: [(identifier) (field_identifier) (qualified_identifier)] @name)) @def.function
        (class_specifier name: (type_identifier) @name) @def.class
        (struct_specifier name: (type_identifier) @name) @def.struct
        (enum_specifier name: (type_identifier) @name) @def.enum
        (namespace_definition name: (namespace_identifier) @name) @def.namespace
    """,
    "java": """
        (class_declaration name: (identifier) @name) @def.class
        (interface_declaration name: (identifier) @name) @def.interface
        (enum_declaration name: (identifier) @name) @def.enum
        (method_declaration name: (identifier) @name) @def.method
        (constructor_declaration name: (identifier) @name) @def.method
    """,
    "csharp": """
        (class_declaration name: (identifier) @name) @def.class
        (interface_declaration name: (identifier) @name) @def.interface
        (struct_declaration name: (identifier) @name) @def.struct
        (record_declaration name: (identifier) @name) @def.record
        (enum_declaration name: (identifier) @name) @def.enum
        (delegate_declaration name: (identifier) @name) @def.delegate
        (method_declaration name: (identifier) @name) @def.method
        (constructor_declaration name: (identifier) @name) @def.method
    """,
    "ruby": """
        (method name: (identifier) @name) @def.method
        (singleton_method name: (identifier) @name) @def.method
        (class name: (constant) @name) @def.class
        (module name: (constant) @name) @def.module
    """,
    "lua": """
        (function_declaration
            name: [(identifier) (dot_index_expression) (method_index_expression)] @name) @def.function
    """,
    "bash": """
        (function_definition name: (word) @name) @def.function
    """,
}

# Grammars that reuse another grammar's query.
_QUERY_ALIASES = {"tsx": "typescript"}


def extract_symbols(file: FileNode) -> tuple[Symbol, ...]:
    """Return the top-level symbols defined in *file* (nested under children)."""
    if file.language == "Python":
        return _python_symbols(file.absolute_path)
    if not backend.AVAILABLE:
        return ()
    pack_name = pack_name_for_ext(file.extension)
    if pack_name is None:
        return ()
    query_source = _SYMBOL_QUERIES.get(_QUERY_ALIASES.get(pack_name, pack_name))
    if query_source is None:
        return ()
    return _query_symbols(file.absolute_path, pack_name, query_source)


def _query_symbols(path: Path, pack_name: str, query_source: str) -> tuple[Symbol, ...]:
    try:
        source = path.read_bytes()
    except OSError:
        return ()
    matches = backend.query_matches(pack_name, source, query_source)
    symbols: list[Symbol] = []
    for capture_map in matches:
        name_nodes = capture_map.get("name")
        if not name_nodes:
            continue
        name_node = name_nodes[0]
        def_node = name_node
        kind = "symbol"
        for capture_name, nodes in capture_map.items():
            if capture_name.startswith("def.") and nodes:
                kind = capture_name[len("def.") :]
                def_node = nodes[0]
                break
        symbols.append(
            Symbol(
                name=name_node.text.decode("utf-8", "ignore"),
                kind=kind,
                start_line=def_node.start_point[0] + 1,
                end_line=def_node.end_point[0] + 1,
            )
        )
    # Deterministic order, de-duplicated on (name, kind, line).
    unique = {(s.name, s.kind, s.start_line): s for s in symbols}
    return tuple(sorted(unique.values(), key=lambda s: (s.start_line, s.name)))


def _python_symbols(path: Path) -> tuple[Symbol, ...]:
    try:
        source = path.read_text(encoding="utf-8", errors="ignore")
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", SyntaxWarning)
            tree = ast.parse(source, filename=str(path))
    except (OSError, SyntaxError, ValueError):
        return ()
    return tuple(_python_node_symbol(node, is_method=False) for node in _def_nodes(tree.body))


def _def_nodes(body: list[ast.stmt]) -> list[ast.stmt]:
    return [n for n in body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))]


def _python_node_symbol(node: ast.stmt, *, is_method: bool) -> Symbol:
    if isinstance(node, ast.ClassDef):
        children = tuple(_python_node_symbol(child, is_method=True) for child in _def_nodes(node.body))
        return Symbol(
            name=node.name,
            kind="class",
            start_line=node.lineno,
            end_line=getattr(node, "end_lineno", node.lineno) or node.lineno,
            doc=ast.get_docstring(node),
            children=children,
        )

    # Function or async function.
    kind = "method" if is_method else "function"
    children = tuple(_python_node_symbol(child, is_method=False) for child in _def_nodes(node.body))  # type: ignore[arg-type]
    return Symbol(
        name=node.name,  # type: ignore[attr-defined]
        kind=kind,
        start_line=node.lineno,
        end_line=getattr(node, "end_lineno", node.lineno) or node.lineno,
        signature=_python_signature(node),
        doc=ast.get_docstring(node),  # type: ignore[arg-type]
        children=children,
    )


def _python_signature(node: ast.stmt) -> str | None:
    try:
        args = ast.unparse(node.args)  # type: ignore[attr-defined]
        prefix = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
        returns = getattr(node, "returns", None)
        suffix = f" -> {ast.unparse(returns)}" if returns is not None else ""
        return f"{prefix} {node.name}({args}){suffix}"  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001 - signature is best-effort
        return None
