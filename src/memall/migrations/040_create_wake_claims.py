"""
Migration 040 — wake_claims: cross-process claim table for agent wakes.
=======================================================================

P1-3 (memory #1000942): every stdio MCP client session spawns its own server
process, and each of those processes runs its own FallbackTimer ->
goal_tick -> wake_agent.  The waker's cooldown and hourly cap
(``agent_waker._last_wake`` / ``._HOURLY``) live in process memory, so they
cannot see across processes: with N live MCP sessions the same
(agent, goal_id, action) was spawnable once per session — and two woken
agent instances would run concurrently against the same store.

The fix is a DB-level claim keyed exactly like the in-process cooldown:

    PRIMARY KEY (agent, goal_id, action)

``agent_waker.wake_agent`` upserts a row before spawning; an INSERT that
conflicts within the cooldown window updates nothing and the second process
skips.  This table is NOT a second storage: it holds no memory content, only
wake decisions — same trust level as ``session_cooldowns``, which set the
precedent for "a coordination fact lives in the main DB".

Idempotent: re-running is a no-op when the table already exists.
"""

MIGRATION_ID = "040_create_wake_claims"
DESCRIPTION = "Create wake_claims table: cross-process spawn claim keyed (agent, goal_id, action)"


def apply(conn) -> None:
    conn.execute("BEGIN")
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS wake_claims (
                agent      TEXT NOT NULL,
                goal_id    INTEGER NOT NULL,
                action     TEXT NOT NULL,
                claimed_at TEXT NOT NULL,
                pid        INTEGER,
                host       TEXT,
                PRIMARY KEY (agent, goal_id, action)
            )
        """)
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise


def rollback(conn) -> None:
    conn.execute("BEGIN")
    try:
        conn.execute("DROP TABLE IF EXISTS wake_claims")
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
