import logging

logger = logging.getLogger(__name__)

"""
Migration 018: Add echo_score column to memories table.

Echo score measures a memory's "staying power" based on how frequently
it is cited (incoming edges) and accessed. Higher echo_score = more
influential/durable memory.

Safe to re-run (ALTER TABLE ADD COLUMN is idempotent with try/except).
"""

MIGRATION_ID = "018_add_echo_score"
DESCRIPTION = "Add echo_score column for echo memory tracking"


def apply(conn):
    try:
        conn.execute("ALTER TABLE memories ADD COLUMN echo_score REAL NOT NULL DEFAULT 0.0")
    except Exception:
        logger.warning("018_add_echo_score.py: silent error", exc_info=True)
