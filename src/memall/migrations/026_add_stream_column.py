"""
Migration 026 — add the `stream` column to the memories table.

Context: the `stream` column (knowledge/ledger) was added to SCHEMA_SQL and to
`_ensure_missing_columns` for fresh databases, but an index on it remained inside
SCHEMA_SQL. Because `executescript(SCHEMA_SQL)` runs *before* `_ensure_missing_columns`
adds the column, the index creation failed with "no such column: stream" on any
database that did not already have the column (production + pre-026 databases),
breaking `init_db(migrate=True)`.

This migration completes the rollout: it adds the column (if missing) and creates
the index *after* the column exists, so init_db works everywhere. It is idempotent
and safe to re-run.
"""

MIGRATION_ID = "026_add_stream_column"


def apply(conn) -> None:
    conn.execute("BEGIN")
    try:
        existing = {r["name"] for r in conn.execute("PRAGMA table_info(memories)")}
        if "stream" not in existing:
            conn.execute(
                "ALTER TABLE memories ADD COLUMN stream TEXT NOT NULL DEFAULT 'knowledge'"
            )
        # Create the index only after the column is guaranteed to exist.
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_memories_stream ON memories(stream)"
        )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise


def rollback(conn) -> None:
    # Index is safe to drop; the column is left in place (default 'knowledge')
    # to avoid data-loss on rollback.
    conn.execute("DROP INDEX IF EXISTS idx_memories_stream")
