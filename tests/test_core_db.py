"""
Test Suite — Core DB utilities
================================
Tests pool_conn, content_hash, DB_PATH, db_stats, init_db.
"""

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


def test_content_hash():
    """content_hash should return consistent SHA-256 hex digests."""
    from memall.core.db import content_hash

    h1 = content_hash("hello world")
    h2 = content_hash("hello world")
    h3 = content_hash("different")
    assert h1 == h2, "Same content should produce same hash"
    assert h1 != h3, "Different content should produce different hash"
    assert len(h1) == 64, "SHA-256 should be 64 hex chars"
    assert isinstance(h1, str)
    print("  PASS test_content_hash")


def test_content_hash_unicode():
    """content_hash should handle Unicode."""
    from memall.core.db import content_hash

    h = content_hash("记忆测试 — 中文内容")
    assert len(h) == 64
    assert isinstance(h, str)
    print("  PASS test_content_hash_unicode")


def test_pool_conn_basic():
    """pool_conn() should yield a working connection."""
    from tests.test_helpers import init_temp_db, cleanup_temp_db
    from memall.core.db import pool_conn

    db_path, patcher = init_temp_db()
    try:
        with pool_conn() as conn:
            row = conn.execute("SELECT 1 as val").fetchone()
            assert row["val"] == 1
        print("  PASS test_pool_conn_basic")
    finally:
        cleanup_temp_db(db_path, patcher)


def test_db_stats():
    """db_stats() should return database statistics."""
    from tests.test_helpers import init_temp_db, cleanup_temp_db
    from memall.core.db import db_stats, get_conn
    from datetime import datetime, timezone

    db_path, patcher = init_temp_db()
    try:
        conn = get_conn()
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "INSERT INTO memories (content, content_hash, level, agent_name, occurred_at, created_at, updated_at) "
            "VALUES (?, ?, 'L4', 'stat_test', ?, ?, ?)",
            ("Stats test memory.", "hash_stats_001", now, now, now),
        )
        conn.commit()
        conn.close()

        stats = db_stats()
        assert isinstance(stats, dict)
        assert "tables" in stats, f"Expected tables in stats, got {list(stats.keys())}"
        assert "file_size_mb" in stats
        print("  PASS test_db_stats")
    finally:
        cleanup_temp_db(db_path, patcher)


def test_ensure_missing_columns():
    """_ensure_missing_columns should add missing columns."""
    from tests.test_helpers import init_temp_db, cleanup_temp_db
    from memall.core.db import _ensure_missing_columns, get_conn

    db_path, patcher = init_temp_db()
    try:
        conn = get_conn()
        # Check that primary_layer exists (added by _ensure_missing_columns)
        cols = [r["name"] for r in conn.execute("PRAGMA table_info(memories)").fetchall()]
        assert "memory_status" in cols, f"Expected memory_status in cols"
        assert "accumulate_key" in cols, f"Expected accumulate_key in cols"
        # _ensure_missing_columns is idempotent
        _ensure_missing_columns(conn)
        print("  PASS test_ensure_missing_columns")
    finally:
        cleanup_temp_db(db_path, patcher)


def test_db_path_resolution():
    """DB_PATH should be a Path object."""
    from memall.core.db import DB_PATH
    assert isinstance(DB_PATH, Path), f"Expected Path, got {type(DB_PATH)}"
    assert ".memall" in str(DB_PATH), f"Expected .memall in path, got {DB_PATH}"
    print("  PASS test_db_path_resolution")


if __name__ == "__main__":
    print("=" * 60)
    print("Core DB Tests")
    print("=" * 60)
    passed = 0
    failed = 0
    for name in sorted(dir()):
        if name.startswith("test_"):
            try:
                globals()[name]()
                passed += 1
            except Exception as e:
                print(f"  FAIL {name}: {e}")
                import traceback
                traceback.print_exc()
                failed += 1
    print(f"\nResults: {passed} passed, {failed} failed")
    if failed:
        sys.exit(1)