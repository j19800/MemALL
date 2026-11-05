"""
Test Suite — Narrative Auto-Generation Scheduler
=================================================

Tests:
  (a) narrative_step() creates/updates narratives for each agent + type
  (b) Re-running is idempotent (updates existing rather than duplicating)
  (c) Scheduler tick block calls narrative_step() when interval elapsed
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from tests.test_helpers import init_temp_db, cleanup_temp_db, insert_memory
from memall.config import reset_config


# ── (a) narrative_step creates narratives ────────────────────────────

def test_narrative_step_creates_for_each_agent_and_type():
    """narrative_step should create weekly/monthly/phase narratives per agent."""
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
        insert_memory(conn, "讨论系统架构设计方案", agent_name="arch_agent",
                      category="architecture", occurred_at=recent, created_at=recent)
        conn.commit()
        conn.close()

        result = narrative_step()
        assert isinstance(result, dict), f"Expected dict, got {type(result)}"
        assert "narratives_created" in result, f"Missing key: {result}"
        assert result["narratives_created"] >= 1, f"Expected >= 1, got {result}"
        # Each agent gets up to 3 narrative types (weekly, monthly, phase)
        assert len(result["narratives"]) >= 1, f"Expected at least 1 agent/entry, got {result}"
        print("  PASS test_narrative_step_creates_for_each_agent_and_type")
    finally:
        cleanup_temp_db(db_path, patcher)


# ── (b) Idempotency: re-running updates, not duplicates ────────────

def test_narrative_step_idempotent_no_duplicates():
    """Re-running narrative_step() updates existing rows rather than duplicating."""
    from memall.pipeline.narrative import narrative_step
    from memall.core.db import get_conn
    from datetime import datetime, timezone, timedelta

    db_path, patcher = init_temp_db()
    try:
        conn = get_conn()
        now = datetime.now(timezone.utc)
        recent = (now - timedelta(hours=1)).isoformat()
        insert_memory(conn, "第一次运行的测试记忆", agent_name="idem_agent",
                      category="general", occurred_at=recent, created_at=recent)
        conn.commit()
        conn.close()

        # First run creates narratives
        r1 = narrative_step()
        created_1 = r1["narratives_created"]

        # Second run with no new data should update, not duplicate
        r2 = narrative_step()
        created_2 = r2["narratives_created"]

        conn = get_conn()
        total_rows = conn.execute("SELECT COUNT(*) as c FROM narratives").fetchone()["c"]
        conn.close()

        # Total narrative rows should equal the first-run count (no duplication)
        assert total_rows == created_1, \
            f"Expected {created_1} rows after idempotent re-run, got {total_rows}"
        # Second run may report 0 newly created (all updated) — either way rows stay stable
        print(f"  PASS test_narrative_step_idempotent_no_duplicates (rows={total_rows}, r1={created_1}, r2={created_2})")
    finally:
        cleanup_temp_db(db_path, patcher)


# ── (c) Scheduler tick block calls narrative_step when interval elapsed ─

def test_scheduler_tick_calls_narrative_when_enabled():
    """Scheduler._tick calls narrative_step() when ENABLE_NARRATIVE and interval elapsed."""
    from memall.scheduler.scheduler import Scheduler, ENABLE_NARRATIVE, INTERVAL_NARRATIVE
    from memall.config import reset_config, get_config
    from memall.core.db import get_conn
    from tests.test_helpers import init_temp_db, cleanup_temp_db
    from memall.pipeline.narrative import narrative_step
    from datetime import datetime, timezone, timedelta

    db_path, patcher = init_temp_db()
    try:
        # Force config reload so our new keys are picked up from defaults
        reset_config()
        # Temporarily enable narrative for this test
        import memall.scheduler.scheduler as sched_mod
        sched_mod.ENABLE_NARRATIVE = True
        sched_mod.INTERVAL_NARRATIVE = 1  # 1 second, so it fires immediately
        sched_mod.Scheduler._last_narrative = 0  # force first tick to fire

        # Insert some memory data so narrative_step has something to work with
        conn = get_conn()
        now = datetime.now(timezone.utc)
        recent = (now - timedelta(hours=1)).isoformat()
        insert_memory(conn, "调度器测试：叙述自动生成验证", agent_name="sched_agent",
                      category="general", occurred_at=recent, created_at=recent)
        conn.commit()
        conn.close()

        s = Scheduler()
        # Manually set _running so _loop doesn't try to thread; just call _tick directly
        s._running = False
        s._tick()

        # Verify a narrative row was created
        from memall.core.db import get_conn as gc
        c = gc()
        rows = c.execute("SELECT COUNT(*) as c FROM narratives").fetchone()["c"]
        c.close()
        assert rows >= 1, f"Expected >= 1 narrative row after scheduler tick, got {rows}"

        # Restore defaults
        sched_mod.ENABLE_NARRATIVE = ENABLE_NARRATIVE
        sched_mod.INTERVAL_NARRATIVE = INTERVAL_NARRATIVE
        print("  PASS test_scheduler_tick_calls_narrative_when_enabled")
    finally:
        cleanup_temp_db(db_path, patcher)


def test_scheduler_tick_skips_narrative_when_disabled():
    """Scheduler._tick does NOT call narrative_step() when ENABLE_NARRATIVE is False."""
    from memall.scheduler.scheduler import Scheduler, ENABLE_NARRATIVE, INTERVAL_NARRATIVE
    from tests.test_helpers import init_temp_db, cleanup_temp_db
    import memall.scheduler.scheduler as sched_mod

    db_path, patcher = init_temp_db()
    try:
        reset_config()
        sched_mod.ENABLE_NARRATIVE = False
        sched_mod.INTERVAL_NARRATIVE = 1

        s = Scheduler()
        s._running = False
        s._tick()  # should not raise, narrative skipped

        # No narrative rows should exist (and no error)
        from memall.core.db import get_conn as gc
        c = gc()
        rows = c.execute("SELECT COUNT(*) as c FROM narratives").fetchone()["c"]
        c.close()
        assert rows == 0, f"Expected 0 narrative rows when disabled, got {rows}"

        sched_mod.ENABLE_NARRATIVE = ENABLE_NARRATIVE
        sched_mod.INTERVAL_NARRATIVE = INTERVAL_NARRATIVE
        print("  PASS test_scheduler_tick_skips_narrative_when_disabled")
    finally:
        cleanup_temp_db(db_path, patcher)


# ── Runner ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 60)
    print("MemALL Narrative Scheduler Tests")
    print("=" * 60)
    passed = 0
    failed = 0

    tests = [
        ("test_narrative_step_creates_for_each_agent_and_type",
         test_narrative_step_creates_for_each_agent_and_type),
        ("test_narrative_step_idempotent_no_duplicates",
         test_narrative_step_idempotent_no_duplicates),
        ("test_scheduler_tick_calls_narrative_when_enabled",
         test_scheduler_tick_calls_narrative_when_enabled),
        ("test_scheduler_tick_skips_narrative_when_disabled",
         test_scheduler_tick_skips_narrative_when_disabled),
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
