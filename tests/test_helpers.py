"""
Common test helpers for MemALL test suite.

Provides temporary database isolation to avoid polluting the real DB.
"""

import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from memall.core.db import get_conn


def _unique_agent() -> str:
    """Return a unique agent name for test isolation."""
    return f"test_agent_{int(time.time() * 1000000)}"


def init_temp_db():
    """Set up a temporary database for test isolation.

    Returns (db_path, original_db_path) for cleanup.
    """
    from memall.core import db as core_db

    tmp_dir = Path(tempfile.mkdtemp()) / ".memall"
    tmp_dir.mkdir(parents=True)
    db_path = tmp_dir / "data.db"

    original_db_path = core_db.DB_PATH
    core_db.DB_PATH = db_path
    core_db._global_pool = None

    from memall.core.db import init_db
    init_db(migrate=True)
    return (db_path, original_db_path)


def cleanup_temp_db(unused_db_path=None, original_db_path=None):
    """Restore original DB_PATH after test."""
    restore_path = original_db_path or unused_db_path
    if restore_path:
        from memall.core import db as core_db
        core_db.DB_PATH = restore_path
        core_db._global_pool = None


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