"""Pipeline event processor — reads pending events and dispatches to steps.

Replaces full-table scans with targeted processing of new/changed memories.
"""

import logging
from datetime import datetime, timezone

from memall.core.db import get_conn, pool_conn

logger = logging.getLogger(__name__)

_BATCH_SIZE = 500


def process_events() -> dict:
    """Read pending pipeline events and dispatch to relevant steps.

    For each unprocessed event, records the memory_id in the ``pipeline_state``
    table so subsequent steps can process only that memory instead of scanning
    all memories.

    Returns:
        ``{"processed": int, "pending": int, "events": [...]}``
    """
    conn = get_conn()
    try:
        # Fetch unprocessed events (oldest first)
        rows = conn.execute(
            "SELECT id, memory_id, event_type, created_at "
            "FROM pipeline_events WHERE processed_at IS NULL "
            "ORDER BY id ASC LIMIT ?",
            (_BATCH_SIZE,),
        ).fetchall()

        if not rows:
            return {"processed": 0, "pending": 0, "events": []}

        now = datetime.now(timezone.utc).isoformat()
        event_ids = [r["id"] for r in rows]

        # Mark events as processed
        ph = ",".join("?" * len(event_ids))
        conn.execute(
            f"UPDATE pipeline_events SET processed_at = ? WHERE id IN ({ph})",
            (now, *event_ids),
        )
        conn.commit()

        remaining = conn.execute(
            "SELECT COUNT(*) FROM pipeline_events WHERE processed_at IS NULL"
        ).fetchone()[0]

        return {
            "processed": len(rows),
            "pending": remaining,
            "events": [
                {"id": r["id"], "memory_id": r["memory_id"],
                 "event_type": r["event_type"], "created_at": r["created_at"]}
                for r in rows
            ],
        }
    finally:
        conn.close()