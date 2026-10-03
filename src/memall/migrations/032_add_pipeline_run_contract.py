"""
Migration 032 — pipeline run idempotency contract + crash recovery.
==================================================================

F-05: the pipeline commits after **every** step (a deliberate choice so a
long run never holds the SQLite write lock across steps).  The cost is that
a crash mid-run leaves the database partially advanced.  Crash-safety
therefore depends on an explicit contract: every step must be *reentrant*
— safe to run again on an already-partially-processed database.

This migration gives ``pipeline_runs`` the fields needed to make that
contract auditable and to detect runs that never finished:

- ``contract_version`` — which idempotency contract the run's steps were
  executed under (see ``memall.pipeline.pipeline.PIPELINE_CONTRACT_VERSION``).
- ``pid`` / ``host`` — the process that owns the run, so a run left in
  ``running`` by a *dead* process can be told apart from a live one.
- ``interrupted_at`` — when a stale ``running`` row was reclassified as
  ``interrupted`` (set by ``recover_stale_pipeline_runs``).
- an index on ``status`` to make the stale-run sweep cheap.

Idempotent: re-running is a no-op when the columns already exist.
"""

MIGRATION_ID = "032_add_pipeline_run_contract"
DESCRIPTION = "Add contract_version/pid/host/interrupted_at to pipeline_runs + status index"


def _columns(conn, table: str) -> set:
    return {r["name"] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def apply(conn) -> None:
    conn.execute("BEGIN")
    try:
        cols = _columns(conn, "pipeline_runs")
        if "contract_version" not in cols:
            conn.execute(
                "ALTER TABLE pipeline_runs ADD COLUMN contract_version INTEGER NOT NULL DEFAULT 1"
            )
        if "pid" not in cols:
            conn.execute("ALTER TABLE pipeline_runs ADD COLUMN pid INTEGER")
        if "host" not in cols:
            conn.execute("ALTER TABLE pipeline_runs ADD COLUMN host TEXT NOT NULL DEFAULT ''")
        if "interrupted_at" not in cols:
            conn.execute("ALTER TABLE pipeline_runs ADD COLUMN interrupted_at TEXT")

        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_pipeline_runs_status ON pipeline_runs(status)"
        )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise


def rollback(conn) -> None:
    conn.execute("BEGIN")
    try:
        conn.execute("DROP INDEX IF EXISTS idx_pipeline_runs_status")
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise