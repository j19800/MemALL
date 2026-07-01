"""Pipeline step: extract structured knowledge from ended sessions.

Scans ended sessions, groups their memories by category
(decision/architecture/problem/fix/rule), and creates structured L6 entries
with ``derived_from`` edges linking back to source memories.
"""

import hashlib
import json
import logging
import sqlite3
from datetime import datetime, timezone

from memall.core.db import get_conn
from memall.pipeline.util import _smart_subject

logger = logging.getLogger(__name__)

_EXTRACT_CATEGORIES = ("decision", "architecture", "problem", "fix", "rule")

# Minimum memories in a category to trigger L6 extraction
_MIN_PER_CATEGORY = 2

# Max key sentences per L6 entry
_MAX_KEY_SENTENCES = 6

# Map category → L6 category label
_CATEGORY_LABEL = {
    "decision": "decision",
    "architecture": "architecture",
    "problem": "problem",
    "fix": "fix",
    "rule": "rule",
}


def _ensure_cursors_table(conn):
    conn.execute(
        "CREATE TABLE IF NOT EXISTS pipeline_cursors "
        "(step TEXT PRIMARY KEY, cursor_id INTEGER, updated_at TEXT)"
    )


def extract_step() -> dict:
    """Scan ended sessions and create structured L6 knowledge entries.

    For each ended session whose ``ended_at`` is past the cursor:
    - Locate the session's L4 memory (via ``json_extract(metadata, '$.session_id')``)
    - Scan memories within the session time window for categories
      ``decision/architecture/problem/fix/rule``
    - Group by category; when a category has ≥2 memories, create an L6 entry
      with key-sentence extraction and ``derived_from`` edges

    Returns:
        ``{"scanned": int, "sessions_processed": int, "l6_created": int, "edges_created": int}``
    """
    conn = get_conn()
    try:
        _ensure_cursors_table(conn)

        # Read cursor: the last ended_at we fully processed
        cursor_row = conn.execute(
            "SELECT updated_at FROM pipeline_cursors WHERE step='extract'"
        ).fetchone()
        cursor = cursor_row["updated_at"] if cursor_row else None

        # Find ended sessions past cursor
        if cursor:
            sessions = conn.execute(
                "SELECT session_id, agent_name, started_at, ended_at FROM sessions "
                "WHERE ended_at IS NOT NULL AND ended_at > ? "
                "ORDER BY ended_at ASC LIMIT 1000",
                (cursor,),
            ).fetchall()
        else:
            sessions = conn.execute(
                "SELECT session_id, agent_name, started_at, ended_at FROM sessions "
                "WHERE ended_at IS NOT NULL "
                "ORDER BY ended_at ASC"
            ).fetchall()

        scanned = len(sessions)
        sessions_processed = 0
        l6_created = 0
        edges_created = 0

        for session in sessions:
            session_id = session["session_id"]
            agent_name = session["agent_name"] or ""
            started_at = session["started_at"]
            ended_at = session["ended_at"]

            # Find the session's L4 memory
            l4_row = conn.execute(
                "SELECT id FROM memories WHERE level = 'L4' AND category = 'session' "
                "AND json_extract(metadata, '$.session_id') = ? LIMIT 1",
                (session_id,),
            ).fetchone()
            if not l4_row:
                continue
            thread_id = l4_row["id"]

            # Determine project from session L4 metadata
            meta_row = conn.execute(
                "SELECT json_extract(metadata, '$.project') AS project FROM memories WHERE id = ?",
                (thread_id,),
            ).fetchone()

            # Query memories within session time window per category
            where = ["created_at >= ?", "created_at <= ?"]
            if agent_name:
                where.append("agent_name = ?")

            for cat in _EXTRACT_CATEGORIES:
                if agent_name:
                    params = [started_at, ended_at, agent_name, cat]
                else:
                    params = [started_at, ended_at, cat]

                cat_rows = conn.execute(
                    f"SELECT id, content FROM memories WHERE {' AND '.join(where)} "
                    f"AND category = ? ORDER BY created_at ASC",
                    params,
                ).fetchall()

                if len(cat_rows) < _MIN_PER_CATEGORY:
                    continue

                source_ids = [r["id"] for r in cat_rows]

                # Extract key sentences: non-empty content lines/sentences
                sentences = []
                for r in cat_rows:
                    text = (r["content"] or "").strip()
                    if not text or len(text) < 10:
                        continue
                    # Use first sentence or first 120 chars
                    idx = text.find("。")
                    if idx > 0:
                        s = text[: idx + 1].strip()
                    else:
                        s = text[:120].strip()
                    if s and s not in sentences:
                        sentences.append(s)
                        if len(sentences) >= _MAX_KEY_SENTENCES:
                            break

                key_sentences = "｜".join(sentences[: _MAX_KEY_SENTENCES])

                # Check if L6 already exists for this session+category
                existing = conn.execute(
                    "SELECT id FROM memories WHERE level = 'L6' AND category = ? "
                    "AND json_extract(metadata, '$.session_id') = ? "
                    "AND json_extract(metadata, '$.extract_category') = ? LIMIT 1",
                    (cat, session_id, cat),
                ).fetchone()
                if existing:
                    continue

                # Build L6 content
                label = _CATEGORY_LABEL.get(cat, cat)
                l6_content = (
                    f"[L6 提取] {agent_name} 在 {label} 领域共 {len(cat_rows)} 条"
                )
                if key_sentences:
                    l6_content += f"\n要点：{key_sentences}"
                content_hash = hashlib.sha256(l6_content.encode()).hexdigest()
                now = datetime.now(timezone.utc).isoformat()
                l6_subject = _smart_subject(l6_content)

                # Get project from session's L4 if available
                project = ""
                if meta_row:
                    try:
                        meta = json.loads(meta_row["project"] or "{}") if meta_row["project"] else {}
                        if isinstance(meta, dict):
                            project = meta.get("project", "") or ""
                    except (json.JSONDecodeError, TypeError):
                        pass

                conn.execute(
                    "INSERT OR IGNORE INTO memories "
                    "(content, content_hash, level, owner, agent_name, category, project, "
                    "subject, summary, occurred_at, created_at, updated_at, "
                    "confidence, visibility, metadata, thread_id) "
                    "VALUES (?, ?, 'L6', 'system', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        l6_content[:2000],
                        content_hash,
                        agent_name,
                        label,
                        project,
                        l6_subject,
                        l6_content[:200],
                        now,
                        now,
                        now,
                        0.6,
                        "private",
                        json.dumps({
                            "session_id": session_id,
                            "extract_category": cat,
                            "source_memory_count": len(cat_rows),
                            "source": "pipeline_extract",
                        }),
                        thread_id,
                    ),
                )

                # Get the new L6 id
                l6_row = conn.execute(
                    "SELECT id FROM memories WHERE content_hash = ? AND level = 'L6'",
                    (content_hash,),
                ).fetchone()
                if not l6_row:
                    continue
                l6_id = l6_row["id"]

                # Create derived_from edges to source memories
                edge_count = 0
                for src_id in source_ids:
                    edge_exists = conn.execute(
                        "SELECT id FROM edges WHERE source_id = ? AND target_id = ? AND relation_type = 'derived_from'",
                        (l6_id, src_id),
                    ).fetchone()
                    if not edge_exists:
                        try:
                            conn.execute(
                                "INSERT INTO edges (source_id, target_id, relation_type, weight, created_at) "
                                "VALUES (?, ?, 'derived_from', 1.0, ?)",
                                (l6_id, src_id, now),
                            )
                            edge_count += 1
                        except Exception:
                            logger.warning("extract: edge insert failed for L6=%s src=%s", l6_id, src_id, exc_info=True)

                l6_created += 1
                edges_created += edge_count

            sessions_processed += 1

            # Update cursor to this session's ended_at
            conn.execute(
                "INSERT INTO pipeline_cursors (step, cursor_id, updated_at) VALUES ('extract', 0, ?) "
                "ON CONFLICT(step) DO UPDATE SET updated_at=excluded.updated_at",
                (ended_at,),
            )

        conn.commit()

        logger.info(
            "extract_step: scanned=%d processed=%d l6=%d edges=%d",
            scanned, sessions_processed, l6_created, edges_created,
        )
        return {
            "scanned": scanned,
            "sessions_processed": sessions_processed,
            "l6_created": l6_created,
            "edges_created": edges_created,
        }
    except sqlite3.Error:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()
