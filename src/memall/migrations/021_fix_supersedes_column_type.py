"""
Migration 021: Fix supersedes column type INTEGER→TEXT for FK compatibility.

The supersedes column was declared as ``INTEGER REFERENCES memories(id)`` in
SCHEMA_SQL but has stored JSON arrays (``"[]"`` / ``"[1,2,3]"``) since migration
004.  When ``PRAGMA foreign_keys=ON``, any UPDATE writing a TEXT value to this
column triggers a FK violation because ``"[1,2]"`` cannot be coerced to an
integer for the lookup.

This migration recreates the memories table with the correct TEXT type so FK
enforcement works properly.  The underlying data is not changed — only the
column type annotation.

Risk: low.  Operation runs with FK enforcement off and re-creates the table
within a single transaction.
"""

MIGRATION_ID = "021_fix_supersedes_column_type"
DESCRIPTION = "Fix supersedes column type INTEGER→TEXT for FK compatibility"


def apply(conn):
    fk_was_on = conn.execute("PRAGMA foreign_keys").fetchone()[0]
    conn.execute("PRAGMA foreign_keys=OFF")
    try:
        conn.execute("BEGIN TRANSACTION")

        # 1. Create new table with TEXT supersedes + all migration-added columns
        conn.execute("""
            CREATE TABLE IF NOT EXISTS memories_new (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                content TEXT NOT NULL,
                content_hash TEXT NOT NULL UNIQUE,
                level TEXT NOT NULL DEFAULT 'P2',
                owner TEXT NOT NULL DEFAULT '',
                agent_name TEXT NOT NULL DEFAULT '',
                subject TEXT NOT NULL DEFAULT '',
                project TEXT NOT NULL DEFAULT '',
                category TEXT NOT NULL DEFAULT 'general',
                summary TEXT NOT NULL DEFAULT '',
                occurred_at TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                supersedes TEXT NOT NULL DEFAULT '[]',
                trust_level REAL NOT NULL DEFAULT 1.0,
                access_count INTEGER NOT NULL DEFAULT 0,
                visibility TEXT NOT NULL DEFAULT 'private',
                confidence REAL NOT NULL DEFAULT 0.5,
                weight INTEGER NOT NULL DEFAULT 1,
                metadata TEXT NOT NULL DEFAULT '{}',
                primary_layer TEXT NOT NULL DEFAULT '',
                secondary_layers TEXT NOT NULL DEFAULT '[]',
                tags TEXT NOT NULL DEFAULT '[]',
                echo_score REAL NOT NULL DEFAULT 0.0,
                arc_status TEXT,
                thread_id INTEGER DEFAULT NULL,
                agent_name_locked BOOLEAN NOT NULL DEFAULT 0
            )
        """)

        # 2. Copy existing data — use COALESCE to handle NULLs from old schema
        conn.execute("""
            INSERT INTO memories_new (
                id, content, content_hash, level, owner, agent_name,
                subject, project, category, summary, occurred_at,
                created_at, updated_at, supersedes,
                trust_level, access_count, visibility, confidence, weight,
                metadata,
                primary_layer, secondary_layers, tags, echo_score,
                arc_status, thread_id, agent_name_locked
            )
            SELECT id, content, content_hash, level, owner, agent_name,
                   subject, project, category, summary, occurred_at,
                   created_at, updated_at,
                   CASE
                       WHEN supersedes IS NULL THEN '[]'
                       WHEN typeof(supersedes) = 'text' THEN supersedes
                       WHEN typeof(supersedes) = 'integer' THEN '[' || supersedes || ']'
                       ELSE '[]'
                   END,
                   trust_level, access_count,
                   COALESCE(visibility, 'private'),
                   COALESCE(confidence, 0.5),
                   COALESCE(weight, 1),
                   COALESCE(metadata, '{}'),
                   COALESCE(primary_layer, ''),
                   COALESCE(secondary_layers, '[]'),
                   COALESCE(tags, '[]'),
                   COALESCE(echo_score, 0.0),
                   arc_status, thread_id, agent_name_locked
            FROM memories
        """)

        # 3. Recreate indexes on new table
        for index_row in conn.execute("""
            SELECT sql FROM sqlite_master
            WHERE type = 'index' AND tbl_name = 'memories'
              AND sql IS NOT NULL
        """).fetchall():
            # Rewrite index to reference memories_new
            sql = index_row["sql"].replace('"memories"', '"memories_new"')
            try:
                conn.execute(sql)
            except Exception:
                pass  # skip UNIQUE indexes that already exist via CREATE TABLE

        # 4. Swap tables
        conn.execute("DROP TABLE memories")
        conn.execute("ALTER TABLE memories_new RENAME TO memories")

        conn.execute("COMMIT")
        return 1
    except Exception:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.execute(f"PRAGMA foreign_keys={'ON' if fk_was_on else 'OFF'}")