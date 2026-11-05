"""
MemALL Knowledge Graph Visualizer
=================================

Reads ``entities`` + ``knowledge_triples`` from the MemALL SQLite DB and
generates a self-contained HTML file with an SVG force-directed graph.

Features
--------
* Nodes = entities (circles), colored by ``entity_type``.
* Edges = triples (lines), labeled with ``predicate``.
* Vanilla-JS Verlet force-directed layout (no CDN / external dependency).
* Zoom + pan on the SVG.
* Click a node to highlight its neighbors and incident edges.
* Hover tooltip showing entity name / type / description.
* Sidebar legend listing entity types with counts.
* Filter controls: by entity type, by predicate.

Usage
-----
    python -m memall.tools.kg_visualize \\
        --db <path-to-data.db> \\
        --out kg_graph.html
"""

from __future__ import annotations

import argparse
import html
import json
import sqlite3
import sys
from pathlib import Path

# Allow running both as a module and as a standalone script.
try:
    from memall.core.db import get_conn
except ImportError:  # pragma: no cover - standalone fallback
    get_conn = None  # type: ignore[assignment]

DEFAULT_DB = r"C:\Users\Administrator\.memall\data.db"
DEFAULT_OUT = r"E:\MemALL\kg_graph.html"


# --------------------------------------------------------------------------- #
# Data access
# --------------------------------------------------------------------------- #
def load_graph(db_path: str | None = None) -> dict:
    """Load entities + triples from the MemALL DB.

    Returns a dict with ``nodes`` and ``links`` lists ready for JSON
    embedding into the HTML page.
    """
    # An explicit db_path wins; otherwise use the library connection pool
    # when importable, falling back to the default file.
    if db_path:
        conn = sqlite3.connect(db_path)
    elif get_conn is not None:
        conn = get_conn()
    else:
        conn = sqlite3.connect(DEFAULT_DB)
    try:
        conn.row_factory = sqlite3.Row

        # Entities: id, name, entity_type, description
        rows = conn.execute(
            """
            SELECT id, name, entity_type, description
            FROM entities
            ORDER BY id
            """
        ).fetchall()
        nodes = [
            {
                "id": r["id"],
                "name": r["name"] or f"entity_{r['id']}",
                "entity_type": r["entity_type"] or "未知",
                "description": r["description"] or "",
            }
            for r in rows
        ]

        # Triples joined to entity names for subject/object.
        links = []
        if conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='knowledge_triples'"
        ).fetchone():
            triple_rows = conn.execute(
                """
                SELECT t.id, t.subject_id, t.predicate, t.object_id,
                       s.name AS subject_name, o.name AS object_name
                FROM knowledge_triples t
                JOIN entities s ON s.id = t.subject_id
                JOIN entities o ON o.id = t.object_id
                """
            ).fetchall()
            links = [
                {
                    "id": r["id"],
                    "source": r["subject_id"],
                    "target": r["object_id"],
                    "source_name": r["subject_name"],
                    "target_name": r["object_name"],
                    "predicate": r["predicate"] or "related_to",
                }
                for r in triple_rows
            ]

        return {"nodes": nodes, "links": links}
    finally:
        conn.close()


# --------------------------------------------------------------------------- #
# HTML generation
# --------------------------------------------------------------------------- #
_ENTITY_TYPE_COLORS = {
    "concept": "#6c5ce7",
    "technology": "#00b894",
    "project": "#e17055",
    "person": "#0984e3",
    "language": "#fdcb6e",
    "未知": "#b2bec3",
}


def _color_for(entity_type: str) -> str:
    return _ENTITY_TYPE_COLORS.get(entity_type, "#636e72")


def _escape_js(s: str) -> str:
    """Escape a string for safe embedding inside a JS string literal."""
    return json.dumps(s, ensure_ascii=False)


