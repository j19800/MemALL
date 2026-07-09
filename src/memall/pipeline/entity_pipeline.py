"""
Entity Extraction Pipeline Step — Scans unprocessed memories for entities.

Uses a cursor on ``pipeline_cursors`` to track progress (``cursor_name='entity_extraction'``).
Skips memories that have already been scanned.
"""

import json
import logging
from datetime import datetime, timezone
from typing import Optional

from memall.core.entity_extractor import extract_entities, extract_triples, resolve_entity

logger = logging.getLogger(__name__)

_CURSOR_NAME = "entity_extraction"


def _get_cursor(conn) -> int:
    """Get the last processed memory ID for entity extraction."""
    row = conn.execute(
        "SELECT cursor_value FROM pipeline_cursors WHERE cursor_name = ?",
        (_CURSOR_NAME,),
    ).fetchone()
    return row["cursor_value"] if row else 0


def _set_cursor(conn, memory_id: int):
    """Advance the cursor."""
    conn.execute(
        "INSERT OR REPLACE INTO pipeline_cursors (cursor_name, cursor_value, updated_at) "
        "VALUES (?, ?, ?)",
        (_CURSOR_NAME, memory_id, datetime.now(timezone.utc).isoformat()),
    )


def entity_extraction_step(conn=None) -> dict:
    """Scan unprocessed memories for named entities.

    Args:
        conn: Optional DB connection (created internally if None).

    Returns:
        dict with keys: scanned, entities_found, triples_found, memories_tagged, error (if any)
    """
    from memall.core.db import get_conn
    own_conn = conn is None
    if own_conn:
        conn = get_conn()

    try:
        cursor = _get_cursor(conn)
        now = datetime.now(timezone.utc).isoformat()

        rows = conn.execute(
            "SELECT id, content, level, agent_name FROM memories "
            "WHERE id > ? AND LENGTH(TRIM(content)) > 20 ORDER BY id LIMIT 500",
            (cursor,),
        ).fetchall()

        if not rows:
            return {"scanned": 0, "entities_found": 0, "triples_found": 0, "memories_tagged": 0}

        total_entities = 0
        total_triples = 0
        tagged = 0
        max_id = cursor

        for row in rows:
            mid = row["id"]
            content = row["content"]
            level = row["level"] or ""
            agent = row["agent_name"] or ""
            max_id = max(max_id, mid)

            # Extract entities
            entities = extract_entities(content, agent)
            for ent in entities:
                eid = resolve_entity(ent["name"], ent["entity_type"], conn)
                conn.execute(
                    "INSERT OR IGNORE INTO memory_entities "
                    "(memory_id, entity_id, role, confidence, context_snippet, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        mid, eid, "mentioned",
                        ent.get("confidence", 1.0),
                        ent.get("context_snippet", "")[:200],
                        now,
                    ),
                )
                total_entities += 1

            # Extract triples from L6+ memories only
            if level in ("L6", "L7", "L8", "L9", "L10", "L11"):
                triples = extract_triples(content, agent)
                for t in triples:
                    subj_id = resolve_entity(t["subject"], t.get("subject_type", "concept"), conn)
                    obj_id = resolve_entity(t["object"], t.get("object_type", "concept"), conn)
                    conn.execute(
                        "INSERT OR IGNORE INTO knowledge_triples "
                        "(subject_id, predicate, object_id, source_memory_id, confidence, weight, created_at) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (
                            subj_id, t["predicate"], obj_id,
                            mid, t.get("confidence", 0.8), 1.0, now,
                        ),
                    )
                    total_triples += 1

            if entities or (level in ("L6", "L7", "L8", "L9", "L10", "L11") and triples):
                tagged += 1

        _set_cursor(conn, max_id)
        if own_conn:
            conn.commit()

        logger.info(
            "entity_extraction: scanned %d, entities %d, triples %d, tagged %d",
            len(rows), total_entities, total_triples, tagged,
        )
        return {
            "scanned": len(rows),
            "entities_found": total_entities,
            "triples_found": total_triples,
            "memories_tagged": tagged,
        }

    except Exception as e:
        logger.error("entity_extraction step failed: %s", e, exc_info=True)
        if own_conn:
            conn.rollback()
        return {"scanned": 0, "entities_found": 0, "triples_found": 0, "memories_tagged": 0, "error": str(e)}

    finally:
        if own_conn and conn:
            try:
                conn.close()
            except Exception:
                pass