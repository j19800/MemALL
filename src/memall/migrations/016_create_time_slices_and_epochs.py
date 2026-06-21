"""
Migration 016: Create time_slices, epochs, and pipeline_state tables.

These tables support the timeline dimension enhancement:
- time_slices: pre-aggregated per-agent time window stats (day/week/month)
- epochs: detected or manually declared periods in an agent's timeline
- pipeline_state: checkpoint tracking for incremental pipeline steps

Safe to re-run (all CREATE TABLE use IF NOT EXISTS).
"""

MIGRATION_ID = "016_create_time_slices_and_epochs"
DESCRIPTION = "Create time_slices, epochs, pipeline_state tables"


def apply(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS time_slices (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agent_name TEXT NOT NULL DEFAULT '*',
            granularity TEXT NOT NULL CHECK(granularity IN ('day', 'week', 'month')),
            slice_key TEXT NOT NULL,
            window_start TEXT NOT NULL,
            window_end TEXT NOT NULL,
            memory_count INTEGER DEFAULT 0,
            category_distribution TEXT NOT NULL DEFAULT '{}',
            level_distribution TEXT NOT NULL DEFAULT '{}',
            avg_confidence REAL DEFAULT 0.0,
            decision_count INTEGER DEFAULT 0,
            certain_count INTEGER DEFAULT 0,
            uncertain_count INTEGER DEFAULT 0,
            domain_set TEXT NOT NULL DEFAULT '[]',
            top_subjects TEXT NOT NULL DEFAULT '[]',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(agent_name, granularity, slice_key)
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_time_slices_agent
            ON time_slices(agent_name, granularity, window_start)
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS epochs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agent_name TEXT NOT NULL,
            label TEXT NOT NULL DEFAULT '',
            started_at TEXT NOT NULL,
            ended_at TEXT,
            boundary_reason TEXT NOT NULL DEFAULT 'auto',
            category TEXT NOT NULL DEFAULT '',
            memory_count INTEGER DEFAULT 0,
            summary TEXT NOT NULL DEFAULT '',
            metadata TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            UNIQUE(agent_name, started_at)
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_epochs_agent
            ON epochs(agent_name, started_at)
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS pipeline_state (
            step_name TEXT PRIMARY KEY,
            last_run_at TEXT,
            last_processed_id INTEGER DEFAULT 0,
            metadata TEXT NOT NULL DEFAULT '{}'
        )
    """)
