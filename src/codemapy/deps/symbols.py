"""Per-file parse results for the agent-readable code map.

Python uses the stdlib ``ast`` so we get accurate signatures, docstrings, and
nesting. Every other tree-sitter-covered language uses dedicated definition
queries (see :data:`_SYMBOL_QUERIES`) which capture each definition node along
with its name; this is far more reliable than the language pack's high-level
structure output, which omits names for C/C++ and members for Ruby.

The Python path also reports whether the file has a top-level ``__main__``
guard, which it can do for free off the tree it already parsed.
"""

from __future__ import annotations

import ast
import warnings
from dataclasses import dataclass, field
from pathlib import Path

from codemapy.models import FileNode, ModuleFacts, Symbol
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
    "gdscript": """
        (class_name_statement (name) @name) @def.class
        (class_definition name: (name) @name) @def.class
        (function_definition name: (name) @name) @def.function
        (signal_statement name: (name) @name) @def.signal
        (enum_definition name: (name) @name) @def.enum
    """,
    "lua": """
        (function_declaration
            name: [(identifier) (dot_index_expression) (method_index_expression)] @name) @def.function
    """,
    "bash": """
        (function_definition name: (word) @name) @def.function
    """,
    # The pack's verilog grammar (SystemVerilog) exposes no named fields, so
    # names are matched structurally. Functions and tasks declared inside a
    # module nest under it by span containment (e.g. `counter.next_value`).
    "verilog": """
        (module_declaration (module_header (simple_identifier) @name)) @def.module
        (function_declaration (function_body_declaration (function_identifier) @name)) @def.function
        (task_declaration (task_body_declaration (task_identifier) @name)) @def.task
    """,
}

# Grammars that reuse another grammar's query.
_QUERY_ALIASES = {"tsx": "typescript"}


def extract_module_facts(file: FileNode) -> ModuleFacts:
    """Return the definitions in *file*, plus whether it is directly runnable.

    Symbols are top-level definitions with nested ones under ``children``. The
    ``__main__`` guard flag is only ever set for Python, the one language where
    that construct marks an entry point.
    """
    if file.language == "Python":
        return _python_facts(file.absolute_path)
    if not backend.AVAILABLE:
        return ModuleFacts()
    pack_name = pack_name_for_ext(file.extension)
    if pack_name is None:
        return ModuleFacts()
    query_source = _SYMBOL_QUERIES.get(_QUERY_ALIASES.get(pack_name, pack_name))
    if query_source is None:
        return ModuleFacts()
    return ModuleFacts(symbols=_query_symbols(file.absolute_path, pack_name, query_source))


@dataclass
class _ProtoSymbol:
    """A captured definition with its full span, before nesting."""

    name: str
    kind: str
    start: tuple[int, int]
    end: tuple[int, int]
    children: list["_ProtoSymbol"] = field(default_factory=list)


def _query_symbols(path: Path, pack_name: str, query_source: str) -> tuple[Symbol, ...]:
    try:
        source = path.read_bytes()
    except OSError:
        return ()
    matches = backend.query_matches(pack_name, source, query_source)
    entries: dict[tuple[str, str, tuple[int, int], tuple[int, int]], _ProtoSymbol] = {}
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
        proto = _ProtoSymbol(
            name=name_node.text.decode("utf-8", "ignore"),
            kind=kind,
            start=tuple(def_node.start_point[:2]),
            end=tuple(def_node.end_point[:2]),
        )
        entries.setdefault((proto.name, proto.kind, proto.start, proto.end), proto)
    return tuple(_freeze_symbols(_nest_by_span(list(entries.values()))))


def _nest_by_span(entries: list[_ProtoSymbol]) -> list[_ProtoSymbol]:
    """Nest definitions whose span is contained in another definition's span.

    This gives methods a parent class (and thus a qualified name in the
    symbols index) without per-grammar nesting queries.
    """
    # Outermost first: earlier start, and at equal starts the larger span.
    entries.sort(key=lambda entry: (entry.start, (-entry.end[0], -entry.end[1])))
    roots: list[_ProtoSymbol] = []
    stack: list[_ProtoSymbol] = []
    for entry in entries:
        while stack and not _contains(stack[-1], entry):
            stack.pop()
        if stack:
            stack[-1].children.append(entry)
        else:
            roots.append(entry)
        stack.append(entry)
    return roots


def _contains(parent: _ProtoSymbol, child: _ProtoSymbol) -> bool:
    if (parent.start, parent.end) == (child.start, child.end):
        return False
    return parent.start <= child.start and child.end <= parent.end


def _freeze_symbols(protos: list[_ProtoSymbol]) -> list[Symbol]:
    return [
        Symbol(
            name=proto.name,
            kind=proto.kind,
            start_line=proto.start[0] + 1,
            end_line=proto.end[0] + 1,
            children=tuple(_freeze_symbols(proto.children)),
        )
        for proto in protos
    ]


def _python_facts(path: Path) -> ModuleFacts:
    try:
        source = path.read_text(encoding="utf-8", errors="ignore")
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", SyntaxWarning)
            tree = ast.parse(source, filename=str(path))
    except (OSError, SyntaxError, ValueError):
        return ModuleFacts()
    return ModuleFacts(
        symbols=tuple(_python_node_symbol(node, is_method=False) for node in _def_nodes(tree.body)),
        has_main_guard=any(
            isinstance(node, ast.If) and _tests_main_name(node.test) for node in tree.body
        ),
    )


def _tests_main_name(test: ast.expr) -> bool:
    """True if *test* is a ``__name__``/``"__main__"`` comparison.

    Covers the canonical ``__name__ == "__main__"``, its reversed form, the
    ``__name__ in ("__main__", ...)`` variant, and any of those as one operand
    of an ``and`` chain.
    """
    if isinstance(test, ast.BoolOp) and isinstance(test.op, ast.And):
        return any(_tests_main_name(operand) for operand in test.values)
    if not isinstance(test, ast.Compare) or len(test.ops) != 1:
        return False
    left, right = test.left, test.comparators[0]
    if isinstance(test.ops[0], ast.Eq):
        return (_is_name_dunder(left) and _is_main_string(right)) or (
            _is_main_string(left) and _is_name_dunder(right)
        )
    if isinstance(test.ops[0], ast.In):
        return _is_name_dunder(left) and isinstance(right, (ast.Tuple, ast.List, ast.Set)) and any(
            _is_main_string(element) for element in right.elts
        )
    return False


def _is_name_dunder(node: ast.expr) -> bool:
    return isinstance(node, ast.Name) and node.id == "__name__"


def _is_main_string(node: ast.expr) -> bool:
    return isinstance(node, ast.Constant) and node.value == "__main__"


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
