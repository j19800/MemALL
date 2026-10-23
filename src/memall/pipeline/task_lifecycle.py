"""
Task lifecycle: execution-side closure for L5 task memories.

The convergence engine produces L5 task memories (category='task') from
converged discussions.  This module makes those tasks *actionable*:

    list_active_tasks(agent_name)   → what's on my plate right now
    acknowledge_task(task_id)       → mark "seen / accepted"
    resolve_task(task_id, result)   → mark done with evidence
    block_task(task_id, reason)     → mark blocked, needs human input
    migrate_wrapped_task_metadata() → one-time data fix

This closes the "decision → action" gap: a converged discussion produces
L4 decision + L5 tasks, and those tasks now surface in the next session
injection so the responsible agent actually sees them.
"""

import json
import logging
from datetime import datetime, timezone

from memall.core.db import get_conn
from memall.core.status import NOT_ARCHIVED, pending_tasks, task_status_of, task_status_eq
from memall.core.utils import now_iso, unwrap

logger = logging.getLogger(__name__)


# ── Query ──


def list_active_tasks(agent_name: str = "") -> list[dict]:
    """Return active (status=active) L5 tasks, optionally filtered by assignee.

    ``agent_name`` matches the task's ``owner``/``agent_name`` field.
    Empty string → all active tasks (for dashboards / CEO view).
    """
    conn = get_conn()
    try:
        base = (
            "SELECT id, subject, content, agent_name, metadata, created_at "
            "FROM memories WHERE level='L5' AND category='task' "
            f"AND {pending_tasks()} "
        )
        if agent_name:
            rows = conn.execute(
                base + "AND agent_name = ? ORDER BY created_at ASC LIMIT 1000",
                (agent_name,),
            ).fetchall()
        else:
            rows = conn.execute(
                base + "ORDER BY created_at ASC LIMIT 1000",
            ).fetchall()

        results = []
        for r in rows:
            meta = json.loads(r["metadata"] or "{}")
            results.append({
                "task_id": r["id"],
                "subject": r["subject"],
                "content": (r["content"] or "")[:300],
                "agent_name": r["agent_name"],
                "source_discussion": unwrap(meta.get("source_discussion")),
                "source_decision": unwrap(meta.get("source_decision")),
                "acknowledged_at": meta.get("acknowledged_at", ""),
                "created_at": r["created_at"],
            })
        return results
    finally:
        conn.close()


def list_blocked_tasks(agent_name: str = "") -> list[dict]:
    """Return blocked L5 tasks (status=blocked)."""
    conn = get_conn()
    try:
        base = (
            "SELECT id, subject, agent_name, metadata, created_at "
            "FROM memories WHERE level='L5' AND category='task' "
            f"AND {task_status_eq('blocked')} AND {NOT_ARCHIVED} "
        )
        if agent_name:
            rows = conn.execute(
                base + "AND agent_name = ? ORDER BY created_at ASC LIMIT 1000",
                (agent_name,),
            ).fetchall()
        else:
            rows = conn.execute(
                base + "ORDER BY created_at ASC LIMIT 1000",
            ).fetchall()

        results = []
        for r in rows:
            meta = json.loads(r["metadata"] or "{}")
            results.append({
                "task_id": r["id"],
                "subject": r["subject"],
                "agent_name": r["agent_name"],
                "blocked_reason": meta.get("blocked_reason", ""),
                "blocked_at": meta.get("blocked_at", ""),
                "created_at": r["created_at"],
            })
        return results
    finally:
        conn.close()


# ── Lifecycle operations ──


def acknowledge_task(task_id: int) -> dict:
    """Mark a task as acknowledged (seen / accepted by the assignee)."""
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT metadata FROM memories "
            f"WHERE id=? AND level='L5' AND category='task' AND {NOT_ARCHIVED}",
            (task_id,),
        ).fetchone()
        if not row:
            return {"error": f"task #{task_id} not found"}

        meta = json.loads(row["metadata"] or "{}")
        status = task_status_of(meta.get("status"))
        if status != "active":
            return {"warning": f"task #{task_id} status is '{status}', not 'active'"}

        now = now_iso()
        meta["acknowledged_at"] = now
        conn.execute(
            "UPDATE memories SET metadata=?, updated_at=? WHERE id=?",
            (json.dumps(meta, ensure_ascii=False), now, task_id),
        )
        conn.commit()
        return {"task_id": task_id, "acknowledged_at": now}
    finally:
        conn.close()


