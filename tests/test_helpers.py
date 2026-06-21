"""
Common test helpers for MemALL test suite.

Provides temporary database isolation to avoid polluting the real DB.
"""

import os
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from memall.core.db import get_conn


def _unique_agent() -> str:
    """Return a unique agent name for test isolation."""
    return f"test_agent_{int(time.time() * 1000000)}"


def init_temp_db():
    """Initialize a temporary database and return (db_path, patcher).

    Usage::

        db_path, patcher = init_temp_db()
        try:
            # ... test code ...
        finally:
            cleanup_temp_db(db_path, patcher)
    """
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    db_path_obj = Path(tmp.name)
    patcher = patch("memall.core.db.DB_PATH", db_path_obj)
    patcher.start()

    from memall.core.db import init_db as _init_db

    _init_db(migrate=True)
    return db_path_obj, patcher


def cleanup_temp_db(db_path_obj: Path, patcher):
    """Clean up the temporary database and restore DB_PATH."""
    patcher.stop()
    try:
        if db_path_obj.exists():
            db_path_obj.unlink()
        for ext in ["-wal", "-shm"]:
            p = db_path_obj.parent / (db_path_obj.name + ext)
            if p.exists():
                p.unlink()
    except Exception:
        pass


def insert_memory(
    conn,
    content: str,
    agent_name: str = "test_agent",
    category: str = "general",
    level: str = "P2",
    created_at: str = None,
    occurred_at: str = None,
    summary: str = "",
    confidence: float = 0.5,
    access_count: int = 0,
    visibility: str = "private",
) -> int:
    """Insert a test memory into the database. Returns the memory id."""
    from datetime import datetime, timezone

    now = (created_at or datetime.now(timezone.utc).isoformat())
    occ = (occurred_at or now)
    import hashlib
    ch = hashlib.sha256(content.encode("utf-8")).hexdigest()
    conn.execute(
        """INSERT OR IGNORE INTO memories
           (content, content_hash, level, owner, agent_name, category,
            summary, occurred_at, created_at, updated_at,
            confidence, access_count, visibility, metadata)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (content, ch, level, "", agent_name, category,
         summary, occ, now, now,
         confidence, access_count, visibility, "{}"),
    )
    conn.commit()
    row = conn.execute(
        "SELECT id FROM memories WHERE content_hash = ?", (ch,)
    ).fetchone()
    return row["id"] if row else -1