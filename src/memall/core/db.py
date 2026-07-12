import sqlite3
import hashlib
import os
import threading
import queue
import logging
from contextlib import contextmanager
from pathlib import Path


logger = logging.getLogger(__name__)

import sqlite_vec

def _resolve_db_path() -> Path:
    from memall.config import get_config
    configured = get_config("db.path", "")
    if configured:
        return Path(configured)
    # On Windows, use USERPROFILE to avoid SYSTEM profile when run as a service
    _env = os.environ.get("MEMALL_DB_PATH") or ""
    if _env:
        return Path(_env)
    _home = os.environ.get("USERPROFILE") or str(Path.home())
    _default = Path(_home) / ".memall" / "data.db"
    # Prefer non-system drive on Windows (DB + backups too large for C:)
    if os.name == "nt" and str(_default).startswith("C:\\"):
        _try_count = 0
        for drive in "DEFGH":
            if _try_count >= 3:
                break
            candidate = Path(f"{drive}:") / ".memall" / "data.db"
            try:
                if candidate.parent.exists() or candidate.parent.mkdir(parents=True, exist_ok=True):
                    return candidate
            except (OSError, PermissionError):
                _try_count += 1
                continue
    return _default

DB_PATH = _resolve_db_path()

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS memories (
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
    arc_status TEXT,
    thread_id INTEGER DEFAULT NULL,
    agent_name_locked BOOLEAN NOT NULL DEFAULT 0,
    memory_status TEXT DEFAULT NULL,
    accumulate_key TEXT DEFAULT NULL
);

CREATE TABLE IF NOT EXISTS edges (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id INTEGER NOT NULL REFERENCES memories(id),
    target_id INTEGER NOT NULL REFERENCES memories(id),
    relation_type TEXT NOT NULL DEFAULT 'refines',
    weight REAL NOT NULL DEFAULT 1.0,
    created_at TEXT NOT NULL,
    metadata TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS identities (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_name TEXT NOT NULL UNIQUE,
    agent_type TEXT NOT NULL DEFAULT 'ai',
    description TEXT NOT NULL DEFAULT '',
    icon TEXT NOT NULL DEFAULT '🤖',
    identity_profile TEXT NOT NULL DEFAULT '{}',
    profile_json TEXT NOT NULL DEFAULT '{}',
    persona_updated_at TEXT NOT NULL DEFAULT '',
    last_heartbeat TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'active',
    metadata TEXT NOT NULL DEFAULT '{}'
);

CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
    content, subject, summary,
    content='memories', content_rowid='id'
);

CREATE VIRTUAL TABLE IF NOT EXISTS mem_vec USING vec0(
    embedding float[512]
);

CREATE INDEX IF NOT EXISTS idx_memories_owner ON memories(owner);
CREATE INDEX IF NOT EXISTS idx_memories_agent ON memories(agent_name);
CREATE INDEX IF NOT EXISTS idx_memories_level_agent ON memories(level, agent_name);
CREATE INDEX IF NOT EXISTS idx_memories_agent_lower ON memories(LOWER(agent_name));
CREATE INDEX IF NOT EXISTS idx_memories_category ON memories(category);
CREATE INDEX IF NOT EXISTS idx_memories_project ON memories(project);
CREATE INDEX IF NOT EXISTS idx_memories_occurred ON memories(occurred_at);
CREATE INDEX IF NOT EXISTS idx_memories_hash ON memories(content_hash);
CREATE INDEX IF NOT EXISTS idx_edges_source ON edges(source_id);
CREATE INDEX IF NOT EXISTS idx_edges_target ON edges(target_id);
CREATE INDEX IF NOT EXISTS idx_edges_type ON edges(relation_type);
CREATE INDEX IF NOT EXISTS idx_memories_memory_status ON memories(memory_status);
CREATE INDEX IF NOT EXISTS idx_memories_thread ON memories(thread_id);
CREATE INDEX IF NOT EXISTS idx_identities_agent_lower ON identities(LOWER(agent_name));

