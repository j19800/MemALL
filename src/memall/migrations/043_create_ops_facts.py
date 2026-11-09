"""
Migration 043 — ops_facts: a dedicated table for operational data.
=================================================================

Why a separate table
--------------------
Operational facts (deployment topology, tokens, ports, change log, runbook
notes) are NOT memories.  Writing them into ``memories`` makes the pipeline
classify/distill/reflect over them, pollutes FTS with self-descriptive noise,
and subjects them to the visibility/trust filter — exactly the failure mode
that produced 9 junk rows from 1 real record.

``ops_facts`` lives in the SAME database file (so it is covered by the same
backup / WAL / single-writer guarantees) but is a completely separate table:

  * no FTS triggers, no ``memories``-scoped pipeline steps ever touch it;
  * no level / owner / visibility / trust_level gate — plain SQL reads;
  * keyed by ``(category, key)`` with an upsert-friendly UNIQUE index;
  * ``superseded_at`` keeps a change history instead of overwriting facts.

Idempotent: re-running is a no-op when the table already exists.
"""

MIGRATION_ID = "043_create_ops_facts"
DESCRIPTION = "Create ops_facts table: operational facts/change log isolated from memories"


def apply(conn) -> None:
    conn.execute("BEGIN")
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS ops_facts (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                category       TEXT NOT NULL,
                key            TEXT NOT NULL,
                value          TEXT NOT NULL,
                detail         TEXT NOT NULL DEFAULT '',
                source         TEXT NOT NULL DEFAULT '',
                valid_from     TEXT NOT NULL,
                superseded_at  TEXT,
                created_at     TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS ux_ops_facts_active
                ON ops_facts(category, key)
                WHERE superseded_at IS NULL
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_ops_facts_category
                ON ops_facts(category, superseded_at)
        """)
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise


def rollback(conn) -> None:
    conn.execute("BEGIN")
    try:
        conn.execute("DROP TABLE IF EXISTS ops_facts")
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
