import logging
"""
Exporter Plugin — Export agent memories to Markdown, JSONL, CSV, and HTML.
"""

import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from memall.core.db import get_conn

logger = logging.getLogger(__name__)

# ── Hook support ───────────────────────────────────────────────────────
_export_counter: int = 0
_EXPORT_INTERVAL = 10  # auto-export every N captures


def _record_plugin_event(hook_point: str, description: str, status: str = "ok") -> None:
    """Record an exporter plugin event into the hook effects ring buffer."""
    try:
        from memall.mcp.hook_effects import record_event as _re
        _re(hook_point=hook_point, description=description, plugin="exporter", status=status)
    except Exception:
        logger.warning("Failed to record exporter plugin event for %s", hook_point, exc_info=True)


def _get_agent_memories(agent_name: str) -> List[Dict[str, Any]]:
    """Fetch all memories + edges for a given agent, ordered by time descending."""
    conn = get_conn()

    memories = conn.execute(
        """SELECT id, agent_name, content, category, level, created_at,
                  subject, project, confidence, visibility, tags
           FROM memories WHERE agent_name = ? ORDER BY created_at DESC LIMIT 1000""",
        (agent_name,),
    ).fetchall()

    result: List[Dict[str, Any]] = []
    for row in memories:
        mem_id = row[0]
        edges = conn.execute(
            "SELECT source_id, target_id, relation, weight FROM edges WHERE source_id = ? OR target_id = ?",
            (mem_id, mem_id),
        ).fetchall()

        result.append({
            "id": mem_id,
            "agent_name": row[1],
            "content": row[2],
            "category": row[3],
            "level": row[4],
            "created_at": row[5],
            "subject": row[6] or "",
            "project": row[7] or "",
            "confidence": row[8],
            "visibility": row[9] or "public",
            "tags": row[10] or "[]",
            "edges": [
                {"source": e[0], "target": e[1], "relation": e[2], "weight": e[3]}
                for e in edges
            ],
        })

    return result


def export_markdown(agent_name: str, output_path: Optional[str] = None) -> str:
    """Export all memories for an agent as a Markdown file.

    Args:
        agent_name: Agent whose memories to export.
        output_path: Output path. Defaults to ~/.memall/exports/{agent}_memories.md.

    Returns:
        Absolute path to the generated file.
    """
    memories = _get_agent_memories(agent_name)

    if output_path is None:
        out_dir = Path.home() / ".memall" / "exports"
        out_dir.mkdir(parents=True, exist_ok=True)
        output_path = str(out_dir / f"{agent_name}_memories.md")
    else:
        output_path = str(output_path)

    lines: List[str] = []
    lines.append(f"# Memories: {agent_name}")
    lines.append(f"Exported at {datetime.now(timezone.utc).isoformat()[:19]}")
    lines.append(f"Total: {len(memories)} memories")
    lines.append("")

    for m in memories:
        ts = str(m["created_at"])[:19] if m["created_at"] else "?"
        lines.append(f"## [{m['level']}] {m['category']} — {ts}")
        if m["subject"]:
            lines.append(f"**Subject:** {m['subject']}")
        if m["project"]:
            lines.append(f"**Project:** {m['project']}")
        tags_str = ""
        try:
            tags = json.loads(m["tags"]) if isinstance(m["tags"], str) else m["tags"]
            if tags:
                tags_str = f"  Tags: {', '.join(tags)}"
        except (json.JSONDecodeError, TypeError):
            tags_str = ""
        lines.append(
            f"*ID: {m['id']} | Confidence: {m['confidence']:.2f} | Visibility: {m['visibility']}*{tags_str}"
        )
        lines.append("")
        lines.append(m["content"])
        lines.append("")
        lines.append("---")
        lines.append("")

    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    return str(Path(output_path).resolve())