CREATE TABLE IF NOT EXISTS clusters (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    label           TEXT NOT NULL,
    centroid_memory_id INTEGER REFERENCES memories(id),
    member_count    INTEGER DEFAULT 0,
    coherence_score REAL,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

    CREATE TABLE IF NOT EXISTS memory_clusters (
    memory_id   INTEGER REFERENCES memories(id),
    cluster_id  INTEGER REFERENCES clusters(id),
    distance    REAL,
    PRIMARY KEY (memory_id, cluster_id)
);

CREATE TABLE IF NOT EXISTS narratives (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_name      TEXT NOT NULL,
    narrative_type  TEXT NOT NULL DEFAULT 'weekly',
    span_start      TEXT NOT NULL,
    span_end        TEXT NOT NULL,
    narrative_text  TEXT NOT NULL,
    events          TEXT NOT NULL DEFAULT '[]',
    summary         TEXT NOT NULL DEFAULT '',
    generated_at    TEXT NOT NULL,
    memory_count    INTEGER DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_narratives_agent ON narratives(agent_name);
CREATE INDEX IF NOT EXISTS idx_narratives_span ON narratives(span_start, span_end);

CREATE TABLE IF NOT EXISTS suggestions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    source_type     TEXT NOT NULL DEFAULT 'memory',
    source_id       INTEGER,
    content         TEXT NOT NULL,
    category        TEXT,
    priority        TEXT DEFAULT 'P2',
    status          TEXT NOT NULL DEFAULT 'pending',
    assigned_to     TEXT,
    created_by      TEXT DEFAULT 'marvis',
    created_at      TEXT NOT NULL,
    accepted_at     TEXT,
    implemented_at  TEXT,
    rejection_reason TEXT,
    implementation_note TEXT,
    related_phase   TEXT,
    tags            TEXT
);

CREATE INDEX IF NOT EXISTS idx_suggestions_status ON suggestions(status);
CREATE INDEX IF NOT EXISTS idx_suggestions_category ON suggestions(category);
CREATE INDEX IF NOT EXISTS idx_suggestions_source ON suggestions(source_id);

CREATE TABLE IF NOT EXISTS narrative_clusters (
    narrative_id  INTEGER REFERENCES narratives(id),
    cluster_id    INTEGER REFERENCES clusters(id),
    distance      REAL,
    PRIMARY KEY (narrative_id, cluster_id)
);

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
);

CREATE INDEX IF NOT EXISTS idx_time_slices_agent ON time_slices(agent_name, granularity, window_start);

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
);

CREATE INDEX IF NOT EXISTS idx_epochs_agent ON epochs(agent_name, started_at);

CREATE TABLE IF NOT EXISTS pipeline_state (
    step_name TEXT PRIMARY KEY,
    last_run_at TEXT,
    last_processed_id INTEGER DEFAULT 0,
    metadata TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS pipeline_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    memory_id INTEGER NOT NULL,
    event_type TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    processed_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_pipeline_events_memory ON pipeline_events(memory_id);

CREATE TABLE IF NOT EXISTS pipeline_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    ended_at TEXT,
    status TEXT NOT NULL DEFAULT 'running',
    total_elapsed_ms INTEGER,
    error TEXT,
    steps TEXT NOT NULL DEFAULT '[]'
);

CREATE TABLE IF NOT EXISTS tracing_spans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trace_id TEXT NOT NULL,
    parent_span_id TEXT,
    span_id TEXT NOT NULL,
    name TEXT NOT NULL,
    span_type TEXT,
    start_time TEXT NOT NULL,
    duration_ms REAL NOT NULL,
    status TEXT NOT NULL DEFAULT 'ok',
    attributes TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tracing_trace ON tracing_spans(trace_id);

-- Entities for EntityStrategy
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
);
CREATE INDEX IF NOT EXISTS idx_entities_name ON entities(name);
CREATE INDEX IF NOT EXISTS idx_entities_type ON entities(entity_type);

