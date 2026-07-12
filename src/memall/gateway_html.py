"""
Gateway HTML rendering — extracted from gateway.py for modularity.

Handles all HTML page rendering endpoints.
"""

import json
import logging
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from memall.core.db import pool_conn, get_conn, db_stats
from memall.core.thin_waist import retrieve, MemoryInput, normalize_agent_name
from memall.core.models import MemoryInput as MemoryInputModel
from memall.gateway_utils import esc_html, _density_color

logger = logging.getLogger("memall.gateway.html")

# Shared HTML style block
HTML_STYLE = """
<style>
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;
max-width:960px;margin:0 auto;padding:20px;background:#fafafa;color:#333}
h1{font-size:20px;border-bottom:1px solid #eee;padding-bottom:8px}
table{width:100%;border-collapse:collapse;font-size:13px}
th,td{padding:6px 10px;text-align:left;border-bottom:1px solid #eee}
th{color:#888;font-weight:600;font-size:11px;text-transform:uppercase}
tr:hover td{background:#f0f0f0}
a{color:#2a7de1;text-decoration:none}
a:hover{text-decoration:underline}
pre{background:#f5f5f5;padding:10px;border-radius:4px;overflow-x:auto;font-size:12px}
.tag{display:inline-block;padding:1px 6px;border-radius:3px;font-size:10px;
font-weight:600;background:#e8e8e8;color:#666;margin:1px}
.lv-L6{background:#e8f5e9;color:#2e7d32}.lv-L4{background:#e3f2fd;color:#1565c0}
.lv-L7{background:#fce4ec;color:#c62828}.lv-L5{background:#fff3e0;color:#e65100}
.lv-L9{background:#f3e5f5;color:#6a1b9a}.lv-L10{background:#e0f2f1;color:#00695c}
.lv-L1{background:#e8eaf6;color:#283593}.lv-P2{background:#f5f5f5;color:#616161}
.text-muted{color:#999;font-size:12px}
.summary{color:#666;font-size:13px;line-height:1.5;margin:4px 0}
.empty-state{text-align:center;padding:60px 20px;color:#aaa;font-size:14px}
</style>
"""

# Shared navigation bar
NAV_HTML = ('<div style="margin-bottom:16px">'
    '<a href="/recent" style="color:#555;text-decoration:none;margin-right:16px">最近</a>'
    '<a href="/timeline" style="color:#555;text-decoration:none;margin-right:16px">时间线</a>'
    '<a href="/dashboard" style="color:#555;text-decoration:none;margin-right:16px">仪表盘</a>'
    '<a href="/todos" style="color:#555;text-decoration:none;margin-right:16px">待办</a>'
    '<a href="/discussions" style="color:#555;text-decoration:none;margin-right:16px">讨论</a>'
    '<a href="/graph" style="color:#555;text-decoration:none;margin-right:16px">图谱</a>'
    '<a href="/artifact" style="color:#555;text-decoration:none;margin-right:16px">工单</a>'
    '<a href="/features" style="color:#555;text-decoration:none;margin-right:16px">功能</a>'
    '</div>')


def handle_recent(conn) -> str:
    """GET /recent — last 30 memories as HTML."""
    rows = conn.execute(
        "SELECT id, content, level, category, agent_name, subject, created_at "
        "FROM memories ORDER BY created_at DESC LIMIT 30"
    ).fetchall()
    items = "".join(
        f"<tr><td>{r['id']}</td>"
        f"<td><span class='tag lv-{r['level']}'>{esc_html(r['level'] or '-')}</span></td>"
        f"<td>{esc_html(r['subject'] or (r['content'] or '')[:60])}</td>"
        f"<td>{esc_html(r['category'] or '-')}</td>"
        f"<td>{esc_html(r['agent_name'] or '-')}</td>"
        f"<td class='text-muted'>{esc_html(r['created_at'] or '')[:16]}</td></tr>"
        for r in rows
    )
    return (
        "<!DOCTYPE html><html><head><meta charset='utf-8'>"
        f"<title>MemALL · 最近记忆</title>{HTML_STYLE}</head><body>"
        f"<h1>🧠 最近记忆 <span style='font-size:14px;color:#999;font-weight:normal'>最新 30 条</span></h1>"
        f"{NAV_HTML}"
        f"<table><thead><tr><th>ID</th><th>Level</th><th>内容</th><th>分类</th><th>Agent</th><th>时间</th></tr></thead>"
        f"<tbody>{items}</tbody></table></body></html>"
    )


