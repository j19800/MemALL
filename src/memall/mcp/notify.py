"""Agent notification bar — one implementation, three transports.

The same block had been copy-pasted into
``mcp/server.py``, ``mcp/http_transport.py`` and ``gateway_mcp_handlers.py``,
and had already drifted: only the stdio server counted pending discussions.
Both copies also reported a bare COUNT ("你有 N 个待完成任务"), which tells an
agent that work exists but not what it is, so it still had to go and fetch.

This module is the single source of truth.  It answers, per agent:

  * which L5 task cards are addressed to me and still owed (active/claimed)
  * which discussions are waiting for MY stance

and renders ``[NOTIFICATION] …`` carrying [ID | 标题 | 状态] so a caller can
act without a second round trip.  It is intentionally stateless — queried on
every tool response — so it can never serve a stale queue (that was the
freshness defect F4, which the 30-minute session cooldown used to hide).
"""
from __future__ import annotations

import json
import logging
from typing import Optional

logger = logging.getLogger("memall.mcp.notify")

# Statuses that still represent owed work.
_OWED_STATUSES = ("active", "claimed")

# Same archive guard used across the codebase.
_NOT_ARCHIVED = "COALESCE(m.memory_status, '') != 'archived'"


def _status_of(raw) -> str:
    """Unwrap an enveloped value ({"value": …}) and normalise."""
    if isinstance(raw, dict):
        raw = raw.get("value")
    if not isinstance(raw, str):
        return "active"
    return raw.strip().lower() or "active"


def pending_tasks_for(conn, agent_name: str, limit: int = 10) -> list[dict]:
    """L5 task cards addressed to *agent_name* that are still owed.

    Addressed by ``metadata.assignee`` when present, falling back to
    ``agent_name`` so every card written before the assignee field existed
    keeps working.

    Only ``category='task'`` rows are task cards.  Without that constraint
    the bar counted every L5 row addressed to the agent, so a discussion, a
    session summary or an L7 lesson — rows that carry no work at all — were
    listed as "待完成任务".  Their ``metadata.status`` is usually absent, and
    an absent status means "active" (AGENTS.md §二), so they all passed the
    owed-status filter: one agent's bar showed ten pending tasks, of which
    only three were tasks.
    """
    if not agent_name:
        return []
    try:
        rows = conn.execute(
            "SELECT m.id, m.subject, m.category, m.metadata, m.created_at "
            "FROM memories m "
            "WHERE m.level = 'L5' "
            "AND m.category = 'task' "
            "AND COALESCE(LOWER(json_extract(m.metadata, '$.assignee')), "
            "             LOWER(m.agent_name)) = LOWER(?) "
            f"AND {_NOT_ARCHIVED} "
            "AND LENGTH(TRIM(m.content)) > 5 "
            "ORDER BY m.created_at DESC LIMIT ?",
            (agent_name, max(limit * 3, 25)),
        ).fetchall()
    except Exception:
        logger.debug("pending_tasks_for failed", exc_info=True)
        return []

    out: list[dict] = []
    for r in rows:
        try:
            meta = r["metadata"]
            meta = json.loads(meta) if isinstance(meta, str) else (meta or {})
        except (json.JSONDecodeError, TypeError):
            meta = {}
        if not isinstance(meta, dict):
            meta = {}
        status = _status_of(meta.get("status"))
        if status not in _OWED_STATUSES:
            continue

        # Embeds are not independent work — they ride on their parent.
        if meta.get("embed_of") or meta.get("chunk_of"):
            continue

        out.append({
            "id": r["id"],
            "subject": (r["subject"] or "").strip(),
            "status": status,
            "category": r["category"] or "",
            "assignee": meta.get("assignee") or "",
            "claimed_by": meta.get("claimed_by") or "",
        })
        if len(out) >= limit:
            break
    return out


def pending_discussions_for(conn, agent_name: str, limit: int = 10) -> list[dict]:
    """Active discussions this agent participates in and has not answered."""
    if not agent_name:
        return []
    try:
        rows = conn.execute(
            "SELECT m.id, m.subject, m.metadata FROM memories m "
            "WHERE m.level = 'L5' AND m.category = 'discussion' "
            f"AND {_NOT_ARCHIVED} "
            "AND json_extract(m.metadata, '$.status') = 'active' "
            "AND EXISTS ("
            "  SELECT 1 FROM json_each(json_extract(m.metadata, '$.participants')) p "
            "  WHERE LOWER(p.value) = LOWER(?)"
            ") "
            "AND NOT EXISTS ("
            "  SELECT 1 FROM edges e JOIN memories r ON e.target_id = r.id "
            "  WHERE e.source_id = m.id AND e.relation_type = 'cites' "
            "  AND r.category = 'discussion_response' "
            "  AND LOWER(r.agent_name) = LOWER(?)"
            ") "
            "ORDER BY m.created_at DESC LIMIT ?",
            (agent_name, agent_name, limit),
        ).fetchall()
    except Exception:
        logger.debug("pending_discussions_for failed", exc_info=True)
        return []

    out = []
    for r in rows:
        try:
            meta = r["metadata"]
            meta = json.loads(meta) if isinstance(meta, str) else (meta or {})
        except (json.JSONDecodeError, TypeError):
            meta = {}
        title = (r["subject"] or "").strip()
        if title.startswith("[讨论]"):
            title = title[len("[讨论]"):].strip()
        out.append({"id": r["id"], "title": title})
    return out


def format_notice(tasks: list[dict], discussions: list[dict]) -> str:
    """Render the notification text, or ``""`` when there is nothing owed."""
    if not tasks and not discussions:
        return ""
    lines = ["[NOTIFICATION]"]

    if tasks:
        lines.append(f"你有 {len(tasks)} 个待完成任务：")
        for t in tasks:
            title = t["subject"] or "(无标题)"
            if len(title) > 60:
                title = title[:60] + "…"
            state = "已认领" if t["status"] == "claimed" else "待认领"
            lines.append(f"  - #{t['id']} | {title} | {state}")

    if discussions:
        lines.append(f"你有 {len(discussions)} 个待回应讨论（表态后即消失）：")
        for d in discussions:
            title = d["title"] or "(无标题)"
            if len(title) > 60:
                title = title[:60] + "…"
            lines.append(f"  - #{d['id']} | {title}")

    return "\n".join(lines)


def build_notice(agent_name: Optional[str]) -> str:
    """Convenience: query and render for *agent_name*.

    Returns ``""`` (not None) when the agent is unknown or owes nothing, so
    callers can simply test truthiness.
    """
    name = (agent_name or "").strip()
    if not name:
        return ""
    try:
        from memall.core.db import get_conn

        conn = get_conn()
        try:
            tasks = pending_tasks_for(conn, name)
            discussions = pending_discussions_for(conn, name)
        finally:
            conn.close()
        return format_notice(tasks, discussions)
    except Exception:
        logger.warning("build_notice failed", exc_info=True)
        return ""
