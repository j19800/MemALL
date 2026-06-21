"""
Migration 006: Add visibility column to memories table.

Created from inline _run_migrations() operation. Idempotent.
"""

MIGRATION_ID = "006_add_memories_visibility"
DESCRIPTION = "Add visibility column to memories table"


def apply(conn):
    cur = conn.execute("PRAGMA table_info(memories)")
    cols = [r["name"] for r in cur.fetchall()]

    if "visibility" not in cols:
        conn.execute(
            "ALTER TABLE memories ADD COLUMN visibility TEXT NOT NULL DEFAULT 'private'"
        )