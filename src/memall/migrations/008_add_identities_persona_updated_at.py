"""
Migration 008: Add persona_updated_at column to identities table.

Created from inline _run_migrations() operation. Idempotent.
"""

MIGRATION_ID = "008_add_identities_persona_updated_at"
DESCRIPTION = "Add persona_updated_at column to identities table"


def apply(conn):
    cur = conn.execute("PRAGMA table_info(identities)")
    cols = [r["name"] for r in cur.fetchall()]

    if "persona_updated_at" not in cols:
        conn.execute(
            "ALTER TABLE identities ADD COLUMN persona_updated_at TEXT NOT NULL DEFAULT ''"
        )