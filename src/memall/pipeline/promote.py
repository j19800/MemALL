"""
Heat-based level promotion — automatically promote frequently accessed memories
to higher knowledge levels based on access patterns and references.

Idempotency contract: ``recompute`` — but only because promotion is now
CONSUMING.  ``access_count`` is a lifetime counter (the hot-topic ranking and
the decay score read it), so it must not be reset.  Instead the step records
``metadata.promoted_at_access`` — the access count as of the last promotion —
and only the INCREMENT beyond that snapshot can promote a memory again.

Without this the step promoted the same row on every run: measured a single
L1 memory walk L1 -> L2 -> L3 -> L4 across three consecutive runs while its
access count stayed at 9.  That is not "recomputable", it is unbounded
escalation — and it is why the step had no declared contract.
"""

from memall.config import get_config
from memall.core.db import get_conn
import json
import sqlite3


def _get_promotion_thresholds():
    """Get promotion thresholds from config."""
    return get_config("promotion.access_thresholds", {})


def _should_promote_memory(conn, memory_id: int, current_level: str) -> tuple[bool, str]:
    """
    Determine if a memory should be promoted and to which level.
    
    Returns:
        (should_promote, target_level) tuple
    """
    thresholds = _get_promotion_thresholds()
    if current_level not in thresholds:
        return False, ""
    
    # Get memory stats
    row = conn.execute(
        "SELECT access_count, created_at, metadata, "
        "julianday('now') - julianday(created_at) as days_old "
        "FROM memories WHERE id = ?",
        (memory_id,)
    ).fetchone()
    
    if not row:
        return False, ""
    
    access_count = row["access_count"] or 0
    days_old = row["days_old"] or 0

    # Only heat SINCE the last promotion counts.  access_count is a lifetime
    # counter and must not be reset (the hot-topic ranking and decay score
    # read it), so the snapshot is what makes this step consuming rather than
    # an unbounded escalator.
    consumed = 0
    try:
        meta = row["metadata"]
        if isinstance(meta, str) and meta:
            meta = json.loads(meta)
        if isinstance(meta, dict):
            consumed = int(meta.get("promoted_at_access") or 0)
    except (json.JSONDecodeError, TypeError, ValueError):
        consumed = 0
    earned = max(0, access_count - consumed)

    # Check minimum age requirement
    min_age_days = get_config("promotion.min_age_days", 7)
    if days_old < min_age_days:
        return False, ""

    # Check access count thresholds against the UNCONSUMED heat
    level_thresholds = thresholds[current_level]
    for target_level, min_access in level_thresholds.items():
        if earned >= min_access:
            # If considering references, also check reference count
            if get_config("promotion.consider_references", True):
                ref_count = conn.execute(
                    "SELECT COUNT(*) FROM edges WHERE target_id = ?",
                    (memory_id,)
                ).fetchone()[0]
                
                ref_threshold = get_config("promotion.reference_threshold", 5)
                if ref_count >= ref_threshold:
                    return True, target_level
            else:
                return True, target_level
    
    return False, ""


def promote_step() -> dict:
    """
    Promote memories based on access patterns (heat-based promotion).
    
    Returns:
        dict with promotion statistics
    """
    thresholds = _get_promotion_thresholds()
    if not thresholds or not get_config("promotion.enabled", True):
        return {"promoted": 0, "scanned": 0, "eligible": 0}
    
    conn = get_conn()
    try:
        # Get all memories that could potentially be promoted
        # We scan memories that are not already at the highest level
        # and have some access count
        placeholders = ",".join(["?"] * len(thresholds))
        rows = conn.execute(
            f"""
            SELECT id, level, access_count
            FROM memories
            WHERE level IN ({placeholders})
              AND access_count > 0
              AND COALESCE(memory_status, '') != 'archived'
            ORDER BY access_count DESC
            """,
            list(thresholds.keys())
        ).fetchall()
        
        scanned = len(rows)
        promoted = 0
        eligible = 0
        
        for row in rows:
            memory_id = row["id"]
            current_level = row["level"]
            access_count = row["access_count"]
            
            should_promote, target_level = _should_promote_memory(conn, memory_id, current_level)
            
            if should_promote:
                eligible += 1
                # Perform the promotion AND consume the heat that earned it.
                # Recording the snapshot is what stops the same row from being
                # promoted again on the next run; the metadata is MERGED so
                # existing keys (pinned_fields, status, …) survive.
                mrow = conn.execute(
                    "SELECT metadata FROM memories WHERE id = ?", (memory_id,)
                ).fetchone()
                meta = {}
                try:
                    if mrow and mrow["metadata"]:
                        meta = json.loads(mrow["metadata"])
                        if not isinstance(meta, dict):
                            meta = {}
                except (json.JSONDecodeError, TypeError):
                    meta = {}
                meta["promoted_at_access"] = access_count
                meta.setdefault("promotion_history", [])
                conn.execute(
                    "UPDATE memories SET level = ?, metadata = ?, "
                    "updated_at = datetime('now') WHERE id = ?",
                    (target_level, json.dumps(meta, ensure_ascii=False), memory_id)
                )
                promoted += 1
                
        conn.commit()
        return {
            "promoted": promoted,
            "scanned": scanned,
            "eligible": eligible,
        }
        
    except sqlite3.Error:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_promotion_config() -> dict:
    """Get current promotion configuration for debugging/monitoring."""
    return {
        "enabled": get_config("promotion.enabled", True),
        "thresholds": get_config("promotion.access_thresholds", {}),
        "min_age_days": get_config("promotion.min_age_days", 7),
        "consider_references": get_config("promotion.consider_references", True),
        "reference_threshold": get_config("promotion.reference_threshold", 5),
    }