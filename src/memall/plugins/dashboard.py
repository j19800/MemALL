"""
Dashboard Plugin — Generate a self-contained HTML dashboard for MemALL.
"""

import json
from pathlib import Path
from typing import Optional

from memall.core.db import get_conn


def generate_dashboard(output_path: Optional[str] = None) -> str:
    """Generate a self-contained HTML dashboard with stats, charts, and timeline.

    The output is a single .html file with embedded CSS and JavaScript (no
    external dependencies). Charts are drawn with Canvas API.

    Args:
        output_path: Where to save the HTML file. Defaults to
                     ~/.memall/dashboard.html.

    Returns:
        The absolute path to the generated HTML file.
    """
    if output_path is None:
        output_path = str(Path.home() / ".memall" / "dashboard.html")
    else:
        output_path = str(Path(output_path))

    # Gather stats
    conn = get_conn()
    total_memories = conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
    total_agents = conn.execute(
        "SELECT COUNT(DISTINCT agent_name) FROM memories"
    ).fetchone()[0]
    total_edges = conn.execute("SELECT COUNT(*) FROM edges").fetchone()[0]
    today_new = conn.execute(
        "SELECT COUNT(*) FROM memories WHERE date(created_at) = date('now')"
    ).fetchone()[0]

    # Category distribution
    cat_rows = conn.execute(
        "SELECT category, COUNT(*) as cnt FROM memories GROUP BY category ORDER BY cnt DESC"
    ).fetchall()
    categories = [r[0] for r in cat_rows]
    cat_counts = [r[1] for r in cat_rows]

    # Recent timeline (last 10)
    recent = conn.execute(
        "SELECT agent_name, content, category, created_at "
        "FROM memories ORDER BY created_at DESC LIMIT 10"
    ).fetchall()

    # ── Build HTML ──────────────────────────────────────────────────────
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>MemALL Dashboard</title>
<style>
* {{ margin:0; padding:0; box-sizing:border-box; }}
body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
       background: #0f172a; color: #e2e8f0; padding: 24px; }}
h1 {{ font-size: 24px; margin-bottom: 24px; color: #f1f5f9; }}
.stats {{ display:grid; grid-template-columns: repeat(auto-fit, minmax(160px,1fr));
          gap: 16px; margin-bottom: 32px; }}
.card {{ background: #1e293b; border-radius: 12px; padding: 20px;
         border: 1px solid #334155; }}
.card .label {{ font-size: 12px; color: #94a3b8; text-transform: uppercase;
               letter-spacing: 0.5px; margin-bottom: 8px; }}
.card .value {{ font-size: 32px; font-weight: 700; color: #f8fafc; }}
.section {{ margin-bottom: 32px; }}
.section h2 {{ font-size: 18px; margin-bottom: 16px; color: #cbd5e1; }}
.chart-wrap {{ background: #1e293b; border-radius:12px; padding:20px;
               border:1px solid #334155; }}
canvas {{ width:100%; max-height:300px; }}
table {{ width:100%; border-collapse:collapse; background:#1e293b;
         border-radius:12px; overflow:hidden; border:1px solid #334155; }}
th, td {{ padding:10px 14px; text-align:left; border-bottom:1px solid #334155; }}
th {{ background:#0f172a; color:#94a3b8; font-size:12px; text-transform:uppercase; }}
td {{ font-size:13px; }}
tr:last-child td {{ border-bottom:none; }}
.content-cell {{ max-width:400px; overflow:hidden; text-overflow:ellipsis;
                white-space:nowrap; }}
.refresh {{ display:inline-block; padding:8px 16px; background:#3b82f6;
           color:white; border:none; border-radius:8px; cursor:pointer;
           font-size:13px; margin-bottom:16px; }}
.refresh:hover {{ background:#2563eb; }}
</style>
</head>
<body>
<h1>MemALL Dashboard</h1>
<div class="stats">
  <div class="card"><div class="label">Total Memories</div><div class="value">{total_memories}</div></div>
  <div class="card"><div class="label">Agents</div><div class="value">{total_agents}</div></div>
  <div class="card"><div class="label">Edges</div><div class="value">{total_edges}</div></div>
  <div class="card"><div class="label">Today New</div><div class="value">{today_new}</div></div>
</div>

<div class="section">
  <h2>Category Distribution</h2>
  <div class="chart-wrap">
    <canvas id="catChart"></canvas>
  </div>
</div>

<div class="section">
  <h2>Recent Timeline</h2>
  <table>
    <thead><tr><th>Agent</th><th>Content</th><th>Category</th><th>Time</th></tr></thead>
    <tbody>
"""

    for row in recent:
        agent, content, cat, ts = row
        display_time = str(ts)[:19] if ts else "-"
        safe_content = content[:120].replace("<", "&lt;").replace(">", "&gt;")
        html += (
            f'<tr><td>{agent}</td>'
            f'<td class="content-cell" title="{safe_content}">{safe_content}</td>'
            f'<td>{cat}</td><td>{display_time}</td></tr>\n'
        )

    cat_labels_json = json.dumps(categories)
    cat_data_json = json.dumps(cat_counts)

    html += f"""    </tbody>
  </table>
</div>

<script>
(function() {{
  var canvas = document.getElementById('catChart');
  if (!canvas) return;
  var ctx = canvas.getContext('2d');

  var labels = {cat_labels_json};
  var data = {cat_data_json};
  var max = Math.max.apply(null, data) || 1;

  canvas.width = canvas.parentElement.clientWidth - 40;
  canvas.height = 280;
  var W = canvas.width, H = canvas.height;
  var barW = Math.max((W - 80) / data.length - 10, 20);
  var colors = ['#3b82f6','#10b981','#f59e0b','#ef4444','#8b5cf6','#ec4899','#06b6d4','#f97316'];

  // Axes
  ctx.strokeStyle = '#475569';
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(60, 20); ctx.lineTo(60, H-40); ctx.lineTo(W-20, H-40);
  ctx.stroke();

  // Y-axis labels
  ctx.fillStyle = '#94a3b8';
  ctx.font = '11px sans-serif';
  ctx.textAlign = 'right';
  for (var i = 0; i <= 4; i++) {{
    var y = H - 40 - (i / 4) * (H - 60);
    ctx.fillText(Math.round(max * i / 4), 55, y + 4);
    ctx.strokeStyle = '#334155';
    ctx.beginPath();
    ctx.moveTo(60, y); ctx.lineTo(W-20, y);
    ctx.stroke();
  }}

  // Bars
  for (var i = 0; i < data.length; i++) {{
    var x = 60 + i * (barW + 10);
    var h = Math.max((data[i] / max) * (H - 60), 2);
    var y = H - 40 - h;

    ctx.fillStyle = colors[i % colors.length];
    ctx.fillRect(x, y, barW, h);

    // X label
    ctx.fillStyle = '#94a3b8';
    ctx.font = '10px sans-serif';
    ctx.textAlign = 'center';
    ctx.fillText(labels[i], x + barW/2, H-25);

    // Value on top
    ctx.fillStyle = '#e2e8f0';
    ctx.font = '900 10px sans-serif';
    ctx.fillText(data[i], x + barW/2, y - 4);
  }}
}})();
</script>
</body>
</html>"""

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)

    return str(Path(output_path).resolve())


# Plugin metadata
def register():
    """Return plugin metadata."""
    return {
        "name": "dashboard",
        "version": "1.0.0",
        "description": "Self-contained HTML dashboard for MemALL statistics",
        "author": "MemALL",
    }