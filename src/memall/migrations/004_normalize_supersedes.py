"""
Migration 004: Normalize supersedes column to TEXT JSON array format.

The supersedes column was originally INTEGER (single FK) but distill.py
was storing comma-separated IDs as TEXT. This migration:
1. Changes the column type declaration to TEXT (SQLite ignores type, but ensures clarity)
2. Normalizes all existing values to JSON array
3. Handles: NULL → "[]", integer → "[id]", "id1,id2,..." → "[id1,id2,...]", valid JSON → keep
"""

import json

MIGRATION_ID = "004_normalize_supersedes"
DESCRIPTION = "Normalize supersedes column to TEXT JSON array"


def apply(conn):
    # The supersedes column was originally INTEGER REFERENCES memories(id),
    # but the code now stores JSON arrays ("[1,2,3]").  We must disable FK
    # enforcement during migration because setting a TEXT value in a column
    # declared as INTEGER REFERENCES would otherwise fail.
    conn.execute("PRAGMA foreign_keys=OFF")

    # 1. Migrate existing data
    rows = conn.execute("SELECT id, supersedes FROM memories").fetchall()
    updated = 0
    for row in rows:
        mid = row["id"]
        raw = row["supersedes"]
        if raw is None or raw == "":
            new_val = "[]"
        elif isinstance(raw, str) and raw.startswith("["):
            try:
                parsed = json.loads(raw)
                if not isinstance(parsed, list):
                    new_val = json.dumps([parsed])
                else:
                    new_val = raw  # already valid
            except (json.JSONDecodeError, TypeError):
                new_val = "[]"
        elif isinstance(raw, str):
            # Comma-separated: "1,2,3" or "1" or " 1 , 2 "
            parts = [int(p.strip()) for p in raw.split(",") if p.strip().isdigit()]
            new_val = json.dumps(parts)
        elif isinstance(raw, (int, float)):
            new_val = json.dumps([int(raw)])
        else:
            new_val = "[]"
        conn.execute("UPDATE memories SET supersedes = ? WHERE id = ?", (new_val, mid))
        updated += 1

    conn.commit()
    return updated
