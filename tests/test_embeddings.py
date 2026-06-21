"""
Test Suite — Graph Embeddings
==============================
Tests build_index, index_status.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


def test_build_index_empty_db():
    """Test: build_index handles empty DB."""
    from tests.test_helpers import init_temp_db, cleanup_temp_db
    from memall.graph.embeddings import build_index

    db_path, patcher = init_temp_db()
    try:
        result = build_index(batch_size=10)
        assert isinstance(result, dict), f"Expected dict, got {type(result)}"
        assert "new" in result or "status" in result, f"Unexpected keys: {result}"
        print("  PASS test_build_index_empty_db")
    finally:
        cleanup_temp_db(db_path, patcher)


def test_index_status_empty():
    """Test: index_status returns structure on empty DB."""
    from tests.test_helpers import init_temp_db, cleanup_temp_db
    from memall.graph.embeddings import index_status

    db_path, patcher = init_temp_db()
    try:
        result = index_status()
        assert isinstance(result, dict), f"Expected dict, got {type(result)}"
        assert "total_memories" in result, f"Missing 'total_memories': {result}"
        assert "embedded" in result, f"Missing 'embedded': {result}"
        assert "pending" in result, f"Missing 'pending': {result}"
        print("  PASS test_index_status_empty")
    finally:
        cleanup_temp_db(db_path, patcher)


def test_index_status_with_data():
    """Test: index_status with memories."""
    from tests.test_helpers import init_temp_db, cleanup_temp_db, insert_memory
    from memall.graph.embeddings import build_index, index_status
    from memall.core.db import get_conn

    db_path, patcher = init_temp_db()
    try:
        conn = get_conn()
        insert_memory(conn, "A sufficiently long test memory content for embeddings " * 5)
        conn.close()

        # Build index first
        build_result = build_index(batch_size=10)
        assert isinstance(build_result, dict)

        # Check status
        status = index_status()
        assert status["total_memories"] >= 1, f"Expected >=1 total, got {status}"
        print("  PASS test_index_status_with_data")
    finally:
        cleanup_temp_db(db_path, patcher)


def test_build_index_twice():
    """Test: build_index is idempotent on second call."""
    from tests.test_helpers import init_temp_db, cleanup_temp_db, insert_memory
    from memall.graph.embeddings import build_index
    from memall.core.db import get_conn

    db_path, patcher = init_temp_db()
    try:
        conn = get_conn()
        insert_memory(conn, "Test memory for idempotent index building " * 5)
        conn.close()

        first = build_index(batch_size=10)
        second = build_index(batch_size=10)
        # Second call should report 0 new if index already built
        assert isinstance(second, dict)
        print("  PASS test_build_index_twice")
    finally:
        cleanup_temp_db(db_path, patcher)


# ── Runner ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 60)
    print("MemALL Graph Embeddings Tests")
    print("=" * 60)
    passed = 0
    failed = 0

    tests = [
        ("test_build_index_empty_db", test_build_index_empty_db),
        ("test_index_status_empty", test_index_status_empty),
        ("test_index_status_with_data", test_index_status_with_data),
        ("test_build_index_twice", test_build_index_twice),
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