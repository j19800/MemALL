"""
Pipeline step: discussion deliberation and resolution.

Lifecycle:
  1. create_discussion()     → capture an L5 memory with metadata
  2. confirm_discussion()    → capture P2 + edge, auto-converges immediately
  3. resolve_pending_deliberations() → pipeline sweep: converge any overlooked
  4. check_pending_discussions()     → agent queries + P2 reminder (dedup''d 1h)

Multi-agent negotiation model removed per #7769.  Single confirmation
converges the discussion.
"""

import hashlib
import json
import re
from memall.core.thin_waist import normalize_agent_name
import logging
import uuid
from datetime import datetime, timezone, timedelta

from memall.core.db import get_conn
from memall.pipeline.util import _smart_subject

logger = logging.getLogger(__name__)


def _short_id() -> str:
    return "dt_" + uuid.uuid4().hex[:8]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _unwrap(value):
    """Unwrap {value: X, _meta: {...}} → bare X.

    cleanup.py's _migrate_value() wraps metadata fields in a versioned
    envelope during the nightly sweep.  All reads of discussion metadata
    must unwrap to get the actual data.
    """
    if isinstance(value, dict) and "_meta" in value and "value" in value:
        return value["value"]
    return value


def _unwrap_meta(meta: dict, key: str):
    """Unwrap a single metadata key; return default (empty list) if absent."""
    raw = meta.get(key)
    if raw is None:
        return [] if key in ("participants", "options", "open_questions", "action_items") else ""
    return _unwrap(raw)


# ── Discussion CRUD (L5 + edges) ──


