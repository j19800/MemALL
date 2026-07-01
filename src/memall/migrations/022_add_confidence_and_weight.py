"""
Migration 022: Add missing visibility + confidence + weight columns.

Several ALTER TABLE migrations (005, 006) were registered as applied
but the columns never got created (DB was recreated at some point).
This migration fixes the gap for all three missing columns.

Covers:
- visibility (migration 006 was a no-op)
- confidence (migration 005 was a no-op)
- weight (new L7 accumulation column)
"""

MIGRATION_ID = "022_add_confidence_and_weight"
DESCRIPTION = "Add missing visibility + confidence + weight columns to memories"


def apply(conn):
    cur = conn.execute("PRAGMA table_info(memories)")
    cols = {r["name"] for r in cur.fetchall()}

    if "visibility" not in cols:
        conn.execute(
            "ALTER TABLE memories ADD COLUMN visibility TEXT NOT NULL DEFAULT 'private'"
        )

    if "confidence" not in cols:
        conn.execute(
            "ALTER TABLE memories ADD COLUMN confidence REAL NOT NULL DEFAULT 0.5"
        )
        conn.execute(
            "UPDATE memories SET confidence = trust_level WHERE trust_level IS NOT NULL AND trust_level != ''"
        )

    if "weight" not in cols:
        conn.execute(
            "ALTER TABLE memories ADD COLUMN weight INTEGER NOT NULL DEFAULT 1"
        )
