"""
Migration 005: Add confidence column to memories table.

Created from inline _run_migrations() operation. Idempotent.
"""

MIGRATION_ID = "005_add_memories_confidence"
DESCRIPTION = "Add confidence column to memories table with trust_level backfill"


def apply(conn):
    cur = conn.execute("PRAGMA table_info(memories)")
    cols = [r["name"] for r in cur.fetchall()]

    if "confidence" not in cols:
        conn.execute(
            "ALTER TABLE memories ADD COLUMN confidence REAL NOT NULL DEFAULT 0.5"
        )
        conn.execute(
            "UPDATE memories SET confidence = trust_level WHERE trust_level IS NOT NULL AND trust_level != ''"
        )