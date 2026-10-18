"""
Heat-based level promotion — automatically promote frequently accessed memories
to higher knowledge levels based on access patterns and references.
"""

from memall.config import get_config
from memall.core.db import get_conn
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
        "SELECT access_count, created_at, julianday('now') - julianday(created_at) as days_old "
        "FROM memories WHERE id = ?",
        (memory_id,)
    ).fetchone()
    
    if not row:
        return False, ""
    
    access_count = row["access_count"] or 0
    days_old = row["days_old"] or 0
    
    # Check minimum age requirement
    min_age_days = get_config("promotion.min_age_days", 7)
    if days_old < min_age_days:
        return False, ""
    
    # Check access count thresholds
    level_thresholds = thresholds[current_level]
    for target_level, min_access in level_thresholds.items():
        if access_count >= min_access:
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
                # Perform the promotion
                conn.execute(
                    "UPDATE memories SET level = ?, updated_at = datetime('now') WHERE id = ?",
                    (target_level, memory_id)
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