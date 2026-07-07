"""
Migration 023: Add memory_status and accumulate_key columns to memories.

Promotes frequently-filtered fields from metadata JSON to dedicated columns
for indexable queries. L5 task status and L7 accumulate_key are the most
commonly filtered metadata fields.

- memory_status TEXT: L5 task status (active/done/archived/blocked), NULL for non-L5
- accumulate_key TEXT: L7 content-prefix dedup key, NULL for non-L7
"""

MIGRATION_ID = "023_add_memory_status_and_accumulate_key"
DESCRIPTION = "Add memory_status and accumulate_key columns to memories"


def apply(conn):
    cur = conn.execute("PRAGMA table_info(memories)")
    cols = {r["name"] for r in cur.fetchall()}

    if "memory_status" not in cols:
        conn.execute(
            "ALTER TABLE memories ADD COLUMN memory_status TEXT DEFAULT NULL"
        )
        # Backfill from metadata JSON for L5 memories
        conn.execute(
            "UPDATE memories SET memory_status = json_extract(metadata, '$.status') "
            "WHERE level = 'L5' AND memory_status IS NULL"
        )

    if "accumulate_key" not in cols:
        conn.execute(
            "ALTER TABLE memories ADD COLUMN accumulate_key TEXT DEFAULT NULL"
        )
        # Backfill from metadata JSON
        conn.execute(
            "UPDATE memories SET accumulate_key = json_extract(metadata, '$.accumulate_key') "
            "WHERE accumulate_key IS NULL"
        )