"""Migration 042 — wake_audit: persistent record of every wake decision.

Why this exists
---------------
``agent_waker._wake_log`` was a module-level list capped at 50 entries.  The
driver that decides to spawn an agent runs inside a short-lived script or a
timer callback, so the evidence died with the process: by the time someone asked
"why did the agent wake and then do nothing?", the record of which agent was
woken, with what argv, whether the claim was taken, and what the process
returned was simply gone.

That is not a cosmetic gap.  Diagnosing a stalled hand-off on 2026-10-30 required
re-running the whole wake by hand — reconstructing the prompt from
``goal_tick`` and watching a fresh process — purely because the audit had
evaporated.  A wake starts a process that can edit code; its decisions must be
reviewable after the fact, from the outside.

Stored facts per attempt, including the ones that are useless in memory but
essential later:

  * the decision (``woke`` / ``skipped``) and the reason it was skipped, so a
    silent ``dry_run`` or a rate limit is distinguishable from a failure;
  * the goal and action it was about, so a wake can be joined back to a hand-off;
  * a short argv summary (never the full prompt — it can be long and may quote
    user data; the shape is what matters for "what did we actually run");
  * pid, return code, duration and the tail of stdout/stderr.

This is an operational ledger, not memory content: same trust level as
``wake_claims`` (040) and ``session_notes`` (041).

Idempotent: re-running is a no-op when the table already exists.
"""

MIGRATION_ID = "042_create_wake_audit"
DESCRIPTION = ("Create wake_audit table: durable record of every agent wake "
               "decision (agent, goal, action, reason, pid, rc, output tails)")


def apply(conn) -> None:
    conn.execute("BEGIN")
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS wake_audit (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                at           TEXT NOT NULL,
                agent        TEXT NOT NULL DEFAULT '',
                goal_id      INTEGER NOT NULL DEFAULT 0,
                action       TEXT NOT NULL DEFAULT '',
                woke         INTEGER NOT NULL DEFAULT 0,
                reason       TEXT NOT NULL DEFAULT '',
                pid          INTEGER,
                rc           INTEGER,
                duration_ms  INTEGER,
                argv_summary TEXT NOT NULL DEFAULT '',
                env_keys     TEXT NOT NULL DEFAULT '',
                stdout_tail  TEXT NOT NULL DEFAULT '',
                stderr_tail  TEXT NOT NULL DEFAULT '',
                host         TEXT NOT NULL DEFAULT ''
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_wake_audit_recent
                ON wake_audit(at DESC)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_wake_audit_goal
                ON wake_audit(goal_id, action)
        """)
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise


def rollback(conn) -> None:
    conn.execute("BEGIN")
    try:
        conn.execute("DROP TABLE IF EXISTS wake_audit")
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
