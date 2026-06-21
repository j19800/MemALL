"""
Migration 011: Create embeddings table.

Created from inline _run_migrations() operation. Idempotent.
"""

MIGRATION_ID = "011_create_embeddings_table"
DESCRIPTION = "Create embeddings table for vector storage"


def apply(conn):
    cur = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='embeddings'"
    )
    if cur.fetchone():
        return

    conn.execute("""CREATE TABLE IF NOT EXISTS embeddings (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        target_type TEXT NOT NULL,
        target_id INTEGER NOT NULL,
        model_name TEXT NOT NULL,
        vector BLOB,
        created_at TEXT NOT NULL,
        UNIQUE(target_type, target_id, model_name)
    )""")