def close_task_reminders(conn, task_id: int) -> int:
    """Delete the outstanding reminder rows for *task_id*.  Returns the count.

    Reminders are keyed by task id and are PERMANENTLY satisfied once written:
    ``agent_round`` treats any existing reminder (even an archived one) as
    "already notified", because its body's content_hash carries a unique
    constraint.  So a reminder left behind by a task that is later resolved
    does not merely become stale — it SILENTLY BLOCKS every future reminder
    for that task.  Measured: create card -> notify (1 reminder) -> resolve
    (reminder stays) -> reopen to active -> notify -> still 1, no new row;
    archiving the stale row does not help either.  Only a hard delete frees
    the (task, agent) pair.

    Called from every terminal transition so that re-opening a task can be
    notified again.
    """
    try:
        cur = conn.execute(
            "DELETE FROM memories WHERE category = 'task_pending' "
            "AND json_extract(metadata, '$.task_id') = ?", (task_id,))
        return cur.rowcount or 0
    except Exception:
        logger.debug("close_task_reminders(%s) failed", task_id, exc_info=True)
        return 0


def resolve_task(task_id: int, result: str = "") -> dict:
    """Mark a task as resolved (done).  Optional result text is stored."""
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT metadata FROM memories "
            f"WHERE id=? AND level='L5' AND category='task' AND {NOT_ARCHIVED}",
            (task_id,),
        ).fetchone()
        if not row:
            return {"error": f"task #{task_id} not found"}

        meta = json.loads(row["metadata"] or "{}")
        status = task_status_of(meta.get("status"))
        if status not in ("active", "blocked"):
            return {"warning": f"task #{task_id} status is '{status}', cannot resolve"}

        now = now_iso()
        meta["status"] = "resolved"
        meta["resolved_at"] = now
        if result:
            meta["resolution"] = result[:2000]
        # Also close the arc so it drops off dashboards
        conn.execute(
            "UPDATE memories SET metadata=?, updated_at=?, arc_status='closed' WHERE id=?",
            (json.dumps(meta, ensure_ascii=False), now, task_id),
        )
        closed = close_task_reminders(conn, task_id)
        conn.commit()
        return {"task_id": task_id, "resolved_at": now,
                "reminders_closed": closed}
    finally:
        conn.close()


def block_task(task_id: int, reason: str) -> dict:
    """Mark a task as blocked, requiring human input."""
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT metadata FROM memories "
            f"WHERE id=? AND level='L5' AND category='task' AND {NOT_ARCHIVED}",
            (task_id,),
        ).fetchone()
        if not row:
            return {"error": f"task #{task_id} not found"}

        meta = json.loads(row["metadata"] or "{}")
        status = task_status_of(meta.get("status"))
        if status not in ("active",):
            return {"warning": f"task #{task_id} status is '{status}', cannot block"}

        now = now_iso()
        meta["status"] = "blocked"
        meta["blocked_at"] = now
        meta["blocked_reason"] = reason[:500]
        conn.execute(
            "UPDATE memories SET metadata=?, updated_at=? WHERE id=?",
            (json.dumps(meta, ensure_ascii=False), now, task_id),
        )
        # A blocked card is no longer owed work, so its outstanding reminder
        # would keep pointing at something that needs a human, not the agent.
        closed = close_task_reminders(conn, task_id)
        conn.commit()
        return {"task_id": task_id, "blocked_at": now,
                "reminders_closed": closed}
    finally:
        conn.close()


# ── One-time data migration ──


def migrate_wrapped_task_metadata() -> dict:
    """Unwrap {value:X, _meta:{...}} envelopes on L5 task metadata keys.

    Affected keys: status, source_discussion, source_decision.
    This is idempotent — already-bare values pass through unchanged.
    """
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT id, metadata FROM memories WHERE level='L5' AND category='task'"
        ).fetchall()

        migrated = 0
        for r in rows:
            meta = json.loads(r["metadata"] or "{}")
            changed = False
            for key in ("status", "source_discussion", "source_decision"):
                if key in meta:
                    unwrapped = unwrap(meta[key])
                    if unwrapped is not meta[key]:
                        meta[key] = unwrapped
                        changed = True
            if changed:
                conn.execute(
                    "UPDATE memories SET metadata=? WHERE id=?",
                    (json.dumps(meta, ensure_ascii=False), r["id"]),
                )
                migrated += 1

        conn.commit()
        return {"scanned": len(rows), "migrated": migrated}
    finally:
        conn.close()