def handle_identity(conn, agent_name: str) -> str:
    """GET /identity/{agent_name} — L1/L7 profile as HTML."""
    ident = conn.execute(
        "SELECT agent_name, agent_type, description, identity_profile, persona_updated_at "
        "FROM identities WHERE LOWER(agent_name) = LOWER(?)", (agent_name,)
    ).fetchone()
    name = agent_name
    profile_data = {}
    if ident:
        name = ident["agent_name"]
        try:
            profile_data = json.loads(ident["identity_profile"] or "{}")
        except Exception:
            profile_data = {}
    l1s = conn.execute(
        "SELECT subject, content, created_at FROM memories "
        "WHERE LOWER(agent_name)=LOWER(?) AND level='L1' ORDER BY created_at DESC LIMIT 20",
        (agent_name,)
    ).fetchall()
    l7s = conn.execute(
        "SELECT subject, content, created_at FROM memories "
        "WHERE LOWER(agent_name)=LOWER(?) AND level='L7' ORDER BY created_at DESC LIMIT 20",
        (agent_name,)
    ).fetchall()
    # Build HTML
    l1_rows = "".join(
        f"<tr><td>{esc_html(r['subject'][:60])}</td><td>{esc_html(r['content'][:120])}</td>"
        f"<td class='text-muted'>{esc_html(r['created_at'])[:16]}</td></tr>"
        for r in l1s
    ) or "<tr><td colspan='3' class='empty-state'>暂无 L1 身份记录</td></tr>"
    l7_rows = "".join(
        f"<tr><td>{esc_html(r['subject'][:60])}</td><td>{esc_html(r['content'][:120])}</td>"
        f"<td class='text-muted'>{esc_html(r['created_at'])[:16]}</td></tr>"
        for r in l7s
    ) or "<tr><td colspan='3' class='empty-state'>暂无 L7 偏好记录</td></tr>"
    return (
        "<!DOCTYPE html><html><head><meta charset='utf-8'>"
        f"<title>MemALL · {esc_html(name)}</title>{HTML_STYLE}</head><body>"
        f"<h1>🧑 {esc_html(name)}</h1>{NAV_HTML}"
        f"<h2>L1 身份</h2><table><thead><tr><th>主题</th><th>内容</th><th>时间</th></tr></thead><tbody>{l1_rows}</tbody></table>"
        f"<h2>L7 偏好</h2><table><thead><tr><th>主题</th><th>内容</th><th>时间</th></tr></thead><tbody>{l7_rows}</tbody></table>"
        "</body></html>"
    )


