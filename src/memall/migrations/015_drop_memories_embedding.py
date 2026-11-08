import logging

logger = logging.getLogger(__name__)

"""
Migration 015: Drop dead embedding column from memories table.

The embedding BLOB column was never populated — vector data lives in
the memory_embeddings table (graph/embeddings.py). SQLite does not
support DROP COLUMN in older versions, so this migration is a no-op
for existing databases; the column is harmless dead weight.
Idempotent.
"""

MIGRATION_ID = "015_drop_memories_embedding"
DESCRIPTION = "Drop dead embedding column (documentation only)"


def _column_exists(conn, table: str, column: str) -> bool:
    """True if ``column`` is present in ``table`` (case-insensitive)."""
    cur = conn.execute(f"PRAGMA table_info({table})")
    return any(row[1].lower() == column.lower() for row in cur.fetchall())


def apply(conn):
    # SQLite >= 3.35 supports DROP COLUMN; this is a best-effort operation.
    # The column may already be gone (migration re-run, or a fresh DB where
    # SCHEMA_SQL never had it) — that is the expected idempotent path, not an
    # error, so check first instead of dumping a traceback on every init.
    if not _column_exists(conn, "memories", "embedding"):
        logger.debug("015: memories.embedding already absent — nothing to drop")
        return
    try:
        conn.execute("ALTER TABLE memories DROP COLUMN embedding")
    except Exception:
        # Genuinely unexpected (e.g. SQLite < 3.35 without the workaround);
        # keep it visible but do not treat as fatal.
        logger.warning("015_drop_memories_embedding.py: could not drop column", exc_info=True)