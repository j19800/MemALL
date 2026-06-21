"""
Migration 019: Create discussion_topics and discussion_responses tables.

Renumbered from 018 to resolve filename collision with 018_add_echo_score.
Also backfills discussions tables for databases where they were previously
created via SCHEMA_SQL but never tracked as an explicit migration.

Safe to re-run (CREATE TABLE IF NOT EXISTS + CREATE INDEX IF NOT EXISTS).
"""

MIGRATION_ID = "019_create_discussion_tables"
DESCRIPTION = "Create discussion_topics + discussion_responses for convergence engine (renamed from 018)"


def apply(conn):
    # Clean up stale MIGRATION_ID from the 018→019 rename
    conn.execute("DELETE FROM schema_version WHERE migration_id = '018_create_discussion_tables'")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS discussion_topics (
            topic_id TEXT NOT NULL PRIMARY KEY,
            l4_memory_id INTEGER REFERENCES memories(id),
            title TEXT NOT NULL DEFAULT '',
            background TEXT NOT NULL DEFAULT '',
            options TEXT NOT NULL DEFAULT '[]',
            participants TEXT NOT NULL DEFAULT '[]',
            open_questions TEXT NOT NULL DEFAULT '[]',
            status TEXT NOT NULL DEFAULT 'active'
                CHECK(status IN ('active', 'converged', 'stale')),
            rounds_without_new_args INTEGER NOT NULL DEFAULT 0,
            timeout_hours INTEGER NOT NULL DEFAULT 24,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            closed_at TEXT
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_discussion_status
            ON discussion_topics(status)
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_discussion_l4
            ON discussion_topics(l4_memory_id)
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS discussion_responses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            topic_id TEXT NOT NULL REFERENCES discussion_topics(topic_id),
            agent_name TEXT NOT NULL,
            stance TEXT NOT NULL CHECK(stance IN ('agree', 'disagree', 'pass', 'abstain')),
            arguments TEXT NOT NULL DEFAULT '',
            response_round INTEGER NOT NULL DEFAULT 1,
            responded_at TEXT NOT NULL,
            UNIQUE(topic_id, agent_name, response_round)
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_disc_resp_topic
            ON discussion_responses(topic_id, response_round)
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_disc_resp_agent
            ON discussion_responses(agent_name)
    """)