def build_html(graph: dict, title: str = "MemALL Knowledge Graph") -> str:
    """Build a self-contained HTML page rendering the force-directed graph."""
    nodes_json = json.dumps(graph["nodes"], ensure_ascii=False)
    links_json = json.dumps(graph["links"], ensure_ascii=False)

    # Build entity-type legend data (type -> count) for the sidebar.
    type_counts: dict[str, int] = {}
    for n in graph["nodes"]:
        t = n["entity_type"] or "未知"
        type_counts[t] = type_counts.get(t, 0) + 1
    legend_items = sorted(type_counts.items())

    # Build predicate options for the filter dropdown.
    predicates: list[str] = sorted({l["predicate"] for l in graph["links"]})

    title_json = json.dumps(title, ensure_ascii=False)
    legend_json = json.dumps(
        [{"type": t, "count": c, "color": _color_for(t)} for t, c in legend_items],
        ensure_ascii=False,
    )
    predicates_json = json.dumps(predicates, ensure_ascii=False)

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>{html.escape(title)}</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto,
      "Helvetica Neue", Arial, "Noto Sans SC", "Microsoft YaHei", sans-serif;
    background: #f5f7fa;
    color: #1a1a1a;
    height: 100vh;
    overflow: hidden;
  }}
  #app {{ display: flex; height: 100vh; }}
  /* ---- Graph panel ---- */
  #graph-panel {{
    flex: 1;
    position: relative;
    background: #ffffff;
  }}
  svg {{
    width: 100%;
    height: 100%;
    display: block;
    cursor: grab;
    overflow: visible;
  }}
  svg:active {{ cursor: grabbing; }}
  .node {{ cursor: pointer; stroke: #fff; stroke-width: 1.5px; }}
  .node-label {{
    font-size: 11px;
    fill: #222;
    pointer-events: none;
    text-anchor: middle;
    user-select: none;
    font-weight: 600;
    mix-blend-mode: multiply;
  }}
  .edge {{ stroke: #9aa5b1; stroke-width: 1.5px; fill: none; }}
  .edge-label {{
    font-size: 10px;
    fill: #666;
    pointer-events: none;
    text-anchor: middle;
    user-select: none;
    paint-order: stroke;
    stroke: #fff;
    stroke-width: 4px;
    stroke-linejoin: round;
  }}
  .highlighted {{ opacity: 1; }}
  .dimmed {{ opacity: 0.12; }}
  .edge-highlighted {{ stroke: #e17055; stroke-width: 2.5px; opacity: 1; }}
  .edge-dimmed {{ opacity: 0.08; }}
  .tooltip {{
    position: absolute;
    pointer-events: none;
    padding: 8px 10px;
    background: rgba(20, 20, 20, 0.9);
    color: #fff;
    border-radius: 6px;
    font-size: 12px;
    line-height: 1.4;
    max-width: 260px;
    opacity: 0;
    transition: opacity 0.15s;
    z-index: 50;
    box-shadow: 0 2px 8px rgba(0,0,0,0.3);
  }}
  .tooltip.visible {{ opacity: 1; }}
  .tooltip b {{ display: block; margin-bottom: 4px; font-size: 13px; }}
  .tooltip .type {{ color: #00b894; font-size: 11px; text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 4px; }}
  /* ---- Sidebar ---- */
  #sidebar {{
    width: 340px;
    background: #ffffff;
    border-left: 1px solid #e1e4e8;
    display: flex;
    flex-direction: column;
    overflow: hidden;
    box-shadow: -2px 0 8px rgba(0,0,0,0.06);
  }}
  .sidebar-header {{
    padding: 14px 16px;
    border-bottom: 1px solid #e1e4e8;
    background: #fafbfc;
  }}
  .sidebar-header h1 {{
    font-size: 15px;
    font-weight: 700;
    color: #1a1a1a;
  }}
  .sidebar-header p {{
    font-size: 11px;
    color: #666;
    margin-top: 4px;
  }}
  .controls {{
    padding: 12px 16px;
    border-bottom: 1px solid #e1e4e8;
    background: #fafbfc;
    display: flex;
    flex-direction: column;
    gap: 10px;
  }}
  .control-group {{
    display: flex;
    flex-direction: column;
    gap: 6px;
  }}
  .control-group label {{
    font-size: 11px;
    font-weight: 600;
    color: #333;
    text-transform: uppercase;
    letter-spacing: 0.4px;
  }}
  select, input[type="text"] {{
    font-size: 12px;
    padding: 6px 8px;
    border: 1px solid #d1d5db;
    border-radius: 6px;
    background: #fff;
    color: #1a1a1a;
    outline: none;
  }}
  select:focus, input[type="text"]:focus {{ border-color: #0984e3; }}
  button.reset {{
    font-size: 11px;
    padding: 5px 10px;
    background: #e1e4e8;
    border: none;
    border-radius: 4px;
    cursor: pointer;
    color: #333;
  }}
  button.reset:hover {{ background: #d1d5db; }}
  .legend {{
    flex: 1;
    overflow-y: auto;
    padding: 12px 16px;
  }}
  .legend h2 {{
    font-size: 12px;
    font-weight: 700;
    color: #333;
    margin-bottom: 10px;
    text-transform: uppercase;
    letter-spacing: 0.4px;
  }}
  .legend-item {{
    display: flex;
    align-items: center;
    gap: 8px;
    font-size: 12px;
    padding: 6px 0;
    border-bottom: 1px solid #f0f2f5;
    cursor: pointer;
  }}
  .legend-item:last-child {{ border-bottom: none; }}
  .legend-color {{
    width: 12px;
    height: 12px;
    border-radius: 50%;
    flex-shrink: 0;
    border: 1px solid rgba(0,0,0,0.15);
  }}
  .legend-name {{ color: #333; }}
  .legend-count {{ margin-left: auto; color: #888; font-size: 11px; }}
  .legend-item.active .legend-name {{ font-weight: 700; color: #0984e3; }}
  .status {{
    padding: 8px 16px;
    font-size: 11px;
    color: #666;
    border-top: 1px solid #e1e4e8;
    background: #fafbfc;
  }}
  .hint {{
    font-size: 11px;
    color: #888;
    font-style: italic;
    margin-top: 6px;
  }}
</style>
</head>
<body>
<div id="app">
  <div id="graph-panel">
    <div class="tooltip" id="tooltip"></div>
    <svg id="graph"></svg>
  </div>
  <div id="sidebar">
    <div class="sidebar-header">
      <h1>MemALL 知识图谱</h1>
      <p>实体与三元组的力导向布局可视化</p>
    </div>
    <div class="controls">
      <div class="control-group">
        <label>实体类型过滤</label>
        <select id="filterType">
          <option value="__all__">全部类型</option>
        </select>
      </div>
      <div class="control-group">
        <label>谓词过滤</label>
        <select id="filterPredicate">
          <option value="__all__">全部谓词</option>
        </select>
      </div>
      <button class="reset" id="btnReset">重置过滤</button>
    </div>
    <div class="legend">
      <h2>实体类型</h2>
      <div id="legendList"></div>
    </div>
    <div class="status" id="status">
      <span id="statusText">加载中…</span>
    </div>
    <div class="hint" id="hint">提示：点击节点高亮邻居，拖动画布平移，滚轮缩放。</div>
  </div>
</div>

<script>
const GRAPH_TITLE = {title_json};
const NODES = {nodes_json};
const LINKS = {links_json};
const LEGEND_ITEMS = {legend_json};
const PREDICATES = {predicates_json};

const width = window.innerWidth - 340;
const height = window.innerHeight;

const svg = document.getElementById("graph");
const tooltip = document.getElementById("tooltip");
const statusText = document.getElementById("statusText");
const legendList = document.getElementById("legendList");
const filterType = document.getElementById("filterType");
const filterPredicate = document.getElementById("filterPredicate");
const btnReset = document.getElementById("btnReset");

svg.setAttribute("width", width);
svg.setAttribute("height", height);

// ---- entity-type colors ----
const TYPE_COLORS = {{
  concept: "#6c5ce7",
  technology: "#00b894",
  project: "#e17055",
  person: "#0984e3",
  language: "#fdcb6e",
  "未知": "#b2bec3",
}};
function colorFor(type) {{ return TYPE_COLORS[type] || "#636e72"; }}

// ---- Build adjacency ----
const nodeById = new Map(NODES.map(n => [n.id, n]));
const adj = new Map(); // node id -> Set of neighbor ids
const linkByPair = new Map(); // "a,b" -> link
for (const l of LINKS) {{
  if (!adj.has(l.source)) adj.set(l.source, new Set());
  if (!adj.has(l.target)) adj.set(l.target, new Set());
  adj.get(l.source).add(l.target);
  adj.get(l.target).add(l.source);
  linkByPair.set(`${{l.source}},${{l.target}}`, l);
  linkByPair.set(`${{l.target}},${{l.source}}`, l);
}}

// ---- Layout state ----
let nodes = NODES.map(n => ({{
  id: n.id,
  name: n.name,
  entity_type: n.entity_type || "未知",
  description: n.description || "",
  x: Math.random() * width,
  y: Math.random() * height,
  vx: 0,
  vy: 0,
  fixed: false,
}}));
let links = LINKS.map(l => ({{
  id: l.id,
  source: l.source,
  target: l.target,
  predicate: l.predicate || "related_to",
}}));

const nodeRadius = 7;
const edgeStroke = 1.5;

// ---- SVG containers ----
const defs = document.createElementNS("http://www.w3.org/2000/svg", "defs");
const marker = document.createElementNS("http://www.w3.org/2000/svg", "marker");
marker.setAttribute("id", "arrowhead");
marker.setAttribute("markerWidth", "7");
marker.setAttribute("markerHeight", "7");
marker.setAttribute("refX", "9");
marker.setAttribute("refY", "3.5");
marker.setAttribute("orient", "auto");
marker.setAttribute("fill", "#9aa5b1");
const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
path.setAttribute("d", "M0,0 L7,3.5 L0,7 Z");
marker.appendChild(path);
defs.appendChild(marker);
svg.appendChild(defs);

const edgeLayer = document.createElementNS("http://www.w3.org/2000/svg", "g");
const nodeLayer = document.createElementNS("http://www.w3.org/2000/svg", "g");
svg.appendChild(edgeLayer);
svg.appendChild(nodeLayer);

// ---- Zoom/pan ----
let scale = 1;
let tx = 0;
let ty = 0;
const view = document.createElementNS("http://www.w3.org/2000/svg", "g");
svg.appendChild(view);
view.appendChild(edgeLayer);
view.appendChild(nodeLayer);

function applyTransform() {{
  view.setAttribute("transform", `translate(${{tx}},${{ty}}) scale(${{scale}})`);
}}

svg.addEventListener("wheel", (e) => {{
  e.preventDefault();
  const delta = -e.deltaY * 0.001;
  const newScale = Math.min(Math.max(scale * (1 + delta), 0.3), 3);
  const cx = e.clientX;
  const cy = e.clientY;
  // Zoom toward mouse pointer
  const dx = (cx - tx) / scale;
  const dy = (cy - ty) / scale;
  scale = newScale;
  tx = cx - dx * scale;
  ty = cy - dy * scale;
  applyTransform();
}}, {{ passive: false }});

let isPanning = false;
let startX = 0, startY = 0;
svg.addEventListener("mousedown", (e) => {{
  if (e.button === 0 && e.target.tagName === "svg") {{
    isPanning = true;
    startX = e.clientX - tx;
    startY = e.clientY - ty;
  }}
}});
window.addEventListener("mousemove", (e) => {{
  if (!isPanning) return;
  tx = e.clientX - startX;
  ty = e.clientY - startY;
  applyTransform();
}});
window.addEventListener("mouseup", () => {{ isPanning = false; }});

// ---- Render ----
let highlightedNodeId = null;
let filterTypeVal = "__all__";
let filterPredVal = "__all__";

function createEdgeEl(l) {{
  const line = document.createElementNS("http://www.w3.org/2000/svg", "line");
  line.setAttribute("class", "edge");
  line.setAttribute("data-id", l.id);
  line.setAttribute("data-source", l.source);
  line.setAttribute("data-target", l.target);
  line.setAttribute("stroke", "#9aa5b1");
  line.setAttribute("stroke-width", edgeStroke);
  line.setAttribute("opacity", "1");
  edgeLayer.appendChild(line);
  return line;
}}

function createNodeEl(n) {{
  const g = document.createElementNS("http://www.w3.org/2000/svg", "g");
  g.setAttribute("class", "node");
  g.setAttribute("data-id", n.id);

  const circle = document.createElementNS("http://www.w3.org/2000/svg", "circle");
  circle.setAttribute("r", nodeRadius);
  circle.setAttribute("fill", colorFor(n.entity_type));
  circle.setAttribute("stroke", "#fff");
  circle.setAttribute("stroke-width", "1.5");
  g.appendChild(circle);

  const label = document.createElementNS("http://www.w3.org/2000/svg", "text");
  label.setAttribute("class", "node-label");
  label.setAttribute("dy", "-10");
  label.textContent = n.name;
  g.appendChild(label);

  // Hover tooltip
  g.addEventListener("mouseenter", () => {{
    tooltip.classList.add("visible");
    tooltip.innerHTML = `
      <b>${{escapeHtml(n.name)}}</b>
      <div class="type">${{escapeHtml(n.entity_type || "未知")}}</div>
      ${{n.description ? `<div>${{escapeHtml(n.description)}}</div>` : ""}}
    `;
  }});
  g.addEventListener("mousemove", (e) => {{
    tooltip.style.left = (e.clientX + 12) + "px";
    tooltip.style.top = (e.clientY + 12) + "px";
  }});
  g.addEventListener("mouseleave", () => {{ tooltip.classList.remove("visible"); }});

  // Click to highlight neighbors
  g.addEventListener("click", (e) => {{
    e.stopPropagation();
    highlightedNodeId = highlightedNodeId === n.id ? null : n.id;
    render();
  }});

  nodeLayer.appendChild(g);
  return g;
}}

function escapeHtml(s) {{
  return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}}

function passesFilter(n) {{
  if (filterTypeVal !== "__all__" && n.entity_type !== filterTypeVal) return false;
  return true;
}}
function linkPassesFilter(l) {{
  if (filterPredVal !== "__all__" && l.predicate !== filterPredVal) return false;
  const sn = nodeById.get(l.source);
  const tn = nodeById.get(l.target);
  return passesFilter(sn) && passesFilter(tn);
}}

function render() {{
  // Clear layers
  edgeLayer.innerHTML = "";
  nodeLayer.innerHTML = "";

  // Filter links
  const activeLinks = links.filter(linkPassesFilter);

  // Determine highlight set
  let highlightIds = new Set();
  if (highlightedNodeId !== null) {{
    highlightIds.add(highlightedNodeId);
    const neigh = adj.get(highlightedNodeId);
    if (neigh) {{
      for (const nb of neigh) highlightIds.add(nb);
    }}
    // highlight incident edges
    for (const l of activeLinks) {{
      if (l.source === highlightedNodeId || l.target === highlightedNodeId) {{
        highlightIds.add(`edge-${{l.id}}`);
      }}
    }}
  }}

  // Render edges first (below nodes)
  for (const l of activeLinks) {{
    const line = createEdgeEl(l);
    const isEdgeHighlighted = highlightIds.has(`edge-${{l.id}}`);
    line.setAttribute("class", isEdgeHighlighted ? "edge edge-highlighted" : "edge edge-dimmed");
    edgeLayer.appendChild(line);
  }}

  // Render nodes
  for (const n of nodes) {{
    if (!passesFilter(n)) continue;
    const g = createNodeEl(n);
    const isNodeHighlighted = highlightIds.has(n.id);
    g.setAttribute("class", `node ${{isNodeHighlighted ? "highlighted" : "dimmed"}}`);
    nodeLayer.appendChild(g);
  }}

  updateStatus();
}}

function updateStatus() {{
  const visibleNodes = nodes.filter(passesFilter).length;
  const visibleLinks = links.filter(linkPassesFilter).length;
  statusText.textContent = `实体: ${{visibleNodes}} / ${{nodes.length}}   三元组: ${{visibleLinks}} / ${{links.length}}`;
  updateLegend();
}}

function updateLegend() {{
  legendList.innerHTML = "";
  for (const [type, count] of LEGEND_ITEMS) {{
    const div = document.createElement("div");
    div.className = `legend-item ${{filterTypeVal === type ? "active" : ""}}`;
    const color = document.createElement("div");
    color.className = "legend-color";
    color.style.backgroundColor = colorFor(type);
    const name = document.createElement("span");
    name.className = "legend-name";
    name.textContent = type;
    const cnt = document.createElement("span");
    cnt.className = "legend-count";
    cnt.textContent = count;
    div.appendChild(color);
    div.appendChild(name);
    div.appendChild(cnt);
    div.addEventListener("click", () => {{
      filterTypeVal = (filterTypeVal === type) ? "__all__" : type;
      filterType.value = filterTypeVal;
      render();
    }});
    legendList.appendChild(div);
  }}
}}

// ---- Force-directed layout (Verlet integration) ----
function tick() {{
  // Repulsion: node-node
  const kRepel = 600;
  const kLink = 0.08;
  const linkDist = 90;
  const gravity = 0.015;
  const damping = 0.85;

  // Clear forces implicitly by recomputing positions from scratch each tick
  const pos = nodes.map(n => ({{x: n.x, y: n.y}}));

  // Repulsion between all pairs (O(n^2), fine for ~2k nodes with sampling)
  // To keep it interactive, limit repulsion to nearby pairs via grid is skipped;
  // we use a simple Barnes-Hut-lite: only repel within a radius.
  const repelRadius = 150;
  for (let i = 0; i < nodes.length; i++) {{
    const a = nodes[i];
    if (!passesFilter(a)) continue;
    for (let j = i + 1; j < nodes.length; j++) {{
      const b = nodes[j];
      if (!passesFilter(b)) continue;
      const dx = a.x - b.x;
      const dy = a.y - b.y;
      const d2 = dx * dx + dy * dy;
      if (d2 === 0 || d2 > repelRadius * repelRadius) continue;
      const d = Math.sqrt(d2);
      const force = kRepel / d2;
      const fx = (dx / d) * force;
      const fy = (dy / d) * force;
      a.vx += fx; a.vy += fy;
      b.vx -= fx; b.vy -= fy;
    }}
  }}

  // Link attraction
  for (const l of links) {{
    if (!linkPassesFilter(l)) continue;
    const a = nodeById.get(l.source);
    const b = nodeById.get(l.target);
    if (!a || !b) continue;
    const dx = b.x - a.x;
    const dy = b.y - a.y;
    const d = Math.sqrt(dx * dx + dy * dy) || 1;
    const force = (d - linkDist) * kLink;
    const fx = (dx / d) * force;
    const fy = (dy / d) * force;
    if (!a.fixed) {{ a.vx -= fx; a.vy -= fy; }}
    if (!b.fixed) {{ b.vx += fx; b.vy += fy; }}
  }}

  // Gravity to center + damping
  const cx = width / 2;
  const cy = height / 2;
  for (const n of nodes) {{
    if (n.fixed) continue;
    n.vx += (cx - n.x) * gravity;
    n.vy += (cy - n.y) * gravity;
    n.vx *= damping;
    n.vy *= damping;
    n.x += n.vx;
    n.y += n.vy;
  }}

  // Apply transform offset so graph stays centered under pan/zoom
  // (positions are in "data" space; view transform handles pan/zoom)
  requestAnimationFrame(() => {{
    drawPositions();
    tick();
  }});
}}

function drawPositions() {{
  // Update edge lines
  const lines = edgeLayer.querySelectorAll("line");
  // Map by data-id
  const lineById = new Map();
  for (const line of lines) lineById.set(line.getAttribute("data-id"), line);
  for (const l of links) {{
    if (!linkPassesFilter(l)) continue;
    const line = lineById.get(String(l.id));
    if (!line) continue;
    const a = nodeById.get(l.source);
    const b = nodeById.get(l.target);
    if (!a || !b) continue;
    line.setAttribute("x1", a.x);
    line.setAttribute("y1", a.y);
    line.setAttribute("x2", b.x);
    line.setAttribute("y2", b.y);
  }}

  // Update node groups
  const groups = nodeLayer.querySelectorAll("g.node");
  const groupById = new Map();
  for (const g of groups) groupById.set(g.getAttribute("data-id"), g);
  for (const n of nodes) {{
    if (!passesFilter(n)) continue;
    const g = groupById.get(String(n.id));
    if (!g) continue;
    g.setAttribute("transform", `translate(${{n.x}},${{n.y}})`);
  }}
}}

// ---- Controls ----
function populateFilters() {{
  for (const p of PREDICATES) {{
    const o = document.createElement("option");
    o.value = p;
    o.textContent = p;
    filterPredicate.appendChild(o);
  }}
  for (const [type] of LEGEND_ITEMS) {{
    const o = document.createElement("option");
    o.value = type;
    o.textContent = type;
    filterType.appendChild(o);
  }}
}}

filterType.addEventListener("change", (e) => {{
  filterTypeVal = e.target.value;
  render();
}});
filterPredicate.addEventListener("change", (e) => {{
  filterPredVal = e.target.value;
  render();
}});
btnReset.addEventListener("click", () => {{
  filterTypeVal = "__all__";
  filterPredVal = "__all__";
  filterType.value = "__all__";
  filterPredicate.value = "__all__";
  highlightedNodeId = null;
  render();
}});

// ---- Init ----
populateFilters();
updateLegend();
render();
tick();
</script>
</body>
</html>
"""


def save_html(html_content: str, output_path: str) -> Path:
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html_content, encoding="utf-8")
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="MemALL knowledge graph visualizer")
    parser.add_argument("--db", default=DEFAULT_DB, help="Path to MemALL SQLite DB")
    parser.add_argument("--out", default=DEFAULT_OUT, help="Output HTML path")
    parser.add_argument("--title", default="MemALL Knowledge Graph", help="Page title")
    args = parser.parse_args()

    graph = load_graph(args.db)
    html_content = build_html(graph, title=args.title)
    out = save_html(html_content, args.out)
    print(f"Nodes: {len(graph['nodes'])}  Links: {len(graph['links'])}")
    print(f"Wrote {out}")


if __name__ == "__main__":
    main()
