"""
Migration 042 — skills: quality-quantified skill registry (P0-②).
=======================================================================

The skill layer closes the loop that the goal lifecycle opened: a verified
goal's outcome becomes a reusable playbook, and that playbook earns trust
only through real outcomes (§3.1 of the 定稿, 分歧 #6).

Two design points matter:

* **Quality signals come from two real outcomes only** — a ``verify_goal``
  pass (+w1, usage_count+1) and a ``returned`` fail (-w2, demote to
  provisional).  No character-count heuristics: the old 4-gram gate
  punished good content for being short, which is the exact failure this
  layer exists to retire.

* **Provisional → trusted is a threshold, not a binary.**  ``quality_score``
  accumulates the weighted pass signals; ``usage_count`` counts how often
  the skill was actually consumed.  Both must clear their thresholds
  (``skills.thresholds``, default 0.6 / 3) before promotion.  A single
  demote drops the skill back to provisional and records why.

Idempotent: re-running is a no-op when the table already exists.
"""

MIGRATION_ID = "042_create_skills"
DESCRIPTION = "Create skills table: quality-quantified skill registry (provisional/trusted)"


def apply(conn) -> None:
    conn.execute("BEGIN")
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS skills (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                name              TEXT    NOT NULL UNIQUE,
                description       TEXT    NOT NULL,
                domain            TEXT    NOT NULL,
                status            TEXT    NOT NULL DEFAULT 'provisional',
                quality_score     REAL    NOT NULL DEFAULT 0,
                usage_count       INTEGER NOT NULL DEFAULT 0,
                source_memory_id  INTEGER,
                frontmatter_hash  TEXT,
                version           INTEGER NOT NULL DEFAULT 1,
                last_demote_reason TEXT,
                created_at        TEXT    NOT NULL,
                updated_at        TEXT    NOT NULL,
                memory_status     TEXT
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_skills_status
                ON skills(status)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_skills_domain
                ON skills(domain)
        """)
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise


def rollback(conn) -> None:
    conn.execute("BEGIN")
    try:
        conn.execute("DROP TABLE IF EXISTS skills")
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
