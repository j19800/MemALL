"""
Test Suite — thin_waist quality gate and edge cases
=====================================================
Tests _score_quality, store_batch, connect edge cases, update L5 status.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


def test_quality_gate_rejects_very_short():
    """Content under 15 chars should be rejected."""
    from tests.test_helpers import init_temp_db, cleanup_temp_db
    from memall.core.thin_waist import capture

    db_path, patcher = init_temp_db()
    try:
        try:
            capture("Hi", agent_name="qtest")
            assert False, "Expected ValueError"
        except ValueError:
            print("  PASS test_quality_gate_rejects_very_short")
    finally:
        cleanup_temp_db(db_path, patcher)


def test_quality_gate_accepts_with_reasoning():
    """L6 content with reasoning markers should pass."""
    from tests.test_helpers import init_temp_db, cleanup_temp_db
    from memall.core.thin_waist import capture

    db_path, patcher = init_temp_db()
    try:
        mid = capture(
            "经过分析和比较，我们决定采用 FastAPI 而不是 Django，因为 FastAPI 的异步支持更好，性能更高。这是我们的技术选型结论。",
            agent_name="qtest", level="L6",
        )
        assert mid > 0, f"Expected positive ID, got {mid}"
        print("  PASS test_quality_gate_accepts_with_reasoning")
    finally:
        cleanup_temp_db(db_path, patcher)


def test_store_batch_basic():
    """store_batch should store multiple items."""
    from tests.test_helpers import init_temp_db, cleanup_temp_db
    from memall.core.thin_waist import store_batch

    db_path, patcher = init_temp_db()
    try:
        items = [
            {"content": "Batch item one with enough content for quality gate.", "agent_name": "batch_test"},
            {"content": "Batch item two with enough content for quality gate.", "agent_name": "batch_test"},
        ]
        result = store_batch(items)
        assert isinstance(result, dict)
        assert "ids" in result or "count" in result or "results" in result
        print("  PASS test_store_batch_basic")
    finally:
        cleanup_temp_db(db_path, patcher)


def test_connect_invalid_relation():
    """Invalid relation_type should raise ValueError."""
    from tests.test_helpers import init_temp_db, cleanup_temp_db, insert_memory
    from memall.core.thin_waist import connect
    from memall.core.db import get_conn

    db_path, patcher = init_temp_db()
    try:
        conn = get_conn()
        m1 = insert_memory(conn, "Source for invalid relation.", agent_name="edge_test")
        m2 = insert_memory(conn, "Target for invalid relation.", agent_name="edge_test")
        conn.close()

        try:
            connect(m1, m2, relation_type="invalid_relation_type")
            assert False, "Expected ValueError for invalid relation type"
        except ValueError:
            print("  PASS test_connect_invalid_relation")
    finally:
        cleanup_temp_db(db_path, patcher)


def test_update_l5_status():
    """Update should validate L5 status values."""
    from tests.test_helpers import init_temp_db, cleanup_temp_db
    from memall.core.thin_waist import update
    from memall.core.db import get_conn, pool_conn
    from datetime import datetime, timezone
    import json

    db_path, patcher = init_temp_db()
    try:
        conn = get_conn()
        now = datetime.now(timezone.utc).isoformat()
        meta = json.dumps({"status": "active"})
        conn.execute(
            "INSERT INTO memories (content, content_hash, level, agent_name, metadata, occurred_at, created_at, updated_at) "
            "VALUES (?, ?, 'L5', 'upd_test', ?, ?, ?, ?)",
            ("L5 update status test memory with enough content for testing.", "hash_l5_test_001", meta, now, now, now),
        )
        mid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.commit()
        conn.close()

        result = update(mid, metadata=json.dumps({"status": "done"}))
        assert result is True

        with pool_conn() as conn:
            row = conn.execute("SELECT metadata FROM memories WHERE id = ?", (mid,)).fetchone()
            meta = json.loads(row["metadata"])
            assert meta.get("status") == "done", f"Expected done, got {meta.get('status')}"
        print("  PASS test_update_l5_status")
    finally:
        cleanup_temp_db(db_path, patcher)


def test_capture_with_thread_id():
    """Capture with thread_id should work."""
    from tests.test_helpers import init_temp_db, cleanup_temp_db
    from memall.core.thin_waist import capture
    from memall.core.db import pool_conn

    db_path, patcher = init_temp_db()
    try:
        mid = capture("Thread test memory with enough content for quality gate validation.", agent_name="thread_test")
        mid2 = capture("Thread child memory with thread reference for testing purposes.", agent_name="thread_test", thread_id=mid)
        assert mid2 > 0
        if mid2:
            with pool_conn() as conn:
                row = conn.execute("SELECT thread_id FROM memories WHERE id = ?", (mid2,)).fetchone()
                assert row["thread_id"] == mid
        print("  PASS test_capture_with_thread_id")
    finally:
        cleanup_temp_db(db_path, patcher)


def test_update_nonexistent():
    """Update nonexistent memory should return False."""
    from tests.test_helpers import init_temp_db, cleanup_temp_db
    from memall.core.thin_waist import update

    db_path, patcher = init_temp_db()
    try:
        result = update(99999, category="test")
        assert result is False, f"Expected False for nonexistent ID, got {result}"
        print("  PASS test_update_nonexistent")
    finally:
        cleanup_temp_db(db_path, patcher)


def test_capture_smart_store_equivalent():
    """smart_store and capture should both work."""
    from tests.test_helpers import init_temp_db, cleanup_temp_db
    from memall.core.thin_waist import smart_store, capture

    db_path, patcher = init_temp_db()
    try:
        r1 = smart_store("Smart store vs capture comparison test content.", agent_name="cmp_test")
        r2 = capture("Smart store vs capture comparison test content.", agent_name="cmp_test")
        assert isinstance(r1, dict)
        assert isinstance(r2, int) or isinstance(r2, dict)
        print("  PASS test_capture_smart_store_equivalent")
    finally:
        cleanup_temp_db(db_path, patcher)


def test_normalize_single_cjk():
    """Single CJK character should be rejected."""
    from memall.core.thin_waist import normalize_agent_name
    assert normalize_agent_name("我") == "system"
    assert normalize_agent_name("会") == "system"
    assert normalize_agent_name("你") == "system"
    print("  PASS test_normalize_single_cjk")


def test_normalize_date_pattern():
    """Date-like agent names should be rejected."""
    from memall.core.thin_waist import normalize_agent_name
    assert normalize_agent_name("2024-01-01") == "system"
    assert normalize_agent_name("1234567890") == "system"
    print("  PASS test_normalize_date_pattern")


if __name__ == "__main__":
    print("=" * 60)
    print("thin_waist Edge Case Tests")
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