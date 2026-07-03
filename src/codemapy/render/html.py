from __future__ import annotations

import html
import json
from pathlib import Path

from codemapy.entrypoints import entry_points
from codemapy.models import ModuleNode, Report

# Top-level symbols embedded per file for the details panel. Full nested
# detail lives in symbols.json; the report only needs a browsable outline.
MAX_HTML_SYMBOLS_PER_FILE = 50


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
    total_symbols = sum(len(symbol.flatten()) for module in report.modules for symbol in module.symbols)
    cycle_count = len(report.cycles)
    cycles_chip = (
        f'<span class="chip warn">&#9888; {cycle_count} cycles</span>'
        if cycle_count
        else '<span class="chip">0 cycles</span>'
    )
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
  --critical: #d03b3b;
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
.chip.warn {{ color: var(--critical); border-color: var(--critical); }}
#legend {{
  display: flex;
  flex-wrap: wrap;
  gap: 14px;
  padding: 9px 28px;
  background: var(--panel);
  border-bottom: 1px solid var(--line);
  font-size: 12px;
  color: var(--muted);
}}
.dot {{ width: 10px; height: 10px; border-radius: 3px; display: inline-block; margin-right: 6px; vertical-align: -1px; }}
main {{
  display: grid;
  grid-template-columns: minmax(230px, 24vw) minmax(0, 1fr) minmax(250px, 24vw);
  grid-template-rows: minmax(280px, 42vh) minmax(320px, 1fr);
  gap: 1px;
  min-height: calc(100vh - 120px);
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
  grid-template-columns: auto 1fr auto;
  align-items: center;
  gap: 8px;
  padding: 4px 6px;
  border-radius: 5px;
  cursor: pointer;
}}
.tree-row:hover, .tree-row.active {{ background: #e7f5f2; }}
.path {{ white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}
.subtle {{ color: var(--muted); }}
#insights {{ grid-row: 1 / span 2; grid-column: 3; overflow: auto; }}
.insight-block {{ padding: 12px 14px; border-bottom: 1px solid var(--line); font-size: 12px; }}
.insight-block h3 {{
  margin: 0 0 8px;
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: var(--muted);
}}
.insight-block ul {{ list-style: none; margin: 0; padding: 0; }}
.insight-block li {{ padding: 3px 0; overflow-wrap: anywhere; }}
.file-link {{ cursor: pointer; color: var(--accent); }}
.file-link:hover {{ text-decoration: underline; }}
.sym-kind {{ color: var(--muted); margin-right: 6px; }}
#fileDetails .path-title {{ font-family: ui-monospace, SFMono-Regular, Consolas, monospace; overflow-wrap: anywhere; font-weight: 600; }}
#treemapCanvas, #graphCanvas {{ width: 100%; height: calc(100% - 46px); display: block; }}
.tile {{ cursor: pointer; stroke: var(--panel); stroke-width: 2; }}
.tile-label {{ font-size: 11px; fill: var(--ink); paint-order: stroke; stroke: white; stroke-width: 3px; pointer-events: none; }}
.node {{ cursor: pointer; }}
.edge {{ stroke: #9aa4b2; stroke-opacity: 0.45; }}
.node circle {{ stroke: white; stroke-width: 2; }}
.node text {{ font-size: 11px; fill: var(--ink); paint-order: stroke; stroke: white; stroke-width: 3px; }}
.dim {{ opacity: 0.18; }}
.empty-note {{ font-size: 12px; fill: var(--muted); }}
@media (max-width: 1100px) {{
  header {{ align-items: start; flex-direction: column; }}
  main {{ display: block; }}
  section {{ height: 420px; border-bottom: 1px solid var(--line); }}
  #tree {{ height: 50vh; }}
  #insights {{ height: auto; max-height: 70vh; }}
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
    <span class="chip">{total_symbols} symbols</span>
    {cycles_chip}
  </div>
</header>
<div id="legend"></div>
<main>
  <section id="tree">
    <div class="section-head"><h2>File Tree</h2><span class="subtle">click to inspect</span></div>
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
  <section id="insights">
    <div class="section-head"><h2>Insights</h2><span class="subtle">click any file to inspect</span></div>
    <div id="fileDetails" class="insight-block" hidden></div>
    <div class="insight-block"><h3>Entry Points</h3><ul id="entryList"></ul></div>
    <div class="insight-block"><h3>Top Hubs</h3><ul id="hubList"></ul></div>
    <div class="insight-block"><h3>Dependency Cycles</h3><ul id="cycleList"></ul></div>
  </section>
</main>
<script>
const report = {data_json};
// Validated categorical palette (fixed assignment order, worst adjacent CVD
// dE 24.2 on white). Languages are ranked by file count; ranks past the
// palette fold into a neutral "other" gray, never a cycled hue.
const SERIES = ["#2a78d6", "#1baf7a", "#eda100", "#008300", "#4a3aa7", "#e34948", "#e87ba4", "#eb6834"];
const OTHER_COLOR = "#898781";
const SVG_NS = "http://www.w3.org/2000/svg";

const langCounts = new Map();
report.files.forEach(file => {{
  const lang = file.language || "Other";
  langCounts.set(lang, (langCounts.get(lang) || 0) + 1);
}});
const rankedLangs = [...langCounts.entries()].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
const langColor = new Map(rankedLangs.map(([lang], index) => [lang, index < SERIES.length ? SERIES[index] : OTHER_COLOR]));
function colorFor(lang) {{
  return langColor.get(lang || "Other") || OTHER_COLOR;
}}

const filePaths = new Set(report.files.map(file => file.path));
const moduleByPath = new Map(report.modules.map(module => [module.path, module]));
let selected = null;

function setSelected(path) {{
  selected = selected === path ? null : path;
  document.querySelectorAll("[data-path]").forEach(el => el.classList.toggle("active", el.dataset.path === selected));
  drawTreemap();
  drawGraph();
  renderDetails();
}}

function fileLink(path, label) {{
  const span = document.createElement("span");
  span.textContent = label || path;
  if (filePaths.has(path)) {{
    span.className = "file-link";
    span.addEventListener("click", () => setSelected(path));
  }}
  return span;
}}

function li(...children) {{
  const item = document.createElement("li");
  children.forEach(child => item.append(child));
  return item;
}}

function subtle(text) {{
  const span = document.createElement("span");
  span.className = "subtle";
  span.textContent = text;
  return span;
}}

function renderLegend() {{
  const legend = document.getElementById("legend");
  legend.innerHTML = "";
  rankedLangs.forEach(([lang, count]) => {{
    const item = document.createElement("span");
    const dot = document.createElement("span");
    dot.className = "dot";
    dot.style.background = colorFor(lang);
    item.append(dot, `${{lang}} `);
    item.append(subtle(`${{count}}`));
    legend.appendChild(item);
  }});
}}

function renderTree() {{
  const list = document.getElementById("treeList");
  list.innerHTML = "";
  report.files.forEach(file => {{
    const row = document.createElement("div");
    row.className = "tree-row";
    row.dataset.path = file.path;
    row.style.paddingLeft = `${{6 + file.path.split("/").length * 10}}px`;
    const dot = document.createElement("span");
    dot.className = "dot";
    dot.style.background = colorFor(file.language);
    dot.style.marginRight = "0";
    const path = document.createElement("span");
    path.className = "path";
    path.textContent = file.path;
    const loc = subtle(`${{file.loc}} loc`);
    row.append(dot, path, loc);
    row.addEventListener("click", () => setSelected(file.path));
    list.appendChild(row);
  }});
}}

function renderEntryPoints() {{
  const list = document.getElementById("entryList");
  list.innerHTML = "";
  if (!report.entry_points.length) {{
    list.appendChild(li(subtle("None detected")));
    return;
  }}
  report.entry_points.forEach(entry => {{
    const item = li(fileLink(entry.path || entry.name, entry.name));
    if (entry.target) item.append(` → ${{entry.target}}`);
    if (entry.note) item.append(" ", subtle(`(${{entry.note}})`));
    list.appendChild(item);
  }});
}}

function renderHubs() {{
  const list = document.getElementById("hubList");
  list.innerHTML = "";
  const hubs = report.modules
    .filter(module => module.fan_in || module.fan_out)
    .sort((a, b) => b.fan_in - a.fan_in || b.fan_out - a.fan_out || a.path.localeCompare(b.path))
    .slice(0, 10);
  if (!hubs.length) {{
    list.appendChild(li(subtle("No internal dependency edges detected")));
    return;
  }}
  hubs.forEach(module => {{
    list.appendChild(li(fileLink(module.path), " ", subtle(`in ${{module.fan_in}} · out ${{module.fan_out}}`)));
  }});
}}

function renderCycles() {{
  const list = document.getElementById("cycleList");
  list.innerHTML = "";
  if (!report.cycles.length) {{
    list.appendChild(li(subtle("None detected")));
    return;
  }}
  report.cycles.slice(0, 10).forEach(cycle => {{
    const item = document.createElement("li");
    cycle.forEach((path, index) => {{
      if (index) item.append(" ↔ ");
      item.append(fileLink(path, path.split("/").pop()));
    }});
    list.appendChild(item);
  }});
  if (report.cycles.length > 10) {{
    list.appendChild(li(subtle(`... and ${{report.cycles.length - 10}} more`)));
  }}
}}

function renderDetails() {{
  const panel = document.getElementById("fileDetails");
  panel.innerHTML = "";
  panel.hidden = !selected;
  if (!selected) return;

  const file = report.files.find(item => item.path === selected);
  const module = moduleByPath.get(selected);
  const title = document.createElement("div");
  title.className = "path-title";
  title.textContent = selected;
  panel.appendChild(title);

  const facts = document.createElement("div");
  facts.className = "subtle";
  const parts = [];
  if (file) parts.push(file.language || "Other", `${{file.loc}} loc`, `${{file.size}} bytes`);
  if (module) parts.push(`fan-in ${{module.fan_in}}`, `fan-out ${{module.fan_out}}`);
  facts.textContent = parts.join(" · ");
  panel.appendChild(facts);

  const outgoing = report.edges.filter(edge => edge.source === selected).map(edge => edge.target);
  const incoming = report.edges.filter(edge => edge.target === selected).map(edge => edge.source);
  const externals = [...new Set(report.external.filter(item => item.source === selected).map(item => item.raw))];
  appendLinkList(panel, "Depends on", [...new Set(outgoing)]);
  appendLinkList(panel, "Used by", [...new Set(incoming)]);
  if (externals.length) {{
    appendHeading(panel, `External refs (${{externals.length}})`);
    const list = document.createElement("ul");
    externals.slice(0, 12).forEach(raw => list.appendChild(li(subtle(raw))));
    if (externals.length > 12) list.appendChild(li(subtle(`... and ${{externals.length - 12}} more`)));
    panel.appendChild(list);
  }}

  if (module && module.symbols.length) {{
    appendHeading(panel, `Symbols (${{module.symbol_count}})`);
    const list = document.createElement("ul");
    module.symbols.forEach(symbol => {{
      const kind = document.createElement("span");
      kind.className = "sym-kind";
      kind.textContent = symbol.kind;
      list.appendChild(li(kind, `${{symbol.name}} `, subtle(`:${{symbol.line}}`)));
    }});
    if (module.symbols_omitted) list.appendChild(li(subtle(`... and ${{module.symbols_omitted}} more`)));
    panel.appendChild(list);
  }}
}}

function appendHeading(panel, text) {{
  const heading = document.createElement("h3");
  heading.textContent = text;
  heading.style.marginTop = "10px";
  panel.appendChild(heading);
}}

function appendLinkList(panel, label, paths) {{
  if (!paths.length) return;
  appendHeading(panel, `${{label}} (${{paths.length}})`);
  const list = document.createElement("ul");
  paths.slice(0, 12).forEach(path => list.appendChild(li(fileLink(path))));
  if (paths.length > 12) list.appendChild(li(subtle(`... and ${{paths.length - 12}} more`)));
  panel.appendChild(list);
}}

function svgTitle(node, text) {{
  const title = document.createElementNS(SVG_NS, "title");
  title.textContent = text;
  node.appendChild(title);
}}

function drawTreemap() {{
  const svg = document.getElementById("treemapCanvas");
  const width = svg.clientWidth || 800;
  const height = svg.clientHeight || 320;
  svg.setAttribute("viewBox", `0 0 ${{width}} ${{height}}`);
  svg.innerHTML = "";
  const total = Math.max(1, report.files.reduce((sum, file) => sum + Math.max(1, file.loc), 0));
  let x = 0, y = 0, rowH = Math.max(70, height / 4);
  report.files.forEach(file => {{
    const area = Math.max(28, (Math.max(1, file.loc) / total) * width * height);
    let w = Math.max(82, area / rowH);
    if (x + w > width) {{ x = 0; y += rowH; rowH = Math.max(58, height - y); }}
    if (y >= height) return;
    w = Math.min(w, width - x);
    const rect = document.createElementNS(SVG_NS, "rect");
    rect.setAttribute("x", x); rect.setAttribute("y", y); rect.setAttribute("width", w); rect.setAttribute("height", rowH);
    rect.setAttribute("rx", 2);
    rect.setAttribute("fill", colorFor(file.language));
    rect.setAttribute("opacity", selected && selected !== file.path ? "0.28" : "0.86");
    rect.setAttribute("class", "tile");
    rect.addEventListener("click", () => setSelected(file.path));
    svgTitle(rect, `${{file.path}}\n${{file.loc}} loc · ${{file.language || "Other"}}`);
    svg.appendChild(rect);
    if (w > 100 && rowH > 44) {{
      const text = document.createElementNS(SVG_NS, "text");
      text.setAttribute("x", x + 8); text.setAttribute("y", y + 20);
      text.setAttribute("class", "tile-label");
      text.textContent = file.path.split("/").pop();
      svg.appendChild(text);
    }}
    x += w;
  }});
}}

function drawGraph() {{
  const svg = document.getElementById("graphCanvas");
  const width = svg.clientWidth || 800;
  const height = svg.clientHeight || 360;
  svg.setAttribute("viewBox", `0 0 ${{width}} ${{height}}`);
  svg.innerHTML = "";
  const modules = report.modules.filter(module => module.fan_in || module.fan_out).slice(0, 120);
  if (!modules.length) {{
    const note = document.createElementNS(SVG_NS, "text");
    note.setAttribute("x", width / 2); note.setAttribute("y", height / 2);
    note.setAttribute("text-anchor", "middle");
    note.setAttribute("class", "empty-note");
    note.textContent = "No internal dependencies detected";
    svg.appendChild(note);
    return;
  }}
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
    const line = document.createElementNS(SVG_NS, "line");
    line.setAttribute("x1", source.x); line.setAttribute("y1", source.y);
    line.setAttribute("x2", target.x); line.setAttribute("y2", target.y);
    line.setAttribute("class", "edge");
    if (selected && edge.source !== selected && edge.target !== selected) line.classList.add("dim");
    svg.appendChild(line);
  }});
  modules.forEach(module => {{
    const group = document.createElementNS(SVG_NS, "g");
    group.setAttribute("class", "node");
    if (selected && module.path !== selected && !edges.some(edge => edge.source === selected && edge.target === module.path || edge.target === selected && edge.source === module.path)) group.classList.add("dim");
    group.addEventListener("click", () => setSelected(module.path));
    const circle = document.createElementNS(SVG_NS, "circle");
    circle.setAttribute("cx", module.x); circle.setAttribute("cy", module.y);
    circle.setAttribute("r", 6 + Math.min(18, module.fan_in * 3));
    circle.setAttribute("fill", colorFor(module.language));
    const label = document.createElementNS(SVG_NS, "text");
    label.setAttribute("x", module.x + 9); label.setAttribute("y", module.y + 4);
    label.textContent = module.path.split("/").pop();
    svgTitle(group, `${{module.path}}\nfan-in ${{module.fan_in}} · fan-out ${{module.fan_out}}`);
    group.appendChild(circle); group.appendChild(label); svg.appendChild(group);
  }});
}}

window.addEventListener("resize", () => {{ drawTreemap(); drawGraph(); }});
renderLegend();
renderTree();
renderEntryPoints();
renderHubs();
renderCycles();
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
                "symbols": _symbol_outline(module),
                "symbols_omitted": max(0, len(module.symbols) - MAX_HTML_SYMBOLS_PER_FILE),
                "symbol_count": sum(len(symbol.flatten()) for symbol in module.symbols),
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
        "entry_points": [
            {"name": entry.name, "target": entry.target, "path": entry.path, "note": entry.note}
            for entry in entry_points(report)
        ],
        "cycles": [list(cycle) for cycle in report.cycles],
    }


def _symbol_outline(module: ModuleNode) -> list[dict[str, object]]:
    """Top-level symbols only, capped, for the report's details panel."""
    return [
        {"name": symbol.name, "kind": symbol.kind, "line": symbol.start_line}
        for symbol in module.symbols[:MAX_HTML_SYMBOLS_PER_FILE]
    ]
