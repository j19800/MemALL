"""
Migration 010: Create narrative_clusters table.

Created from inline _run_migrations() operation. Idempotent.
"""

MIGRATION_ID = "010_create_narrative_clusters_table"
DESCRIPTION = "Create narrative_clusters junction table"


def apply(conn):
    cur = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='narrative_clusters'"
    )
    if cur.fetchone():
        return

    conn.execute("""CREATE TABLE IF NOT EXISTS narrative_clusters (
        narrative_id INTEGER REFERENCES narratives(id),
        cluster_id INTEGER REFERENCES clusters(id),
        distance REAL,
        PRIMARY KEY (narrative_id, cluster_id)
    )""")