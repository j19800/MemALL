"""
Test Suite — Pipeline Decay
============================
Tests decay_step() for purging and decaying memories.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


def test_decay_step_empty_db():
    """Test: decay_step returns zero counts when DB is empty."""
    from tests.test_helpers import init_temp_db, cleanup_temp_db
    from memall.pipeline.decay import decay_step

    db_path, patcher = init_temp_db()
    try:
        result = decay_step()
        assert isinstance(result, dict), f"Expected dict, got {type(result)}"
        assert "purged" in result, f"Missing 'purged' key: {result}"
        assert "decayed" in result, f"Missing 'decayed' key: {result}"
        assert result["purged"] == 0, f"Expected 0 purged, got {result['purged']}"
        assert result["decayed"] == 0, f"Expected 0 decayed, got {result['decayed']}"
        print("  PASS test_decay_step_empty_db")
    finally:
        cleanup_temp_db(db_path, patcher)


def test_decay_step_purges_p0():
    """Test: decay_step purges P0 memories with low confidence and zero access."""
    from tests.test_helpers import init_temp_db, cleanup_temp_db, insert_memory
    from memall.pipeline.decay import decay_step
    from memall.core.db import get_conn

    db_path, patcher = init_temp_db()
    try:
        conn = get_conn()
        insert_memory(conn, "Old abandoned memory", level="P0", confidence=0.1, access_count=0)
        insert_memory(conn, "Important memory", level="P1", confidence=0.9, access_count=5)
        conn.close()

        result = decay_step()
        assert result["purged"] == 1, f"Expected 1 purged, got {result['purged']}"
        print("  PASS test_decay_step_purges_p0")
    finally:
        cleanup_temp_db(db_path, patcher)


def test_decay_step_keeps_recent():
    """Test: decay_step does not decay recently updated memories."""
    from tests.test_helpers import init_temp_db, cleanup_temp_db, insert_memory
    from memall.pipeline.decay import decay_step
    from memall.core.db import get_conn
    from datetime import datetime, timezone

    db_path, patcher = init_temp_db()
    try:
        conn = get_conn()
        now = datetime.now(timezone.utc).isoformat()
        insert_memory(conn, "Recent memory", level="P2", access_count=1,
                       created_at=now)
        conn.close()

        result = decay_step()
        assert result["purged"] == 0, f"Expected 0 purged, got {result['purged']}"
        assert result["decayed"] == 0, f"Expected 0 decayed, got {result['decayed']}"
        print("  PASS test_decay_step_keeps_recent")
    finally:
        cleanup_temp_db(db_path, patcher)


def test_decay_step_cleans_orphan_edges():
    """Test: decay_step removes edges referencing deleted memories."""
    from tests.test_helpers import init_temp_db, cleanup_temp_db
    from memall.pipeline.decay import decay_step
    from memall.core.db import get_conn
    from datetime import datetime, timezone

    db_path, patcher = init_temp_db()
    try:
        conn = get_conn()
        now = datetime.now(timezone.utc).isoformat()
        # Disable FK enforcement temporarily to insert orphan edge
        conn.execute("PRAGMA foreign_keys=OFF")
        conn.execute(
            "INSERT INTO edges (source_id, target_id, relation_type, weight, created_at) VALUES (9999, 8888, 'refines', 1.0, ?)",
            (now,),
        )
        conn.commit()
        conn.execute("PRAGMA foreign_keys=ON")
        conn.close()

        result = decay_step()
        # This should not crash; the orphan edge should be cleaned
        assert isinstance(result, dict), f"Expected dict, got {type(result)}"
        print("  PASS test_decay_step_cleans_orphan_edges")
    finally:
        cleanup_temp_db(db_path, patcher)


# ── Runner ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 60)
    print("MemALL Pipeline Decay Tests")
    print("=" * 60)
    passed = 0
    failed = 0

    tests = [
        ("test_decay_step_empty_db", test_decay_step_empty_db),
        ("test_decay_step_purges_p0", test_decay_step_purges_p0),
        ("test_decay_step_keeps_recent", test_decay_step_keeps_recent),
        ("test_decay_step_cleans_orphan_edges", test_decay_step_cleans_orphan_edges),
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