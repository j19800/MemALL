import logging

logger = logging.getLogger(__name__)

"""
Migration 017: Add arc_status column to memories table.

Tracks decision arc lifecycle for L4 memories:
  NULL       = non-L4 or not yet scanned
  'open'     = decision made, no execution or reflection
  'in_progress' = has L5 task reference via edge
  'closed'   = has L6 reflection reference via edge (terminal)

Safe to re-run (checks for the column first — truly idempotent, no
traceback spam on already-migrated databases).
"""

MIGRATION_ID = "017_add_arc_status"
DESCRIPTION = "Add arc_status column for decision arc tracking"


def _column_exists(conn, table: str, column: str) -> bool:
    """True if ``column`` is present in ``table`` (case-insensitive)."""
    cur = conn.execute(f"PRAGMA table_info({table})")
    return any(row[1].lower() == column.lower() for row in cur.fetchall())


def apply(conn):
    # "duplicate column" here is the expected idempotent path (SCHEMA_SQL may
    # already create arc_status, or the migration ran before) — skip quietly.
    if _column_exists(conn, "memories", "arc_status"):
        logger.debug("017: memories.arc_status already exists — nothing to add")
        return
    conn.execute("ALTER TABLE memories ADD COLUMN arc_status TEXT")