def export_jsonl(agent_name: str, output_path: Optional[str] = None) -> str:
    """Export all memories for an agent as JSONL (one JSON object per line).

    Args:
        agent_name: Agent whose memories to export.
        output_path: Output path. Defaults to ~/.memall/exports/{agent}_memories.jsonl.

    Returns:
        Absolute path to the generated file.
    """
    memories = _get_agent_memories(agent_name)

    if output_path is None:
        out_dir = Path.home() / ".memall" / "exports"
        out_dir.mkdir(parents=True, exist_ok=True)
        output_path = str(out_dir / f"{agent_name}_memories.jsonl")
    else:
        output_path = str(output_path)

    with open(output_path, "w", encoding="utf-8") as f:
        for m in memories:
            # Convert datetime to string for JSON serialization
            export = dict(m)
            if isinstance(export["created_at"], (datetime,)):
                export["created_at"] = export["created_at"].isoformat()
            f.write(json.dumps(export, ensure_ascii=False) + "\n")

    return str(Path(output_path).resolve())


def export_csv(agent_name: str, output_path: Optional[str] = None) -> str:
    """Export all memories for an agent as CSV.

    Columns: id, agent_name, category, level, content, tags, created_at,
             subject, project, confidence, visibility.

    Args:
        agent_name: Agent whose memories to export.
        output_path: Output path. Defaults to ~/.memall/exports/{agent}_memories.csv.

    Returns:
        Absolute path to the generated file.
    """
    memories = _get_agent_memories(agent_name)

    if output_path is None:
        out_dir = Path.home() / ".memall" / "exports"
        out_dir.mkdir(parents=True, exist_ok=True)
        output_path = str(out_dir / f"{agent_name}_memories.csv")
    else:
        output_path = str(output_path)

    fieldnames = [
        "id", "agent_name", "category", "level", "content",
        "tags", "created_at", "subject", "project", "confidence", "visibility",
    ]

    with open(output_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for m in memories:
            row = {k: m.get(k, "") for k in fieldnames}
            if isinstance(row["created_at"], (datetime,)):
                row["created_at"] = row["created_at"].isoformat()
            if isinstance(row["tags"], list):
                row["tags"] = ",".join(row["tags"])
            writer.writerow(row)

    return str(Path(output_path).resolve())


def export_html(agent_name: str, output_path: Optional[str] = None) -> str:
    """Export all memories for an agent as a self-contained HTML page
    with search, filter, and collapsible categories.

    Args:
        agent_name: Agent whose memories to export.
        output_path: Output path. Defaults to ~/.memall/exports/{agent}_memories.html.

    Returns:
        Absolute path to the generated file.
    """
    memories = _get_agent_memories(agent_name)

    if output_path is None:
        out_dir = Path.home() / ".memall" / "exports"
        out_dir.mkdir(parents=True, exist_ok=True)
        output_path = str(out_dir / f"{agent_name}_memories.html")
    else:
        output_path = str(output_path)

    # Build category groups
    cat_groups: Dict[str, list] = {}
    for m in memories:
        cat = m.get("category", "general")
        cat_groups.setdefault(cat, []).append(m)

    rows_html_parts: List[str] = []
    for cat, items in sorted(cat_groups.items()):
        rows_html_parts.append(
            f'<tr class="cat-header"><td colspan="4">'
            f'<strong>{cat}</strong> ({len(items)})</td></tr>'
        )
        for m in items:
            ts = str(m["created_at"])[:19] if m["created_at"] else "?"
            safe_content = m["content"][:200].replace("<", "&lt;").replace(">", "&gt;")
            tags = ""
            try:
                t = json.loads(m["tags"]) if isinstance(m["tags"], str) else m["tags"]
                if t:
                    tags = ", ".join(t)
            except (json.JSONDecodeError, TypeError):
                logger.warning("exporter.py: silent error", exc_info=True)
            rows_html_parts.append(
                f'<tr data-search="{safe_content.lower()} {m["category"]} {tags}">'
                f"<td>{ts}</td><td>[{m['level']}]</td>"
                f"<td>{safe_content}</td><td>{tags}</td></tr>"
            )
    rows_html = "\n".join(rows_html_parts)

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Memories: {agent_name}</title>
<style>
* {{ margin:0; padding:0; box-sizing:border-box; }}
body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
       background:#0f172a; color:#e2e8f0; padding:24px; }}
h1 {{ margin-bottom:16px; }}
input[type=text], select {{ padding:8px 12px; border-radius:8px; border:1px solid #334155;
       background:#1e293b; color:#e2e8f0; font-size:13px; margin-right:8px; }}
input[type=text] {{ width:260px; }}
table {{ width:100%; border-collapse:collapse; background:#1e293b;
         border-radius:12px; overflow:hidden; border:1px solid #334155; margin-top:16px; }}
th, td {{ padding:10px 14px; text-align:left; border-bottom:1px solid #334155; font-size:13px; }}
th {{ background:#0f172a; color:#94a3b8; text-transform:uppercase; font-size:11px; }}
.cat-header td {{ background:#334155; color:#94a3b8; font-size:12px; font-weight:600; }}
tr:last-child td {{ border-bottom:none; }}
.hidden {{ display:none; }}
</style>
</head>
<body>
<h1>Memories: {agent_name} ({len(memories)} total)</h1>
<input type="text" id="search" placeholder="Search..." oninput="filter()">
<select id="levelFilter" onchange="filter()">
  <option value="">All Levels</option>
  <option value="P0">P0</option>
  <option value="P1">P1</option>
  <option value="P2">P2</option>
</select>
<table><thead><tr><th>Time</th><th>Level</th><th>Content</th><th>Tags</th></tr></thead>
<tbody>{rows_html}</tbody></table>
<script>
function filter() {{
  var q = document.getElementById('search').value.toLowerCase();
  var lv = document.getElementById('levelFilter').value;
  var rows = document.querySelectorAll('tbody tr:not(.cat-header)');
  var headers = document.querySelectorAll('.cat-header');
  rows.forEach(function(r) {{
    var text = r.getAttribute('data-search') || '';
    var match = (!q || text.indexOf(q) >= 0) &&
                (!lv || text.indexOf('[' + lv + ']') >= 0);
    r.classList.toggle('hidden', !match);
  }});
  headers.forEach(function(h) {{ h.classList.remove('hidden'); }});
}}
</script>
</body>
</html>"""

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)

    return str(Path(output_path).resolve())


def on_capture(**kwargs) -> None:
    """Auto-export every N captures to JSONL."""
    global _export_counter
    _export_counter += 1

    memory_id = kwargs.get("memory_id")
    detail = f"memory #{memory_id}" if memory_id else f"#{_export_counter} total"

    if _export_counter % _EXPORT_INTERVAL != 0:
        _record_plugin_event("on_capture", f"Export skipped (counter={_export_counter}, interval={_EXPORT_INTERVAL})", status="skipped")
        return
    agent = kwargs.get("data", None)
    agent_name = getattr(agent, "agent_name", None) if agent else None
    if not agent_name:
        _record_plugin_event("on_capture", f"Export skipped (no agent_name for {detail})", status="skipped")
        return
    try:
        path = export_jsonl(agent_name)
        logger.info("Auto-exported %s to %s", agent_name, path)
        _record_plugin_event("on_capture", f"Auto-exported {agent_name} to JSONL ({detail})")
    except Exception:
        logger.exception("Auto-export failed for %s", agent_name)
        _record_plugin_event("on_capture", f"Auto-export failed for {agent_name} ({detail})", status="failed")


def register():
    """Return plugin metadata."""
    return {
        "name": "exporter",
        "version": "1.0.0",
        "description": "Export agent memories to Markdown, JSONL, CSV, and HTML",
        "author": "MemALL",
    }