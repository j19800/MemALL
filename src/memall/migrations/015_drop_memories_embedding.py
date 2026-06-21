import logging
"""
Migration 015: Drop dead embedding column from memories table.
logger = logging.getLogger(__name__)


The embedding BLOB column was never populated — vector data lives in
the memory_embeddings table (graph/embeddings.py). SQLite does not
support DROP COLUMN in older versions, so this migration is a no-op
for existing databases; the column is harmless dead weight.
Idempotent.
"""

MIGRATION_ID = "015_drop_memories_embedding"
DESCRIPTION = "Drop dead embedding column (documentation only)"


def apply(conn):
    # SQLite >= 3.35 supports DROP COLUMN; this is a best-effort operation.
    try:
        conn.execute("ALTER TABLE memories DROP COLUMN embedding")
    except Exception:
        logger.warning("015_drop_memories_embedding.py: silent error", exc_info=True)
