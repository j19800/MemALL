"""
Migration 024: Create entities, memory_entities, and knowledge_triples tables.

Supports EntityStrategy and KGStrategy — entity extraction and knowledge
graph triple storage for agent memory augmentation.

Safe to re-run (IF NOT EXISTS guards on all tables/indexes).
"""

MIGRATION_ID = "024_create_entities"
DESCRIPTION = "Create entities, memory_entities, and knowledge_triples tables"


def apply(conn):
    # ── Entities table ─────────────────────────────────────────────
    conn.execute("""
        CREATE TABLE IF NOT EXISTS entities (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            entity_type TEXT NOT NULL DEFAULT 'unknown',
            canonical_name TEXT NOT NULL DEFAULT '',
            description TEXT NOT NULL DEFAULT '',
            metadata TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(name, entity_type)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_entities_name ON entities(name)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_entities_type ON entities(entity_type)")

    # ── Memory–Entity junction ─────────────────────────────────────
    conn.execute("""
        CREATE TABLE IF NOT EXISTS memory_entities (
            memory_id INTEGER NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
            entity_id INTEGER NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
            role TEXT NOT NULL DEFAULT 'mentioned',
            confidence REAL NOT NULL DEFAULT 1.0,
            context_snippet TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            PRIMARY KEY (memory_id, entity_id)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_mem_entities_mem ON memory_entities(memory_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_mem_entities_ent ON memory_entities(entity_id)")

    # ── Knowledge triples ──────────────────────────────────────────
    conn.execute("""
        CREATE TABLE IF NOT EXISTS knowledge_triples (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            subject_id INTEGER NOT NULL REFERENCES entities(id),
            predicate TEXT NOT NULL,
            object_id INTEGER NOT NULL REFERENCES entities(id),
            source_memory_id INTEGER REFERENCES memories(id),
            confidence REAL NOT NULL DEFAULT 1.0,
            weight REAL NOT NULL DEFAULT 1.0,
            metadata TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            UNIQUE(subject_id, predicate, object_id)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_triples_subj ON knowledge_triples(subject_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_triples_obj ON knowledge_triples(object_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_triples_pred ON knowledge_triples(predicate)")

    # ── Shared records (for MemorySharing) ─────────────────────────
    conn.execute("""
        CREATE TABLE IF NOT EXISTS shared_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            memory_id INTEGER NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
            source_agent TEXT NOT NULL,
            target_agent TEXT NOT NULL,
            trust_level TEXT NOT NULL DEFAULT 'family',
            ttl_days INTEGER NOT NULL DEFAULT 0,
            expires_at TEXT,
            created_at TEXT NOT NULL,
            UNIQUE(memory_id, target_agent)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_shared_target ON shared_records(target_agent)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_shared_source ON shared_records(source_agent)")