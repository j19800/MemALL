"""
Migration 030 — tag L10 integration rows with a structured ``kind`` marker.

``integrate_step`` and ``dedupe_l10`` used to identify the single cross-domain
L10 integration row per agent by matching the *content prefix*
``'[L10 整合]%'``.  That couples business semantics to a display string: any
format change, manual edit, or historical migration can mis-match and corrupt
the upsert (review F-07).

The pipeline now uses ``metadata.kind = 'l10_integration'`` instead.  This
migration backfills that marker onto every legacy row whose content still
carries the prefix, so the structured lookup finds existing rows.

Idempotent and safe to re-run.
"""

import json
import logging

logger = logging.getLogger(__name__)

MIGRATION_ID = "030_backfill_l10_kind"
DESCRIPTION = "Backfill metadata.kind='l10_integration' for legacy L10 integration rows"

_LEGACY_PREFIX = "[L10 整合]"
_KIND = "l10_integration"


def apply(conn) -> None:
    conn.execute("BEGIN")
    try:
        rows = conn.execute(
            "SELECT id, metadata FROM memories WHERE content LIKE ?",
            (f"{_LEGACY_PREFIX}%",),
        ).fetchall()

        tagged = 0
        for r in rows:
            raw = r["metadata"] or ""
            try:
                meta = json.loads(raw) if raw.strip() else {}
            except (json.JSONDecodeError, TypeError):
                meta = {}
            if not isinstance(meta, dict):
                meta = {}
            if meta.get("kind") == _KIND:
                continue
            meta["kind"] = _KIND
            conn.execute(
                "UPDATE memories SET metadata = ? WHERE id = ?",
                (json.dumps(meta, ensure_ascii=False), r["id"]),
            )
            tagged += 1

        logger.info("030: tagged %d legacy L10 integration rows with kind=%s", tagged, _KIND)
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise


def rollback(conn) -> None:
    conn.execute("BEGIN")
    try:
        rows = conn.execute(
            "SELECT id, metadata FROM memories "
            "WHERE json_valid(metadata) "
            "AND json_extract(metadata, '$.kind') = ?",
            (_KIND,),
        ).fetchall()
        for r in rows:
            try:
                meta = json.loads(r["metadata"] or "{}")
            except (json.JSONDecodeError, TypeError):
                continue
            if isinstance(meta, dict) and meta.get("kind") == _KIND:
                meta.pop("kind", None)
                conn.execute(
                    "UPDATE memories SET metadata = ? WHERE id = ?",
                    (json.dumps(meta, ensure_ascii=False), r["id"]),
                )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise