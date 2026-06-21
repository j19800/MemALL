import logging
"""Metadata cleanup step.
logger = logging.getLogger(__name__)


Migrates bare metadata values to versioned format, prunes stale entries,
and enforces expiry rules. Runs as part of the pipeline after enrich.
"""

import json
from datetime import datetime, timezone, timedelta
from memall.core.db import get_conn

# Key expiry: None = never expire
_KEY_RULES = {
    "quality": {"max_versions": 2, "expiry_days": None},
    "enrich": {"max_versions": 2, "expiry_days": 30},
    "procedure": {"max_versions": 2, "expiry_days": 30},
    "layer_source": {"max_versions": 2, "expiry_days": None},
}


def _migrate_value(value, key: str) -> dict:
    """Wrap bare value into versioned format if not already migrated."""
    if isinstance(value, dict) and "_meta" in value:
        return value  # already migrated
    return {
        "value": value,
        "_meta": {"version": 1, "written_at": datetime.now(timezone.utc).isoformat()},
    }


def _is_empty(value) -> bool:
    """Check if a value is semantically empty."""
    if value is None:
        return True
    if isinstance(value, str) and not value.strip():
        return True
    if isinstance(value, (dict, list)) and not value:
        return True
    return False


def cleanup_step() -> dict:
    """Scan all memories, migrate and prune metadata.

    Returns dict with counts of memories scanned and modified.
    """
    conn = get_conn()
    try:
        now = datetime.now(timezone.utc)
        rows = conn.execute(
            "SELECT id, metadata FROM memories WHERE metadata IS NOT NULL AND metadata != '{}'"
        ).fetchall()

        scanned = len(rows)
        migrated = 0
        pruned = 0
        rewritten = 0

        for row in rows:
            mid = row["id"]
            raw = row["metadata"]
            try:
                meta = json.loads(raw) if isinstance(raw, str) else raw
            except (json.JSONDecodeError, TypeError):
                continue

            if not isinstance(meta, dict):
                continue

            changed = False
            keys_to_delete = []

            for key, value in meta.items():
                rule = _KEY_RULES.get(key)

                # Migrate bare values to versioned format (only for known keys)
                if rule and not (isinstance(value, dict) and "_meta" in value):
                    meta[key] = _migrate_value(value, key)
                    migrated += 1
                    changed = True
                    # Re-read the migrated value
                    value = meta[key]

                # Check expiry (only for keys we know about)
                if rule and rule.get("expiry_days") is not None:
                    meta_ts = value.get("_meta", {}).get("written_at")
                    if meta_ts:
                        try:
                            written = datetime.fromisoformat(meta_ts)
                            if (now - written).days > rule["expiry_days"]:
                                keys_to_delete.append(key)
                                continue
                        except (ValueError, TypeError):
                            logger.warning("cleanup.py: silent error", exc_info=True)

                # Check empty (only for keys we know about - skip bare discussion metadata)
                if not rule:
                    continue
                val = value.get("value")
                if _is_empty(val):
                    keys_to_delete.append(key)
                    continue

            # Delete expired/empty keys
            for k in keys_to_delete:
                del meta[k]
                pruned += 1
                changed = True

            if changed:
                rewritten += 1
                conn.execute(
                    "UPDATE memories SET metadata = ? WHERE id = ?",
                    (json.dumps(meta, ensure_ascii=False), mid),
                )

        conn.commit()
        return {
            "scanned": scanned,
            "migrated": migrated,
            "pruned": pruned,
            "rewritten": rewritten,
        }
    finally:
        conn.close()