-- Memory–Entity junction
CREATE TABLE IF NOT EXISTS memory_entities (
    memory_id INTEGER NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
    entity_id INTEGER NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    role TEXT NOT NULL DEFAULT 'mentioned',
    confidence REAL NOT NULL DEFAULT 1.0,
    context_snippet TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    PRIMARY KEY (memory_id, entity_id)
);
CREATE INDEX IF NOT EXISTS idx_mem_entities_mem ON memory_entities(memory_id);
CREATE INDEX IF NOT EXISTS idx_mem_entities_ent ON memory_entities(entity_id);

-- Knowledge triples for KGStrategy
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
);
CREATE INDEX IF NOT EXISTS idx_triples_subj ON knowledge_triples(subject_id);
CREATE INDEX IF NOT EXISTS idx_triples_obj ON knowledge_triples(object_id);
CREATE INDEX IF NOT EXISTS idx_triples_pred ON knowledge_triples(predicate);

-- Shared records for MemorySharing
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
);
CREATE INDEX IF NOT EXISTS idx_shared_target ON shared_records(target_agent);
CREATE INDEX IF NOT EXISTS idx_shared_source ON shared_records(source_agent);
"""

FTS5_TRIGGERS = """
CREATE TRIGGER IF NOT EXISTS memories_ai AFTER INSERT ON memories BEGIN
    INSERT INTO memories_fts(rowid, content, subject, summary)
    VALUES (new.id, new.content, new.subject, new.summary);
END;

CREATE TRIGGER IF NOT EXISTS memories_ad AFTER DELETE ON memories BEGIN
    INSERT INTO memories_fts(memories_fts, rowid, content, subject, summary)
    VALUES ('delete', old.id, old.content, old.subject, old.summary);
END;

CREATE TRIGGER IF NOT EXISTS memories_au AFTER UPDATE ON memories BEGIN
    INSERT INTO memories_fts(memories_fts, rowid, content, subject, summary)
    VALUES ('delete', old.id, old.content, old.subject, old.summary);
    INSERT INTO memories_fts(rowid, content, subject, summary)
    VALUES (new.id, new.content, new.subject, new.summary);
