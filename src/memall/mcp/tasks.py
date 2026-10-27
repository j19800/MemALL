"""Precise task hand-off: claim a card, list my cards (design #1000007).

The task loop had no "accept" step: a card was either active or done, and
nothing recorded WHO took it.  That made three things impossible:

  * telling "nobody picked this up" apart from "someone is working on it";
  * refusing a claim by an agent the card was not addressed to;
  * letting an executor ask "what do I owe?" without scanning everything.

These helpers implement the state machine
``active -> claimed -> done -> archived`` on the existing L5 task card, using
``metadata`` (no schema migration) and falling back to ``agent_name`` as the
addressee so every card written before ``assignee`` existed still works.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger("memall.mcp.tasks")

_NOT_ARCHIVED = "COALESCE(m.memory_status, '') != 'archived'"


def _meta_of(raw) -> dict:
    try:
        m = json.loads(raw) if isinstance(raw, str) else (raw or {})
    except (json.JSONDecodeError, TypeError):
        return {}
    return m if isinstance(m, dict) else {}


def _status_of(raw) -> str:
    if isinstance(raw, dict):
        raw = raw.get("value")
    if not isinstance(raw, str):
        return "active"
    return raw.strip().lower() or "active"


def _addressed_to(meta: dict, agent_name: str, assignee: str) -> bool:
    target = (meta.get("assignee") or assignee or "").strip()
    return bool(target) and target.lower() == (agent_name or "").strip().lower()


def claim_task(task_id: int, agent_name: str) -> dict:
    """Accept a task card. Only the addressee may claim it.

    Returns a dict the MCP layer can serialise directly.  A claim by any other
    agent is REFUSED (``status: forbidden``) rather than silently allowed —
    that is the "zero cross-talk" property the design asks for.
    """
    from memall.core.db import get_conn

    if not agent_name:
        return {"status": "error",
                "error": "agent identity required to claim a task"}
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT id, level, category, subject, content, agent_name, metadata "
            "FROM memories WHERE id = ?", (task_id,)).fetchone()
        if not row:
            return {"status": "error", "error": f"task #{task_id} not found"}
        if row["level"] != "L5" or row["category"] not in ("task", "todo"):
            return {"status": "error",
                    "error": f"#{task_id} is not a task card "
                             f"(level={row['level']}, category={row['category']})"}

        meta = _meta_of(row["metadata"])
        status = _status_of(meta.get("status"))
        if status in ("done", "archived"):
            return {"status": "already_closed", "task_id": task_id,
                    "state": status,
                    "subject": row["subject"]}
        if not _addressed_to(meta, agent_name, row["agent_name"]):
            return {"status": "forbidden", "task_id": task_id,
                    "reason": "this card is addressed to another agent",
                    "assignee": meta.get("assignee") or row["agent_name"],
                    "caller": agent_name}

        # ── dependency gate ───────────────────────────────────────────
        # depends_on was stored and displayed but never enforced, so an agent
        # could claim a downstream card before its input existed.  Measured:
        # B (depends_on=[A], A still active) was claimed successfully.  That
        # is parallelism, not collaboration - a claim is a promise that the
        # prerequisites are done, so a violated promise must fail loudly.
        deps = meta.get("depends_on") or []
        if isinstance(deps, (str, int)):
            deps = [deps]
        blocking = []
        for d in deps:
            try:
                did = int(d)
            except (TypeError, ValueError):
                continue
            # NOTE: _NOT_ARCHIVED is written against the alias `m`, so this
            # query must alias the table too — without it SQLite raises
            # "no such column: m.memory_status" and the whole claim fails.
            drow = conn.execute(
                "SELECT m.id, m.subject, m.metadata FROM memories m "
                "WHERE m.id = ? AND " + _NOT_ARCHIVED, (did,)).fetchone()
            if not drow:
                continue  # dependency deleted: nothing left to wait for
            dmeta = _meta_of(drow["metadata"])
            dstatus = _status_of(dmeta.get("status"))
            if dstatus not in ("done", "resolved", "archived", "converged",
                               "verified"):
                blocking.append({"task_id": drow["id"],
                                 "subject": drow["subject"],
                                 "status": dstatus})
        if blocking:
            return {"status": "blocked_by_dependency", "task_id": task_id,
                    "reason": "prerequisites are not complete yet",
                    "blocked_by": blocking}

        now = datetime.now(timezone.utc).isoformat()
        already = meta.get("claimed_by")
        meta["status"] = "claimed"
        meta["claimed_by"] = agent_name
        meta["claimed_at"] = meta.get("claimed_at") or now
        meta["assignee"] = meta.get("assignee") or row["agent_name"] or agent_name
        conn.execute(
            "UPDATE memories SET metadata = ?, updated_at = ? WHERE id = ?",
            (json.dumps(meta, ensure_ascii=False), now, task_id))
        conn.commit()

        body = row["content"] or ""
        return {
            "status": "reclaimed" if already else "claimed",
            "task_id": task_id,
            "subject": row["subject"],
            "state": "claimed",
            "claimed_by": agent_name,
            "claimed_at": meta["claimed_at"],
            "content": body[:2000],
        }
    finally:
        conn.close()


def list_tasks(agent_name: str, status: Optional[str] = None,
               limit: int = 20) -> dict:
    """Task cards addressed to *agent_name*.

    ``status`` may be a single state or "open" (active+claimed).
    """
    from memall.mcp.notify import pending_tasks_for, _OWED_STATUSES
    from memall.core.db import get_conn

    if not agent_name:
        return {"status": "error",
                "error": "agent identity required to list tasks"}
    want = (status or "open").strip().lower()
    conn = get_conn()
    try:
        if want in ("open", "all", ""):
            rows = pending_tasks_for(conn, agent_name, limit=limit)
            if want == "all":
                # ask for every state the agent owns
                # Only REAL task cards: reminders (task_pending) and session
                # summaries also live at L5 and are addressed to the agent, so
                # without this filter "my tasks" was mostly noise.
                all_rows = conn.execute(
                    "SELECT m.id, m.subject, m.metadata, m.category FROM memories m "
                    "WHERE m.level = 'L5' AND m.category IN ('task', 'todo') "
                    "AND COALESCE(LOWER(json_extract("
                    "m.metadata, '$.assignee')), LOWER(m.agent_name)) = LOWER(?) "
                    f"AND {_NOT_ARCHIVED} ORDER BY m.created_at DESC LIMIT ?",
                    (agent_name, limit)).fetchall()
                out = []
                for r in all_rows:
                    meta = _meta_of(r["metadata"])
                    out.append({"id": r["id"], "subject": r["subject"],
                                "status": _status_of(meta.get("status")),
                                "claimed_by": meta.get("claimed_by") or ""})
                return {"status": "ok", "agent": agent_name, "filter": "all",
                        "total": len(out), "tasks": out}
        else:
            rows = conn.execute(
                "SELECT m.id, m.subject, m.metadata, m.category FROM memories m "
                "WHERE m.level = 'L5' AND m.category IN ('task', 'todo') "
                "AND COALESCE(LOWER(json_extract("
                "m.metadata, '$.assignee')), LOWER(m.agent_name)) = LOWER(?) "
                f"AND {_NOT_ARCHIVED} ORDER BY m.created_at DESC LIMIT ?",
                (agent_name, limit * 3)).fetchall()
            rows = [{"id": r["id"], "subject": r["subject"],
                     "status": _status_of(_meta_of(r["metadata"]).get("status")),
                     "claimed_by": _meta_of(r["metadata"]).get("claimed_by") or ""}
                    for r in rows]
            rows = [r for r in rows if r["status"] == want][:limit]
            return {"status": "ok", "agent": agent_name, "filter": want,
                    "total": len(rows), "tasks": rows}

        return {"status": "ok", "agent": agent_name,
                "filter": "open" if want in ("open", "") else want,
                "total": len(rows), "tasks": rows}
    finally:
        conn.close()
