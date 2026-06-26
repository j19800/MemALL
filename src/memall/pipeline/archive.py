"""Pipeline step: archive cold terminal memories to archive.db.

Hot/cold separation (S3-02): moves L6/L9/L10/L11 memories beyond their
TTL from ``data.db`` to ``archive.db``, keeping the hot DB lean.
"""

import logging
from memall.core.db import get_conn, ARCHIVE_DB_PATH, init_archive_db
from memall.pipeline.forget import _LEVEL_TTL, _ttl_cutoff

logger = logging.getLogger(__name__)

# Only these terminal layers are eligible for archiving.
# They are write-once, read-rarely — perfect archive candidates.
_ARCHIVE_LEVELS = ("L6", "L9", "L10", "L11")

_ARCHIVE_STEP_SCALE = 90  # same baseline as forget.py (days param)


def archive_step(days: int = _ARCHIVE_STEP_SCALE) -> dict:
    """Move cold terminal memories from data.db to archive.db.

    Args:
        days: Scaling factor (same semantics as forget's ``days`` param).
              Default 90 = use raw TTLs from ``_LEVEL_TTL``.

    Returns:
        ``{"archived_memories": int, "archived_edges": int, "by_level": {...}}``
    """
    scale = days / 90.0
    conn = get_conn()
    init_archive_db()

    try:
        conn.execute("ATTACH DATABASE ? AS archive_db", (str(ARCHIVE_DB_PATH),))
        conn.execute("BEGIN")

        archived_memories = 0
        archived_edges = 0
        by_level = {}

        for level in _ARCHIVE_LEVELS:
            ttl = _LEVEL_TTL.get(level)
            if ttl is None or ttl <= 0:
                by_level[level] = 0
                continue
            scaled_ttl = max(1, int(ttl * scale))
            cutoff = _ttl_cutoff(scaled_ttl)

            rows = conn.execute(
                "SELECT id FROM memories WHERE level = ? AND created_at < ?",
                (level, cutoff),
            ).fetchall()
            ids = [r["id"] for r in rows]
            if not ids:
                by_level[level] = 0
                continue

            ph = ",".join("?" * len(ids))

            # Copy memories to archive
            conn.execute(f"""
                INSERT OR IGNORE INTO archive_db.archived_memories
                (id, content, content_hash, level, owner, agent_name, subject,
                 project, category, summary, occurred_at, created_at, updated_at,
                 supersedes, trust_level, access_count, metadata, arc_status,
                 thread_id, agent_name_locked, archived_at)
                SELECT m.id, m.content, m.content_hash, m.level, m.owner,
                       m.agent_name, m.subject, m.project, m.category, m.summary,
                       m.occurred_at, m.created_at, m.updated_at, m.supersedes,
                       m.trust_level, m.access_count, m.metadata, m.arc_status,
                       m.thread_id, m.agent_name_locked, datetime('now')
                FROM memories m WHERE m.id IN ({ph})
            """, ids)

            # Copy edges to archive (both directions)
            edge_ids = conn.execute(
                f"SELECT id FROM edges WHERE source_id IN ({ph}) OR target_id IN ({ph})",
                ids * 2,
            ).fetchall()
            eids = [r["id"] for r in edge_ids]

            if eids:
                e_ph = ",".join("?" * len(eids))
                conn.execute(f"""
                    INSERT OR IGNORE INTO archive_db.archived_edges
                    (id, source_id, target_id, relation_type, weight, created_at,
                     metadata, archived_at)
                    SELECT e.id, e.source_id, e.target_id, e.relation_type, e.weight,
                           e.created_at, e.metadata, datetime('now')
                    FROM edges e WHERE e.id IN ({e_ph})
                """, eids)
                archived_edges += len(eids)

            # Delete edges from hot DB first (both directions)
            conn.execute(
                f"DELETE FROM edges WHERE source_id IN ({ph}) OR target_id IN ({ph})",
                ids * 2,
            )

            # Delete memories from hot DB
            conn.execute(
                f"DELETE FROM memories WHERE id IN ({ph})", ids
            )

            # Clean up orphan vec0 entries
            conn.execute(
                f"DELETE FROM mem_vec WHERE rowid IN ({ph})", ids
            )

            archived_memories += len(ids)
            by_level[level] = len(ids)

        conn.execute("DETACH DATABASE archive_db")
        conn.commit()

        return {
            "archived_memories": archived_memories,
            "archived_edges": archived_edges,
            "by_level": by_level,
        }
    except Exception:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()
