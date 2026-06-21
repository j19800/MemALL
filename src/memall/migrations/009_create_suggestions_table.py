"""
Migration 009: Create suggestions table.

Created from inline _run_migrations() operation. Idempotent.
"""

MIGRATION_ID = "009_create_suggestions_table"
DESCRIPTION = "Create suggestions table with indexes"


def apply(conn):
    cur = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='suggestions'"
    )
    if cur.fetchone():
        return

    conn.execute("""CREATE TABLE IF NOT EXISTS suggestions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        source_type TEXT NOT NULL DEFAULT 'memory',
        source_id INTEGER,
        content TEXT NOT NULL,
        category TEXT,
        priority TEXT DEFAULT 'P2',
        status TEXT NOT NULL DEFAULT 'pending',
        assigned_to TEXT,
        created_by TEXT DEFAULT 'marvis',
        created_at TEXT NOT NULL,
        accepted_at TEXT,
        implemented_at TEXT,
        rejection_reason TEXT,
        implementation_note TEXT,
        related_phase TEXT,
        tags TEXT
    )""")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_suggestions_status ON suggestions(status)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_suggestions_category ON suggestions(category)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_suggestions_source ON suggestions(source_id)"
    )