from __future__ import annotations

import html
import json
from pathlib import Path

from codemapy.models import Report


def write_html(report: Report, output: Path) -> Path:
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(render_html(report), encoding="utf-8")
    return output


def render_html(report: Report) -> str:
    payload = _report_payload(report)
    # Escape "</" so scanned content (e.g. an import string containing
    # "</script>") cannot break out of the inline <script> block.
    data_json = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(report.name)} codemapy report</title>
<style>
:root {{
  color-scheme: light;
  --bg: #f7f8fb;
  --panel: #ffffff;
  --ink: #1f2937;
  --muted: #667085;
  --line: #d8dee9;
  --accent: #0f766e;
  --accent-2: #7c3aed;
}}
* {{ box-sizing: border-box; }}
body {{
  margin: 0;
  font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  color: var(--ink);
  background: var(--bg);
}}
header {{
  display: flex;
  align-items: end;
  justify-content: space-between;
  gap: 24px;
  padding: 22px 28px;
  border-bottom: 1px solid var(--line);
  background: var(--panel);
}}
h1, h2 {{ margin: 0; letter-spacing: 0; }}
h1 {{ font-size: 24px; }}
h2 {{ font-size: 15px; }}
.meta {{ display: flex; flex-wrap: wrap; gap: 10px; color: var(--muted); font-size: 13px; }}
.chip {{ border: 1px solid var(--line); border-radius: 999px; padding: 4px 9px; background: #fbfcfe; }}
main {{
  display: grid;
  grid-template-columns: minmax(280px, 34vw) 1fr;
  grid-template-rows: minmax(280px, 42vh) minmax(320px, 1fr);
  gap: 1px;
  min-height: calc(100vh - 82px);
  background: var(--line);
}}
section {{ background: var(--panel); min-width: 0; min-height: 0; overflow: hidden; }}
.section-head {{
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 12px 14px;
  border-bottom: 1px solid var(--line);
}}
#tree {{ grid-row: 1 / span 2; overflow: auto; }}
#treeList {{ padding: 10px 14px 24px; font-family: ui-monospace, SFMono-Regular, Consolas, monospace; font-size: 12px; }}
.tree-row {{
  display: grid;
  grid-template-columns: 1fr auto;
  gap: 10px;
  padding: 4px 6px;
  border-radius: 5px;
  cursor: pointer;
}}
.tree-row:hover, .tree-row.active {{ background: #e7f5f2; }}
.path {{ white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}
.subtle {{ color: var(--muted); }}
#treemapCanvas, #graphCanvas {{ width: 100%; height: calc(100% - 46px); display: block; }}
.tile {{ cursor: pointer; stroke: #fff; stroke-width: 1; }}
.node {{ cursor: pointer; }}
.edge {{ stroke: #9aa4b2; stroke-opacity: 0.45; }}
.node circle {{ fill: var(--accent); stroke: white; stroke-width: 1.5; }}
.node text {{ font-size: 11px; fill: var(--ink); paint-order: stroke; stroke: white; stroke-width: 3px; }}
.dim {{ opacity: 0.18; }}
@media (max-width: 860px) {{
  header {{ align-items: start; flex-direction: column; }}
  main {{ display: block; }}
  section {{ height: 420px; border-bottom: 1px solid var(--line); }}
  #tree {{ height: 50vh; }}
}}
</style>
</head>
<body>
<header>
  <div>
    <h1>{html.escape(report.name)}</h1>
    <div class="subtle">{html.escape(str(report.root))}</div>
  </div>
  <div class="meta">
    <span class="chip">{len(report.files)} files</span>
    <span class="chip">{report.total_loc} loc</span>
    <span class="chip">{len(report.edges)} internal deps</span>
    <span class="chip">{len(report.external_imports)} external refs</span>
  </div>
</header>
<main>
  <section id="tree">
    <div class="section-head"><h2>File Tree</h2><span class="subtle">click to highlight</span></div>
    <div id="treeList"></div>
  </section>
  <section>
    <div class="section-head"><h2>Treemap</h2><span class="subtle">sized by LOC</span></div>
    <svg id="treemapCanvas" role="img" aria-label="Project treemap"></svg>
  </section>
  <section>
    <div class="section-head"><h2>Dependency Graph</h2><span class="subtle">node size = fan-in</span></div>
    <svg id="graphCanvas" role="img" aria-label="Dependency graph"></svg>
  </section>
</main>
<script>
const report = {data_json};
const colors = ["#0f766e", "#2563eb", "#7c3aed", "#c2410c", "#be123c", "#4d7c0f", "#0369a1"];
const langColor = new Map();
function colorFor(lang) {{
  if (!langColor.has(lang)) langColor.set(lang, colors[langColor.size % colors.length]);
  return langColor.get(lang);
}}
let selected = null;

function setSelected(path) {{
  selected = path;
  document.querySelectorAll("[data-path]").forEach(el => el.classList.toggle("active", el.dataset.path === path));
  drawTreemap();
  drawGraph();
}}

function renderTree() {{
  const list = document.getElementById("treeList");
  list.innerHTML = "";
  report.files.forEach(file => {{
    const row = document.createElement("div");
    row.className = "tree-row";
    row.dataset.path = file.path;
    row.style.paddingLeft = `${{6 + file.path.split("/").length * 10}}px`;
    row.innerHTML = `<span class="path">${{escapeHtml(file.path)}}</span><span class="subtle">${{file.loc}} loc</span>`;
    row.addEventListener("click", () => setSelected(file.path));
    list.appendChild(row);
  }});
}}

function drawTreemap() {{
  const svg = document.getElementById("treemapCanvas");
  const width = svg.clientWidth || 800;
  const height = svg.clientHeight || 320;
  svg.setAttribute("viewBox", `0 0 ${{width}} ${{height}}`);
  svg.innerHTML = "";
  const total = Math.max(1, report.files.reduce((sum, file) => sum + Math.max(1, file.loc), 0));
  let x = 0, y = 0, rowH = Math.max(70, height / 4), rowW = 0;
  report.files.forEach(file => {{
    const area = Math.max(28, (Math.max(1, file.loc) / total) * width * height);
    let w = Math.max(82, area / rowH);
    if (x + w > width) {{ x = 0; y += rowH; rowH = Math.max(58, height - y); }}
    if (y >= height) return;
    w = Math.min(w, width - x);
    const rect = document.createElementNS("http://www.w3.org/2000/svg", "rect");
    rect.setAttribute("x", x); rect.setAttribute("y", y); rect.setAttribute("width", w); rect.setAttribute("height", rowH);
    rect.setAttribute("fill", colorFor(file.language || "Other"));
    rect.setAttribute("opacity", selected && selected !== file.path ? "0.28" : "0.86");
    rect.setAttribute("class", "tile");
    rect.addEventListener("click", () => setSelected(file.path));
    svg.appendChild(rect);
    if (w > 100 && rowH > 44) {{
      const text = document.createElementNS("http://www.w3.org/2000/svg", "text");
      text.setAttribute("x", x + 8); text.setAttribute("y", y + 20); text.setAttribute("fill", "white"); text.setAttribute("font-size", "11");
      text.textContent = file.path.split("/").pop();
      svg.appendChild(text);
    }}
    x += w;
    rowW += w;
  }});
}}

function drawGraph() {{
  const svg = document.getElementById("graphCanvas");
  const width = svg.clientWidth || 800;
  const height = svg.clientHeight || 360;
  svg.setAttribute("viewBox", `0 0 ${{width}} ${{height}}`);
  svg.innerHTML = "";
  const modules = report.modules.filter(module => module.fan_in || module.fan_out).slice(0, 120);
  const byPath = new Map(modules.map((module, index) => [module.path, {{...module, index}}]));
  const edges = report.edges.filter(edge => byPath.has(edge.source) && byPath.has(edge.target));
  const radius = Math.min(width, height) * 0.38;
  const cx = width / 2, cy = height / 2;
  modules.forEach((module, index) => {{
    const angle = (index / Math.max(1, modules.length)) * Math.PI * 2 - Math.PI / 2;
    module.x = cx + Math.cos(angle) * radius;
    module.y = cy + Math.sin(angle) * radius;
  }});
  edges.forEach(edge => {{
    const source = byPath.get(edge.source);
    const target = byPath.get(edge.target);
    const line = document.createElementNS("http://www.w3.org/2000/svg", "line");
    line.setAttribute("x1", source.x); line.setAttribute("y1", source.y);
    line.setAttribute("x2", target.x); line.setAttribute("y2", target.y);
    line.setAttribute("class", "edge");
    if (selected && edge.source !== selected && edge.target !== selected) line.classList.add("dim");
    svg.appendChild(line);
  }});
  modules.forEach(module => {{
    const group = document.createElementNS("http://www.w3.org/2000/svg", "g");
    group.setAttribute("class", "node");
    if (selected && module.path !== selected && !edges.some(edge => edge.source === selected && edge.target === module.path || edge.target === selected && edge.source === module.path)) group.classList.add("dim");
    group.addEventListener("click", () => setSelected(module.path));
    const circle = document.createElementNS("http://www.w3.org/2000/svg", "circle");
    circle.setAttribute("cx", module.x); circle.setAttribute("cy", module.y);
    circle.setAttribute("r", 6 + Math.min(18, module.fan_in * 3));
    circle.setAttribute("fill", colorFor(module.language || "Other"));
    const label = document.createElementNS("http://www.w3.org/2000/svg", "text");
    label.setAttribute("x", module.x + 9); label.setAttribute("y", module.y + 4);
    label.textContent = module.path.split("/").pop();
    group.appendChild(circle); group.appendChild(label); svg.appendChild(group);
  }});
}}

function escapeHtml(value) {{
  return value.replace(/[&<>"']/g, char => ({{"&":"&amp;","<":"&lt;",">":"&gt;","\\"":"&quot;","'":"&#039;"}}[char]));
}}

window.addEventListener("resize", () => {{ drawTreemap(); drawGraph(); }});
renderTree();
drawTreemap();
drawGraph();
</script>
</body>
</html>"""


def _report_payload(report: Report) -> dict[str, object]:
    return {
        "root": str(report.root),
        "files": [
            {
                "path": file.path,
                "loc": file.loc,
                "size": file.size,
                "language": file.language,
                "extension": file.extension,
            }
            for file in report.files
        ],
        "modules": [
            {
                "path": module.path,
                "language": module.language,
                "loc": module.loc,
                "size": module.size,
                "fan_in": module.fan_in,
                "fan_out": module.fan_out,
            }
            for module in report.modules
        ],
        "edges": [
            {"source": edge.source, "target": edge.target, "raw": edge.raw, "kind": edge.kind}
            for edge in report.edges
        ],
        "external": [
            {"source": item.source, "raw": item.raw, "kind": item.kind}
            for item in report.external_imports
        ],
    }
