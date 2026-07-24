"""
Decay pipeline — adaptive forgetting based on access patterns.

Key improvements over static decay:
- High access frequency → slower decay (memories that are read often live longer)
- Referenced by other memories → protected from decay
- Long-term unaccessed → accelerated decay
- Temporary P0 with any access history → still eligible for deletion after longer window
"""

from memall.core.db import get_conn
from memall.pipeline.forget import _backup_before_delete
import sqlite3

# (base_decay_rate, inactivity_days) for each level
_LEVEL_CONFIG = {
    "P0": (0, 0),
    "P1": (0.01, 30),
    "P2": (0.02, 14),
    "P3": (0.04, 10),
    "P4": (0.06, 7),
    "L1": (0, 0),    # permanent
    "L2": (0.02, 30),
    "L3": (0.02, 30),
    "L4": (0.03, 14),
    "L5": (0.02, 30),
    "L6": (0.01, 60),
    "L7": (0, 0),    # permanent
    "L8": (0.02, 30),
    "L9": (0, 0),    # permanent
    "L10": (0, 0),   # permanent
}


def _get_access_multiplier(conn, memory_id: int) -> float:
    """Calculate access-based decay multiplier.

    Returns:
        > 1.0 = faster decay (rarely accessed)
        < 1.0 = slower decay (frequently accessed)
        0     = protected (referenced by other memories)
    """
    # Check if referenced by other memories
    ref_count = conn.execute(
        "SELECT COUNT(*) FROM edges WHERE target_id = ?", (memory_id,)
    ).fetchone()[0]
    if ref_count > 0:
        return 0  # protected

    # Check access count
    row = conn.execute(
        "SELECT access_count FROM memories WHERE id = ?", (memory_id,)
    ).fetchone()
    if not row:
        return 1.0

    acc = row["access_count"]
    if acc >= 10:
        return 0.3  # frequently accessed → slow decay
    elif acc >= 5:
        return 0.5
    elif acc >= 3:
        return 0.7
    elif acc >= 1:
        return 0.9
    return 1.5  # never accessed → accelerate decay


def decay_step() -> dict:
    """Run adaptive decay: purge low-value P0, decay per level with access-awareness.

    Compared to the static version, this:
    - Queries per-memory access_count and edge references
    - Adjusts decay rate dynamically: 0.3x (frequent) to 1.5x (never accessed)
    - Protects referenced memories from decay entirely

    Returns:
        dict with keys ``purged`` (int), ``decayed`` (int), ``protected`` (int).
    """
    _backup_before_delete()

    conn = get_conn()
    try:
        conn.execute("BEGIN")

        # P0 — delete when confidence too low, regardless of access_count
        # (previously required access_count=0, which let P0 accumulate forever)
        purged = conn.execute(
            "DELETE FROM memories WHERE level = 'P0' AND confidence < 0.3"
        ).rowcount

        # For remaining levels, decay each memory individually based on access pattern
        decayed = 0
        protected = 0
        for level, (base_rate, days) in _LEVEL_CONFIG.items():
            if level == "P0" or base_rate <= 0:
                continue

            # Fetch candidate memories
            rows = conn.execute(
                "SELECT id, access_count, confidence FROM memories "
                "WHERE level = ? AND updated_at < datetime('now', ? || ' days')",
                (level, f"-{days}"),
            ).fetchall()

            for row in rows:
                mid = row["id"]
                multiplier = _get_access_multiplier(conn, mid)

                if multiplier == 0:
                    # Referenced — protect from decay
                    protected += 1
                    continue

                new_conf = max(0.1, row["confidence"] - base_rate * multiplier)
                conn.execute(
                    "UPDATE memories SET confidence = ?, updated_at = datetime('now') WHERE id = ?",
                    (new_conf, mid),
                )
                decayed += 1

        # Clean orphan edges
        conn.execute(
            "DELETE FROM edges WHERE source_id NOT IN (SELECT id FROM memories) "
            "OR target_id NOT IN (SELECT id FROM memories)"
        )
        conn.execute("COMMIT")
        return {"purged": purged, "decayed": decayed, "protected": protected}

    except sqlite3.Error:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()