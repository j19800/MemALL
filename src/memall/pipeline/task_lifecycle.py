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
from memall.core.task_cas import cas_update_metadata
from memall.core.task_identity import can_mutate, forbidden_payload
from memall.core.utils import now_iso, unwrap

logger = logging.getLogger(__name__)


# ── Caller gate (design #1000539 AC4) ─────────────────────────────────────
# resolve / block / acknowledge all mutate someone else's card if left
# unguarded, so they go through the shared identity module.  The predicate
# itself lives in ``memall.core.task_identity`` — this module used to carry a
# second copy (P2-4: two ``_addressed_to`` implementations that had already
# drifted apart from ``mcp/tasks.py``; a supervisor exemption added on one side
# would silently not apply on the other).  Keep only the thin wrapper below so
# every mutation path answers "may this caller act?" the same way.
#
# ``caller is None`` is the trusted-internal path (scheduler, migration,
# guards) and skips the gate; the MCP handlers always pass a real caller string
# (possibly ""), which is gated — an empty caller can only be the addressee if
# the card has no assignee at all.

def _caller_gate(meta, row_agent_name, caller, task_id):
    """Return a forbidden dict, or None when *caller* may act.

    ``caller is None`` → trusted internal caller, gate skipped.
    """
    if can_mutate(meta, row_agent_name, caller):
        return None
    return forbidden_payload(meta, row_agent_name, caller, task_id,
                             operation="change")


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
                "claimed_by": meta.get("claimed_by", ""),
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


def acknowledge_task(task_id: int, caller=None) -> dict:
    """Mark a task as acknowledged (seen / accepted by the assignee).

    ``caller`` is the acting agent identity; it is gated like resolve/block
    (see ``_caller_gate``). Pass a real string from the MCP surface; ``None``
    is the trusted-internal path.

    NOTE: this is NOT exposed as an MCP action.  It only sets
    ``acknowledged_at`` and leaves ``status`` at ``active``, so a card the
    agent "ack'ed" is still matched by ``pending_tasks()`` and the scheduler
    keeps nudging it — the exact semantic clash that got ack cut from the
    tool surface (design #1000539 AC-range).  Kept only as an internal helper.
    """
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT metadata, agent_name FROM memories "
            f"WHERE id=? AND level='L5' AND category='task' AND {NOT_ARCHIVED}",
            (task_id,),
        ).fetchone()
        if not row:
            return {"error": f"task #{task_id} not found"}

        meta = json.loads(row["metadata"] or "{}")
        denied = _caller_gate(meta, row["agent_name"], caller, task_id)
        if denied:
            return denied
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


def _find_dependents(conn, task_id: int) -> list[dict]:
    """Open L5 cards whose ``metadata.depends_on`` names *task_id*.

    ``depends_on`` is stored as a JSON array of memory ids (goals.finalize
    writes ``"depends_on": deps`` where deps are the ids of earlier children),
    but older writers stored a bare scalar, so both shapes are accepted here —
    the same tolerance the claim gate in ``mcp/tasks.py`` applies.

    Only cards still owed work are returned: a dependent that is already done
    or archived has nothing left to notify.  The dependent's own
    ``blocked_reason`` is irrelevant — the point of the notification is that
    the prerequisite is now satisfied, whatever the card last recorded.
    """
    rows = conn.execute(
        "SELECT id, subject, agent_name, metadata FROM memories "
        "WHERE level = 'L5' AND category IN ('task', 'todo') "
        f"AND {NOT_ARCHIVED} AND json_type(metadata, '$.depends_on') IS NOT NULL"
    ).fetchall()

    dependents: list[dict] = []
    for r in rows:
        meta = json.loads(r["metadata"] or "{}")
        deps = meta.get("depends_on")
        if isinstance(deps, (str, int)):
            deps = [deps]
        if not isinstance(deps, list):
            continue
        hit = False
        for d in deps:
            try:
                if int(d) == int(task_id):
                    hit = True
                    break
            except (TypeError, ValueError):
                continue
        if not hit:
            continue
        if task_status_of(meta.get("status")) in (
                "done", "resolved", "archived", "converged"):
            continue
        dependents.append({
            "task_id": r["id"],
            "subject": r["subject"] or "",
            "assignee": (meta.get("assignee") or r["agent_name"] or "").strip(),
        })
    return dependents


