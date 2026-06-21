"""
Migration 002: Add owner_type column to identities table.

Idempotent — safe to run multiple times.
"""

MIGRATION_ID = "002_add_owner_type"
DESCRIPTION = "Add owner_type column to identities table"


def apply(conn):
    cur = conn.execute("PRAGMA table_info(identities)")
    cols = [r["name"] for r in cur.fetchall()]

    if "owner_type" not in cols:
        conn.execute(
            "ALTER TABLE identities ADD COLUMN owner_type TEXT NOT NULL DEFAULT 'human'"
        )
