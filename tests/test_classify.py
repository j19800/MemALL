"""
Test Suite — Pipeline Classify
===============================
Tests classify_step() category classification.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


def test_classify_step_empty_db():
    """Test: classify_step returns 0 category_updates when no memories exist."""
    from tests.test_helpers import init_temp_db, cleanup_temp_db
    from memall.pipeline.classify import classify_step

    db_path, patcher = init_temp_db()
    try:
        result = classify_step()
        assert isinstance(result, dict), f"Expected dict, got {type(result)}"
        assert result.get("scanned") == 0, f"Expected 0 scanned, got {result}"
        print("  PASS test_classify_step_empty_db")
    finally:
        cleanup_temp_db(db_path, patcher)


def test_classify_step_general_unchanged():
    """Test: classify_step assigns L2 fallback to neutral text (no category matching)."""
    from tests.test_helpers import init_temp_db, cleanup_temp_db, insert_memory
    from memall.pipeline.classify import classify_step
    from memall.core.db import get_conn

    db_path, patcher = init_temp_db()
    try:
        conn = get_conn()
        # A truly neutral text that matches no classify rules
        insert_memory(conn, "abcdefghijklmnopqrstuvwxyz", category="general")
        conn.close()

        result = classify_step()
        # Neutral text matches no rule → stays at P2
        assert result.get("changed") == 0, f"Expected 0 changed, got {result}"
        print("  PASS test_classify_step_general_unchanged")
    finally:
        cleanup_temp_db(db_path, patcher)


def _assert_category(conn, expected_cat: str):
    row = conn.execute("SELECT category FROM memories").fetchone()
    assert row["category"] == expected_cat, f"expected {expected_cat} got {row['category']}"


def test_classify_step_decision():
    """Test: classify_step detects decision category + L4 layer."""
    from tests.test_helpers import init_temp_db, cleanup_temp_db, insert_memory
    from memall.pipeline.classify import classify_step
    from memall.core.db import get_conn

    db_path, patcher = init_temp_db()
    try:
        conn = get_conn()
        insert_memory(conn, "本次会话决定采用 React 作为前端框架，替代旧的方案", category="general")
        conn.close()

        result = classify_step()
        assert result.get("scanned") == 1

        conn = get_conn()
        _assert_category(conn, "decision")
        conn.close()
        print("  PASS test_classify_step_decision")
    finally:
        cleanup_temp_db(db_path, patcher)


def test_classify_step_problem():
    """Test: classify_step detects problem category."""
    from tests.test_helpers import init_temp_db, cleanup_temp_db, insert_memory
    from memall.pipeline.classify import classify_step
    from memall.core.db import get_conn

    db_path, patcher = init_temp_db()
    try:
        conn = get_conn()
        insert_memory(conn, "存在一个严重 bug，系统在高并发下返回错误结果", category="general")
        conn.close()

        result = classify_step()
        assert result.get("scanned") == 1

        conn = get_conn()
        _assert_category(conn, "problem")
        conn.close()
        print("  PASS test_classify_step_problem")
    finally:
        cleanup_temp_db(db_path, patcher)


def test_classify_step_architecture():
    """Test: classify_step detects architecture category."""
    from tests.test_helpers import init_temp_db, cleanup_temp_db, insert_memory
    from memall.pipeline.classify import classify_step
    from memall.core.db import get_conn

    db_path, patcher = init_temp_db()
    try:
        conn = get_conn()
        insert_memory(conn, "系统架构采用微服务设计方案，遵循领域驱动设计原则", category="general")
        conn.close()

        result = classify_step()
        assert result.get("scanned") == 1

        conn = get_conn()
        _assert_category(conn, "architecture")
        conn.close()
        print("  PASS test_classify_step_architecture")
    finally:
        cleanup_temp_db(db_path, patcher)


# ── Runner ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 60)
    print("MemALL Pipeline Classify Tests")
    print("=" * 60)
    passed = 0
    failed = 0


def test_detect_layers_module_noise():
    """Test: MODULE noise content gets P2, not L2."""
    from memall.pipeline.classify import _detect_layers
    result = _detect_layers("[MODULE:root/agent_memory] agent_memory")
    assert result["primary"] == "P2", f"Expected P2 for MODULE, got {result['primary']}"
    result2 = _detect_layers("content about 今天 and 上线")
    assert result2["primary"] == "L2", f"Expected L2 for event content, got {result2['primary']}"
    print("  PASS test_detect_layers_module_noise")


def test_classify_step_module_noise():
    """Test: classify_step reclassifies MODULE L2 to P2."""
    from tests.test_helpers import init_temp_db, cleanup_temp_db, insert_memory
    from memall.pipeline.classify import classify_step
    from memall.core.db import get_conn

    db_path, patcher = init_temp_db()
    try:
        conn = get_conn()
        insert_memory(conn, "[MODULE:root/test] test module registration", level="L2", category="general")
        conn.close()

        result = classify_step()
        conn = get_conn()
        row = conn.execute("SELECT level FROM memories").fetchone()
        assert row["level"] == "P2", f"Expected P2 after reclassify, got {row['level']}"
        conn.close()
        print("  PASS test_classify_step_module_noise")
    finally:
        cleanup_temp_db(db_path, patcher)


# ── Runner ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 60)
    print("MemALL Pipeline Classify Tests")
    print("=" * 60)
    passed = 0
    failed = 0

    tests = [
        ("test_classify_step_empty_db", test_classify_step_empty_db),
        ("test_classify_step_general_unchanged", test_classify_step_general_unchanged),
        ("test_classify_step_decision", test_classify_step_decision),
        ("test_classify_step_problem", test_classify_step_problem),
        ("test_classify_step_architecture", test_classify_step_architecture),
        ("test_detect_layers_module_noise", test_detect_layers_module_noise),
        ("test_classify_step_module_noise", test_classify_step_module_noise),
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