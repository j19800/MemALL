"""
Migration 033: Retire ``discussion_topics`` / ``discussion_responses``.

Migration 019 created these two tables for the convergence engine, but the engine
does not use them: a discussion is an ``L5`` memory with
``category='discussion'`` and a response is a ``P2`` memory with
``category='discussion_response'`` linked by an ``edges`` row with
``relation_type='cites'`` (see :mod:`memall.pipeline.convergence`).  Nothing in
the tree reads or writes the tables — the only reference is 019 itself.
Meanwhile the live database lost them: absent from ``sqlite_master`` while
``schema_version`` still records 019 as applied, and one of their
``l4_memory_id`` targets (#6303) no longer exists.  Schema and recorded contract
disagreed, and a fresh install silently diverged from production.

Rather than drop dead schema along with any history it still holds, this
migration first rewrites rows it finds into the live model, then drops the tables
so every install converges on the one discussion representation:

* topic → ``L5`` / ``category='discussion'``, body in the same
  问题描述/解决方案/建议 layout ``create_discussion()`` writes, ``metadata.status``
  mapped onto the workflow track (``converged`` stays; ``active``/``stale``
  become ``archived`` — unanswered history must not resurface as pending work or
  get auto-converged into phantom tasks).
* response → ``P2`` / ``category='discussion_response'`` with ``metadata.stance``
  normalised to the convergence vocabulary (agree→confirm, disagree→reject,
  pass/abstain→abstain), plus the ``cites`` edge readers expect.

``memories.content_hash`` has no unique index, so ``INSERT OR IGNORE`` alone would
not dedup: every row is checked by hash first and re-imported only when it is
genuinely new.  Re-running is therefore safe — and pointless once the tables are
dropped, which is when a half-applied run would otherwise have duplicated history.
"""

import hashlib
import json
import logging
import sqlite3
import uuid

MIGRATION_ID = "033_retire_discussion_tables"
DESCRIPTION = "Import discussion_topics/responses into the L5+edges model, then drop them"

logger = logging.getLogger(__name__)

# Old table stance vocabulary → the one respond_discussion()/validator use.
_STANCE_MAP = {
    "agree": "confirm",
    "disagree": "reject",
    "pass": "abstain",
    "abstain": "abstain",
    "confirm": "confirm",
    "reject": "reject",
}

_TOPIC_INSERT = """
    INSERT OR IGNORE INTO memories
      (content, content_hash, level, owner, agent_name, creator, subject,
       project, category, summary, occurred_at, created_at, updated_at,
       supersedes, confidence, visibility, metadata, tags, memory_status,
       arc_status)
    VALUES (?, ?, 'L5', ?, ?, ?, ?, ?, 'discussion', ?,
            ?, ?, ?, '[]', 0.6, 'private', ?, '[]', ?, 'closed')
"""

_RESPONSE_INSERT = """
    INSERT OR IGNORE INTO memories
      (content, content_hash, level, owner, agent_name, creator, subject,
       project, category, summary, occurred_at, created_at, updated_at,
       supersedes, confidence, visibility, metadata, tags)
    VALUES (?, ?, 'P2', ?, ?, ?, ?, ?, 'discussion_response', ?,
            ?, ?, ?, '[]', 0.6, 'private', ?, '[]')
"""


def _has_table(conn, name: str) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
        ).fetchone()
        is not None
    )


def _as_list(value):
    """The topics table stores list fields as JSON text; tolerate bare values."""
    if value is None:
        return []
    if isinstance(value, (list, dict)):
        return value
    try:
        parsed = json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return [value] if str(value).strip() else []
    return parsed if isinstance(parsed, list) else [parsed]


def _topic_content(row) -> str:
    """Rebuild the structured body create_discussion() writes."""
    uid = row["topic_id"] or "dt_" + uuid.uuid4().hex[:8]
    parts = [f"[讨论] {row['title']} [{uid}]"]
    if row["background"]:
        parts.append(f"\n\n== 问题描述（事实与数据）==\n{row['background']}")
    options = _as_list(row["options"])
    if options:
        parts.append(
            "\n\n== 解决方案 ==\n"
            + "\n".join(f"{i}. {o}" for i, o in enumerate(options, 1))
        )
    return "".join(parts)[:2000]


def _topic_status(old: str) -> tuple:
    """Map the old topic status onto (metadata.status, memory_status)."""
    if old == "converged":
        return "converged", None
    return "archived", "archived"


def _memory_id_for(conn, content_hash: str) -> int:
    row = conn.execute(
        "SELECT id FROM memories WHERE content_hash = ?", (content_hash,)
    ).fetchone()
    return row["id"] if row else 0


