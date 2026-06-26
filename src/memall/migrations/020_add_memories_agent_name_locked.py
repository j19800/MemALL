"""
Migration 020: Add agent_name_locked column to memories table.

The column was added to CREATE TABLE IF NOT EXISTS in db.py but has no
ALTER TABLE migration for existing databases. Without this migration,
every capture() INSERT will crash with:
  sqlite3.OperationalError: table memories has no column named agent_name_locked

Safe to re-run (ALTER TABLE ... ADD COLUMN is ignored if column exists
in SQLite, though the error is caught and suppressed).
"""

MIGRATION_ID = "020_add_memories_agent_name_locked"
DESCRIPTION = "Add agent_name_locked BOOLEAN column to memories table"


def apply(conn):
    try:
        conn.execute("ALTER TABLE memories ADD COLUMN agent_name_locked BOOLEAN NOT NULL DEFAULT 0")
    except Exception:
        # Column may already exist if the CREATE TABLE IF NOT EXISTS was
        # already updated at schema init — safe to ignore.
        pass
