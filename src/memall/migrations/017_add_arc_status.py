import logging

logger = logging.getLogger(__name__)

"""
Migration 017: Add arc_status column to memories table.

Tracks decision arc lifecycle for L4 memories:
  NULL       = non-L4 or not yet scanned
  'open'     = decision made, no execution or reflection
  'in_progress' = has L5 task reference via edge
  'closed'   = has L6 reflection reference via edge (terminal)

Safe to re-run (ALTER TABLE ADD COLUMN is idempotent with try/except).
"""

MIGRATION_ID = "017_add_arc_status"
DESCRIPTION = "Add arc_status column for decision arc tracking"


def apply(conn):
    try:
        conn.execute("ALTER TABLE memories ADD COLUMN arc_status TEXT")
    except Exception:
        logger.warning("017_add_arc_status.py: silent error", exc_info=True)