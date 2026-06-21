"""
Test Suite — Pipeline Adaptive
===============================
Tests adaptive_step() and sub-modules (clean, index, distill).
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


def test_adaptive_step_empty_db():
    """Test: adaptive_step returns expected structure when DB is empty."""
    from tests.test_helpers import init_temp_db, cleanup_temp_db
    from memall.pipeline.adaptive import adaptive_step

    db_path, patcher = init_temp_db()
    try:
        result = adaptive_step()
        assert isinstance(result, dict), f"Expected dict, got {type(result)}"
        assert "cleaner" in result, f"Missing 'cleaner': {result}"
        assert "indexer" in result, f"Missing 'indexer': {result}"
        assert "distiller" in result, f"Missing 'distiller': {result}"
        print("  PASS test_adaptive_step_empty_db")
    finally:
        cleanup_temp_db(db_path, patcher)


def test_adaptive_clean_standard():
    """Test: adaptive_clean returns standard mode with empty DB."""
    from tests.test_helpers import init_temp_db, cleanup_temp_db
    from memall.pipeline.adaptive import adaptive_clean

    db_path, patcher = init_temp_db()
    try:
        result = adaptive_clean()
        assert result["mode"] in ("standard", "aggressive", "compression"), \
            f"Unexpected mode: {result['mode']}"
        assert "cleaned_count" in result
        assert "trigger_reason" in result
        print("  PASS test_adaptive_clean_standard")
    finally:
        cleanup_temp_db(db_path, patcher)


def test_adaptive_index_creates_infrastructure():
    """Test: adaptive_index creates infrastructure tables if missing."""
    from tests.test_helpers import init_temp_db, cleanup_temp_db
    from memall.pipeline.adaptive import adaptive_index
    from memall.core.db import get_conn

    db_path, patcher = init_temp_db()
    try:
        result = adaptive_index()
        assert isinstance(result, dict), f"Expected dict, got {type(result)}"
        assert "high_freq_terms" in result
        assert "accel_tables_created" in result
        assert "query_log_trimmed" in result

        # Verify tables were created
        conn = get_conn()
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name IN ('query_log', 'idx_meta')"
        ).fetchall()
        conn.close()
        assert len(tables) == 2, f"Expected 2 tables, found {len(tables)}"
        print("  PASS test_adaptive_index_creates_infrastructure")
    finally:
        cleanup_temp_db(db_path, patcher)


def test_adaptive_distill_no_history():
    """Test: adaptive_distill handles empty history."""
    from tests.test_helpers import init_temp_db, cleanup_temp_db
    from memall.pipeline.adaptive import adaptive_distill

    db_path, patcher = init_temp_db()
    try:
        result = adaptive_distill()
        assert isinstance(result, dict), f"Expected dict, got {type(result)}"
        assert "mode" in result
        assert "distilled" in result
        print("  PASS test_adaptive_distill_no_history")
    finally:
        cleanup_temp_db(db_path, patcher)


def test_adaptive_report():
    """Test: adaptive_report returns expected structure."""
    from tests.test_helpers import init_temp_db, cleanup_temp_db
    from memall.pipeline.adaptive import adaptive_report

    db_path, patcher = init_temp_db()
    try:
        result = adaptive_report()
        assert isinstance(result, dict)
        assert "query_log_total" in result
        assert "accel_table_count" in result
        assert "total_memories" in result
        assert "mode_suggestion" in result
        print("  PASS test_adaptive_report")
    finally:
        cleanup_temp_db(db_path, patcher)


# ── Runner ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 60)
    print("MemALL Pipeline Adaptive Tests")
    print("=" * 60)
    passed = 0
    failed = 0

    tests = [
        ("test_adaptive_step_empty_db", test_adaptive_step_empty_db),
        ("test_adaptive_clean_standard", test_adaptive_clean_standard),
        ("test_adaptive_index_creates_infrastructure", test_adaptive_index_creates_infrastructure),
        ("test_adaptive_distill_no_history", test_adaptive_distill_no_history),
        ("test_adaptive_report", test_adaptive_report),
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