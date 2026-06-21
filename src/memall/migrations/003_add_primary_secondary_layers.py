"""
Migration 003: Add primary_layer and secondary_layers columns to memories table.

Supports multi-label classification: primary_layer holds the main layer,
secondary_layers holds a JSON array of additional matched layers.
Idempotent — safe to run multiple times.
"""

MIGRATION_ID = "003_add_primary_secondary_layers"
DESCRIPTION = "Add primary_layer and secondary_layers columns to memories table"


def apply(conn):
    cur = conn.execute("PRAGMA table_info(memories)")
    cols = [r["name"] for r in cur.fetchall()]

    if "primary_layer" not in cols:
        conn.execute(
            "ALTER TABLE memories ADD COLUMN primary_layer TEXT DEFAULT ''"
        )
    if "secondary_layers" not in cols:
        conn.execute(
            "ALTER TABLE memories ADD COLUMN secondary_layers TEXT DEFAULT '[]'"
        )

    # Backfill primary_layer from existing level values
    conn.execute(
        "UPDATE memories SET primary_layer = level WHERE primary_layer = '' AND level != ''"
    )