"""
Test Suite — Core Context Assembler
====================================
Tests get_persona().
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


def test_get_persona_empty():
    """Test: get_persona returns empty structure for unknown agent."""
    from tests.test_helpers import init_temp_db, cleanup_temp_db
    from memall.core.context_assembler import get_persona

    db_path, patcher = init_temp_db()
    try:
        result = get_persona("nonexistent_agent")
        assert isinstance(result, dict), f"Expected dict, got {type(result)}"
        assert "recent_decisions" in result
        assert "active_topics" in result
        assert "contradictions_unresolved" in result
        assert "derived_insights" in result
        assert result["sample_size"] == 0, f"Expected 0, got {result['sample_size']}"
        print("  PASS test_get_persona_empty")
    finally:
        cleanup_temp_db(db_path, patcher)


def test_get_persona_with_l6_memories():
    """Test: get_persona finds L6/L7 memories."""
    from tests.test_helpers import init_temp_db, cleanup_temp_db, insert_memory
    from memall.core.context_assembler import get_persona
    from memall.core.db import get_conn

    db_path, patcher = init_temp_db()
    try:
        conn = get_conn()
        insert_memory(conn, "决定采用微服务架构作为系统设计方案",
                       agent_name="arch_agent", level="L6", category="architecture")
        insert_memory(conn, "总结：系统架构设计完成",
                       agent_name="arch_agent", level="L7", category="architecture")
        conn.close()

        result = get_persona("arch_agent")
        assert isinstance(result, dict), f"Expected dict, got {type(result)}"
        assert result["sample_size"] >= 1, f"Expected at least 1 sample, got {result['sample_size']}"
        assert len(result["active_topics"]) >= 1, "Expected at least 1 active topic"
        print("  PASS test_get_persona_with_l6_memories")
    finally:
        cleanup_temp_db(db_path, patcher)


def test_get_persona_detects_decisions():
    """Test: get_persona extracts decisions from text."""
    from tests.test_helpers import init_temp_db, cleanup_temp_db, insert_memory
    from memall.core.context_assembler import get_persona
    from memall.core.db import get_conn

    db_path, patcher = init_temp_db()
    try:
        conn = get_conn()
        insert_memory(conn, "我们决定采用 React 作为前端框架",
                       agent_name="decider", level="L6", category="decision")
        conn.close()

        result = get_persona("decider")
        assert len(result["recent_decisions"]) >= 1, \
            f"Expected at least 1 decision, got {result['recent_decisions']}"
        print("  PASS test_get_persona_detects_decisions")
    finally:
        cleanup_temp_db(db_path, patcher)


def test_get_persona_active_topics():
    """Test: get_persona aggregates active topics."""
    from tests.test_helpers import init_temp_db, cleanup_temp_db, insert_memory
    from memall.core.context_assembler import get_persona
    from memall.core.db import get_conn

    db_path, patcher = init_temp_db()
    try:
        conn = get_conn()
        insert_memory(conn, "Architecture decision one", agent_name="topic_agent",
                       level="L6", category="architecture")
        insert_memory(conn, "Architecture decision two", agent_name="topic_agent",
                       level="L6", category="architecture")
        insert_memory(conn, "Testing related memory", agent_name="topic_agent",
                       level="L6", category="testing")
        conn.close()

        result = get_persona("topic_agent")
        assert len(result["active_topics"]) >= 1
        # architecture should be most frequent
        top_topic = result["active_topics"][0]
        assert top_topic["count"] >= 1
        print("  PASS test_get_persona_active_topics")
    finally:
        cleanup_temp_db(db_path, patcher)


# ── Runner ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 60)
    print("MemALL Core Context Assembler Tests")
    print("=" * 60)
    passed = 0
    failed = 0

    tests = [
        ("test_get_persona_empty", test_get_persona_empty),
        ("test_get_persona_with_l6_memories", test_get_persona_with_l6_memories),
        ("test_get_persona_detects_decisions", test_get_persona_detects_decisions),
        ("test_get_persona_active_topics", test_get_persona_active_topics),
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