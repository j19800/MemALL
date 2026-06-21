"""
Test Suite — Pipeline Narrative
================================
Tests narrative_step() and _build_narrative().
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


def test_narrative_step_empty_db():
    """Test: narrative_step returns empty dict when no agents."""
    from tests.test_helpers import init_temp_db, cleanup_temp_db
    from memall.pipeline.narrative import narrative_step

    db_path, patcher = init_temp_db()
    try:
        result = narrative_step()
        assert isinstance(result, dict), f"Expected dict, got {type(result)}"
        assert "narratives_created" in result, f"Missing key: {result}"
        assert result["narratives_created"] == 0, f"Expected 0, got {result['narratives_created']}"
        print("  PASS test_narrative_step_empty_db")
    finally:
        cleanup_temp_db(db_path, patcher)


def test_narrative_step_creates_weekly():
    """Test: narrative_step creates weekly narrative for an agent."""
    from tests.test_helpers import init_temp_db, cleanup_temp_db, insert_memory
    from memall.pipeline.narrative import narrative_step
    from memall.core.db import get_conn
    from datetime import datetime, timezone, timedelta

    db_path, patcher = init_temp_db()
    try:
        conn = get_conn()
        now = datetime.now(timezone.utc)
        recent = (now - timedelta(hours=1)).isoformat()
        insert_memory(conn, "今天完成了一个重要功能的开发", agent_name="dev_agent",
                       category="implementation", occurred_at=recent, created_at=recent)
        insert_memory(conn, "修复了一个关键 bug", agent_name="dev_agent",
                       category="fix", occurred_at=recent, created_at=recent)
        conn.close()

        result = narrative_step()
        assert result["narratives_created"] >= 1, f"Expected at least 1, got {result}"
        print("  PASS test_narrative_step_creates_weekly")
    finally:
        cleanup_temp_db(db_path, patcher)


def test_build_narrative_empty():
    """Test: _build_narrative handles empty events."""
    from memall.pipeline.narrative import _build_narrative
    from datetime import datetime, timezone

    text = _build_narrative("test_agent", [], datetime.now(timezone.utc), "weekly")
    assert "没有记录任何记忆活动" in text, f"Expected empty-event text, got: {text}"
    print("  PASS test_build_narrative_empty")


def test_build_narrative_with_events():
    """Test: _build_narrative constructs meaningful text."""
    from memall.pipeline.narrative import _build_narrative
    from datetime import datetime, timezone

    events = [
        {"id": 1, "content": "讨论系统架构设计方案", "category": "architecture", "level": "P1",
         "occurred_at": "2025-06-01T10:00:00", "agent_name": "arch"},
        {"id": 2, "content": "决定采用微服务架构", "category": "decision", "level": "P1",
         "occurred_at": "2025-06-02T10:00:00", "agent_name": "arch"},
    ]
    text = _build_narrative("arch", events, datetime.now(timezone.utc), "weekly")
    assert "arch" in text or "共产生了" in text, f"Expected agent name in narrative: {text[:100]}"
    assert len(text) > 50, f"Expected lengthy narrative, got {len(text)} chars"
    print("  PASS test_build_narrative_with_events")


def test_narrative_template_keys():
    """Test: NARRATIVE_TEMPLATES has expected keys."""
    from memall.pipeline.narrative import NARRATIVE_TEMPLATES

    for key in ("weekly", "monthly", "phase"):
        assert key in NARRATIVE_TEMPLATES, f"Missing template: {key}"
        assert "prefix" in NARRATIVE_TEMPLATES[key], f"Missing 'prefix' in {key}"
    print("  PASS test_narrative_template_keys")


# ── Runner ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 60)
    print("MemALL Pipeline Narrative Tests")
    print("=" * 60)
    passed = 0
    failed = 0

    tests = [
        ("test_narrative_step_empty_db", test_narrative_step_empty_db),
        ("test_narrative_step_creates_weekly", test_narrative_step_creates_weekly),
        ("test_build_narrative_empty", test_build_narrative_empty),
        ("test_build_narrative_with_events", test_build_narrative_with_events),
        ("test_narrative_template_keys", test_narrative_template_keys),
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