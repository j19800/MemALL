"""MCP tool: memall_distill_pending — list groups needing LLM-written summaries.

After pipeline runs, L9/L10 groups have keyword fingerprints + source samples
but no human-readable narrative summary. This tool lets LLMs find and fill them.
"""

import json
import logging
from memall.core.db import get_conn

logger = logging.getLogger(__name__)


def _has_narrative(text: str) -> bool:
    """Check if content has human-written narrative beyond keywords + samples.

    A real narrative is a paragraph (2+ consecutive non-bullet lines)
    that contains reasoning language (这组、讨论、核心、结论、涉及、决定).
    """
    if not text:
        return False
    lines = text.split("\n")
    # Look for a paragraph section after the metadata/sample block
    para_started = False
    para_words = 0
    for line in lines:
        stripped = line.strip()
        # Skip empty lines, bullet samples, and keyword lines
        if not stripped or stripped.startswith("•") or stripped.startswith("关键词"):
            if para_started:
                # Paragraph ended — check if it was long enough
                if para_words >= 20:
                    return True
                para_started = False
                para_words = 0
            continue
        # A non-metadata line with narrative markers
        if any(m in stripped for m in ["这组", "讨论", "核心", "结论", "涉及", "决定",
                                        "主要", "包括", "说明", "建议", "分析"]):
            para_started = True
            para_words += len(stripped)
        elif para_started:
            para_words += len(stripped)
    # Check the last paragraph
    return para_started and para_words >= 20


def handle(arguments: dict) -> str:
    """List L9/L10 groups waiting for LLM-written narrative summary."""
    conn = get_conn()
    try:
        limit = arguments.get("limit", 10)
        action = arguments.get("action", "list")
        group_id = arguments.get("group_id")

        if action == "summarize" and group_id:
            analysis = {}
            if arguments.get("insight"):
                analysis["insight"] = arguments["insight"]
            if arguments.get("gap"):
                analysis["gap"] = arguments["gap"]
            if arguments.get("next"):
                analysis["next"] = arguments["next"]
            return _do_summarize(conn, group_id, arguments.get("summary", ""), analysis or None)

        # Find L9 groups with minimal content (need LLM summary)
        l9_rows = conn.execute(
            "SELECT m.id, m.agent_name, m.category, m.content, m.subject, "
            "COUNT(e.target_id) as source_count "
            "FROM memories m "
            "LEFT JOIN edges e ON e.source_id = m.id AND e.relation_type = 'refines' "
            "WHERE m.level = 'L9' "
            "GROUP BY m.id ORDER BY m.id DESC LIMIT ?",
            (limit * 3,),
        ).fetchall()

        pending = []
        for r in l9_rows:
            if _has_narrative(r["content"] or ""):
                continue
            source_ids = conn.execute(
                "SELECT target_id FROM edges WHERE source_id = ? AND relation_type = 'refines' LIMIT 10",
                (r["id"],),
            ).fetchall()
            source_id_list = [s["target_id"] for s in source_ids]
            pending.append({
                "id": r["id"],
                "level": "L9",
                "agent": r["agent_name"],
                "category": r["category"],
                "source_count": r["source_count"],
                "source_ids": source_id_list,
                "current_content": (r["content"] or "")[:200],
            })

        # Same for L10
        l10_rows = conn.execute(
            "SELECT m.id, m.agent_name, m.category, m.content, "
            "COUNT(e.target_id) as source_count "
            "FROM memories m "
            "LEFT JOIN edges e ON e.source_id = m.id AND e.relation_type = 'integrates' "
            "WHERE m.level = 'L10' "
            "GROUP BY m.id ORDER BY m.id DESC LIMIT ?",
            (limit,),
        ).fetchall()

        for r in l10_rows:
            if _has_narrative(r["content"] or ""):
                continue
            source_ids = conn.execute(
                "SELECT target_id FROM edges WHERE source_id = ? AND relation_type = 'integrates' LIMIT 10",
                (r["id"],),
            ).fetchall()
            source_id_list = [s["target_id"] for s in source_ids]
            pending.append({
                "id": r["id"],
                "level": "L10",
                "agent": r["agent_name"],
                "category": r["category"],
                "source_count": r["source_count"],
                "source_ids": source_id_list,
                "current_content": (r["content"] or "")[:200],
            })

        if not pending:
            return json.dumps({"status": "all_complete", "pending": []}, ensure_ascii=False)

        # Truncate to limit
        pending = pending[:limit]

        return json.dumps({
            "status": "pending_found",
            "count": len(pending),
            "pending": pending,
            "hint": "Use action=summarize&group_id=N&summary=... to write a narrative",
        }, ensure_ascii=False)

    finally:
        conn.close()


def _do_summarize(conn, group_id: int, summary: str, analysis: dict = None) -> str:
    """Write LLM-written narrative + optional structured analysis to L9/L10."""
    row = conn.execute(
        "SELECT id, level, content, metadata FROM memories WHERE id = ? AND level IN ('L9', 'L10')",
        (group_id,),
    ).fetchone()
    if not row:
        return json.dumps({"error": f"memory #{group_id} not found or not L9/L10"})

    current = row["content"] or ""
    level = row["level"]
    existing_meta = json.loads(row["metadata"]) if row["metadata"] and row["metadata"] != "{}" else {}

    if not summary or len(summary.strip()) < 10:
        return json.dumps({"error": "summary too short (min 10 chars)"})

    # Validate: summary should include narrative language markers
    narrative_markers = ["这组", "讨论", "核心", "涉及", "总结"]
    has_marker = any(m in summary for m in narrative_markers)
    if not has_marker:
        return json.dumps({
            "warning": "summary may be too thin — try including what this group is about",
            "hint": "add context like '这组记忆讨论了...'",
        })

    # Preserve the existing metadata header (keywords + samples)
    lines = current.split("\n")
    body_start = 0
    for i, line in enumerate(lines):
        body_start = i + 1
        if line.startswith("•") or line.startswith("关键词") or line.strip() == "":
            continue
        if i > 0 and (lines[i-1].startswith("[L9 ") or lines[i-1].startswith("[L10 ")):
            continue
        break

    # Keep header, append human-readable summary + analysis sections
    header = "\n".join(lines[:body_start]) if body_start < len(lines) else current
    parts = [header, "", summary]

    if analysis:
        insight = analysis.get("insight", "").strip()
        gap = analysis.get("gap", "").strip()
        next_step = analysis.get("next", "").strip()
        if insight:
            parts.append(f"\n💡 洞察：{insight}")
        if gap:
            parts.append(f"🔍 缺失：{gap}")
        if next_step:
            parts.append(f"👉 建议：{next_step}")

    new_content = "\n".join(parts)

    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()

    # Store analysis in metadata for machine readability
    if analysis:
        existing_meta["distill_analysis"] = {
            "value": {k: v for k, v in analysis.items() if v},
            "_meta": {"version": 1, "written_at": now},
        }

    conn.execute(
        "UPDATE memories SET content = ?, metadata = ?, updated_at = ? WHERE id = ?",
        (new_content[:4000], json.dumps(existing_meta, ensure_ascii=False), now, group_id),
    )
    conn.commit()

    response = {
        "status": "updated",
        "id": group_id,
        "level": level,
        "summary_length": len(summary),
    }
    if analysis:
        response["analysis"] = {k: v for k, v in analysis.items() if v}

    return json.dumps(response, ensure_ascii=False)