def _notify_task_dependents(task_id: int) -> list[dict]:
    """Tell each dependent's assignee that its prerequisite just closed.

    ``depends_on`` was enforced at claim time but nothing told the downstream
    owner when the blocker cleared, so a card could sit untouched until the
    ``goal_tick`` sweep happened to mention it.  This closes that gap.

    Contract, in order of importance:

    1. Called AFTER the resolve transaction commits.  A note pushed before the
       commit could be read by an agent whose next ``task_claim`` still sees
       the prerequisite as active — a notification that is a lie at the moment
       it arrives.
    2. Never fails the resolve.  Notification is a courtesy; a broken note
       store or an absent bus must degrade to "no note", not to "the card is
       still open" after the caller already believes it closed.
    3. One note per assignee, not per card.  A resolve can fan out to several
       dependents owned by the same agent; five near-identical notes is a
       notification storm the agent then has to wade through.
    4. Delivery goes through ``push_session_note`` with a stable dedup key, so
       a retried resolve cannot queue the same notice twice — the same
       idempotency key discipline the bus-side publish uses (A3-2).
    """
    try:
        conn = get_conn()
        try:
            dependents = _find_dependents(conn, task_id)
        finally:
            conn.close()
    except Exception:
        logger.debug("notify_task_dependents(%s): lookup failed", task_id,
                     exc_info=True)
        return []

    if not dependents:
        return []

    by_agent: dict[str, list[dict]] = {}
    for dep in dependents:
        who = dep["assignee"]
        if not who:
            # A card with no assignee has nobody to tell.  Do not invent one:
            # an empty assignee already means "the creator's commitment"
            # (convergence.py), and guessing here would notify the wrong desk.
            continue
        by_agent.setdefault(who, []).append(dep)

    notified = []
    for who, cards in by_agent.items():
        lines = "\n".join(
            f"  - #{c['task_id']} {c['subject'][:80]}" for c in cards)
        ids = ",".join(str(c["task_id"]) for c in cards)
        text = (
            f"[依赖解除] 前置任务 #{task_id} 已完成，你的卡现在可以认领：\n"
            f"{lines}\n"
            f"用 memall_write action=task_claim 认领，完成后 action=task_resolve。"
        )
        try:
            from memall.mcp.shared import push_session_note

            pushed = push_session_note(
                who, text, dedup_key=f"dep-resolved:{task_id}:{ids}")
        except Exception:
            logger.debug("notify_task_dependents(%s): note for %s failed",
                         task_id, who, exc_info=True)
            continue
        if pushed:
            notified.append({"agent": who, "task_ids": [c["task_id"] for c in cards]})
    return notified


def resolve_task(task_id: int, result: str = "", caller=None) -> dict:
    """Mark a task as resolved (done).  Optional result text is stored.

    ``caller`` gates who may close the card (see ``_caller_gate``): a real
    string must be the addressee or a supervisor; ``None`` is the
    trusted-internal path (scheduler / migration / guards).
    """
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT metadata, agent_name FROM memories "
            f"WHERE id=? AND level='L5' AND category='task' AND {NOT_ARCHIVED}",
            (task_id,),
        ).fetchone()
        if not row:
            return {"error": f"task #{task_id} not found"}

        meta = json.loads(row["metadata"] or "{}")
        denied = _caller_gate(meta, row["agent_name"], caller, task_id)
        if denied:
            return denied
        status = task_status_of(meta.get("status"))
        if status not in ("active", "claimed", "blocked"):
            return {"warning": f"task #{task_id} status is '{status}', cannot resolve"}

        now = now_iso()
        meta["status"] = "resolved"
        meta["resolved_at"] = now
        if result:
            meta["resolution"] = result[:2000]
        # Also close the arc so it drops off dashboards.
        #
        # The guard above ("status not in active/claimed/blocked") is a CHECK,
        # and a check is not a fence: a concurrent claim can land between it
        # and this write, after which a stale copy overwrites the other's
        # change.  Measured 10/40 real claim/resolve interleavings ended with
        # a completed card back at "claimed".  The predicate makes the write
        # atomic at the row.
        won = cas_update_metadata(conn, task_id, meta, expected_status=status, now=now)
        if not won:
            conn.rollback()
            current = conn.execute(
                "SELECT metadata FROM memories WHERE id=?", (task_id,)).fetchone()
            live = task_status_of(
                (json.loads(current["metadata"] or "{}").get("status") if current else ""))
            return {"error": (
                f"task #{task_id} changed while it was being resolved "
                f"(observed '{status}', now '{live}'). Nothing was written."
            ), "task_id": task_id, "observed_status": status,
                "current_status": live}
        conn.execute("UPDATE memories SET arc_status='closed' WHERE id=?", (task_id,))
        closed = close_task_reminders(conn, task_id)
        conn.commit()

        # B3: tell anyone waiting on this card that it just closed.  Deliberately
        # after the commit — see _notify_task_dependents' contract — and its
        # internal try/except means a dead note store cannot turn a successful
        # resolve into an error response.
        notified = _notify_task_dependents(task_id)

        out = {"task_id": task_id, "resolved_at": now,
               "reminders_closed": closed}
        if notified:
            out["dependents_notified"] = notified
        return out
    finally:
        conn.close()


def block_task(task_id: int, reason: str, caller=None) -> dict:
    """Mark a task as blocked, requiring human input.

    ``caller`` gates who may block the card (see ``_caller_gate``).
    """
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT metadata, agent_name FROM memories "
            f"WHERE id=? AND level='L5' AND category='task' AND {NOT_ARCHIVED}",
            (task_id,),
        ).fetchone()
        if not row:
            return {"error": f"task #{task_id} not found"}

        meta = json.loads(row["metadata"] or "{}")
        denied = _caller_gate(meta, row["agent_name"], caller, task_id)
        if denied:
            return denied
        status = task_status_of(meta.get("status"))
        if status not in ("active", "claimed"):
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
