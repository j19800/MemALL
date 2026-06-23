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
import logging
from datetime import datetime, timezone, timedelta

from memall.core.db import pool_conn

logger = logging.getLogger("memall.scheduler.agent_round")

# Minimum gap between two rounds for the same agent (avoids pointless loops)
_MIN_GAP_SECONDS = 60
# Pending-task reminder dedup window
_TASK_REMINDER_HOURS = 6


def get_active_agents() -> list[str]:
    """Return list of active agent names from the identities table."""
    with pool_conn() as conn:
        rows = conn.execute(
            "SELECT agent_name FROM identities WHERE status = 'active'"
        ).fetchall()
        return [r["agent_name"] for r in rows]


def notify_pending_tasks(agent_name: str) -> list[dict]:
    """Check active L5 tasks assigned to *agent_name* and create P2 reminders.

    Dedup: skips if a P2 reminder for the same task already exists within
    ``_TASK_REMINDER_HOURS`` hours.
    """
    now = datetime.now(timezone.utc)
    with pool_conn() as conn:
        tasks = conn.execute(
            "SELECT id, subject, content, metadata FROM memories "
            "WHERE level='L5' AND category='task' AND agent_name = ? "
            "AND json_extract(metadata, '$.status') = 'active' "
            "ORDER BY id",
            (agent_name,),
        ).fetchall()

        reminders = []
        for t in tasks:
            # Dedup: recent P2 for same task+agent
            cutoff = (now - timedelta(hours=_TASK_REMINDER_HOURS)).isoformat()
            existing = conn.execute(
                "SELECT id FROM memories WHERE agent_name = ? AND level = 'P2' "
                "AND category = 'task_pending' AND metadata LIKE ? AND created_at > ?",
                (agent_name, f"%task_id\": {t['id']}%", cutoff),
            ).fetchone()
            if existing:
                continue

            content = f"[待办任务] {t['subject']} — 分配给: {agent_name}"[:2000]
            h = hashlib.sha256(content.encode("utf-8")).hexdigest()
            rem_meta = {
                "task_id": t["id"],
                "subject": t["subject"],
                "assigned_to": agent_name,
            }
            conn.execute(
                """INSERT INTO memories
                   (content, content_hash, level, owner, agent_name, subject,
                    category, project, summary, occurred_at, created_at, updated_at,
                    supersedes, confidence, visibility, metadata, arc_status)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (content, h, "P2", agent_name, agent_name,
                 f"待办: {t['subject']}", "task_pending", "", "",
                 now.isoformat(), now.isoformat(), now.isoformat(),
                 None, 0.5, "private", str(rem_meta).replace("'", '"'), "open"),
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