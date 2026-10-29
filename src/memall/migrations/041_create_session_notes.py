"""Migration 041 — session_notes: cross-process delivery of directed notes.

Why this exists
---------------
``memall.mcp.shared.push_session_note`` used to write into module-level dicts
(``_PENDING_SESSION_NOTES`` / ``_NOTE_DEDUP`` / ``_NOTE_TS``).  That looked
harmless while every note was produced and consumed by the same process, and it
is not: every stdio MCP client session spawns its OWN server process, so a note
pushed for ``opencode`` by one process was invisible to the process that
actually serves ``opencode``.  The note was, in effect, delivered to nobody.

That matters most for dispatch.  ``goal_finalize`` now pushes a directed note to
each child task's assignee so the card reaches that agent's own session instead
of waiting for the assignee to happen to poll.  With in-memory storage that
intent silently did nothing across agents — the very failure mode that makes
"dispatch to another agent" look implemented while achieving nothing.

Storing the note in the main DB is the same precedent as ``task_pending``
reminders and ``session_cooldowns``: a coordination fact this small belongs
where every process can see it.  It holds no memory content, only a pending
message for a named agent.

Semantics preserved exactly:

  * a note stays pending until ACK (a client that times out mid-response can
    retry and still receive it);
  * ACK drops the note AND frees its dedup key, because "delivered" is not
    "done" — an unacted prompt must be re-issuable on a later tick;
  * a dedup key blocks a re-push only while that note is still pending;
  * notes expire after ``session.note_ttl_seconds``;
  * a note belongs to exactly one agent — never "the oldest pending one".

Idempotent: re-running is a no-op when the table already exists.
"""

MIGRATION_ID = "041_create_session_notes"
DESCRIPTION = ("Create session_notes table: directed notes delivered to a named "
               "agent across processes")


def apply(conn) -> None:
    conn.execute("BEGIN")
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS session_notes (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                agent      TEXT NOT NULL,
                dedup_key  TEXT NOT NULL DEFAULT '',
                body       TEXT NOT NULL,
                created_at TEXT NOT NULL,
                acked_at   TEXT
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_session_notes_pending
                ON session_notes(agent, acked_at)
        """)
        # A dedup key blocks a re-push only while its note is unacked, so the
        # uniqueness constraint is scoped to pending rows.  Notes without a key
        # never collide.
        conn.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS idx_session_notes_dedup_pending
                ON session_notes(agent, dedup_key)
                WHERE dedup_key <> '' AND acked_at IS NULL
        """)
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise


def rollback(conn) -> None:
    conn.execute("BEGIN")
    try:
        conn.execute("DROP TABLE IF EXISTS session_notes")
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
