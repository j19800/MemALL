"""
Test Suite — Pipeline Suggest
==============================
Tests suggest_step() and internal suggestion extraction helpers.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from memall.pipeline.suggest import _detect_category, _extract_from_content


def test_detect_category():
    """Test: _detect_category maps text to correct category."""
    assert _detect_category("优化系统性能减少延迟") == "performance"
    assert _detect_category("添加安全权限控制") == "security"
    assert _detect_category("改进CLI界面输出") == "ux"
    assert _detect_category("部署备份和监控方案") == "ops"
    print("  PASS test_detect_category")


def test_detect_category_other():
    """Test: _detect_category returns 'other' for unrecognized text."""
    cat = _detect_category("今天天气很好适合出去玩")
    assert cat == "other", f"Expected 'other', got '{cat}'"
    print("  PASS test_detect_category_other")


def test_extract_from_content():
    """Test: _extract_from_content finds suggestion patterns."""
    text = "建议：应该优化数据库查询性能，可以考虑添加合理的索引和缓存机制来加速"
    results = _extract_from_content(text)
    assert len(results) >= 1, f"Expected at least 1 suggestion, got {results}"
    print("  PASS test_extract_from_content")


def test_extract_from_content_todo():
    """Test: _extract_from_content finds TODO patterns."""
    text = "TODO：需要重构用户认证模块的代码，当前实现存在安全隐患和性能问题"
    results = _extract_from_content(text)
    assert len(results) >= 1, f"Expected at least 1 TODO, got {results}"
    print("  PASS test_extract_from_content_todo")


def test_suggest_step_empty_db():
    """Test: suggest_step returns 0 extracted when no memories exist."""
    from tests.test_helpers import init_temp_db, cleanup_temp_db
    from memall.pipeline.suggest import suggest_step

    db_path, patcher = init_temp_db()
    try:
        result = suggest_step(limit=10)
        assert isinstance(result, dict), f"Expected dict, got {type(result)}"
        assert result["extracted"] == 0, f"Expected 0, got {result['extracted']}"
        print("  PASS test_suggest_step_empty_db")
    finally:
        cleanup_temp_db(db_path, patcher)


def test_suggest_step_extracts_suggestions():
    """Test: suggest_step extracts suggestions from memory content."""
    from tests.test_helpers import init_temp_db, cleanup_temp_db, insert_memory
    from memall.pipeline.suggest import suggest_step
    from memall.core.db import get_conn

    db_path, patcher = init_temp_db()
    try:
        conn = get_conn()
        insert_memory(conn, "建议：应该添加自动化测试来提升代码质量", agent_name="dev", level="P0")
        conn.close()

        result = suggest_step(limit=10)
        assert result["extracted"] >= 1, f"Expected at least 1, got {result['extracted']}"

        conn = get_conn()
        row = conn.execute("SELECT * FROM suggestions").fetchone()
        conn.close()
        assert row is not None, "Suggestion should exist in DB"
        print("  PASS test_suggest_step_extracts_suggestions")
    finally:
        cleanup_temp_db(db_path, patcher)


# ── Runner ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 60)
    print("MemALL Pipeline Suggest Tests")
    print("=" * 60)
    passed = 0
    failed = 0

    tests = [
        ("test_detect_category", test_detect_category),
        ("test_detect_category_other", test_detect_category_other),
        ("test_extract_from_content", test_extract_from_content),
        ("test_extract_from_content_todo", test_extract_from_content_todo),
        ("test_suggest_step_empty_db", test_suggest_step_empty_db),
        ("test_suggest_step_extracts_suggestions", test_suggest_step_extracts_suggestions),
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