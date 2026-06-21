"""
Pipeline step: echo_step — Echo memory score calculation.

Computes echo_score for each memory as a composite "asset value" metric.
The score reflects how valuable this memory is as a long-term asset,
considering:
  - Citation value  (25%) — how often other memories reference it
  - Access value    (20%) — how often it's been retrieved
  - Recency value   (20%) — how recently it was active (↘ decay over time)
  - Level value     (20%) — higher cognitive layers are more valuable
  - Quality value   (15%) — intrinsic content quality (completeness, clarity, etc.)

Score range: 0 (lowest value) to ~100 (highest value)
"""

import json
import logging
from datetime import datetime, timezone
from memall.core.db import get_conn

logger = logging.getLogger(__name__)

_BATCH_SIZE = 1000

# Cognitive level → asset weight
# Higher layers (L9/L10) are synthesised knowledge → more valuable
_LEVEL_WEIGHTS = {
    "L10": 1.0,   # Integrated knowledge — highest value
    "L9":  0.9,   # Distilled summaries
    "L6":  0.8,   # Reflection / self-improvement
    "L7":  0.7,   # Preference (permanent)
    "L1":  0.7,   # Identity (permanent)
    "L4":  0.6,   # Decision arc
    "L5":  0.5,   # Task / plan
    "L3":  0.4,   # Low priority
    "L2":  0.4,   # Derived identity
    "L8":  0.4,   # Agent-specific
    "P1":  0.3,   # Important raw event
    "P2":  0.2,   # Default raw event
    "P0":  0.1,   # Temporary
}
_DEFAULT_LEVEL_WEIGHT = 0.3


def _level_weight(level: str) -> float:
    return _LEVEL_WEIGHTS.get(level, _DEFAULT_LEVEL_WEIGHT)


def _recency_value(updated_at: str | None) -> float:
    """Score how recent this memory is (0..1)."""
    if not updated_at:
        return 0.3
    try:
        ts = updated_at.replace("Z", "+00:00")
        dt = datetime.fromisoformat(ts)
        now = datetime.now(timezone.utc)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        days = (now - dt).total_seconds() / 86400.0
    except (ValueError, TypeError):
        return 0.3

    if days < 0:
        return 1.0          # future timestamp? treat as brand new
    if days <= 7:
        return 1.0          # last week → full value
    if days <= 30:
        return 0.9          # last month
    if days <= 90:
        return 0.7          # last quarter
    if days <= 180:
        return 0.5          # half year
    if days <= 365:
        return 0.3          # one year
    return 0.1              # older → minimal


def _quality_value(metadata_raw: str | None) -> float:
    """Extract quality score from stored metadata.quality.avg (0..10 → 0..1).

    Also handles string quality values used by L6 reflections:
      "high" → 0.9, "medium" → 0.6, "low" → 0.3, "aggregated" → 0.5
    Also handles cleanup.py's wrapped format: {"value": "high", "_meta": {...}}
    """
    if not metadata_raw:
        return 0.5
    try:
        meta = json.loads(metadata_raw) if isinstance(metadata_raw, str) else {}
        quality = meta.get("quality", {})
        # Unwrap cleanup.py's {value: X, _meta: {...}} envelope
        if isinstance(quality, dict) and "_meta" in quality and "value" in quality:
            quality = quality["value"]
        # Handle string quality values (used by L6 reflect_step)
        if isinstance(quality, str):
            return {"high": 0.9, "medium": 0.6, "low": 0.3, "aggregated": 0.5}.get(quality, 0.5)
        if isinstance(quality, dict):
            avg = quality.get("avg") or (quality.get("value") if isinstance(quality.get("value"), (int, float)) else None)
            if avg is not None:
                return min(1.0, max(0.0, avg / 10.0))
            # fallback: value might be a dict with avg inside
            inner = quality.get("value", {})
            if isinstance(inner, dict):
                avg = inner.get("avg", None)
                if avg is not None:
                    return min(1.0, max(0.0, avg / 10.0))
    except (json.JSONDecodeError, AttributeError, TypeError):
        logger.warning("echo.py: silent error", exc_info=True)
    return 0.5


def echo_step() -> dict:
    """Recalculate echo_score for all memories using the asset value formula.

    Returns dict with count of updated memories.
    """
    conn = get_conn()
    try:
        total = conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
        updated = 0
        offset = 0

        while offset < total:
            rows = conn.execute(
                "SELECT id, level, updated_at, metadata FROM memories ORDER BY id LIMIT ? OFFSET ?",
                (_BATCH_SIZE, offset),
            ).fetchall()

            for row in rows:
                mid = row["id"]
                level = row["level"] or "P2"
                updated_at = row["updated_at"]
                metadata = row["metadata"]

                # 1. Citation value (25%) — incoming edges
                edge_count = conn.execute(
                    "SELECT COUNT(*) as c FROM edges WHERE target_id = ? AND relation_type != 'deleted'",
                    (mid,),
                ).fetchone()["c"]
                # Normalize: cap at 50 edges, 50+ → 1.0
                edge_val = min(1.0, edge_count / 50.0)

                # 2. Access value (20%) — retrieve/access frequency
                acc = conn.execute(
                    "SELECT access_count FROM memories WHERE id = ?", (mid,)
                ).fetchone()
                access_count = acc["access_count"] if acc else 0
                # Normalize: cap at 100 accesses
                access_val = min(1.0, access_count / 100.0)

                # 3. Recency value (20%)
                recency_val = _recency_value(updated_at)

                # 4. Level value (20%)
                level_val = _level_weight(level)

                # 5. Quality value (15%)
                quality_val = _quality_value(metadata)

                # Composite score (0..1 range, then scale to 0..100 for display)
                raw = (
                    edge_val * 0.25 +
                    access_val * 0.20 +
                    recency_val * 0.20 +
                    level_val * 0.20 +
                    quality_val * 0.15
                )
                score = round(raw * 100, 1)

                conn.execute(
                    "UPDATE memories SET echo_score = ? WHERE id = ?",
                    (score, mid),
                )
                updated += 1

            conn.commit()
            offset += _BATCH_SIZE

        return {"updated": updated, "total": total}
    finally:
        conn.close()
