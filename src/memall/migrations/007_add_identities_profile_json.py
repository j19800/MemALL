"""
Migration 007: Add profile_json column to identities table.

Created from inline _run_migrations() operation. Idempotent.
"""

MIGRATION_ID = "007_add_identities_profile_json"
DESCRIPTION = "Add profile_json column to identities table"


def apply(conn):
    cur = conn.execute("PRAGMA table_info(identities)")
    cols = [r["name"] for r in cur.fetchall()]

    if "profile_json" not in cols:
        conn.execute(
            "ALTER TABLE identities ADD COLUMN profile_json TEXT NOT NULL DEFAULT '{}'"
        )