def create_discussion(
    title: str,
    background: str = "",
    options: list | None = None,
    open_questions: list | None = None,
    action_items: list | None = None,
    recommendation: str = "",
    creator: str = "system",
    **kwargs,
) -> dict:
    """Create a discussion as an L5 memory (category=discussion).

    Required format (enforced by content builder):
      == 问题描述 ==   ← background field
      == 解决方案 ==   ← options list
      == 建议 ==       ← recommendation field

    Simplified: a single confirm_discussion() call converges it.
    """
    creator = normalize_agent_name(creator)
    participants_list = [normalize_agent_name(p) for p in kwargs.get("participants", [])]
    timeout_hours = kwargs.get("timeout_hours", 24)
    conn = get_conn()
    try:
        now = _now()
        uid = _short_id()
        subject = f"[讨论] {title}"[:200]

        # Build structured content: 问题描述 → 方案 → 建议
        parts = [f"[讨论] {title} [{uid}]"]
        if background:
            parts.append(f"\n\n== 问题描述（事实与数据）==\n{background}")
        if options:
            parts.append(f"\n\n== 解决方案 ==\n" + "\n".join(f"{i}. {o}" for i, o in enumerate(options, 1)))
        if recommendation:
            parts.append(f"\n\n== 建议 ==\n{recommendation}")
        content = "".join(parts)[:2000]
        if not content:
            content = f"[讨论] {title} [{uid}]"

        h = hashlib.sha256(content.encode("utf-8")).hexdigest()

        meta = json.dumps({
            "status": "active",
            "participants": participants_list,
            "options": options or [],
            "open_questions": open_questions or [],
            "action_items": action_items or [],
            "conclusion": "",
            "converged_at": "",
            "convergence_reason": "",
        })

        cur = conn.execute(
            """INSERT INTO memories
               (content, content_hash, level, owner, agent_name, subject,
                category, project, summary, occurred_at, created_at, updated_at,
                supersedes, confidence, visibility, metadata, arc_status)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (content, h, "L5", creator, creator, subject,
             "discussion", "", "", now, now, now,
             None, 0.5, "private", meta, "open"),
        )
        memory_id = cur.lastrowid
        conn.commit()

        try:
            from memall.lark_notify import notify_discussion_created
            notify_discussion_created(
                title=title, memory_id=memory_id, creator=creator,
                participants=participants_list, options=options or [],
                timeout_hours=timeout_hours,
            )
        except Exception:
            logger.warning("convergence.py: silent error", exc_info=True)

        return {
            "discussion_id": memory_id,
            "memory_id": memory_id,
            "subject": subject,
            "title": title,
            "background": background,
            "status": "active",
            "participants": participants_list,
            "action_items": action_items or [],
            "open_questions": open_questions or [],
            "recommendation": recommendation,
        }
    finally:
        conn.close()


def get_discussion(discussion_id: int) -> dict:
    """Fetch a single L5 discussion + its response edges."""
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT * FROM memories WHERE id=? AND level='L5' AND category='discussion'",
            (discussion_id,),
        ).fetchone()
        if not row:
            return {"error": "not found"}

        disc = dict(row)
        meta = json.loads(disc.get("metadata") or "{}")

        responses = conn.execute(
            "SELECT m.id, m.content, m.metadata, m.agent_name, m.created_at "
            "FROM memories m JOIN edges e ON e.target_id = m.id "
            "WHERE e.source_id=? AND e.relation_type='cites' "
            "ORDER BY m.created_at",
            (discussion_id,),
        ).fetchall()

        return {
            "discussion": {
                "id": disc["id"],
                "subject": disc["subject"],
                "content": disc["content"],
                "status": meta.get("status", "active"),
                "options": _unwrap_meta(meta, "options"),
                "open_questions": _unwrap_meta(meta, "open_questions"),
                "action_items": _unwrap_meta(meta, "action_items"),
                "participants": _unwrap_meta(meta, "participants"),
                "created_at": disc["created_at"],
                "conclusion": _unwrap_meta(meta, "conclusion"),
                "converged_at": meta.get("converged_at", ""),
            },
            "responses": [dict(r) for r in responses],
        }
    finally:
        conn.close()


def list_active_discussions() -> list[dict]:
    """List all L5 discussions with status=active."""
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT id, subject, content, metadata, created_at FROM memories "
            "WHERE level='L5' AND category='discussion' "
            "AND json_extract(metadata, '$.status') = 'active' "
            "ORDER BY created_at DESC",
        ).fetchall()

        results = []
        for r in rows:
            meta = json.loads(r["metadata"] or "{}")
            count = conn.execute(
                "SELECT COUNT(*) as c FROM edges "
                "WHERE source_id=? AND relation_type='cites'",
                (r["id"],),
            ).fetchone()["c"]
            resp_rows = conn.execute(
                "SELECT m.agent_name FROM memories m JOIN edges e ON e.target_id = m.id "
                "WHERE e.source_id=? AND e.relation_type='cites' "
                "GROUP BY m.agent_name",
                (r["id"],),
            ).fetchall()
            responded_agents = [rr["agent_name"] for rr in resp_rows]
            results.append({
                "memory_id": r["id"],
                "subject": r["subject"],
                "participants": _unwrap_meta(meta, "participants"),
                "responded_agents": responded_agents,
                "action_items": _unwrap_meta(meta, "action_items"),
                "response_count": count,
                "created_at": r["created_at"],
            })
        return results
    finally:
        conn.close()


def list_all_discussions() -> list[dict]:
    """List ALL L5 discussions (active, converged, stale) for HTML dashboard."""
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT id, subject, content, metadata, summary, created_at FROM memories "
            "WHERE level='L5' AND category='discussion' "
            "ORDER BY created_at DESC",
        ).fetchall()

        results = []
        for r in rows:
            meta = json.loads(r["metadata"] or "{}")
            count = conn.execute(
                "SELECT COUNT(*) as c FROM edges "
                "WHERE source_id=? AND relation_type='cites'",
                (r["id"],),
            ).fetchone()["c"]
            resp_rows = conn.execute(
                "SELECT m.agent_name FROM memories m JOIN edges e ON e.target_id = m.id "
                "WHERE e.source_id=? AND e.relation_type='cites' "
                "GROUP BY m.agent_name",
                (r["id"],),
            ).fetchall()
            responded_agents = [rr["agent_name"] for rr in resp_rows]
            results.append({
                "memory_id": r["id"],
                "subject": r["subject"],
                "summary": r["summary"] or r["content"][:200] if r["content"] else "",
                "status": meta.get("status", "active"),
                "participants": _unwrap_meta(meta, "participants"),
                "responded_agents": responded_agents,
                "response_count": count,
                "created_at": r["created_at"],
            })
        return results
    finally:
        conn.close()


# ── Confirm & converge ──


def _converge_single(conn, disc: dict) -> dict:
    """Inner: converge one active discussion.

    Called by confirm_discussion() and resolve_pending_deliberations().
    Expects *conn* with an open transaction.
    """
    meta = json.loads(disc.get("metadata") or "{}")
    if meta.get("status") != "active":
        return {"warning": f"already {meta.get('status')}"}

    responses = conn.execute(
        "SELECT m.* FROM memories m JOIN edges e ON e.target_id = m.id "
        "WHERE e.source_id = ? AND e.relation_type = 'cites' ORDER BY m.created_at",
        (disc["id"],),
    ).fetchall()

    if not responses:
        return {"warning": "no responses, can not converge"}

    return converge_discussion(conn, disc, [dict(r) for r in responses], "confirmed")


def confirm_discussion(
    discussion_id: int,
    agent_name: str,
    stance: str = "confirm",
    note: str = "",
) -> dict:
    """Confirm a stance on a discussion.

    Creates a P2 response + edge (cites), then immediately converges
    the discussion — no multi-agent waiting.

    Returns decision_id + task_ids from converge_discussion.
    """
    agent_name = normalize_agent_name(agent_name)
    conn = get_conn()
    try:
        now = _now()

        # Fetch the discussion
        row = conn.execute(
            "SELECT * FROM memories WHERE id=? AND level='L5' AND category='discussion'",
            (discussion_id,),
        ).fetchone()
        if not row:
            return {"error": f"discussion #{discussion_id} not found"}
        disc = dict(row)

        disc_meta = json.loads(disc.get("metadata") or "{}")
        if disc_meta.get("status") != "active":
            return {"warning": f"discussion #{discussion_id} is already {disc_meta.get('status')}"}

        # ── P2 response ──
        subject = f"[表态] 讨论#{discussion_id} | {agent_name}: {stance}"
        content = f"[表态] 讨论#{discussion_id} | {agent_name}: {stance}\n\n{note}"[:2000]
        h = hashlib.sha256(content.encode("utf-8")).hexdigest()
        rmeta = json.dumps({
            "stance": stance,
            "discussion_id": discussion_id,
            "agent_name": agent_name,
            "note": note[:500],
        })
        cur = conn.execute(
            """INSERT INTO memories
               (content, content_hash, level, owner, agent_name, subject,
                category, project, summary, occurred_at, created_at, updated_at,
                supersedes, confidence, visibility, metadata, arc_status)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (content, h, "P2", agent_name, agent_name, subject,
             "discussion_response", "", "", now, now, now,
             None, 0.6, "private", rmeta, "open"),
        )
        resp_id = cur.lastrowid

        # Edge: response cites discussion
        conn.execute(
            "INSERT INTO edges (source_id, target_id, relation_type, created_at) VALUES (?,?,?,?)",
            (discussion_id, resp_id, "cites", now),
        )

        conn.commit()

        # ── Auto-converge: one confirm is enough for simplified flow ──
        responses = conn.execute(
            "SELECT * FROM memories WHERE id IN ("
            "  SELECT target_id FROM edges WHERE source_id = ? AND relation_type = 'cites'"
            ") AND level = 'P2' ORDER BY created_at ASC",
            (discussion_id,),
        ).fetchall()
        result = converge_discussion(conn, disc, [dict(r) for r in responses], f"Confirmed by {agent_name}")
        result["response_id"] = resp_id
        return result

    finally:
        conn.close()


