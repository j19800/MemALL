"""
Test Suite — Federation Health
===============================
Tests federation_health().
"""

import os
import sys
import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


def _create_family_db(path: str):
    """Create a minimal family.db with all required tables."""
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("""CREATE TABLE IF NOT EXISTS shared_memories (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        original_id INTEGER NOT NULL,
        source_agent TEXT NOT NULL,
        source_db TEXT NOT NULL,
        content TEXT NOT NULL,
        category TEXT DEFAULT '',
        level TEXT DEFAULT 'P2',
        owner TEXT DEFAULT '',
        published_at TEXT NOT NULL,
        conflict_status TEXT DEFAULT 'none'
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS conflicts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        status TEXT DEFAULT 'open'
    )""")
    conn.commit()
    conn.close()


def _cleanup_family_db(path: str):
    p = Path(path)
    p.unlink(missing_ok=True)
    for ext in ["-wal", "-shm"]:
        (Path(path + ext)).unlink(missing_ok=True)


def test_federation_health_empty_db():
    """Test: federation_health returns structure on empty family DB."""
    from tests.test_helpers import init_temp_db, cleanup_temp_db
    from memall.federation.health import federation_health

    db_path, patcher = init_temp_db()
    try:
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
            tmp_family = tmp.name
        _create_family_db(tmp_family)

        with patch("memall.federation.health.get_family_db_path",
                   return_value=Path(tmp_family)):
            result = federation_health(detail=False)

        _cleanup_family_db(tmp_family)

        assert isinstance(result, dict), f"Expected dict, got {type(result)}"
        assert "total" in result, f"Missing 'total': {result}"
        assert result["total"] == 0, f"Expected 0, got {result['total']}"
        print("  PASS test_federation_health_empty_db")
    finally:
        cleanup_temp_db(db_path, patcher)


def test_federation_health_structure():
    """Test: federation_health returns expected keys."""
    from tests.test_helpers import init_temp_db, cleanup_temp_db
    from memall.federation.health import federation_health

    db_path, patcher = init_temp_db()
    try:
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
            tmp_family = tmp.name
        _create_family_db(tmp_family)

        # Insert one shared memory for realistic data
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        conn = sqlite3.connect(tmp_family)
        conn.row_factory = sqlite3.Row
        conn.execute(
            "INSERT INTO shared_memories (original_id, source_agent, source_db, content, published_at) VALUES (1, 'agent1', 'local', 'test memory', ?)",
            (now,),
        )
        conn.commit()
        conn.close()

        with patch("memall.federation.health.get_family_db_path",
                   return_value=Path(tmp_family)):
            result = federation_health(detail=False)

        _cleanup_family_db(tmp_family)

        assert isinstance(result, dict)
        assert "total" in result
        assert "agents" in result
        assert "conflict_status" in result
        assert "trend" in result
        assert result["total"] >= 1
        print("  PASS test_federation_health_structure")
    finally:
        cleanup_temp_db(db_path, patcher)


# ── Runner ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 60)
    print("MemALL Federation Health Tests")
    print("=" * 60)
    passed = 0
    failed = 0

    tests = [
        ("test_federation_health_empty_db", test_federation_health_empty_db),
        ("test_federation_health_structure", test_federation_health_structure),
    ]

    for name, func in tests:
        try:
            func()
            passed += 1
        except Exception as e:
            print(f"  FAIL {name}: {e}")
            import traceback
            traceback.print_exc()
            failed += 1

    print(f"\nResults: {passed} passed, {failed} failed")
    if failed:
        sys.exit(1)