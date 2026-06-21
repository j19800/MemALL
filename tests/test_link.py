"""
Test Suite — Pipeline Link
===========================
Tests link_step() and _jaccard, _infer_relation.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from memall.pipeline.link import _jaccard, _infer_relation


# ── Unit tests for internal helpers ─────────────────────────────────

def test_jaccard_identical():
    """Test: _jaccard returns 1.0 for identical sets."""
    assert _jaccard({"a", "b", "c"}, {"a", "b", "c"}) == 1.0
    print("  PASS test_jaccard_identical")


def test_jaccard_disjoint():
    """Test: _jaccard returns 0.0 for disjoint sets."""
    assert _jaccard({"a", "b"}, {"c", "d"}) == 0.0
    print("  PASS test_jaccard_disjoint")


def test_jaccard_partial():
    """Test: _jaccard returns correct intersection-over-union."""
    sim = _jaccard({"a", "b", "c"}, {"b", "c", "d"})
    assert sim == 2 / 4, f"Expected 0.5, got {sim}"
    print("  PASS test_jaccard_partial")


def test_jaccard_empty():
    """Test: _jaccard returns 0.0 when either set is empty."""
    assert _jaccard(set(), {"a", "b"}) == 0.0
    assert _jaccard({"a", "b"}, set()) == 0.0
    print("  PASS test_jaccard_empty")


def test_infer_relation_cites():
    """Test: _infer_relation detects 'cites' relationship."""
    rel = _infer_relation("之前的方案是A", "基于之前的方案做改进")
    assert rel == "cites", f"Expected 'cites', got '{rel}'"
    print("  PASS test_infer_relation_cites")


def test_infer_relation_contradicts():
    """Test: _infer_relation detects 'contradicts' relationship."""
    rel = _infer_relation("推荐使用方案A，它可靠稳定", "不推荐方案A，有严重问题")
    assert rel == "contradicts", f"Expected 'contradicts', got '{rel}'"
    print("  PASS test_infer_relation_contradicts")


def test_link_step_empty_db():
    """Test: link_step returns 0 when DB is empty."""
    from tests.test_helpers import init_temp_db, cleanup_temp_db
    from memall.pipeline.link import link_step

    db_path, patcher = init_temp_db()
    try:
        result = link_step()
        assert result == 0, f"Expected 0, got {result}"
        print("  PASS test_link_step_empty_db")
    finally:
        cleanup_temp_db(db_path, patcher)


def test_link_step_with_data():
    """Test: link_step creates edges between similar memories."""
    from tests.test_helpers import init_temp_db, cleanup_temp_db, insert_memory
    from memall.pipeline.link import link_step
    from memall.core.db import get_conn

    db_path, patcher = init_temp_db()
    try:
        conn = get_conn()
        aid = insert_memory(conn, "推荐使用 Vue 框架，简单快速好上手", level="P2")
        bid = insert_memory(conn, "不推荐 Vue 框架，有性能问题", level="P2")
        conn.close()

        result = link_step()
        assert result >= 1, f"Expected at least 1 edge, got {result}"

        conn = get_conn()
        row = conn.execute(
            "SELECT relation_type FROM edges WHERE source_id=? AND target_id=?",
            (aid, bid),
        ).fetchone()
        conn.close()
        assert row is not None, "Edge should exist"
        assert row["relation_type"] == "contradicts", f"Expected 'contradicts', got '{row['relation_type']}'"
        print("  PASS test_link_step_with_data")
    finally:
        cleanup_temp_db(db_path, patcher)


# ── Runner ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 60)
    print("MemALL Pipeline Link Tests")
    print("=" * 60)
    passed = 0
    failed = 0

    tests = [
        ("test_jaccard_identical", test_jaccard_identical),
        ("test_jaccard_disjoint", test_jaccard_disjoint),
        ("test_jaccard_partial", test_jaccard_partial),
        ("test_jaccard_empty", test_jaccard_empty),
        ("test_infer_relation_cites", test_infer_relation_cites),
        ("test_infer_relation_contradicts", test_infer_relation_contradicts),
        ("test_link_step_empty_db", test_link_step_empty_db),
        ("test_link_step_with_data", test_link_step_with_data),
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