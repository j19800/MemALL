"""
Migration 012: Drop dead `embeddings` table in favor of `memory_embeddings`.

The SCHEMA_SQL in db.py previously created a dead `embeddings` table that was
never used. The active embedding storage is `memory_embeddings` created by
graph/embeddings.py. This migration drops the dead table.
"""

MIGRATION_ID = "012_drop_dead_embeddings"
DESCRIPTION = "Drop dead embeddings table (memory_embeddings is canonical)"


def apply(conn):
    cur = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='embeddings'"
    )
    if cur.fetchone():
        conn.execute("DROP TABLE embeddings")