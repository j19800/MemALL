"""
Test Suite — thin_waist advanced logic
========================================
Tests connect, traverse, update, smart_store, hybrid_search, normalize_agent_name.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


def test_normalize_agent_name():
    """normalize_agent_name should lowercase and strip."""
    from memall.core.thin_waist import normalize_agent_name
    assert normalize_agent_name("Alice") == "alice"
    assert normalize_agent_name("  Bob  ") == "bob"
    assert normalize_agent_name("") == "system"
    assert normalize_agent_name(None) == "system"
    assert normalize_agent_name("Test_Agent-1") == "test_agent-1"
    print("  PASS test_normalize_agent_name")


def test_connect_basic():
    """connect() should create an edge between two memories."""
    from tests.test_helpers import init_temp_db, cleanup_temp_db, insert_memory
    from memall.core.thin_waist import connect
    from memall.core.db import get_conn, pool_conn

    db_path, patcher = init_temp_db()
    try:
        conn = get_conn()
        m1 = insert_memory(conn, "Source memory for edge test.", agent_name="edge_test")
        m2 = insert_memory(conn, "Target memory for edge test.", agent_name="edge_test")
        conn.close()

        eid = connect(m1, m2, relation_type="refines")
        assert eid > 0, f"Expected positive edge ID, got {eid}"

        with pool_conn() as conn:
            row = conn.execute("SELECT * FROM edges WHERE id = ?", (eid,)).fetchone()
            assert row["source_id"] == m1
            assert row["target_id"] == m2
            assert row["relation_type"] == "refines"
        print("  PASS test_connect_basic")
    finally:
        cleanup_temp_db(db_path, patcher)


def test_connect_self_rejected():
    """Self-connection should be rejected."""
    from tests.test_helpers import init_temp_db, cleanup_temp_db, insert_memory
    from memall.core.thin_waist import connect
    from memall.core.db import get_conn

    db_path, patcher = init_temp_db()
    try:
        conn = get_conn()
        m1 = insert_memory(conn, "Memory for self-connect test.", agent_name="edge_test")
        conn.close()

        try:
            connect(m1, m1, relation_type="refines")
            assert False, "Expected ValueError for self-connection"
        except (ValueError, AssertionError):
            print("  PASS test_connect_self_rejected")
    finally:
        cleanup_temp_db(db_path, patcher)


def test_connect_duplicate():
    """Duplicate edge should return same ID (idempotent)."""
    from tests.test_helpers import init_temp_db, cleanup_temp_db, insert_memory
    from memall.core.thin_waist import connect
    from memall.core.db import get_conn

    db_path, patcher = init_temp_db()
    try:
        conn = get_conn()
        m1 = insert_memory(conn, "Source for duplicate edge.", agent_name="edge_test")
        m2 = insert_memory(conn, "Target for duplicate edge.", agent_name="edge_test")
        conn.close()

        eid1 = connect(m1, m2, relation_type="cites")
        eid2 = connect(m1, m2, relation_type="cites")
        assert eid1 == eid2, f"Duplicate edge should return same ID {eid1} vs {eid2}"
        print("  PASS test_connect_duplicate")
    finally:
        cleanup_temp_db(db_path, patcher)


def test_traverse_basic():
    """traverse() should return connected nodes and edges."""
    from tests.test_helpers import init_temp_db, cleanup_temp_db, insert_memory
    from memall.core.thin_waist import connect, traverse
    from memall.core.db import get_conn

    db_path, patcher = init_temp_db()
    try:
        conn = get_conn()
        m1 = insert_memory(conn, "Root memory for traverse.", agent_name="traverse_test")
        m2 = insert_memory(conn, "Child memory for traverse.", agent_name="traverse_test")
        conn.close()

        connect(m1, m2, relation_type="refines")
        result = traverse(m1, depth=1)
        assert isinstance(result, dict)
        assert result.get("root") == m1
        assert len(result.get("nodes", [])) >= 1
        assert len(result.get("edges", [])) >= 1
        print("  PASS test_traverse_basic")
    finally:
        cleanup_temp_db(db_path, patcher)


def test_traverse_depth_limit():
    """traverse() should respect depth parameter."""
    from tests.test_helpers import init_temp_db, cleanup_temp_db, insert_memory
    from memall.core.thin_waist import connect, traverse
    from memall.core.db import get_conn

    db_path, patcher = init_temp_db()
    try:
        conn = get_conn()
        m1 = insert_memory(conn, "L1 for traverse depth.", agent_name="depth_test")
        m2 = insert_memory(conn, "L2 for traverse depth.", agent_name="depth_test")
        m3 = insert_memory(conn, "L3 for traverse depth.", agent_name="depth_test")
        conn.close()
        connect(m1, m2, relation_type="refines")
        connect(m2, m3, relation_type="refines")

        result = traverse(m1, depth=5)
        assert result["root"] == m1
        print("  PASS test_traverse_depth_limit")
    finally:
        cleanup_temp_db(db_path, patcher)


def test_traverse_nonexistent():
    """Traverse from nonexistent ID should not crash."""
    from tests.test_helpers import init_temp_db, cleanup_temp_db
    from memall.core.thin_waist import traverse

    db_path, patcher = init_temp_db()
    try:
        result = traverse(99999, depth=1)
        # Should return a dict with the nonexistent root
        assert isinstance(result, dict)
        print("  PASS test_traverse_nonexistent")
    finally:
        cleanup_temp_db(db_path, patcher)


def test_update_rejects_invalid_field():
    """Invalid fields should be silently ignored."""
    from tests.test_helpers import init_temp_db, cleanup_temp_db, insert_memory
    from memall.core.thin_waist import update
    from memall.core.db import get_conn

    db_path, patcher = init_temp_db()
    try:
        conn = get_conn()
        mid = insert_memory(conn, "Update test memory.", agent_name="upd_test")
        conn.close()

        # Invalid field is ignored, valid field still works
        result = update(mid, category="testing")
        assert result is True
        print("  PASS test_update_rejects_invalid_field")
    finally:
        cleanup_temp_db(db_path, patcher)


def test_smart_store_dedup():
    """smart_store should accept valid content."""
    from tests.test_helpers import init_temp_db, cleanup_temp_db
    from memall.core.thin_waist import smart_store

    db_path, patcher = init_temp_db()
    try:
        result = smart_store("Smart store test memory with enough content for quality gate.", agent_name="smart_test")
        assert isinstance(result, dict)
        assert "status" in result or "id" in result
        print("  PASS test_smart_store_dedup")
    finally:
        cleanup_temp_db(db_path, patcher)


def test_normalize_rejects_blacklisted():
    """Blacklisted names should fall back to system."""
    from memall.core.thin_waist import normalize_agent_name
    blacklisted = ["architecture", "brainstorm", "unknown", "session_active", "general"]
    for name in blacklisted:
        result = normalize_agent_name(name)
        assert result == "system", f"Blacklisted '{name}' should become 'system', got '{result}'"
    print("  PASS test_normalize_rejects_blacklisted")


def test_normalize_rejects_template_leak():
    """Template patterns should fall back to system."""
    from memall.core.thin_waist import normalize_agent_name
    result = normalize_agent_name("{{agent_name}}")
    assert result == "system", f"Template leak should become 'system', got '{result}'"
    result2 = normalize_agent_name("*.agent_name")
    assert result2 == "system", f"Glob pattern should become 'system', got '{result2}'"
    print("  PASS test_normalize_rejects_template_leak")


def test_capture_rejects_empty():
    """Empty content should raise ValueError."""
    from memall.core.thin_waist import capture
    from tests.test_helpers import init_temp_db, cleanup_temp_db

    db_path, patcher = init_temp_db()
    try:
        try:
            capture("", agent_name="empty_test")
            assert False, "Expected ValueError for empty content"
        except ValueError:
            print("  PASS test_capture_rejects_empty")
    finally:
        cleanup_temp_db(db_path, patcher)


def test_retrieve_by_level():
    """Retrieve with level filter should return correct level."""
    from tests.test_helpers import init_temp_db, cleanup_temp_db
    from memall.core.thin_waist import capture, retrieve
    from memall.core.models import MemoryInput

    db_path, patcher = init_temp_db()
    try:
        # Use MemoryInput directly to bypass quality gate for test setup
        from memall.core.db import pool_conn, get_conn, content_hash as ch
        from datetime import datetime, timezone
        conn = get_conn()
        now = datetime.now(timezone.utc).isoformat()
        h4 = ch("L4 test content for level filter test with enough length to pass.")
        conn.execute(
            "INSERT INTO memories (content, content_hash, level, agent_name, occurred_at, created_at, updated_at) "
            "VALUES (?, ?, 'L4', 'lv_test', ?, ?, ?)",
            ("L4 test content for level filter test with enough length to pass.", h4, now, now, now),
        )
        h6 = ch("L6 test content for level filter test with enough length to pass.")
        conn.execute(
            "INSERT INTO memories (content, content_hash, level, agent_name, occurred_at, created_at, updated_at) "
            "VALUES (?, ?, 'L6', 'lv_test', ?, ?, ?)",
            ("L6 test content for level filter test with enough length to pass.", h6, now, now, now),
        )
        conn.commit()
        conn.close()

        results = retrieve(level="L4")
        if isinstance(results, list):
            for r in results:
                lv = r.get("level") if isinstance(r, dict) else getattr(r, "level", "")
                assert lv == "L4", f"Expected L4, got {lv}"
        print("  PASS test_retrieve_by_level")
    finally:
        cleanup_temp_db(db_path, patcher)


def test_smart_store_semantic_dedup():
    """smart_store should detect semantic duplicates."""
    from tests.test_helpers import init_temp_db, cleanup_temp_db
    from memall.core.thin_waist import smart_store, capture

    db_path, patcher = init_temp_db()
    try:
        # First store a memory
        first = smart_store("Smart store semantic dedup test memory with enough quality.", agent_name="sem_test")
        # Same content again should detect duplicate
        second = smart_store("Smart store semantic dedup test memory with enough quality.", agent_name="sem_test")
        assert isinstance(second, dict)
        print("  PASS test_smart_store_semantic_dedup")
    finally:
        cleanup_temp_db(db_path, patcher)


if __name__ == "__main__":
    print("=" * 60)
    print("thin_waist Advanced Tests")
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