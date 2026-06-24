"""
Test Suite — Pipeline Distill
==============================
Tests distill_step().
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


def test_distill_step_empty_db():
    """Test: distill_step returns 0 when no memories exist."""
    from tests.test_helpers import init_temp_db, cleanup_temp_db
    from memall.pipeline.distill import distill_step

    db_path, patcher = init_temp_db()
    try:
        result = distill_step()
        assert isinstance(result, dict), f"Expected dict, got {type(result)}"
        assert result["distilled"] == 0, f"Expected 0, got {result['distilled']}"
        assert result["groups_processed"] == 0, f"Expected 0, got {result['groups_processed']}"
        print("  PASS test_distill_step_empty_db")
    finally:
        cleanup_temp_db(db_path, patcher)


def test_distill_step_skips_small_groups():
    """Test: distill_step skips groups with fewer than 3 memories."""
    from tests.test_helpers import init_temp_db, cleanup_temp_db, insert_memory
    from memall.pipeline.distill import distill_step
    from memall.core.db import get_conn

    db_path, patcher = init_temp_db()
    try:
        conn = get_conn()
        insert_memory(conn, "First memory about architecture decisions in the system",
                       agent_name="agent1", category="architecture")
        insert_memory(conn, "Second memory about architecture decisions",
                       agent_name="agent1", category="architecture")
        # Only 2 memories in the group, should not trigger distillation
        conn.close()

        result = distill_step()
        assert result["distilled"] == 0, f"Expected 0 distilled, got {result['distilled']}"
        assert result["groups_processed"] >= 0
        print("  PASS test_distill_step_skips_small_groups")
    finally:
        cleanup_temp_db(db_path, patcher)


def test_distill_step_creates_l9():
    """Test: distill_step creates L9 distilled memory."""
    from tests.test_helpers import init_temp_db, cleanup_temp_db, insert_memory
    from memall.pipeline.distill import distill_step
    from memall.core.db import get_conn

    db_path, patcher = init_temp_db()
    try:
        conn = get_conn()
        for i in range(3):
            insert_memory(conn, f"System architecture decision #{i}: using microservices pattern",
                           agent_name="arch_agent", category="architecture",
                           summary=f"Architecture note {i}", level="L3")
        conn.close()

        result = distill_step()
        assert result["distilled"] == 1, f"Expected 1 distilled, got {result['distilled']}"

        conn = get_conn()
        l9 = conn.execute(
            "SELECT content, level FROM memories WHERE level='L9'"
        ).fetchone()
        conn.close()
        assert l9 is not None, "Should find L9 memory"
        assert "[L9 聚合]" in l9["content"], "L9 content should have aggregate marker"
        print("  PASS test_distill_step_creates_l9")
    finally:
        cleanup_temp_db(db_path, patcher)


def test_distill_step_with_different_agents():
    """Test: distill_step handles memories from different agents separately."""
    from tests.test_helpers import init_temp_db, cleanup_temp_db, insert_memory
    from memall.pipeline.distill import distill_step
    from memall.core.db import get_conn

    db_path, patcher = init_temp_db()
    try:
        conn = get_conn()
        for i in range(3):
            insert_memory(conn, f"AgentA design discussion #{i}",
                           agent_name="agent_a", category="implementation", level="L3")
            insert_memory(conn, f"AgentB design discussion #{i}",
                           agent_name="agent_b", category="implementation", level="L3")
        conn.close()

        result = distill_step()
        # Both agents have 3+ memories in same category, so 2 distillations
        assert result["distilled"] == 2, f"Expected 2 distilled, got {result['distilled']}"
        print("  PASS test_distill_step_with_different_agents")
    finally:
        cleanup_temp_db(db_path, patcher)


# ── Runner ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 60)
    print("MemALL Pipeline Distill Tests")
    print("=" * 60)
    passed = 0
    failed = 0

    tests = [
        ("test_distill_step_empty_db", test_distill_step_empty_db),
        ("test_distill_step_skips_small_groups", test_distill_step_skips_small_groups),
        ("test_distill_step_creates_l9", test_distill_step_creates_l9),
        ("test_distill_step_with_different_agents", test_distill_step_with_different_agents),
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