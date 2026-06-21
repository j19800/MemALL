"""
Migration 013: Add permission_level column to identities table.

Previously added inline in security.py. Formal migration for tracking.
Idempotent — safe to run multiple times.
"""

MIGRATION_ID = "013_add_identities_permission_level"
DESCRIPTION = "Add permission_level column to identities table"


def apply(conn):
    cur = conn.execute("PRAGMA table_info(identities)")
    cols = [r["name"] for r in cur.fetchall()]

    if "permission_level" not in cols:
        conn.execute(
            "ALTER TABLE identities ADD COLUMN permission_level TEXT NOT NULL DEFAULT 'private'"
        )