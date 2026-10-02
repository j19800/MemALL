"""
Migration 030 — drop the redundant non-unique identities lower() index.

Before migration 029 the bootstrap DDL in ``core/db.py`` created a *non-unique*
index ``idx_identities_agent_lower`` on ``identities(LOWER(agent_name))``.
Migration 029 replaced it with the UNIQUE index
``idx_identities_agent_name_lower_unique`` but, because ``init_db()`` runs the
bootstrap ``CREATE INDEX IF NOT EXISTS`` on every startup, the old non-unique
index kept being resurrected after 029 dropped it — leaving two indexes over the
same expression.

``core/db.py`` no longer creates the old index (the migration system now owns
it). This migration removes any stale copy so every database converges on a
single unique index. Idempotent: it is a no-op once the old index is gone.
"""

MIGRATION_ID = "030_drop_redundant_identity_lower_index"
DESCRIPTION = "Drop stale non-unique idx_identities_agent_lower once the unique lower() index exists"


def _index_exists(conn, name: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='index' AND name = ?", (name,)
    ).fetchone() is not None


def apply(conn) -> None:
    conn.execute("BEGIN")
    try:
        # Only drop the legacy index when the unique replacement is present, so
        # we never leave identities without any lower() index.
        if _index_exists(conn, "idx_identities_agent_name_lower_unique"):
            conn.execute("DROP INDEX IF EXISTS idx_identities_agent_lower")
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise


def rollback(conn) -> None:
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_identities_agent_lower ON identities(LOWER(agent_name))"
    )