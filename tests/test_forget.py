"""
Test Suite — Pipeline Forget
=============================
Tests forget_expired, forget_low_value, forget_stats.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


def test_forget_expired_empty_db():
    """Test: forget_expired returns 0 deleted on empty DB."""
    from tests.test_helpers import init_temp_db, cleanup_temp_db
    from memall.pipeline.forget import forget_expired

    db_path, patcher = init_temp_db()
    try:
        result = forget_expired(days=90)
        assert isinstance(result, dict), f"Expected dict, got {type(result)}"
        assert result["deleted_memories"] == 0, f"Expected 0, got {result['deleted_memories']}"
        assert result["deleted_edges"] == 0, f"Expected 0, got {result['deleted_edges']}"
        print("  PASS test_forget_expired_empty_db")
    finally:
        cleanup_temp_db(db_path, patcher)


def test_forget_expired_deletes_old():
    """Test: forget_expired deletes old memories."""
    from tests.test_helpers import init_temp_db, cleanup_temp_db, insert_memory
    from memall.pipeline.forget import forget_expired
    from memall.core.db import get_conn
    from datetime import datetime, timezone, timedelta

    db_path, patcher = init_temp_db()
    try:
        conn = get_conn()
        old = (datetime.now(timezone.utc) - timedelta(days=200)).isoformat()
        recent = datetime.now(timezone.utc).isoformat()
        insert_memory(conn, "Old expired memory", created_at=old)
        insert_memory(conn, "Recent memory", created_at=recent)
        conn.close()

        result = forget_expired(days=90)
        assert result["deleted_memories"] == 1, f"Expected 1 deleted, got {result}"
        print("  PASS test_forget_expired_deletes_old")
    finally:
        cleanup_temp_db(db_path, patcher)


def test_forget_expired_agent_filter():
    """Test: forget_expired respects agent filter."""
    from tests.test_helpers import init_temp_db, cleanup_temp_db, insert_memory
    from memall.pipeline.forget import forget_expired
    from memall.core.db import get_conn
    from datetime import datetime, timezone, timedelta

    db_path, patcher = init_temp_db()
    try:
        conn = get_conn()
        old = (datetime.now(timezone.utc) - timedelta(days=200)).isoformat()
        insert_memory(conn, "Agent A old memory", agent_name="agent_a", created_at=old)
        insert_memory(conn, "Agent B old memory", agent_name="agent_b", created_at=old)
        conn.close()

        result = forget_expired(days=90, agent_name="agent_a")
        assert result["deleted_memories"] == 1, f"Expected 1 for agent_a, got {result}"
        print("  PASS test_forget_expired_agent_filter")
    finally:
        cleanup_temp_db(db_path, patcher)


def test_forget_low_value():
    """Test: forget_low_value deletes short, isolated, old memories."""
    from tests.test_helpers import init_temp_db, cleanup_temp_db, insert_memory
    from memall.pipeline.forget import forget_low_value
    from memall.core.db import get_conn
    from datetime import datetime, timezone, timedelta

    db_path, patcher = init_temp_db()
    try:
        conn = get_conn()
        old = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        pid = insert_memory(conn, "Short old isolated memory with no edges",
                            created_at=old)
        # Add a reference so the memory is NOT a low-value candidate
        long_mid = insert_memory(conn, "This is a much longer memory with enough content to be valuable",
                                  created_at=old)
        conn.execute(
            "INSERT INTO edges (source_id, target_id, relation_type, weight, created_at) VALUES (?, ?, 'refines', 1.0, ?)",
            (long_mid, pid, old),
        )
        conn.commit()
        conn.close()

        result = forget_low_value()
        assert isinstance(result, dict), f"Expected dict, got {type(result)}"
        assert "deleted_memories" in result
        assert "candidate_count" in result
        print("  PASS test_forget_low_value")
    finally:
        cleanup_temp_db(db_path, patcher)


def test_forget_stats_empty():
    """Test: forget_stats returns structure with empty DB."""
    from tests.test_helpers import init_temp_db, cleanup_temp_db
    from memall.pipeline.forget import forget_stats

    db_path, patcher = init_temp_db()
    try:
        result = forget_stats()
        assert isinstance(result, dict), f"Expected dict, got {type(result)}"
        assert result["total_memories"] == 0, f"Expected 0, got {result['total_memories']}"
        required = ["total_memories", "total_edges", "expired_count", "low_value_count",
                     "orphaned_edge_count", "avg_content_length", "size_estimate_mb"]
        for key in required:
            assert key in result, f"Missing key '{key}' in {result}"
        print("  PASS test_forget_stats_empty")
    finally:
        cleanup_temp_db(db_path, patcher)


def test_forget_stats_with_data():
    """Test: forget_stats computes correct values with data."""
    from tests.test_helpers import init_temp_db, cleanup_temp_db, insert_memory
    from memall.pipeline.forget import forget_stats
    from memall.core.db import get_conn
    from datetime import datetime, timezone, timedelta

    db_path, patcher = init_temp_db()
    try:
        conn = get_conn()
        old = (datetime.now(timezone.utc) - timedelta(days=200)).isoformat()
        recent = datetime.now(timezone.utc).isoformat()
        insert_memory(conn, "Old memory that should be expired", created_at=old)
        insert_memory(conn, "Recent memory", created_at=recent)
        conn.close()

        result = forget_stats()
        assert result["total_memories"] == 2
        assert result["expired_count"] == 1, f"Expected 1 expired, got {result['expired_count']}"
        print("  PASS test_forget_stats_with_data")
    finally:
        cleanup_temp_db(db_path, patcher)


# ── Runner ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 60)
    print("MemALL Pipeline Forget Tests")
    print("=" * 60)
    passed = 0
    failed = 0

    tests = [
        ("test_forget_expired_empty_db", test_forget_expired_empty_db),
        ("test_forget_expired_deletes_old", test_forget_expired_deletes_old),
        ("test_forget_expired_agent_filter", test_forget_expired_agent_filter),
        ("test_forget_low_value", test_forget_low_value),
        ("test_forget_stats_empty", test_forget_stats_empty),
        ("test_forget_stats_with_data", test_forget_stats_with_data),
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