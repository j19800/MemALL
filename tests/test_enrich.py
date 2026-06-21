"""
Test Suite — Pipeline Enrich
============================
Tests enrich_step(), _find_memory_refs(), _is_summary_like().
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from memall.pipeline.enrich import enrich_step, _find_memory_refs, _is_summary_like


# ── Unit tests for internal helpers ─────────────────────────────────

def test_find_memory_refs():
    """Test: _find_memory_refs extracts numeric IDs from text."""
    text = "参考 00123 和 ID 456，以及 #789"
    refs = _find_memory_refs(text)
    assert 123 in refs, f"Expected 123 in refs, got {refs}"
    assert 456 in refs, f"Expected 456 in refs, got {refs}"
    assert 789 in refs, f"Expected 789 in refs, got {refs}"
    assert len(refs) == 3, f"Expected 3 refs, got {len(refs)}"
    print("  PASS test_find_memory_refs")


def test_find_memory_refs_boundary():
    """Test: _find_memory_refs filters out-of-range IDs."""
    text = "ID 99999 ID 0 ID -1"
    refs = _find_memory_refs(text)
    assert len(refs) == 0, f"Expected empty, got {refs}"
    print("  PASS test_find_memory_refs_boundary")


def test_is_summary_like_positive():
    """Test: _is_summary_like returns True for summary-like text."""
    assert _is_summary_like("这是一个总结"), "Expected True for '总结'"
    assert _is_summary_like("综合以上几点"), "Expected True for '综合以上'"
    assert _is_summary_like("基于以上内容提炼"), "Expected True for '提炼'"
    print("  PASS test_is_summary_like_positive")


def test_is_summary_like_negative():
    """Test: _is_summary_like returns False for normal text."""
    assert not _is_summary_like("今天天气很好"), "Expected False for normal text"
    assert not _is_summary_like(""), "Expected False for empty string"
    print("  PASS test_is_summary_like_negative")


def test_enrich_step_empty_db():
    """Test: enrich_step returns 0 when DB is empty."""
    from tests.test_helpers import init_temp_db, cleanup_temp_db

    db_path, patcher = init_temp_db()
    try:
        from memall.core.db import get_conn, init_db
        init_db(migrate=False)
        result = enrich_step()
        assert result == 0, f"Expected 0, got {result}"
        print("  PASS test_enrich_step_empty_db")
    finally:
        cleanup_temp_db(db_path, patcher)


def test_enrich_step_with_data():
    """Test: enrich_step enriches memories with entities."""
    from tests.test_helpers import init_temp_db, cleanup_temp_db, insert_memory

    db_path, patcher = init_temp_db()
    try:
        from memall.core.db import get_conn
        conn = get_conn()
        insert_memory(conn, "决定采用 Vue 框架，因为它性能好且生态完善", level="P2")
        conn.close()

        result = enrich_step()
        assert result >= 1, f"Expected at least 1 enrichment, got {result}"
        print("  PASS test_enrich_step_with_data")
    finally:
        cleanup_temp_db(db_path, patcher)


# ── Runner ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 60)
    print("MemALL Pipeline Enrich Tests")
    print("=" * 60)
    passed = 0
    failed = 0

    tests = [
        ("test_find_memory_refs", test_find_memory_refs),
        ("test_find_memory_refs_boundary", test_find_memory_refs_boundary),
        ("test_is_summary_like_positive", test_is_summary_like_positive),
        ("test_is_summary_like_negative", test_is_summary_like_negative),
        ("test_enrich_step_empty_db", test_enrich_step_empty_db),
        ("test_enrich_step_with_data", test_enrich_step_with_data),
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