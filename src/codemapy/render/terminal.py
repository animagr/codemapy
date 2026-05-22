from __future__ import annotations

from codemapy.models import Report, TreeNode
from codemapy.scanner import build_tree


def render_tree(report: Report) -> str:
    lines = [
        f"{report.name}",
        f"files: {len(report.files)} | loc: {report.total_loc} | deps: {len(report.edges)}",
    ]
    language_line = ", ".join(f"{name} ({count})" for name, count in report.languages.items())
    if language_line:
        lines.append(f"languages: {language_line}")
    lines.append("")
    lines.extend(_render_node(build_tree(report.files), prefix=""))
    return "\n".join(lines)


def _render_node(node: TreeNode, prefix: str) -> list[str]:
    lines: list[str] = []
    children = sorted(node.children.values(), key=lambda child: (child.is_file, child.name.lower()))
    for index, child in enumerate(children):
        last = index == len(children) - 1
        branch = "`-- " if last else "|-- "
        next_prefix = "    " if last else "|   "
        if child.file:
            file = child.file
            label = f"{child.name} ({file.loc} loc, {_format_size(file.size)})"
            lines.append(prefix + branch + label)
        else:
            lines.append(prefix + branch + child.name + "/")
            lines.extend(_render_node(child, prefix + next_prefix))
    return lines


def _format_size(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.1f}{unit}"
        value /= 1024
    return f"{value:.1f}GB"
