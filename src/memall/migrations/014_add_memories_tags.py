"""
Migration 014: Add tags column to memories table.

Previously added inline in ops.py. Formal migration for tracking.
Idempotent — safe to run multiple times.
"""

MIGRATION_ID = "014_add_memories_tags"
DESCRIPTION = "Add tags column to memories table"


def apply(conn):
    cur = conn.execute("PRAGMA table_info(memories)")
    cols = [r["name"] for r in cur.fetchall()]

    if "tags" not in cols:
        conn.execute(
            "ALTER TABLE memories ADD COLUMN tags TEXT NOT NULL DEFAULT '[]'"
        )