END;
"""


def get_db_path() -> Path:
    return _resolve_db_path()


def ensure_dir():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)


def content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def get_conn(db_path=None) -> sqlite3.Connection:
    """Get a new SQLite connection with performance-oriented PRAGMAs.

    Sets WAL journal mode, NORMAL synchronous (safe in WAL), 8 MB cache,
    5-second busy timeout, and foreign-key enforcement.

    On first call, auto-initializes the database schema and migrations
    so no explicit ``memall init`` is required.

    Args:
        db_path: Optional path override.  Falls back to ``DB_PATH``.

    Returns:
        A ``sqlite3.Connection`` with ``row_factory = sqlite3.Row``.
    """
    global _auto_init_done
    path = db_path or DB_PATH
    ensure_dir()
    conn = sqlite3.connect(str(path), timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
    except Exception:
        logger.warning("db.py: failed to load sqlite_vec extension", exc_info=True)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA cache_size=-8000")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")

    if not _auto_init_done:
        with _auto_init_lock:
            if not _auto_init_done:
                init_db(conn=conn)
                _auto_init_done = True

    return conn


def _ensure_missing_columns(conn):
    """Add columns that migrations assume exist but SCHEMA_SQL may not create.

    This is a safety net for the gap between SCHEMA_SQL (which defines the base
    table) and the migration scripts.  Some migration-added columns are present
    in SCHEMA_SQL for fresh databases but may not survive ``executescript``
    splitting — we add them explicitly here.
    """
    cur = conn.execute("PRAGMA table_info(memories)")
    existing = {r["name"] for r in cur.fetchall()}
    additions = {
        "primary_layer": "TEXT NOT NULL DEFAULT ''",
        "secondary_layers": "TEXT NOT NULL DEFAULT '[]'",
        "tags": "TEXT NOT NULL DEFAULT '[]'",
        "echo_score": "REAL NOT NULL DEFAULT 0.0",
        "memory_status": "TEXT DEFAULT NULL",
        "accumulate_key": "TEXT DEFAULT NULL",
    }
    for col, typedef in additions.items():
        if col not in existing:
            conn.execute(f"ALTER TABLE memories ADD COLUMN {col} {typedef}")


def init_db(conn=None, migrate=True, db_path_for_backup: str = ""):
    close = False
    if conn is None:
        conn = get_conn()
        close = True
    try:
        conn.executescript(SCHEMA_SQL)
        # Safety net: add any columns that may not have survived executescript.
        # Must commit here so subsequent migrations see the DDL changes.
        _ensure_missing_columns(conn)
        conn.commit()
        # Only execute FTS5 triggers if they haven't been set up yet
        _fts_triggers_done = conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='trigger' AND name LIKE 'memories_%'"
        ).fetchone()[0]
        if _fts_triggers_done < 3:
            conn.executescript(FTS5_TRIGGERS)
        if migrate:
            # Formal migration system (GAP-7: auto-migration on pip upgrade)
            try:
                from memall.migrations import run_migrations as run_formal_migrations, \
                    get_pending_migrations as get_pending

                # Auto-backup before applying formal migrations
                _db_path = db_path_for_backup or str(DB_PATH)
                pending = get_pending(conn)
                if pending:
                    import logging
                    log = logging.getLogger("memall.db")
                    log.info(f"Applying {len(pending)} pending migration(s): {pending}")
                    result = run_formal_migrations(conn, db_path=_db_path)
                    if result.get("errors", 0) > 0:
                        log.warning(f"Migrations completed with {result['errors']} error(s)")
                    conn.commit()
            except ImportError:
                logger.warning("db.py: migration import failed", exc_info=True)
        # Seed "system" identity — required by capture() identity check
        # (normalize_agent_name() falls back to "system" for empty/invalid names)
        conn.execute(
            "INSERT OR IGNORE INTO identities (agent_name, agent_type, description) "
            "VALUES ('system', 'system', 'System agent for internal operations')"
        )
        conn.commit()
    finally:
        if close:
            conn.close()


def rebuild_fts(conn):
    conn.execute("INSERT INTO memories_fts(memories_fts) VALUES('rebuild')")
    conn.commit()


# _run_migrations() removed in v0.2.0 — all operations migrated to
# formal migration files (001-011 in memall/migrations/).


# ══════════════════════════════════════════════════════════════════
# Connection Pool
# ══════════════════════════════════════════════════════════════════

class ConnectionPool:
    """Thread-safe SQLite connection pool backed by ``queue.Queue``.

    Connections are created lazily on demand up to ``max_connections``.
    Because SQLite connections are tied to the thread that created them,
    the pool **discards** connections received by a different thread and
    creates a new one for the requesting thread.

    Attributes:
        db_path: Absolute path to the SQLite database file.
        max_connections: Maximum number of open connections (default 5).
    """

    def __init__(self, db_path: str, max_connections: int = 5) -> None:
        self.db_path = db_path
        self.max_connections = max_connections
        self._pool: queue.Queue = queue.Queue()
        self._created = 0
        self._lock = threading.Lock()
        self._closed = False
        self._conn_tids: Dict[int, int] = {}  # id(conn) → thread ident

    def _new_conn(self) -> sqlite3.Connection:
        """Create and configure a fresh connection."""
        global _auto_init_done
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            conn.enable_load_extension(True)
            sqlite_vec.load(conn)
        except Exception:
            logger.warning("db.py: get_conn vec load failed", exc_info=True)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA cache_size=-8000")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=5000")
        self._conn_tids[id(conn)] = threading.get_ident()

        if not _auto_init_done:
            init_db(conn=conn)
            _auto_init_done = True

        return conn

    def get(self) -> sqlite3.Connection:
        """Obtain a connection valid for the current thread.

        If the pool is empty and ``max_connections`` has not been
        reached yet a new connection is created.  Otherwise the caller
        blocks until a connection is returned via :meth:`put`.

        Returns:
            A ready-to-use ``sqlite3.Connection`` valid for the calling thread.

        Raises:
            RuntimeError: If the pool has been closed via :meth:`close_all`.
        """
        if self._closed:
            raise RuntimeError("ConnectionPool is closed")

        # Fast path — empty pool, create fresh connection
        try:
            conn = self._pool.get_nowait()
        except queue.Empty:
            with self._lock:
                if self._created < self.max_connections:
                    self._created += 1
                    return self._new_conn()
            # Pool exhausted — block until one is returned (max 30s to avoid deadlock)
            try:
                conn = self._pool.get(timeout=30)
            except queue.Empty:
                raise RuntimeError(
                    "ConnectionPool: no connection available after 30s timeout "
                    f"(max_connections={self.max_connections})"
                )

        # The returned connection may belong to a different thread —
        # discard it and create a new one for the current thread.
        cur_tid = threading.get_ident()
        with self._lock:
            if self._conn_tids.get(id(conn)) != cur_tid:
                self._conn_tids.pop(id(conn), None)
                try:
                    conn.close()
                except sqlite3.ProgrammingError:
                    pass  # can't close connection owned by another thread
                return self._new_conn()

        return conn

    def put(self, conn: sqlite3.Connection) -> None:
        """Return a connection to the pool for reuse.

        Args:
            conn: The connection previously obtained from :meth:`get`.
        """
        if self._closed:
            try:
                conn.close()
            except sqlite3.Error:
                logger.warning("db.py: silent error", exc_info=True)
            return
        try:
            self._pool.put_nowait(conn)
        except queue.Full:
            conn.close()

    def close_all(self) -> None:
        """Drain the pool and close every connection."""
        with self._lock:
            self._closed = True
        while True:
            try:
                conn = self._pool.get_nowait()
                conn.close()
            except queue.Empty:
                break
        self._conn_tids.clear()

    def connection(self):
        """Context manager yielding a pooled connection.

        Usage::

            with pool.connection() as conn:
                conn.execute("SELECT ...")
        """

        class _ConnectionGuard:
            def __init__(self, pool):
                self.pool = pool

            def __enter__(self):
                self.conn = self.pool.get()
                return self.conn

            def __exit__(self, *args):
                self.pool.put(self.conn)
                return False

        return _ConnectionGuard(self)


_global_pool: "ConnectionPool | None" = None
_global_pool_lock = threading.Lock()


@contextmanager
def pool_conn(db_path: "str | None" = None):
    """Context manager yielding a pooled connection.

    Usage::

        with pool_conn() as conn:
            conn.execute("SELECT ...")

    The connection is automatically returned to the pool on exit.
    Commits only when the ``with`` block succeeds; rolls back on
    exception to respect the caller's rollback intent.
    """
    import sys as _sys
    pool = get_pool(db_path)
    conn = pool.get()
    try:
        yield conn
    except Exception:
        try:
            conn.rollback()
        except sqlite3.Error as e:
            logger.warning(f"Rollback failed during exception path: {e}")
        raise
    finally:
        if not _sys.exc_info()[0]:
            try:
                conn.commit()
            except sqlite3.Error as e:
                logger.warning(f"Commit failed in pool_conn finally: {e}")
        pool.put(conn)


def get_pool(db_path: "str | None" = None,
             max_connections: int = 5) -> ConnectionPool:
    """Return the global singleton ``ConnectionPool`` (lazy init).

    Args:
        db_path: Path to the database.  Falls back to ``DB_PATH``.
        max_connections: Maximum connections (used on first call only).

    Returns:
        The module-level ``ConnectionPool`` instance.
    """
    global _global_pool
    if _global_pool is None:
        with _global_pool_lock:
            if _global_pool is None:
                path = str(db_path or DB_PATH)
                Path(path).parent.mkdir(parents=True, exist_ok=True)
                _global_pool = ConnectionPool(path, max_connections)
    return _global_pool


# ══════════════════════════════════════════════════════════════════
# Maintenance — VACUUM / ANALYZE / OPTIMIZE / Stats
# ══════════════════════════════════════════════════════════════════

def _db_file_size_mb(path: str) -> float:
    """Return the size of the database file in MB (0 if missing)."""
    try:
        return round(Path(path).stat().st_size / (1024 * 1024), 2)
    except (OSError, FileNotFoundError):
        return 0.0


def vacuum_db(db_path: "str | None" = None) -> dict:
    """Run ``VACUUM`` to reclaim disk space.

    Args:
        db_path: Optional path override.

    Returns:
        ``{"before_mb": ..., "after_mb": ..., "reclaimed_mb": ...}``
    """
    path = str(db_path or DB_PATH)
    before = _db_file_size_mb(path)
    conn = get_conn(path)
    try:
        conn.execute("VACUUM")
    finally:
        conn.close()
    after = _db_file_size_mb(path)
    return {
        "before_mb": before,
        "after_mb": after,
        "reclaimed_mb": round(max(0, before - after), 2),
    }


def analyze_db(db_path: "str | None" = None) -> dict:
    """Run ``ANALYZE`` to update query-planning statistics.

    Args:
        db_path: Optional path override.

    Returns:
        ``{"analyzed": True}``
    """
    path = str(db_path or DB_PATH)
    conn = get_conn(path)
    try:
        conn.execute("ANALYZE")
    finally:
        conn.close()
    return {"analyzed": True}


def optimize_db(db_path: "str | None" = None) -> dict:
    """Run a full optimization cycle: ANALYZE → VACUUM → PRAGMA optimize.

    ``PRAGMA optimize`` performs a quick per-table ``ANALYZE``-equivalent
    pass on tables that need it, complementing the explicit ``ANALYZE``.

    Args:
        db_path: Optional path override.

    Returns:
        ``{"analyzed": True, "vacuumed": {...}, "optimized": True}``
    """
    path = str(db_path or DB_PATH)
    conn = get_conn(path)
    try:
        conn.execute("ANALYZE")
        conn.execute("PRAGMA optimize")
    finally:
        conn.close()
    vacuum_result = vacuum_db(path)
    return {
        "analyzed": True,
        "vacuumed": vacuum_result,
        "optimized": True,
    }


def db_stats(db_path: "str | None" = None) -> dict:
    """Collect database metadata and size statistics.

    Args:
        db_path: Optional path override.

    Returns:
        A dict with ``db_path``, ``file_size_mb``, ``tables`` (mapping
        table names to row counts), and ``wal_size_mb``.
    """
    path = str(db_path or DB_PATH)
    file_mb = _db_file_size_mb(path)

    # WAL file size
    wal_path = Path(path + "-wal")
    wal_mb = round(wal_path.stat().st_size / (1024 * 1024), 2) if wal_path.exists() else 0.0

    tables: dict = {}
    conn = get_conn(path)
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        for (name,) in rows:
            cnt = conn.execute(
                f"SELECT COUNT(*) FROM [{name}]"
            ).fetchone()[0]
            tables[name] = cnt

        # Memory status distribution
        status_rows = conn.execute(
            "SELECT COALESCE(memory_status, 'normal') as status, COUNT(*) as cnt "
            "FROM memories GROUP BY memory_status ORDER BY cnt DESC"
        ).fetchall()
        memory_status_counts = {r["status"]: r["cnt"] for r in status_rows}
    finally:
        conn.close()

    return {
        "db_path": path,
        "file_size_mb": file_mb,
        "wal_size_mb": wal_mb,
        "tables": tables,
        "memory_status_counts": memory_status_counts,
    }


# Auto-init flag: ``get_conn()`` / ``ConnectionPool._new_conn()``
# run ``init_db()`` exactly once on the first connection.
_auto_init_done = False

_auto_init_lock = threading.Lock()

# ── Archive DB (hot/cold separation, S3-02) ──

ARCHIVE_DB_PATH: Path = DB_PATH.parent / "archive.db"

ARCHIVE_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS archived_memories (
    id INTEGER PRIMARY KEY,
    content TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    level TEXT NOT NULL,
    owner TEXT NOT NULL DEFAULT '',
    agent_name TEXT NOT NULL DEFAULT '',
    subject TEXT NOT NULL DEFAULT '',
    project TEXT NOT NULL DEFAULT '',
    category TEXT NOT NULL DEFAULT 'general',
    summary TEXT NOT NULL DEFAULT '',
    occurred_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    supersedes INTEGER,
    trust_level REAL NOT NULL DEFAULT 1.0,
    access_count INTEGER NOT NULL DEFAULT 0,
    metadata TEXT NOT NULL DEFAULT '{}',
    arc_status TEXT,
    thread_id INTEGER DEFAULT NULL,
    agent_name_locked BOOLEAN NOT NULL DEFAULT 0,
    archived_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS archived_edges (
    id INTEGER PRIMARY KEY,
    source_id INTEGER NOT NULL,
    target_id INTEGER NOT NULL,
    relation_type TEXT NOT NULL DEFAULT 'refines',
    weight REAL NOT NULL DEFAULT 1.0,
    created_at TEXT NOT NULL,
    metadata TEXT NOT NULL DEFAULT '{}',
    archived_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_archived_memories_level ON archived_memories(level);
CREATE INDEX IF NOT EXISTS idx_archived_memories_agent ON archived_memories(agent_name);
CREATE INDEX IF NOT EXISTS idx_archived_edges_source ON archived_edges(source_id);
CREATE INDEX IF NOT EXISTS idx_archived_edges_target ON archived_edges(target_id);
"""