def handle_graph_stats(conn) -> str:
    """GET /graph — memory/edge graph stats as HTML."""
    mem_count = conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
    edge_count = conn.execute("SELECT COUNT(*) FROM edges").fetchone()[0]
    agents = conn.execute(
        "SELECT agent_name, COUNT(*) as cnt FROM memories GROUP BY agent_name ORDER BY cnt DESC LIMIT 20"
    ).fetchall()
    level_dist = conn.execute(
        "SELECT level, COUNT(*) as cnt FROM memories GROUP BY level ORDER BY cnt DESC"
    ).fetchall()
    recent_edges = conn.execute(
        "SELECT e.id, e.source_id, e.target_id, e.relation_type, "
        "m1.content as src, m2.content as tgt, e.created_at "
        "FROM edges e JOIN memories m1 ON e.source_id=m1.id "
        "JOIN memories m2 ON e.target_id=m2.id ORDER BY e.id DESC LIMIT 50"
    ).fetchall()
    agent_rows = "".join(
        f"<tr><td>{esc_html(r['agent_name'])}</td><td>{r['cnt']}</td></tr>"
        for r in agents
    )
    level_rows = "".join(
        f"<tr><td><span class='tag lv-{r['level']}'>{esc_html(r['level'])}</span></td><td>{r['cnt']}</td></tr>"
        for r in level_dist
    )
    edge_rows = "".join(
        f"<tr><td>{r['id']}</td><td>{r['source_id']}</td><td>{r['target_id']}</td>"
        f"<td><span class='tag'>{esc_html(r['relation_type'])}</span></td>"
        f"<td>{esc_html((r['src'] or '')[:40])}</td>"
        f"<td>{esc_html((r['tgt'] or '')[:40])}</td>"
        f"<td class='text-muted'>{esc_html(r['created_at'])[:16]}</td></tr>"
        for r in recent_edges
    )
    return (
        "<!DOCTYPE html><html><head><meta charset='utf-8'>"
        f"<title>MemALL · 图谱</title>{HTML_STYLE}"
        "<script src='https://cdn.jsdelivr.net/npm/d3@7'></script>"
        "</head><body>"
        f"<h1>🕸️ 记忆图谱</h1>{NAV_HTML}"
        f"<p>记忆: {mem_count} | 边: {edge_count}</p>"
        "<div id='graph-canvas' style='width:100%;height:400px;border:1px solid #eee;border-radius:6px;margin:12px 0;background:#fff'></div>"
        "<script>\n"
        "const edges = " + json.dumps([{"source": r["source_id"], "target": r["target_id"], "type": r["relation_type"]} for r in recent_edges], ensure_ascii=False) + ";\n"
        "const nodes = [];\n"
        "const seen = new Set();\n"
        "for (const e of edges) {\n"
        "  if (!seen.has(e.source)) { seen.add(e.source); nodes.push({id: e.source, label: '#'+e.source}); }\n"
        "  if (!seen.has(e.target)) { seen.add(e.target); nodes.push({id: e.target, label: '#'+e.target}); }\n"
        "}\n"
        "const width = document.getElementById('graph-canvas').clientWidth;\n"
        "const height = 400;\n"
        "const svg = d3.select('#graph-canvas').append('svg').attr('width', width).attr('height', height);\n"
        "const simulation = d3.forceSimulation(nodes)\n"
        "  .force('link', d3.forceLink(edges).id(d => d.id).distance(80))\n"
        "  .force('charge', d3.forceManyBody().strength(-200))\n"
        "  .force('center', d3.forceCenter(width/2, height/2));\n"
        "const link = svg.append('g').selectAll('line').data(edges).join('line')\n"
        "  .attr('stroke', '#ccc').attr('stroke-width', 1).attr('stroke-opacity', 0.6);\n"
        "const node = svg.append('g').selectAll('circle').data(nodes).join('circle')\n"
        "  .attr('r', 6).attr('fill', '#4a90d9')\n"
        "  .call(d3.drag().on('start', (e,d) => { if(!e.active) simulation.alphaTarget(0.3).restart(); d.fx = d.x; d.fy = d.y; })\n"
        "    .on('drag', (e,d) => { d.fx = e.x; d.fy = e.y; })\n"
        "    .on('end', (e,d) => { if(!e.active) simulation.alphaTarget(0); d.fx = null; d.fy = null; }));\n"
        "node.append('title').text(d => d.label);\n"
        "simulation.on('tick', () => { link.attr('x1', d=>d.source.x).attr('y1', d=>d.source.y).attr('x2', d=>d.target.x).attr('y2', d=>d.target.y); node.attr('cx', d=>d.x).attr('cy', d=>d.y); });\n"
        "</script>"
        f"<h2>Agent 分布</h2><table><thead><tr><th>Agent</th><th>记忆数</th></tr></thead><tbody>{agent_rows}</tbody></table>"
        f"<h2>Level 分布</h2><table><thead><tr><th>Level</th><th>计数</th></tr></thead><tbody>{level_rows}</tbody></table>"
        f"<h2>最近 50 条边</h2><table><thead><tr><th>ID</th><th>源</th><th>目标</th><th>类型</th><th>源内容</th><th>目标内容</th><th>时间</th></tr></thead><tbody>{edge_rows}</tbody></table>"
        "</body></html>"
    )


def render_artifact_html() -> str:
    """Render session artifact checklist as (static) HTML."""
    return f"""<!DOCTYPE html><html><head><meta charset='utf-8'>
<title>MemALL · 工单</title>{HTML_STYLE}</head><body>
<h1>📋 Session 成果清单</h1>{NAV_HTML}
<div class='summary'>
<p>每次 session 结束后，应产出以下至少一项成果：</p>
<ul>
<li>L4 会话摘要（session_end 自动生成）</li>
<li>L6 反思（复杂操作后自主生成）</li>
<li>L7 教训 / 偏好（distill_l7 从 L6 提取）</li>
<li>L9 蒸馏（distill 按分类聚合低层记忆）</li>
<li>L10 集成（integrate 从多个 L9 中提炼）</li>
<li>L11 领域知识（改进后的业务 insights）</li>
</ul>
</div></body></html>"""


def handle_artifact() -> str:
    """GET /artifact — static artifact page."""
    return render_artifact_html()


