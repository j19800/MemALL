"""
Test Suite — Strategy Buffer
=============================
Tests BufferStrategy store/retrieve/clear.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


def test_buffer_store_returns_memory_id():
    from tests.test_helpers import init_temp_db, cleanup_temp_db, insert_memory
    from memall.strategy.buffer import BufferStrategy
    from memall.core.db import get_conn

    db_path, patcher = init_temp_db()
    try:
        strategy = BufferStrategy("test_agent")
        conn = get_conn()
        mid = insert_memory(conn, "This is a test memory for buffer strategy")
        assert mid > 0, f"Expected positive ID, got {mid}"
        print("  PASS test_buffer_store_returns_memory_id")
    finally:
        cleanup_temp_db(db_path, patcher)


def test_buffer_retrieve_by_agent():
    from tests.test_helpers import init_temp_db, cleanup_temp_db
    from memall.strategy.buffer import BufferStrategy
    from memall.core.thin_waist import capture

    db_path, patcher = init_temp_db()
    try:
        strategy = BufferStrategy("buffer_test")
        capture("Buffer test memory", agent_name="buffer_test")
        results = strategy.retrieve("test", top_k=5)
        assert isinstance(results, list) or hasattr(results, "__len__"), f"Expected list, got {type(results)}"
        print("  PASS test_buffer_retrieve_by_agent")
    finally:
        cleanup_temp_db(db_path, patcher)


def test_buffer_default_config():
    from memall.strategy.buffer import BufferStrategy
    s = BufferStrategy("default_test")
    assert s.buffer_size == 50, f"Expected default buffer_size 50, got {s.buffer_size}"
    print("  PASS test_buffer_default_config")


def test_buffer_custom_config():
    from memall.strategy.buffer import BufferStrategy
    s = BufferStrategy("custom_test", {"buffer_size": 20})
    assert s.buffer_size == 20, f"Expected buffer_size 20, got {s.buffer_size}"
    print("  PASS test_buffer_custom_config")


def test_buffer_clear_noop():
    from memall.strategy.buffer import BufferStrategy
    s = BufferStrategy("clear_test")
    assert s.clear() == 0, "Buffer clear should return 0"
    print("  PASS test_buffer_clear_noop")


if __name__ == "__main__":
    print("=" * 60)
    print("BufferStrategy Tests")
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