def respond_discussion(
    discussion_id: int,
    agent_name: str,
    stance: str,
    arguments: str = "",
    round_num: int = 1,
) -> dict:
    """Deprecated: use confirm_discussion() instead.

    This wrapper maps *arguments* to *note* and ignores *round_num*.
    """
    return confirm_discussion(
        discussion_id=discussion_id,
        agent_name=agent_name,
        stance=stance,
        note=arguments,
    )


# ── Pipeline step ──



def converge_discussion(conn, disc: dict, responses: list[dict], reason: str) -> dict:
    """Mark discussion converged, create L4 decision + L5 tasks.

    All writes happen on *conn* in a single transaction.
    Caller is responsible for commit.
    """
    meta = json.loads(disc.get("metadata") or "{}")
    if meta.get("status") != "active":
        return {"warning": "already " + str(meta.get("status"))}

    now = _now()
    action_items = _unwrap_meta(meta, "action_items")
    title = re.sub(r'^(\[\?\?\] |\[讨论\] )', '', disc.get("subject", ""))

    # Collect participant names (used as fallback assignees for string action_items)
    participants = _unwrap_meta(meta, "participants") or []

    # Aggregate latest stances from responses (simplified: just the confirming agent)
    latest: dict[str, dict] = {}
    for resp in responses:
        rmeta = json.loads(resp.get("metadata") or "{}")
        agent = rmeta.get("agent_name", resp.get("agent_name", ""))
        if isinstance(agent, str) and agent:
            latest[agent] = rmeta

    # ?? 1. Update discussion L5 ??
    meta["status"] = "converged"
    meta["converged_at"] = now
    meta["convergence_reason"] = reason
    conn.execute(
        "UPDATE memories SET metadata = ?, updated_at = ? WHERE id = ?",
        (json.dumps(meta), now, disc["id"]),
    )

    # ?? 2. Create L4 decision ??
    conclusion = _unwrap_meta(meta, "conclusion")
    stances_lines = []
    for agent, s in latest.items():
        stance = s.get("stance", "confirm")
        args = (s.get("arguments") or s.get("note") or "")[:200]
        stances_lines.append(f"  {agent}: {stance} - {args}")

    l4_content = (
        f"# [L4 会话] {title}\n\n"
        f"## 结论\n{conclusion if conclusion else '(未记录)'}\n\n"
        f"## 各方立场\n" + "\n".join(stances_lines) + "\n\n"
        f"## 收敛原因\n{reason}"
    )[:2000]
    l4_hash = hashlib.sha256(l4_content.encode("utf-8")).hexdigest()
    l4_meta = json.dumps({
        "source_discussion": disc["id"],
        "final_stances": latest,
        "convergence_reason": reason,
        "converged_at": now,
    })
    cur = conn.execute(
        """INSERT INTO memories
           (content, content_hash, level, owner, agent_name, subject,
            category, summary, occurred_at, created_at, updated_at,
            supersedes, confidence, visibility, metadata, arc_status)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (l4_content, l4_hash, "L4", "system", "system",
         f"[L4 会话] {title}", "decision", "", now, now, now,
         None, 0.7, "private", l4_meta, "open"),
    )
    l4_id = cur.lastrowid

    # edge: discussion --refines--> decision
    conn.execute(
        "INSERT INTO edges (source_id, target_id, relation_type, created_at) VALUES (?,?,?,?)",
        (disc["id"], l4_id, "refines", now),
    )

    # ?? 3. Archive response P2 memories ??
    resp_ids = [r["id"] for r in responses if r.get("id")]
    if resp_ids:
        ph = ",".join("?" * len(resp_ids))
        conn.execute(
            f"UPDATE memories SET arc_status='closed', updated_at=? WHERE id IN ({ph})",
            (now, *resp_ids),
        )

    # Create L5 tasks per action_items (may be strings or dicts)
    task_ids = []
    for i, item in enumerate(action_items):
        if isinstance(item, str):
            desc = item
            assigned_to = participants[i % len(participants)] if participants else ""
        else:
            assigned_to = item.get("assigned_to", "")
            desc = item.get("description", "")
        assigned_to = normalize_agent_name(assigned_to)
        task_subject = f"[任务] {title} — {_smart_subject(desc)}"[:200]
        task_content = f"[任务] {title} | {desc}"[:2000]
        task_hash = hashlib.sha256(task_content.encode("utf-8")).hexdigest()
        task_meta = json.dumps({
            "status": "active",
            "assignee": assigned_to,
            "source_discussion": disc["id"],
            "source_decision": l4_id,
        })
        cur = conn.execute(
            """INSERT INTO memories
               (content, content_hash, level, owner, agent_name, subject,
                category, project, summary, occurred_at, created_at, updated_at,
                supersedes, confidence, visibility, metadata, arc_status)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (task_content, task_hash, "L5", assigned_to, assigned_to,
             task_subject, "task", disc.get("project", ""), "", now, now, now,
             None, 0.6, "private", task_meta, "open"),
        )
        tid = cur.lastrowid
        task_ids.append(tid)

        # edge: decision --refines--> task
        conn.execute(
            "INSERT INTO edges (source_id, target_id, relation_type, created_at) VALUES (?,?,?,?)",
            (l4_id, tid, "refines", now),
        )

    conn.commit()

    try:
        from memall.lark_notify import notify_discussion_converged
        notify_discussion_converged(
            discussion_id=disc["id"],
            title=title[:80],
            reason=reason,
            participants=list(latest.keys()),
            stances={a: latest.get(a, {}).get("stance", "confirm") for a in latest},
            task_count=len(task_ids),
        )
    except Exception:
        logger.warning("convergence.py: silent error", exc_info=True)

    return {
        "status": "converged",
        "discussion_id": disc["id"],
        "decision_id": l4_id,
        "task_ids": task_ids,
        "tasks_created": len(task_ids),
    }


