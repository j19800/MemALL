import logging

logger = logging.getLogger(__name__)

"""
Migration 018: Add echo_score column to memories table.

Echo score measures a memory's "staying power" based on how frequently
it is cited (incoming edges) and accessed. Higher echo_score = more
influential/durable memory.

Safe to re-run (checks for the column first — truly idempotent, no
traceback spam on already-migrated databases).
"""

MIGRATION_ID = "018_add_echo_score"
DESCRIPTION = "Add echo_score column for echo memory tracking"


def _column_exists(conn, table: str, column: str) -> bool:
    """True if ``column`` is present in ``table`` (case-insensitive)."""
    cur = conn.execute(f"PRAGMA table_info({table})")
    return any(row[1].lower() == column.lower() for row in cur.fetchall())


def apply(conn):
    # "duplicate column" here is the expected idempotent path (SCHEMA_SQL may
    # already create echo_score, or the migration ran before) — skip quietly.
    if _column_exists(conn, "memories", "echo_score"):
        logger.debug("018: memories.echo_score already exists — nothing to add")
        return
    conn.execute("ALTER TABLE memories ADD COLUMN echo_score REAL NOT NULL DEFAULT 0.0")