def render_features_html(gateway_version: str = "unknown") -> str:
    """Render full feature report as HTML."""
    try:
        from memall.gateway import _handle_api_routes
    except ImportError:
        pass
    routes_html = "<tr><td colspan='3' class='empty-state'>(gateway not available)</td></tr>"
    try:
        from memall.gateway import MemAllGateway
        # Minimal route list
        routes = [
            ("GET", "/health", "服务器健康"),
            ("GET", "/recent", "最近记忆 (HTML)"),
            ("GET", "/todos", "任务管理 (HTML)"),
            ("GET", "/timeline", "时间线 (HTML)"),
            ("GET", "/dashboard", "仪表盘 (HTML)"),
            ("GET", "/graph", "图谱 (HTML)"),
            ("GET", "/identity/{name}", "身份画像 (HTML)"),
            ("GET", "/discussions", "讨论列表 (HTML)"),
            ("GET", "/artifact", "工单 (HTML)"),
            ("GET", "/features", "功能列表 (HTML)"),
            ("POST", "/capture", "写入记忆"),
            ("POST", "/retrieve", "搜索记忆"),
            ("POST", "/traverse", "遍历图谱"),
            ("POST", "/timeline", "时间线 (JSON)"),
            ("POST", "/profile", "生成画像"),
            ("POST", "/pair", "配对"),
            ("GET", "/api/timeline", "时间线 (API)"),
            ("GET", "/api/arcs", "决策弧"),
            ("GET", "/api/slices", "时间片"),
            ("GET", "/memories", "记忆列表"),
            ("POST", "/memories", "写入记忆 (API)"),
            ("GET", "/memories/search", "搜索 (API)"),
            ("GET", "/memories/stats", "统计"),
            ("POST", "/edges", "创建边"),
            ("GET", "/graph/{node_id}", "图遍历"),
            ("POST", "/forget", "遗忘"),
            ("POST", "/ops", "操作"),
            ("POST", "/security", "安全审计"),
            ("POST", "/adaptive", "自适应"),
            ("GET", "/agents", "Agent 列表"),
            ("GET", "/db/stats", "DB 统计"),
            ("GET", "/db/optimize", "DB 优化"),
            ("POST", "/pipeline/run", "运行管线"),
            ("POST", "/mcp", "MCP JSON-RPC"),
            ("GET", "/mcp", "MCP SSE"),
        ]
        routes_html = "".join(
            f"<tr><td>{m}</td><td>{p}</td><td>{d}</td></tr>"
            for m, p, d in routes
        )
    except Exception:
        pass
    return f"""<!DOCTYPE html><html><head><meta charset='utf-8'>
<title>MemALL · 功能</title>{HTML_STYLE}</head><body>
<h1>📡 MemALL Gateway v{gateway_version}</h1>{NAV_HTML}
<h2>HTTP API 路由</h2><table><thead><tr><th>方法</th><th>路径</th><th>说明</th></tr></thead><tbody>{routes_html}</tbody></table>
</body></html>"""


def handle_features(gateway_version: str = "unknown") -> str:
    """GET /features — static features page."""
    return render_features_html(gateway_version)


def handle_todos(conn) -> str:
    """GET /todos — task board as HTML."""
    active = conn.execute(
        "SELECT id, content, subject, category, created_at FROM memories "
        "WHERE level='L5' AND json_extract(metadata, '$.status')='active' "
        "ORDER BY created_at DESC LIMIT 50"
    ).fetchall()
    blocked = conn.execute(
        "SELECT id, content, subject, category, created_at FROM memories "
        "WHERE level='L5' AND json_extract(metadata, '$.status')='blocked' "
        "ORDER BY created_at DESC LIMIT 20"
    ).fetchall()
    resolved = conn.execute(
        "SELECT id, content, subject, category, created_at FROM memories "
        "WHERE level='L5' AND json_extract(metadata, '$.status') IN ('done','archived') "
        "ORDER BY created_at DESC LIMIT 20"
    ).fetchall()
    def _rows(rows):
        return "".join(
            f"<tr><td>{r['id']}</td><td>{esc_html(r['subject'][:60])}</td>"
            f"<td>{esc_html(r['category'])}</td>"
            f"<td class='text-muted'>{esc_html(r['created_at'])[:16]}</td></tr>"
            for r in rows
        ) or "<tr><td colspan='4' class='empty-state'>空</td></tr>"
    return (
        "<!DOCTYPE html><html><head><meta charset='utf-8'>"
        f"<title>MemALL · 待办</title>{HTML_STYLE}</head><body>"
        f"<h1>📋 任务管理</h1>{NAV_HTML}"
        f"<h2>进行中 ({len(active)})</h2><table><thead><tr><th>ID</th><th>任务</th><th>分类</th><th>创建</th></tr></thead><tbody>{_rows(active)}</tbody></table>"
        f"<h2>阻塞 ({len(blocked)})</h2><table><thead><tr><th>ID</th><th>任务</th><th>分类</th><th>创建</th></tr></thead><tbody>{_rows(blocked)}</tbody></table>"
        f"<h2>已完成 ({len(resolved)})</h2><table><thead><tr><th>ID</th><th>任务</th><th>分类</th><th>创建</th></tr></thead><tbody>{_rows(resolved)}</tbody></table>"
        "</body></html>"
    )