"""
Migration 032: add ``memories.dedup_hits``.

Audit P1-4: a hash hit (re-writing the same content) incremented
``access_count``.  ``access_count`` is the signal behind the hot-topic
ranking, the decay score and the "zero_access" health statistic — counting
duplicate WRITES as READS inflated exactly those three.  The two concepts
now have their own columns: ``access_count`` = times retrieved,
``dedup_hits`` = times re-written with identical content.

``ALTER TABLE ADD COLUMN`` is idempotent-safe here because the column
check runs first.

Also backfills: rows whose access_count is 0 stay 0; existing duplicate
writes are not reconstructible, so dedup_hits starts at 0 for all rows.
"""

MIGRATION_ID = "032_add_dedup_hits_column"
DESCRIPTION = "Add memories.dedup_hits (duplicate writes, separate from access_count)"


def apply(conn):
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(memories)").fetchall()}
    if "dedup_hits" in cols:
        return  # already migrated
    conn.execute(
        "ALTER TABLE memories ADD COLUMN dedup_hits INTEGER NOT NULL DEFAULT 0"
    )
    conn.commit()
