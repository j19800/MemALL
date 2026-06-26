"""
Phase 11: Automatic Forgetting Mechanism

TTL expiration cleanup, low-value memory decay, preview/review,
database statistics, and combined forget step.

All destructive operations are preceded by an automatic backup
(via the existing backup_restore module). Deletions use explicit
transactions (BEGIN/COMMIT/ROLLBACK) for consistency.

Level-based TTL (forget_expired):

  Level | TTL (days)
  ------|-----------
  P0    | excluded (never auto-expired)
  P1    | 180
  P2    | 90  (default)
  P3    | 45
  P4    | 30
"""

import logging

logger = logging.getLogger(__name__)

import json
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any, List

from memall.core.db import get_conn


def _ttl_cutoff(days: int) -> str:
    """Return SQLite-compatible datetime string for `days` ago (UTC).

    Uses the same format as SQLite datetime('now') — no T separator,
    no timezone suffix — so lexicographic comparison with stored
    created_at values produces correct results.
    """
    return (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")


def _backup_before_delete() -> Dict[str, Any]:
    """Ensure a backup exists before any destructive forget operation."""
    from memall.cli.backup_restore import backup_db

    return backup_db()


# Per-level TTL (days). P0/L1/L7 are excluded (permanent).
_LEVEL_TTL = {
    "P1": 180,
    "P2": 90,
    "P3": 45,
    "P4": 30,
    "L1": -1,   # permanent (identity)
    "L2": 365,
    "L3": 365,
    "L4": 180,
    "L5": 365,
    "L6": 730,
    "L7": -1,   # permanent (preference)
    "L8": 365,
    "L9": 730,
    "L10": 1095,
    "L11": 730,
}


# ══════════════════════════════════════════════════════════════════
# 1. TTL 过期清理  (level-aware)
# ══════════════════════════════════════════════════════════════════

def forget_expired(days: int = 90, agent_name: Optional[str] = None) -> Dict[str, Any]:
    """Delete memories older than their level-specific TTL.

    Per-level TTLs are defined in ``_LEVEL_TTL``.  The ``days`` parameter
    acts as a global scaling factor: when ``days != 90``, all TTLs are
    proportionally adjusted by ``days / 90`` (so ``--days 45`` halves
    every level's TTL).

    P0 memories are **excluded** — they are handled by the decay pipeline.

    Performs an automatic backup before deletion.  Edges referencing
    deleted memories are removed explicitly (no ON DELETE CASCADE).

    Args:
        days: Base TTL scaling in days (default 90, maps to P2).
        agent_name: Optional agent filter (case-insensitive).

    Returns:
        ``{"deleted_memories": N, "deleted_edges": N}``
    """
    _backup_before_delete()

    scale = days / 90.0
    conn = get_conn()
    try:
        conn.execute("BEGIN")

        expired_ids: List[int] = []
        for level, ttl in _LEVEL_TTL.items():
            if isinstance(ttl, int) and ttl <= 0:
                continue  # permanent level, never expires
            scaled_ttl = max(1, int(ttl * scale))
            cutoff = _ttl_cutoff(scaled_ttl)
            if agent_name:
                rows = conn.execute(
                    "SELECT id FROM memories WHERE level = ? AND created_at < ? AND LOWER(agent_name) = LOWER(?)",
                    (level, cutoff, agent_name),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT id FROM memories WHERE level = ? AND created_at < ?",
                    (level, cutoff),
                ).fetchall()
            expired_ids.extend(r["id"] for r in rows)

        if not expired_ids:
            conn.execute("COMMIT")
            return {"deleted_memories": 0, "deleted_edges": 0}

        placeholders = ",".join("?" * len(expired_ids))

        # Delete edges first (both directions)
        cur = conn.execute(
            f"DELETE FROM edges WHERE source_id IN ({placeholders}) OR target_id IN ({placeholders})",
            expired_ids + expired_ids,
        )
        deleted_edges = cur.rowcount

        # Delete memories
        cur = conn.execute(
            f"DELETE FROM memories WHERE id IN ({placeholders})",
            expired_ids,
        )
        deleted_memories = cur.rowcount

        conn.execute("COMMIT")
        return {"deleted_memories": deleted_memories, "deleted_edges": deleted_edges}
    except Exception:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()


# ══════════════════════════════════════════════════════════════════
# 2. 低价值衰减
# ══════════════════════════════════════════════════════════════════

def forget_low_value(agent_name: Optional[str] = None) -> Dict[str, Any]:
    """Delete low-value memories that satisfy ALL of the following:

    a. ``LENGTH(content) < 30``  —  overly short
    b. No incoming edges  —  not referenced by other memories
    c. No outgoing edges  —  does not reference other memories
    d. ``created_at`` older than 7 days

    Args:
        agent_name: Optional agent filter (case-insensitive).

    Returns:
        ``{"deleted_memories": N, "candidate_count": N}``
    """
    _backup_before_delete()

    cutoff = _ttl_cutoff(7)
    conn = get_conn()
    try:
        conn.execute("BEGIN")

        base_sql = (
            "SELECT id FROM memories "
            "WHERE LENGTH(content) < 30 "
            "AND created_at < ? "
            "AND id NOT IN (SELECT DISTINCT target_id FROM edges WHERE target_id IS NOT NULL) "
            "AND id NOT IN (SELECT DISTINCT source_id FROM edges WHERE source_id IS NOT NULL)"
        )
        if agent_name:
            rows = conn.execute(
                base_sql + " AND LOWER(agent_name) = LOWER(?)",
                (cutoff, agent_name),
            ).fetchall()
        else:
            rows = conn.execute(base_sql, (cutoff,)).fetchall()

        candidate_ids: List[int] = [r["id"] for r in rows]
        candidate_count = len(candidate_ids)

        if not candidate_ids:
            conn.execute("COMMIT")
            return {"deleted_memories": 0, "candidate_count": 0}

        placeholders = ",".join("?" * candidate_count)

        # Safety: delete any stray edges (should be none by definition)
        conn.execute(
            f"DELETE FROM edges WHERE source_id IN ({placeholders}) OR target_id IN ({placeholders})",
            candidate_ids + candidate_ids,
        )

        cur = conn.execute(
            f"DELETE FROM memories WHERE id IN ({placeholders})",
            candidate_ids,
        )
        deleted_memories = cur.rowcount

        conn.execute("COMMIT")
        return {"deleted_memories": deleted_memories, "candidate_count": candidate_count}
    except Exception:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()


# ══════════════════════════════════════════════════════════════════
# 3. 遗忘审查（预览，不执行）
# ══════════════════════════════════════════════════════════════════

def forget_review(days: int = 90, agent_name: Optional[str] = None) -> Dict[str, Any]:
    """Preview which memories *would* be deleted — does **not** execute.

    Uses the same level-aware TTL logic as ``forget_expired``.

    Args:
        days: Base TTL scaling in days (default 90).
        agent_name: Optional agent filter.

    Returns:
        ``{"expired_candidates": N, "low_value_candidates": N, "details": [...]}``
        where ``details`` contains up to 10 preview entries from each category.
    """
    scale = days / 90.0
    cutoff_low = _ttl_cutoff(7)
    conn = get_conn()
    try:
        # ── Expired candidates (level-aware) ──
        expired: List[Any] = []
        for level, ttl in _LEVEL_TTL.items():
            if isinstance(ttl, int) and ttl <= 0:
                continue
            scaled_ttl = max(1, int(ttl * scale))
            cutoff = _ttl_cutoff(scaled_ttl)
            if agent_name:
                rows = conn.execute(
                    "SELECT id, content, created_at, agent_name, level FROM memories "
                    "WHERE level = ? AND created_at < ? AND LOWER(agent_name) = LOWER(?) ORDER BY created_at",
                    (level, cutoff, agent_name),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT id, content, created_at, agent_name, level FROM memories "
                    "WHERE level = ? AND created_at < ? ORDER BY created_at",
                    (level, cutoff),
                ).fetchall()
            expired.extend(rows)

        # ── Low-value candidates ──
        low_sql = (
            "SELECT id, content, created_at, agent_name FROM memories "
            "WHERE LENGTH(content) < 30 AND created_at < ? "
            "AND id NOT IN (SELECT DISTINCT target_id FROM edges WHERE target_id IS NOT NULL) "
            "AND id NOT IN (SELECT DISTINCT source_id FROM edges WHERE source_id IS NOT NULL)"
        )
        if agent_name:
            low_val = conn.execute(
                low_sql + " AND LOWER(agent_name) = LOWER(?) ORDER BY created_at",
                (cutoff_low, agent_name),
            ).fetchall()
        else:
            low_val = conn.execute(
                low_sql + " ORDER BY created_at",
                (cutoff_low,),
            ).fetchall()

        # ── Build detail previews (top 10 each) ──
        details: List[Dict[str, Any]] = []
        for r in expired[:10]:
            details.append({
                "type": "expired",
                "id": r["id"],
                "content_preview": r["content"][:60] if r["content"] else "",
                "created_at": r["created_at"][:19] if r["created_at"] else "",
                "agent_name": r["agent_name"],
            })
        for r in low_val[:10]:
            details.append({
                "type": "low_value",
                "id": r["id"],
                "content_preview": r["content"][:60] if r["content"] else "",
                "created_at": r["created_at"][:19] if r["created_at"] else "",
                "agent_name": r["agent_name"],
            })

        return {
            "expired_candidates": len(expired),
            "low_value_candidates": len(low_val),
            "details": details,
        }
    finally:
        conn.close()


# ══════════════════════════════════════════════════════════════════
# L5 自动归档：status=done 超过 30 天 → archived
# ══════════════════════════════════════════════════════════════════

def forget_l5_archive(days: int = 30) -> Dict[str, Any]:
    """Archive L5 todos that have been ``status=done`` for longer than ``days``.

    These are not deleted — status moves from ``done`` to ``archived`` so
    they no longer appear in session_start auto_inject but remain retrievable.

    Returns:
        ``{"archived": int, "kept_active": int}``
    """
    conn = get_conn()
    try:
        cutoff = _ttl_cutoff(days)
        rows = conn.execute(
            "SELECT id, metadata FROM memories WHERE level = 'L5' ORDER BY id DESC LIMIT 2000"
        ).fetchall()
        archived = 0
        for r in rows:
            meta = {}
            try:
                meta = json.loads(r["metadata"]) if isinstance(r["metadata"], str) else r["metadata"]
            except Exception:
                logger.warning("forget.py: silent error", exc_info=True)
            if not isinstance(meta, dict):
                continue
            status = meta.get("status", "active")
            if status != "done":
                continue
            updated_at = None
            for src in [meta.get("completed_at"), meta.get("_completed_at")]:
                if src:
                    updated_at = src
                    break
            if not updated_at:
                continue
            if updated_at < cutoff:
                meta["status"] = "archived"
                conn.execute(
                    "UPDATE memories SET metadata = ?, updated_at = ? WHERE id = ?",
                    (json.dumps(meta, ensure_ascii=False), datetime.now(timezone.utc).isoformat(), r["id"]),
                )
                archived += 1
        conn.commit()
        return {"archived": archived, "kept_active": len(rows) - archived}
    finally:
        conn.close()


# ══════════════════════════════════════════════════════════════════
# 4. 遗忘统计
# ══════════════════════════════════════════════════════════════════

def forget_stats() -> Dict[str, Any]:
    """Return a snapshot of database state relevant to forgetting.

    Returns:
        ``{
            "total_memories": int,
            "total_edges": int,
            "expired_count": int,              # > 90 days
            "low_value_count": int,            # short + isolated + > 7 days
            "orphaned_edge_count": int,        # edges pointing to missing memories
            "oldest_memory_date": str | None,
            "newest_memory_date": str | None,
            "avg_content_length": float,
            "size_estimate_mb": float,
        }``
    """
    cutoff_90 = _ttl_cutoff(90)
    cutoff_7 = _ttl_cutoff(7)
    conn = get_conn()
    try:
        total_memories = conn.execute("SELECT COUNT(*) AS c FROM memories").fetchone()["c"]
        total_edges = conn.execute("SELECT COUNT(*) AS c FROM edges").fetchone()["c"]

        expired_count = conn.execute(
            "SELECT COUNT(*) AS c FROM memories WHERE created_at < ?", (cutoff_90,)
        ).fetchone()["c"]

        low_value_count = conn.execute(
            "SELECT COUNT(*) AS c FROM memories "
            "WHERE LENGTH(content) < 30 AND created_at < ? "
            "AND id NOT IN (SELECT DISTINCT target_id FROM edges WHERE target_id IS NOT NULL) "
            "AND id NOT IN (SELECT DISTINCT source_id FROM edges WHERE source_id IS NOT NULL)",
            (cutoff_7,),
        ).fetchone()["c"]

        orphaned_edge_count = conn.execute(
            "SELECT COUNT(*) AS c FROM edges "
            "WHERE source_id NOT IN (SELECT id FROM memories) "
            "OR target_id NOT IN (SELECT id FROM memories)",
        ).fetchone()["c"]

        oldest = conn.execute(
            "SELECT created_at FROM memories ORDER BY created_at ASC LIMIT 1"
        ).fetchone()
        newest = conn.execute(
            "SELECT created_at FROM memories ORDER BY created_at DESC LIMIT 1"
        ).fetchone()

        avg_len = conn.execute("SELECT AVG(LENGTH(content)) FROM memories").fetchone()[0]

        total_bytes = conn.execute(
            "SELECT COALESCE(SUM(LENGTH(content)), 0) FROM memories"
        ).fetchone()[0]

        return {
            "total_memories": total_memories,
            "total_edges": total_edges,
            "expired_count": expired_count,
            "low_value_count": low_value_count,
            "orphaned_edge_count": orphaned_edge_count,
            "oldest_memory_date": oldest["created_at"][:19] if (oldest and oldest["created_at"]) else None,
            "newest_memory_date": newest["created_at"][:19] if (newest and newest["created_at"]) else None,
            "avg_content_length": round(avg_len, 1) if avg_len else 0,
            "size_estimate_mb": round(total_bytes / 1024 / 1024, 3),
        }
    finally:
        conn.close()


# ══════════════════════════════════════════════════════════════════
# 5. 自动遗忘 step
# ══════════════════════════════════════════════════════════════════

def forget_step(days: int = 90, agent_name: Optional[str] = None) -> Dict[str, Any]:
    """Run ``forget_expired``, ``forget_low_value``, and ``forget_l5_archive`` consecutively.

    A single backup is taken before any deletions (both sub-operations
    also perform their own backup guard, so the DB is safely snapshotted
    before each destructive step).

    Args:
        days: TTL threshold for expired check.
        agent_name: Optional agent filter.

    Returns:
        ``{
            "expired": {...},
            "low_value": {...},
            "l5_archive": {...},
            "total_deleted_memories": int,
            "total_deleted_edges": int,
        }``
    """
    result_expired = forget_expired(days=days, agent_name=agent_name)
    result_low = forget_low_value(agent_name=agent_name)
    result_l5 = forget_l5_archive(days=30)

    return {
        "expired": result_expired,
        "low_value": result_low,
        "l5_archive": result_l5,
        "total_deleted_memories": result_expired["deleted_memories"] + result_low["deleted_memories"],
        "total_deleted_edges": result_expired["deleted_edges"] + result_low.get("deleted_edges", 0),
    }
