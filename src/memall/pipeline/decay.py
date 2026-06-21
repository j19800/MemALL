"""
Decay pipeline — purge low-value memories and gradually reduce confidence
of stale entries, with automatic backup and transactional safety.

Tiered decay per level (P0–P4):

  Level   | Semantics     | Decay rate | Inactivity buffer | TTL (forget)
  --------|---------------|------------|-------------------|-------------
  P0      | Temporary     | direct del | none              | excluded
  P1      | Important     | -0.01/cyc  | 30 days           | 180 days
  P2      | Default       | -0.02/cyc  | 14 days           | 90 days
  P3      | Low priority  | -0.04/cyc  | 10 days           | 45 days
  P4      | Reference     | -0.06/cyc  | 7 days            | 30 days
"""

from memall.core.db import get_conn
from memall.pipeline.forget import _backup_before_delete

# (decay_rate, inactivity_days) for each level
_LEVEL_CONFIG = {
    "P0": (0, 0),         # handled separately (DELETE, not UPDATE)
    "P1": (0.01, 30),
    "P2": (0.02, 14),
    "P3": (0.04, 10),
    "P4": (0.06, 7),
    "L1": (0, 0),         # permanent (identity)
    "L2": (0.02, 30),
    "L3": (0.02, 30),
    "L4": (0.03, 14),
    "L5": (0.02, 30),
    "L6": (0.01, 60),     # reflection, slow decay
    "L7": (0, 0),         # permanent (preference)
    "L8": (0.02, 30),
    "L9": (0, 0),         # permanent (distilled knowledge)
    "L10": (0, 0),        # permanent (integrated knowledge)
}


def decay_step() -> dict:
    """Run one decay cycle: purge abandoned P0, decay stale per level, clean orphan edges.

    Returns:
        dict with keys ``purged`` (int), ``decayed`` (int).
    """
    _backup_before_delete()

    conn = get_conn()
    try:
        conn.execute("BEGIN")

        # P0 — direct delete when confidence too low and never accessed
        cur = conn.execute(
            "DELETE FROM memories WHERE level = 'P0' AND confidence < 0.3 AND access_count = 0"
        )
        purged = cur.rowcount

        # P1–P4, L1–L10 — tiered decay
        decayed = 0
        for level, (rate, days) in _LEVEL_CONFIG.items():
            if level == "P0" or rate <= 0:
                continue  # P0 handled above; L1/L7 are permanent
            cur = conn.execute(
                "UPDATE memories SET confidence = MAX(0.1, confidence - ?) "
                "WHERE level = ? AND access_count = 0 "
                "AND updated_at < datetime('now', ? || ' days')",
                (rate, level, f"-{days}"),
            )
            decayed += cur.rowcount

        conn.execute(
            "DELETE FROM edges WHERE source_id NOT IN (SELECT id FROM memories) "
            "OR target_id NOT IN (SELECT id FROM memories)"
        )
        conn.execute("COMMIT")
        return {"purged": purged, "decayed": decayed}
    except Exception:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()