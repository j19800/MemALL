"""
Migration 030: Add a ``tags`` column to the FTS5 index.

Context
-------
``memories.tags`` has existed since the base schema and is writable through
``ops:tag``, but it was never indexed and never read — 0 of ~4.5k production
rows carried a tag, and ``memories_fts`` only covered content/subject/summary.
This migration activates the keyword-retrieval path: tags are flattened to
space-separated text (``memall_tags_text``) and CJK-split (``memall_split_cjk``)
into their own FTS column, so a memory is findable by what it is *about*.

Operation
---------
FTS5 virtual tables cannot ``ALTER TABLE ADD COLUMN``.  For an
external-content table the index is derived data, so we drop and recreate it
with the new column, then rebuild from ``memories`` through the sync triggers'
exact transformation.  No user data is touched — only the index is rebuilt.

Idempotent: a database whose ``memories_fts`` DDL already lists ``tags`` is
left untouched.
"""

MIGRATION_ID = "030_add_fts_tags_column"
DESCRIPTION = "Add tags column to memories_fts and rebuild the index"


def apply(conn):
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='memories_fts'"
    ).fetchone()
    if row and row[0] and "tags" in row[0].lower():
        return  # already migrated

    # Make sure the SQL functions the triggers/backfill depend on exist.
    from memall.core.db import _register_sql_functions

    _register_sql_functions(conn)

    # 1. Drop the old 3-column index and recreate it with the tags column.
    conn.execute("DROP TABLE IF EXISTS memories_fts")
    conn.execute(
        "CREATE VIRTUAL TABLE memories_fts USING fts5("
        "    content, subject, summary, tags,"
        "    tokenize=\"unicode61\","
        "    content='memories', content_rowid='id'"
        ")"
    )

    # 2. Rebuild the index through the same transformation the triggers apply.
    #    (The table was just created and is empty, so no delete is needed.)
    conn.execute(
        "INSERT INTO memories_fts(rowid, content, subject, summary, tags) "
        "SELECT id, memall_split_cjk(content), memall_split_cjk(subject), "
        "memall_split_cjk(summary), memall_split_cjk(memall_tags_text(tags)) "
        "FROM memories"
    )

    # 3. Recreate the sync triggers with the tags column (the table drop did
    #    not remove them, but an old 3-column trigger would now mismatch —
    #    drop and re-add from the canonical definition).
    conn.execute("DROP TRIGGER IF EXISTS memories_ai")
    conn.execute("DROP TRIGGER IF EXISTS memories_ad")
    conn.execute("DROP TRIGGER IF EXISTS memories_au")
    from memall.core.db import FTS5_TRIGGERS

    conn.executescript(FTS5_TRIGGERS)
    conn.commit()
