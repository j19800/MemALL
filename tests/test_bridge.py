"""
Test Suite — Pipeline Bridge
=============================
Tests bridge_analysis_step() and _build_memory_narrative_map().
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


def test_bridge_analysis_step_empty_db():
    """Test: bridge_analysis_step returns error when no edges exist."""
    from tests.test_helpers import init_temp_db, cleanup_temp_db
    from memall.pipeline.bridge import bridge_analysis_step

    db_path, patcher = init_temp_db()
    try:
        result = bridge_analysis_step()
        assert isinstance(result, dict), f"Expected dict, got {type(result)}"
        # Either error or edges=0
        assert result.get("total_edges", 0) == 0 or "error" in result
        print("  PASS test_bridge_analysis_step_empty_db")
    finally:
        cleanup_temp_db(db_path, patcher)


def test_bridge_analysis_step_no_clusters():
    """Test: bridge_analysis_step returns error when no clusters exist."""
    from tests.test_helpers import init_temp_db, cleanup_temp_db, insert_memory
    from memall.pipeline.bridge import bridge_analysis_step
    from memall.core.db import get_conn
    from datetime import datetime, timezone

    db_path, patcher = init_temp_db()
    try:
        conn = get_conn()
        now = datetime.now(timezone.utc).isoformat()
        aid = insert_memory(conn, "Memory A", agent_name="agent1")
        bid = insert_memory(conn, "Memory B", agent_name="agent1")
        conn.execute(
            "INSERT INTO edges (source_id, target_id, relation_type, weight, created_at) VALUES (?, ?, ?, ?, ?)",
            (aid, bid, "refines", 1.0, now),
        )
        conn.commit()
        conn.close()

        result = bridge_analysis_step()
        # Should have edges but no clusters
        assert "error" in result or "agents" in result
        print("  PASS test_bridge_analysis_step_no_clusters")
    finally:
        cleanup_temp_db(db_path, patcher)


def test_build_memory_narrative_map_empty():
    """Test: _build_memory_narrative_map returns empty dict when no data."""
    from memall.pipeline.bridge import _build_memory_narrative_map
    from tests.test_helpers import init_temp_db, cleanup_temp_db
    from memall.core.db import get_conn

    db_path, patcher = init_temp_db()
    try:
        conn = get_conn()
        mapping = _build_memory_narrative_map(conn)
        conn.close()
        assert isinstance(mapping, dict), f"Expected dict, got {type(mapping)}"
        assert len(mapping) == 0, f"Expected empty, got {len(mapping)}"
        print("  PASS test_build_memory_narrative_map_empty")
    finally:
        cleanup_temp_db(db_path, patcher)


def test_bridge_analysis_step_structure():
    """Test: bridge_analysis_step returns expected structure."""
    from tests.test_helpers import init_temp_db, cleanup_temp_db
    from memall.pipeline.bridge import bridge_analysis_step

    db_path, patcher = init_temp_db()
    try:
        result = bridge_analysis_step()
        assert isinstance(result, dict)
        print("  PASS test_bridge_analysis_step_structure")
    finally:
        cleanup_temp_db(db_path, patcher)


# ── Runner ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 60)
    print("MemALL Pipeline Bridge Tests")
    print("=" * 60)
    passed = 0
    failed = 0

    tests = [
        ("test_bridge_analysis_step_empty_db", test_bridge_analysis_step_empty_db),
        ("test_bridge_analysis_step_no_clusters", test_bridge_analysis_step_no_clusters),
        ("test_build_memory_narrative_map_empty", test_build_memory_narrative_map_empty),
        ("test_bridge_analysis_step_structure", test_bridge_analysis_step_structure),
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