def init_archive_db(conn=None):
    """Create archive tables if they don't exist."""
    close = False
    if conn is None:
        conn = sqlite3.connect(str(ARCHIVE_DB_PATH), timeout=10)
        conn.row_factory = sqlite3.Row
        close = True
    try:
        conn.executescript(ARCHIVE_SCHEMA_SQL)
        conn.commit()
    finally:
        if close:
            conn.close()


def get_archive_db_path() -> Path:
    return ARCHIVE_DB_PATH


def archive_db_stats() -> dict:
    """Return stats for archive.db."""
    path = str(ARCHIVE_DB_PATH)
    if not Path(path).exists():
        return {"db_path": path, "exists": False, "file_size_mb": 0, "memories": 0, "edges": 0}
    file_mb = _db_file_size_mb(path)
    conn = sqlite3.connect(path, timeout=5)
    conn.row_factory = sqlite3.Row
    try:
        mem_count = conn.execute("SELECT COUNT(*) FROM archived_memories").fetchone()[0]
        edge_count = conn.execute("SELECT COUNT(*) FROM archived_edges").fetchone()[0]
        level_dist = {}
        for row in conn.execute(
            "SELECT level, COUNT(*) as cnt FROM archived_memories GROUP BY level ORDER BY cnt DESC"
        ).fetchall():
            level_dist[row["level"]] = row["cnt"]
        return {
            "db_path": path,
            "exists": True,
            "file_size_mb": file_mb,
            "memories": mem_count,
            "edges": edge_count,
            "level_distribution": level_dist,
        }
    finally:
        conn.close()


def vacuum_archive_db() -> dict:
    """Run VACUUM on archive.db. Returns before/after sizes."""
    path = str(ARCHIVE_DB_PATH)
    if not Path(path).exists():
        return {"before_mb": 0, "after_mb": 0, "reclaimed_mb": 0, "note": "archive.db does not exist"}
    before = _db_file_size_mb(path)
    conn = sqlite3.connect(path, timeout=10)
    try:
        conn.execute("VACUUM")
    finally:
        conn.close()
    after = _db_file_size_mb(path)
    return {
        "before_mb": before,
        "after_mb": after,
        "reclaimed_mb": round(max(0, before - after), 2),
    }
