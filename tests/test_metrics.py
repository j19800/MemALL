"""
Test Suite — Pipeline Metrics
==============================
Tests collect_metrics().
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


def test_collect_metrics_empty_db():
    """Test: collect_metrics returns empty structure for empty DB."""
    from tests.test_helpers import init_temp_db, cleanup_temp_db
    from memall.pipeline.metrics import collect_metrics

    db_path, patcher = init_temp_db()
    try:
        result = collect_metrics()
        assert isinstance(result, dict), f"Expected dict, got {type(result)}"
        assert result["total_memories"] == 0, f"Expected 0, got {result['total_memories']}"
        print("  PASS test_collect_metrics_empty_db")
    finally:
        cleanup_temp_db(db_path, patcher)


def test_collect_metrics_with_data():
    """Test: collect_metrics returns correct counts."""
    from tests.test_helpers import init_temp_db, cleanup_temp_db, insert_memory
    from memall.pipeline.metrics import collect_metrics
    from memall.core.db import get_conn
    from datetime import datetime, timezone

    db_path, patcher = init_temp_db()
    try:
        conn = get_conn()
        now = datetime.now(timezone.utc).isoformat()
        a = insert_memory(conn, "Memory about architecture decisions", agent_name="agent1",
                           category="architecture")
        b = insert_memory(conn, "Memory about testing practices", agent_name="agent1",
                           category="testing")
        insert_memory(conn, "Uncategorized note", agent_name="agent1", category="general")
        # Add an edge between a and b
        conn.execute(
            "INSERT INTO edges (source_id, target_id, relation_type, weight, created_at) VALUES (?, ?, ?, ?, ?)",
            (a, b, "refines", 1.0, now),
        )
        conn.commit()
        conn.close()

        result = collect_metrics()
        assert result["total_memories"] == 3, f"Expected 3, got {result['total_memories']}"
        assert result["total_edges"] == 1, f"Expected 1 edge, got {result['total_edges']}"
        assert result["categories"] >= 2, f"Expected >=2 categories, got {result['categories']}"
        assert result["active_agents"] == 1, f"Expected 1 agent, got {result['active_agents']}"
        print("  PASS test_collect_metrics_with_data")
    finally:
        cleanup_temp_db(db_path, patcher)


def test_collect_metrics_keys():
    """Test: collect_metrics returns all expected keys."""
    from tests.test_helpers import init_temp_db, cleanup_temp_db, insert_memory
    from memall.pipeline.metrics import collect_metrics
    from memall.core.db import get_conn

    db_path, patcher = init_temp_db()
    try:
        conn = get_conn()
        insert_memory(conn, "Sample memory for metrics test", agent_name="metrics_agent")
        conn.close()

        result = collect_metrics()
        expected_keys = [
            "total_memories", "total_edges", "connection_density",
            "classification_coverage", "categories", "active_agents",
        ]
        for key in expected_keys:
            assert key in result, f"Missing key '{key}' in {result}"
        print("  PASS test_collect_metrics_keys")
    finally:
        cleanup_temp_db(db_path, patcher)


def test_show_metrics():
    """Test: show_metrics calls collect and append."""
    from tests.test_helpers import init_temp_db, cleanup_temp_db
    from memall.pipeline.metrics import show_metrics

    db_path, patcher = init_temp_db()
    try:
        result = show_metrics()
        assert isinstance(result, dict)
        assert "total_memories" in result
        print("  PASS test_show_metrics")
    finally:
        cleanup_temp_db(db_path, patcher)


def test_read_history():
    """Test: read_history returns list."""
    from tests.test_helpers import init_temp_db, cleanup_temp_db
    from memall.pipeline.metrics import read_history

    db_path, patcher = init_temp_db()
    try:
        history = read_history(limit=5)
        assert isinstance(history, list), f"Expected list, got {type(history)}"
        print("  PASS test_read_history")
    finally:
        cleanup_temp_db(db_path, patcher)


# ── Runner ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 60)
    print("MemALL Pipeline Metrics Tests")
    print("=" * 60)
    passed = 0
    failed = 0

    tests = [
        ("test_collect_metrics_empty_db", test_collect_metrics_empty_db),
        ("test_collect_metrics_with_data", test_collect_metrics_with_data),
        ("test_collect_metrics_keys", test_collect_metrics_keys),
        ("test_show_metrics", test_show_metrics),
        ("test_read_history", test_read_history),
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