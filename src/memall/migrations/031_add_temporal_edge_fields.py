"""
Migration 031 — bitemporal graph edges (valid_from / invalid_at).

P0-3 of the MemALL optimisation roadmap: facts used to be *overwritten*
when updated, losing history ("user lived in London" is gone once they
move to Tokyo). This migration turns ``edges`` into a bitemporal graph:

- ``valid_from``  — when the edge started being true (backfilled from
  ``created_at`` for existing rows; NULL = origin unknown, treated as
  always valid by query windows).
- ``invalid_at``  — when the edge stopped being true (NULL = still
  valid). A new write for the same (source, target, relation) marks
  the current valid edge as invalid and inserts a fresh one instead of
  mutating or duplicating it.

``memories`` gets the same pair of columns so memory-level temporality
(per the roadmap) is also expressible; the existing supersedes /
archive mechanisms continue to work unchanged.

Another structural change: the old full UNIQUE index on
(source_id, target_id, relation_type) made multiple *versions* of an
edge impossible. It is replaced by a *partial* unique index that only
constrains currently-valid edges (invalid_at IS NULL), so historical
versions can coexist with the live one. ``pipeline/enrich.py`` is
updated in lockstep to build the same partial index.

Idempotent: re-running is a no-op when the columns are already present.
"""

MIGRATION_ID = "031_add_temporal_edge_fields"
DESCRIPTION = "Add valid_from/invalid_at to edges and memories; partial unique index for currently-valid edges"


def _columns(conn, table: str) -> set:
    return {r["name"] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _index_exists(conn, name: str) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='index' AND name = ?", (name,)
        ).fetchone()
        is not None
    )


def apply(conn) -> None:
    conn.execute("BEGIN")
    try:
        # ── edges: bitemporal validity window ──
        edge_cols = _columns(conn, "edges")
        if "valid_from" not in edge_cols:
            conn.execute("ALTER TABLE edges ADD COLUMN valid_from TEXT")
        if "invalid_at" not in edge_cols:
            conn.execute("ALTER TABLE edges ADD COLUMN invalid_at TEXT")
        # Backfill: existing edges became valid when they were created.
        conn.execute(
            "UPDATE edges SET valid_from = created_at WHERE valid_from IS NULL AND created_at IS NOT NULL"
        )

        # ── memories: temporality columns (roadmap P0-3, memories 层) ──
        mem_cols = _columns(conn, "memories")
        if "valid_from" not in mem_cols:
            conn.execute("ALTER TABLE memories ADD COLUMN valid_from TEXT")
        if "invalid_at" not in mem_cols:
            conn.execute("ALTER TABLE memories ADD COLUMN invalid_at TEXT")
        conn.execute(
            "UPDATE memories SET valid_from = created_at WHERE valid_from IS NULL AND created_at IS NOT NULL"
        )

        # ── uniqueness: only currently-valid edges are unique ──
        if _index_exists(conn, "idx_edges_unique"):
            conn.execute("DROP INDEX idx_edges_unique")
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_edges_unique_active "
            "ON edges(source_id, target_id, relation_type) WHERE invalid_at IS NULL"
        )
        # Index for time-window scans on invalidated edges.
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_edges_invalid_at ON edges(invalid_at) WHERE invalid_at IS NOT NULL"
        )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise


def rollback(conn) -> None:
    conn.execute("BEGIN")
    try:
        # Restore the full unique index on the live edges. Edges that are
        # currently valid still satisfy it; historically-invalidated rows
        # (which share the triple) are downgraded to NULL validity so the
        # table re-converges on the old single-version model.
        conn.execute("UPDATE edges SET invalid_at = NULL, valid_from = NULL")
        conn.execute("DROP INDEX IF EXISTS idx_edges_unique_active")
        conn.execute("DROP INDEX IF EXISTS idx_edges_invalid_at")
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_edges_unique "
            "ON edges(source_id, target_id, relation_type)"
        )
        conn.execute("UPDATE memories SET invalid_at = NULL, valid_from = NULL")
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
