"""
Pipeline step: arc_status_step — Decision Arc lifecycle management.

Scans L4 memories and updates arc_status based on edge relationships:
  NULL → 'open' (initial state after backfill)
  'open' → 'in_progress' (L5 edge detected)
  'open'/'in_progress' → 'closed' (L6 edge detected, terminal)

Edge direction is bidirectional: both L4→L5 and L5→L4 are recognized.
"""

import logging

from memall.core.db import get_conn

logger = logging.getLogger(__name__)

_BATCH_SIZE = 1000


def _status_from_edges(conn, memory_id: int) -> str | None:
    """Determine arc_status from edge relationships.

    Returns 'closed' if L6 edge exists, 'in_progress' if L5 edge exists, else None.
    """
    # Check L6: closed (terminal - highest priority)
    has_l6 = conn.execute(
        "SELECT 1 FROM edges WHERE relation_type != 'deleted' "
        "AND ((source_id = ? AND target_id IN (SELECT id FROM memories WHERE level = 'L6')) "
        "OR (target_id = ? AND source_id IN (SELECT id FROM memories WHERE level = 'L6'))) "
        "LIMIT 1",
        (memory_id, memory_id),
    ).fetchone()
    if has_l6:
        return "closed"

    # Check L5: in_progress
    # Edge source/target not constrained by direction — bidirectional
    has_l5 = conn.execute(
        "SELECT 1 FROM edges WHERE relation_type != 'deleted' "
        "AND ((source_id = ? AND target_id IN (SELECT id FROM memories WHERE level = 'L5')) "
        "OR (target_id = ? AND source_id IN (SELECT id FROM memories WHERE level = 'L5'))) "
        "LIMIT 1",
        (memory_id, memory_id),
    ).fetchone()
    if has_l5:
        return "in_progress"

    return None


def arc_status_step() -> dict:
    """Scan L4 memories and update arc_status based on edge relationships.

    Backfill mode (first run): process all L4s where arc_status IS NULL.
    Incremental mode: process all L4s where arc_status != 'closed'.

    Returns dict with counts of status changes.
    """
    conn = get_conn()
    try:
        # Phase 1: Backfill NULL arc_status L4s
        null_rows = conn.execute(
            "SELECT id, created_at FROM memories WHERE level = 'L4' AND arc_status IS NULL"
        ).fetchall()

        backfilled = 0
        for row in null_rows:
            determined = _status_from_edges(conn, row["id"])
            new_status = determined or "open"
            conn.execute(
                "UPDATE memories SET arc_status = ? WHERE id = ?",
                (new_status, row["id"]),
            )
            backfilled += 1

            if backfilled % _BATCH_SIZE == 0:
                conn.commit()

        if backfilled > 0:
            conn.commit()

        # Phase 2: Update non-closed L4s (both existing open/in_progress and newly backfilled)
        active_rows = conn.execute(
            "SELECT id, arc_status FROM memories WHERE level = 'L4' AND "
            "arc_status IS NOT NULL AND arc_status != 'closed'"
        ).fetchall()

        upgraded = 0
        for row in active_rows:
            determined = _status_from_edges(conn, row["id"])
            if determined and determined != row["arc_status"]:
                conn.execute(
                    "UPDATE memories SET arc_status = ? WHERE id = ?",
                    (determined, row["id"]),
                )
                upgraded += 1

                if upgraded % _BATCH_SIZE == 0:
                    conn.commit()

        if upgraded > 0:
            conn.commit()

        # Stats
        stats = conn.execute(
            "SELECT arc_status, COUNT(*) as cnt FROM memories WHERE level = 'L4' "
            "AND arc_status IS NOT NULL GROUP BY arc_status"
        ).fetchall()
        status_counts = {r["arc_status"]: r["cnt"] for r in stats}

        return {
            "backfilled": backfilled,
            "upgraded": upgraded,
            "status_counts": status_counts,
        }
    finally:
        conn.close()
