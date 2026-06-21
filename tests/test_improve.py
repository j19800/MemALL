"""
Test Suite — Improve Step (Phase 22: Self-Improvement)
=======================================================
Tests correction rule extraction from L6 reflections,
per-agent vs global rules, dedup, and session injection.
"""

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
ROOT = os.path.dirname(os.path.dirname(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def _make_l6(conn, content: str, agent_name: str = "test_agent") -> None:
    """Helper: insert an L6 memory with standard fields."""
    from datetime import datetime, timezone
    from tests.test_helpers import insert_memory
    insert_memory(conn, content, agent_name=agent_name, level="L6",
                  created_at=datetime.now(timezone.utc).isoformat())


def test_improve_personal_created():
    """Test: L6 with known patterns creates personal corrections."""
    from tests.test_helpers import init_temp_db, cleanup_temp_db
    from memall.pipeline.improve import improve_step, get_active_corrections
    from memall.core.db import get_conn

    db_path, patcher = init_temp_db()
    try:
        conn = get_conn()
        _make_l6(conn, "涉及完成标记：不应改 level，应 metadata.done=true。要记住这个原则。",
                 agent_name="agent_a")
        _make_l6(conn, "改完了但数据没变——下次先查根因再动手，不凭直觉。",
                 agent_name="agent_a")
        conn.close()

        result = improve_step(agent_name="agent_a")

        assert result["scanned_l6"] >= 2, f"Expected >=2, got {result['scanned_l6']}"
        corr = get_active_corrections("agent_a")
        assert len(corr) > 0, f"Expected >0 corrections, got 0"
        print(f"  PASS test_improve_personal_created ({len(corr)} rules)")
    finally:
        cleanup_temp_db(db_path, patcher)


def test_improve_global_created():
    """Test: same pattern across >=2 agents creates global rule."""
    from tests.test_helpers import init_temp_db, cleanup_temp_db
    from memall.pipeline.improve import improve_step, get_active_corrections
    from memall.core.db import get_conn

    db_path, patcher = init_temp_db()
    try:
        conn = get_conn()
        for i, agent in enumerate(["agent_a", "agent_b", "agent_c"]):
            _make_l6(conn, f"反思{i}: session_end 几乎不被调用，不要依赖结束回调。这是个大坑。",
                     agent_name=agent)
        conn.close()

        result = improve_step(agent_name=None)  # Scan all agents

        assert result["scanned_l6"] >= 3, f"Expected >=3 L6, got {result['scanned_l6']}"

        corr = get_active_corrections("agent_a")
        global_rules = [c for c in corr if c["category"] == "global"]
        assert len(global_rules) >= 1, \
            f"Expected >=1 global rule, got {len(global_rules)}"
        print(f"  PASS test_improve_global_created ({len(global_rules)} global, {len(corr)} total)")
    finally:
        cleanup_temp_db(db_path, patcher)


def test_improve_dedup():
    """Test: running improve_step twice doesn't create duplicate rules."""
    from tests.test_helpers import init_temp_db, cleanup_temp_db
    from memall.pipeline.improve import improve_step, get_active_corrections
    from memall.core.db import get_conn

    db_path, patcher = init_temp_db()
    try:
        conn = get_conn()
        _make_l6(conn, "先查根因再改代码，不凭直觉下结论。很重要。",
                 agent_name="agent_a")
        _make_l6(conn, "验证是关键，不验证直接改容易出问题。",
                 agent_name="agent_a")
        conn.close()

        # Run twice
        improve_step(agent_name="agent_a")
        improve_step(agent_name="agent_a")

        corr = get_active_corrections("agent_a")
        rule_texts = [c["rule_text"] for c in corr]
        assert len(rule_texts) == len(set(rule_texts)), \
            f"Duplicate rules detected: {rule_texts}"
        print(f"  PASS test_improve_dedup ({len(corr)} unique rules)")
    finally:
        cleanup_temp_db(db_path, patcher)


def test_improve_no_l6():
    """Test: no L6 memories returns zero counts."""
    from tests.test_helpers import init_temp_db, cleanup_temp_db
    from memall.pipeline.improve import improve_step

    db_path, patcher = init_temp_db()
    try:
        result = improve_step(agent_name="agent_a")
        assert result["scanned_l6"] == 0, f"Expected 0, got {result['scanned_l6']}"
        print("  PASS test_improve_no_l6")
    finally:
        cleanup_temp_db(db_path, patcher)


def test_injection_corrections():
    """Test: session_start with auto_inject includes [CORRECTIONS] section."""
    from unittest.mock import patch
    from tests.test_helpers import init_temp_db, cleanup_temp_db
    from memall.pipeline.session import session_start, _ensure_sessions_table
    from memall.core.db import get_conn

    db_path, patcher = init_temp_db()
    try:
        # Insert L6 to trigger correction rules
        conn = get_conn()
        _make_l6(conn, "涉及完成任务时，应标记 done 而不是改 level。",
                 agent_name="test_agent")
        _make_l6(conn, "完成标记要记住：不能改 level。done 才是对的。",
                 agent_name="test_agent")
        _ensure_sessions_table(conn)
        conn.close()

        # Run improve first to create corrections
        from memall.pipeline.improve import improve_step
        improve_step(agent_name="test_agent")

        # Mock auto_inject to return empty dict (avoids federation_tools logger bug)
        with patch("memall.mcp.federation_tools.auto_inject", return_value={}):
            result = session_start(agent_name="test_agent", auto_inject=True)

        formatted = result.get("injection_formatted", "")
        assert "[CORRECTIONS]" in formatted, \
            f"[CORRECTIONS] not found in injection:\n{formatted[:500]}"
        print("  PASS test_injection_corrections (injection contains [CORRECTIONS])")
    finally:
        cleanup_temp_db(db_path, patcher)


def test_improve_pipeline_wired():
    """Test: run_pipeline with include_improve=True calls improve_step."""
    from tests.test_helpers import init_temp_db, cleanup_temp_db
    from memall.pipeline.pipeline import run_pipeline
    from memall.core.db import get_conn

    db_path, patcher = init_temp_db()
    try:
        conn = get_conn()
        _make_l6(conn, "session_end 几乎不被调用，结束逻辑应挂在 session_start 中。",
                 agent_name="agent_x")
        _make_l6(conn, "先查记录再改，不凭直觉下结论。每次都要。",
                 agent_name="agent_x")
        conn.close()

        result = run_pipeline(
            dry_run=False,
            include_persona=False,
            include_reflect=False,
            include_distill=False,
            include_integrate=False,
            include_improve=True,
        )
        assert "improve" in result.get("results", {}), \
            f"improve not in results: {result.keys()}"
        imp = result["results"]["improve"]
        assert isinstance(imp, dict), f"Expected dict, got {type(imp)}"
        print(f"  PASS test_improve_pipeline_wired ({imp})")
    finally:
        cleanup_temp_db(db_path, patcher)


# ── Runner ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 60)
    print("MemALL Improve Step Tests (Phase 22)")
    print("=" * 60)
    passed = 0
    failed = 0

    tests = [
        ("test_improve_personal_created", test_improve_personal_created),
        ("test_improve_global_created", test_improve_global_created),
        ("test_improve_dedup", test_improve_dedup),
        ("test_improve_no_l6", test_improve_no_l6),
        ("test_improve_pipeline_wired", test_improve_pipeline_wired),
        ("test_injection_corrections", test_injection_corrections),
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