def resolve_pending_deliberations() -> dict:
    """Sweep active L5 discussions, converge any with at least one response.

    Formerly called convergence_step (kept as alias below).
    This catches any discussions that were created but missed the
    auto-converge in confirm_discussion().
    """
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM memories WHERE level='L5' AND category='discussion' "
            "AND json_extract(metadata, '$.status') = 'active'",
        ).fetchall()

        result: dict = {
            "checked": len(rows),
            "converged": 0,
            "tasks_created": 0,
        }

        for disc in rows:
            dd = dict(disc)
            meta = json.loads(dd.get("metadata") or "{}")
            if meta.get("status") != "active":
                continue

            # Check if any response edges exist
            has_responses = conn.execute(
                "SELECT 1 FROM edges WHERE source_id=? AND relation_type='cites' LIMIT 1",
                (disc["id"],),
            ).fetchone()
            if not has_responses:
                continue

            cr = _converge_single(conn, dd)
            if cr.get("task_ids"):
                result["tasks_created"] += len(cr["task_ids"])
            result["converged"] += 1

        # status distribution
        stats = conn.execute(
            "SELECT json_extract(metadata, '$.status') as st, COUNT(*) as cnt "
            "FROM memories WHERE level='L5' AND category='discussion' GROUP BY st",
        ).fetchall()
        result["status_counts"] = {r["st"]: r["cnt"] for r in stats}

        return result
    finally:
        conn.close()


