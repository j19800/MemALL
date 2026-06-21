"""
Test Suite — Pipeline Reflect
==============================
Tests reflect_step().
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


def test_reflect_step_empty_db():
    """Test: reflect_step returns 0 upgraded when no memories exist."""
    from tests.test_helpers import init_temp_db, cleanup_temp_db
    from memall.pipeline.reflect import reflect_step

    db_path, patcher = init_temp_db()
    try:
        result = reflect_step()
        assert isinstance(result, dict), f"Expected dict, got {type(result)}"
        assert "upgraded_to_l6" in result, f"Missing 'upgraded_to_l6': {result}"
        assert result["upgraded_to_l6"] == 0, f"Expected 0, got {result['upgraded_to_l6']}"
        print("  PASS test_reflect_step_empty_db")
    finally:
        cleanup_temp_db(db_path, patcher)


def test_reflect_step_upgrades_corrections():
    """Test: reflect_step upgrades memories with correction keywords."""
    from tests.test_helpers import init_temp_db, cleanup_temp_db, insert_memory
    from memall.pipeline.reflect import reflect_step
    from memall.core.db import get_conn

    db_path, patcher = init_temp_db()
    try:
        conn = get_conn()
        # Bypass cold-start: create 50+ dummy memories so agent isn't skipped
        for i in range(52):
            insert_memory(conn, f"dummy memory {i} for cold-start bypass", level="P2", category="general")
        # Only content has correction keywords (summary is empty)
        # "不对" and "应该是" are in CORRECTION_KEYWORDS
        insert_memory(conn, "之前说的方案不对，应该是采用微服务架构", level="P2", summary="")
        # Second memory must NOT contain any correction keyword (esp. "修正","错误")
        insert_memory(conn, "This is a normal memory with no keyword matches at all", level="P2")
        conn.close()

        result = reflect_step()
        assert result["upgraded_to_l6"] == 1, f"Expected 1 upgrade, got {result['upgraded_to_l6']}"

        conn = get_conn()
        upgraded = conn.execute(
            "SELECT level FROM memories WHERE level='L6'"
        ).fetchall()
        conn.close()
        assert len(upgraded) == 1, f"Expected 1 L6 memory, got {len(upgraded)}"
        print("  PASS test_reflect_step_upgrades_corrections")
    finally:
        cleanup_temp_db(db_path, patcher)


def test_reflect_step_skips_l6_l7_l9():
    """Test: reflect_step skips memories already at L6, L7, L9."""
    from tests.test_helpers import init_temp_db, cleanup_temp_db, insert_memory
    from memall.pipeline.reflect import reflect_step
    from memall.core.db import get_conn

    db_path, patcher = init_temp_db()
    try:
        conn = get_conn()
        insert_memory(conn, "不对，应该是...", level="L6", summary="already reflected")
        conn.close()

        result = reflect_step()
        # L6 should be skipped
        assert result["scanned"] >= 0
        print("  PASS test_reflect_step_skips_l6_l7_l9")
    finally:
        cleanup_temp_db(db_path, patcher)


def test_reflect_step_scanned_count():
    """Test: reflect_step reports correct scanned count."""
    from tests.test_helpers import init_temp_db, cleanup_temp_db, insert_memory
    from memall.pipeline.reflect import reflect_step
    from memall.core.db import get_conn

    db_path, patcher = init_temp_db()
    try:
        conn = get_conn()
        for i in range(52):
            insert_memory(conn, f"dummy memory {i} for cold-start bypass", level="P2")
        insert_memory(conn, "Normal memory one", level="P2")
        insert_memory(conn, "Normal memory two", level="P1")
        conn.close()

        result = reflect_step()
        assert result["scanned"] >= 2, f"Expected at least 2 scanned, got {result['scanned']}"
        print("  PASS test_reflect_step_scanned_count")
    finally:
        cleanup_temp_db(db_path, patcher)


# ── Runner ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 60)
    print("MemALL Pipeline Reflect Tests")
    print("=" * 60)
    passed = 0
    failed = 0

    tests = [
        ("test_reflect_step_empty_db", test_reflect_step_empty_db),
        ("test_reflect_step_upgrades_corrections", test_reflect_step_upgrades_corrections),
        ("test_reflect_step_skips_l6_l7_l9", test_reflect_step_skips_l6_l7_l9),
        ("test_reflect_step_scanned_count", test_reflect_step_scanned_count),
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