def _project_for(conn, l4_memory_id) -> str:
    """Inherit the project of the L4 memory the topic was raised from."""
    if not l4_memory_id:
        return ""
    row = conn.execute(
        "SELECT project FROM memories WHERE id = ?", (l4_memory_id,)
    ).fetchone()
    return (row["project"] or "") if row else ""


def _import_responses(conn, topic_row, topic_id: int, project: str, counts: dict) -> None:
    responses = conn.execute(
        "SELECT agent_name, stance, arguments, response_round, responded_at"
        " FROM discussion_responses WHERE topic_id = ?"
        " ORDER BY response_round, id",
        (topic_row["topic_id"],),
    ).fetchall()
    for resp in responses:
        agent = resp["agent_name"] or "system"
        stance = _STANCE_MAP.get((resp["stance"] or "").lower(), "abstain")
        subject = f"[表态] 讨论#{topic_id} | {agent}: {stance}"[:200]
        content = f"{subject}\n\n{resp['arguments'] or ''}"[:2000]
        h = hashlib.sha256(content.encode("utf-8")).hexdigest()
        at = resp["responded_at"] or topic_row["created_at"]
        meta = {
            "stance": stance,
            "discussion_id": topic_id,
            "agent_name": agent,
            "note": resp["arguments"] or "",
            "response_round": resp["response_round"],
            "migrated_from": "discussion_responses",
            "source_topic_id": topic_row["topic_id"],
        }
        if _memory_id_for(conn, h):
            continue
        cur = conn.execute(
            _RESPONSE_INSERT,
            (
                content,
                h,
                agent,
                agent,
                agent,
                subject,
                project,
                (resp["arguments"] or "")[:200],
                at,
                at,
                at,
                json.dumps(meta, ensure_ascii=False),
            ),
        )
        resp_id = cur.lastrowid
        counts["responses"] += 1
        conn.execute(
            "INSERT INTO edges (source_id, target_id, relation_type, weight,"
            " created_at, metadata, valid_from) VALUES (?, ?, 'cites', 1.0, ?, '{}', ?)",
            (topic_id, resp_id, at, at),
        )
        counts["edges"] += 1


def _import_topics(conn) -> dict:
    counts = {"topics": 0, "responses": 0, "edges": 0}
    topics = conn.execute(
        "SELECT topic_id, l4_memory_id, title, background, options, participants,"
        " open_questions, status, timeout_hours, created_at, updated_at, closed_at"
        " FROM discussion_topics ORDER BY created_at"
    ).fetchall()

    for row in topics:
        content = _topic_content(row)
        h = hashlib.sha256(content.encode("utf-8")).hexdigest()
        status, lifecycle = _topic_status(row["status"])
        participants = _as_list(row["participants"])
        creator = participants[0] if participants else "system"
        project = _project_for(conn, row["l4_memory_id"])
        meta = {
            "status": status,
            "pinned_fields": ["category", "level"],
            "participants": participants,
            "suggested_participants": [],
            "options": _as_list(row["options"]),
            "open_questions": _as_list(row["open_questions"]),
            "action_items": [],
            "conclusion": "",
            "converged_at": row["closed_at"] or "",
            "convergence_reason": "migrated from discussion_topics",
            "migrated_from": "discussion_topics",
            "source_topic_id": row["topic_id"],
            "source_l4_memory_id": row["l4_memory_id"],
            "source_timeout_hours": row["timeout_hours"],
        }
        topic_id = _memory_id_for(conn, h)
        if not topic_id:
            cur = conn.execute(
                _TOPIC_INSERT,
                (
                    content,
                    h,
                    creator,
                    creator,
                    creator,
                    f"[讨论] {row['title']}"[:200],
                    project,
                    (row["background"] or row["title"] or "")[:200],
                    row["created_at"],
                    row["created_at"],
                    row["updated_at"] or row["created_at"],
                    json.dumps(meta, ensure_ascii=False),
                    lifecycle,
                ),
            )
            topic_id = cur.lastrowid
            counts["topics"] += 1
        _import_responses(conn, row, topic_id, project, counts)
    return counts


def apply(conn):
    if not _has_table(conn, "discussion_topics"):
        return
    conn.row_factory = sqlite3.Row
    counts = _import_topics(conn)
    conn.execute("DROP TABLE IF EXISTS discussion_responses")
    conn.execute("DROP TABLE IF EXISTS discussion_topics")
    conn.commit()
    logger.info(
        "migration 033: retired discussion tables after importing %d topic(s),"
        " %d response(s), %d edge(s)",
        counts["topics"],
        counts["responses"],
        counts["edges"],
    )