# ── Backward-compat aliases ──


def convergence_step() -> dict:
    """Deprecated: use resolve_pending_deliberations() instead."""
    return resolve_pending_deliberations()


# ── Pending check for session injection ──


def check_pending_discussions(agent_name: str) -> list[dict]:
    """Check active L5 discussions that have NOT yet received any response.

    Captures a P2 reminder memory (dedup''d: skips if one exists within 1h
    for the same discussion+agent pair).
    """
    agent_name = normalize_agent_name(agent_name)
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM memories WHERE level='L5' AND category='discussion' "
            "AND json_extract(metadata, '$.status') = 'active'",
        ).fetchall()

        pending = []
        for disc in rows:
            dd = dict(disc)

            # Has ANY response been recorded via edges?
            has_responses = conn.execute(
                "SELECT 1 FROM edges WHERE source_id=? AND relation_type='cites' LIMIT 1",
                (disc["id"],),
            ).fetchone()
            if has_responses:
                continue

            # Dedup: recent P2 reminder for same discussion+agent
            now = datetime.now(timezone.utc)
            one_hour_ago = (now - timedelta(hours=1)).isoformat()
            existing = conn.execute(
                "SELECT id FROM memories WHERE agent_name = ? AND level = 'P2' "
                "AND category = 'discussion_pending' AND metadata LIKE ? AND created_at > ?",
                (agent_name, f"%discussion_id\": {disc['id']}%", one_hour_ago),
            ).fetchone()
            if existing:
                continue

            meta = json.loads(dd.get("metadata") or "{}")

            # Capture P2 reminder
            content = (
                f"[待回应讨论] {dd['subject']} — "
                f"提醒: {agent_name} 尚未确认"
            )[:2000]
            h = hashlib.sha256(content.encode("utf-8")).hexdigest()
            rem_meta = json.dumps({
                "discussion_id": disc["id"],
                "title": dd["subject"],
                "reminder_for": agent_name,
            })
            conn.execute(
                """INSERT OR IGNORE INTO memories
                   (content, content_hash, level, owner, agent_name, subject,
                    category, summary, occurred_at, created_at, updated_at,
                    supersedes, confidence, visibility, metadata, arc_status)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (content, h, "P2", agent_name, agent_name,
                 f"待回应: {dd['subject']}", "discussion_pending", "",
                 now.isoformat(), now.isoformat(), now.isoformat(),
                 None, 0.5, "private", rem_meta, "open"),
            )
            conn.commit()

            pending.append({
                "discussion_id": disc["id"],
                "subject": dd["subject"],
                "reminder_captured": True,
            })

        return pending
    finally:
        conn.close()
