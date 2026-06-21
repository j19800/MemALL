"""
Migration 001: Add trusted_by column to identities table.

Ensures the `trusted_by` column exists on the `identities` table.
Idempotent — safe to run multiple times.
"""

MIGRATION_ID = "001_add_identity_trusted_by"
DESCRIPTION = "Add trusted_by column to identities table"


def apply(conn):
    cur = conn.execute("PRAGMA table_info(identities)")
    cols = [r["name"] for r in cur.fetchall()]

    if "trusted_by" not in cols:
        conn.execute(
            "ALTER TABLE identities ADD COLUMN trusted_by TEXT NOT NULL DEFAULT '[]'"
        )
