"""Agent round — periodic session emulation for discussion/task awareness.

The core problem: MemALL's discussion system assumes agents call
``session_start()`` to see pending discussions and tasks. But agents
have no runtime — they never "wake up" on their own.

This module solves it by running a lightweight periodic check for each
registered agent, creating P2 reminder memories that the agent will
see on its next real session.  No full ``session_start()`` (which would
flood the sessions table); just targeted notification writes.
"""

import hashlib
import json
import logging
from datetime import datetime, timezone

from memall.core.db import pool_conn
from memall.core.status import pending_tasks

logger = logging.getLogger("memall.scheduler.agent_round")

# Minimum gap between two rounds for the same agent (avoids pointless loops)
_MIN_GAP_SECONDS = 60
# Reminder dedup is by content_hash EXISTENCE, not by a time window — the
# body is deterministic per (task, agent), so its hash is deterministic, and
# content_hash carries a permanent UNIQUE constraint.  A window could only
# ever be a guess about when the constraint would bite (audit F6: it bit
# every 6 hours).  See notify_pending_tasks().


def get_active_agents() -> list[str]:
    """Return list of active agent names from the identities table.

    Agents on the ``security.disabled_agents`` write deny-list are excluded:
    the background round would otherwise keep generating P2 reminder
    memories for identities the deployment has explicitly opted out of
    (test/audit agents, one-off task workers).  Patterns (``autocap*``) are
    supported — see ``memall.core.denylist``.
    """
    from memall.core.denylist import is_disabled_agent

    with pool_conn() as conn:
        rows = conn.execute(
            "SELECT agent_name FROM identities WHERE status = 'active'"
        ).fetchall()
        return [r["agent_name"] for r in rows
                if not is_disabled_agent(r["agent_name"])]


def notify_pending_tasks(agent_name: str) -> list[dict]:
    """Check active L5 tasks assigned to *agent_name* and create P2 reminders.

    Dedup: one reminder per task, keyed by the reminder body's content_hash
    (the body is deterministic per (task, agent)).  If the reminder row is
    later forgotten or archived, the row is gone and a fresh one is created.

    Note: the reminder table has no reader in the current codebase — the
    agent-facing list is the L5 task card itself, which auto_inject reads as
    ``pending_tasks``.  This table is a notification copy, not the source of
    truth.
    """
    now = datetime.now(timezone.utc)
    with pool_conn() as conn:
        tasks = conn.execute(
            "SELECT id, subject, content, metadata FROM memories "
            "WHERE level='L5' AND category='task' AND agent_name = ? "
            f"AND {pending_tasks()} "
            "ORDER BY id",
            (agent_name,),
        ).fetchall()

        reminders = []
        for t in tasks:
            # The reminder body is deterministic for a (task, agent) pair, so
            # its content_hash is deterministic too — but content_hash carries
            # a PERMANENT UNIQUE constraint while the dedup window was only
            # _TASK_REMINDER_HOURS (6h).  Past the window the INSERT re-used
            # the same hash and raised
            #   UNIQUE constraint failed: memories.content_hash
            # which aborted the whole round (audit F6: the only two agents
            # erroring were exactly the only two with reminders older than
            # 6h).
            #
            # Fix: dedup on hash *existence*, not on a time window.  The
            # window becomes irrelevant, and if the reminder row is later
            # forgotten/archived the row is gone and a fresh one is created
            # — self-correcting, which the time window was not (it skipped
            # re-notification for 6h after the reminder disappeared).
            #
            # The task id goes into the body so that two DIFFERENT tasks
            # sharing a subject still get their own reminder: without it the
            # bodies would hash identically and the second task would be
            # silently dropped.
            content = (f"[待办任务] {t['subject']} — 分配给: {agent_name} "
                       f"(task #{t['id']})")[:2000]
            h = hashlib.sha256(content.encode("utf-8")).hexdigest()
            existing = conn.execute(
                "SELECT id FROM memories WHERE content_hash = ?", (h,)
            ).fetchone()
            if existing:
                continue

            rem_meta = {
                "task_id": t["id"],
                "subject": t["subject"],
                "assigned_to": agent_name,
            }
            # INSERT OR IGNORE: two rounds racing on the same task would both
            # pass the existence check above; the second INSERT would then
            # abort the transaction and lose the whole batch's reminders.
            conn.execute(
                """INSERT OR IGNORE INTO memories
                   (content, content_hash, level, owner, agent_name, subject,
                    category, project, summary, occurred_at, created_at, updated_at,
                    supersedes, confidence, visibility, metadata, arc_status)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (content, h, "P2", agent_name, agent_name,
                 f"待办: {t['subject']}", "task_pending", "", "",
                 # supersedes is NOT NULL DEFAULT '[]'; passing None here
                 # raised IntegrityError, so the reminder INSERT never
                 # succeeded and notify_pending_tasks always returned an
                 # empty list (or crashed) — the task chain was broken at
                 # the last step even after category stayed 'task'.
                 now.isoformat(), now.isoformat(), now.isoformat(),
                 "[]", 0.5, "private", json.dumps(rem_meta, ensure_ascii=False), "open"),
            )
            reminders.append({"task_id": t["id"], "subject": t["subject"]})

        if reminders:
            conn.commit()
        return reminders


def agent_round() -> dict:
    """One round-robin pass over all active agents.

    For each agent:
      1. ``check_pending_discussions(agent_name)`` — P2 reminders for
         discussions needing a response (1h dedup built in).
      2. ``notify_pending_tasks(agent_name)`` — P2 reminders for
         active L5 tasks (6h dedup).
    """
    from memall.pipeline.convergence import check_pending_discussions

    agents = get_active_agents()
    stats = {
        "agents_checked": len(agents),
        "discussion_reminders": 0,
        "task_reminders": 0,
    }

    for name in agents:
        try:
            pending_discs = check_pending_discussions(name)
            stats["discussion_reminders"] += len(pending_discs)
        except Exception as e:
            logger.warning("agent_round: check_pending_discussions(%s) error: %s", name, e)

        try:
            pending_tasks = notify_pending_tasks(name)
            stats["task_reminders"] += len(pending_tasks)
        except Exception as e:
            logger.warning("agent_round: notify_pending_tasks(%s) error: %s", name, e)

    if stats["discussion_reminders"] or stats["task_reminders"]:
        logger.info(
            "agent_round: %d agents, %d discussion reminders, %d task reminders",
            stats["agents_checked"],
            stats["discussion_reminders"],
            stats["task_reminders"],
        )

    return stats