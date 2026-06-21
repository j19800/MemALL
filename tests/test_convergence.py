"""
Tests for the simplified convergence engine.
Single confirmation converges.
"""

import json
import hashlib
from datetime import datetime, timezone


def _cleanup_test_memories(subject_prefix="[TEST]"):
    from memall.core.db import get_conn
    conn = get_conn()
    try:
        conn.execute("DELETE FROM edges WHERE source_id IN (SELECT id FROM memories WHERE subject LIKE ? OR subject LIKE ?)", (f"{subject_prefix}%", f"%{subject_prefix}%"))
        conn.execute("DELETE FROM edges WHERE target_id IN (SELECT id FROM memories WHERE subject LIKE ? OR subject LIKE ?)", (f"{subject_prefix}%", f"%{subject_prefix}%"))
        conn.execute("DELETE FROM memories WHERE subject LIKE ? OR subject LIKE ?", (f"{subject_prefix}%", f"%{subject_prefix}%"))
        conn.commit()
    finally:
        conn.close()


def _count_tasks_from(disc_id: int) -> int:
    from memall.core.db import get_conn
    conn = get_conn()
    try:
        r = conn.execute(
            "SELECT COUNT(*) as c FROM memories WHERE level=" + chr(39) + "L5" + chr(39) + " AND category=" + chr(39) + "task" + chr(39) + " "
            + "AND json_extract(metadata, " + chr(39) + "$.source_discussion" + chr(39) + ") = ?",
            (disc_id,),
        ).fetchone()
        return r["c"]
    finally:
        conn.close()


def test_create_discussion_simplified():
    from memall.pipeline.convergence import create_discussion
    result = create_discussion(title="[TEST] Simplified", creator="codex")
    assert result["status"] == "active"
    assert "memory_id" in result
    assert "participants" not in result
    assert "convergence_rule" not in result
    print("  PASS test_create_discussion_simplified")


def test_confirm_discussion_auto_converges():
    from memall.pipeline.convergence import create_discussion, confirm_discussion
    disc = create_discussion(
        title="[TEST] Auto-converge",
        action_items=[{"assigned_to": "codex", "description": "Verify"}],
        creator="codex",
    )
    result = confirm_discussion(disc["memory_id"], "codex", stance="confirm", note="looks good")
    assert result["status"] == "converged"
    assert len(result.get("task_ids", [])) > 0
    assert result.get("decision_id") is not None
    print("  PASS test_confirm_discussion_auto_converges")


def test_confirm_twice():
    from memall.pipeline.convergence import create_discussion, confirm_discussion
    disc = create_discussion(
        title="[TEST] Double confirm",
        action_items=[{"assigned_to": "codex", "description": "test"}],
        creator="codex",
    )
    r1 = confirm_discussion(disc["memory_id"], "codex")
    assert r1["status"] == "converged"
    r2 = confirm_discussion(disc["memory_id"], "codex")
    assert "warning" in r2
    tasks = _count_tasks_from(disc["memory_id"])
    assert tasks == 1
    print("  PASS test_confirm_twice")


def test_backward_compat_respond():
    from memall.pipeline.convergence import create_discussion, respond_discussion
    disc = create_discussion(
        title="[TEST] Backward respond",
        action_items=[{"assigned_to": "codex", "description": "test"}],
        creator="codex",
    )
    result = respond_discussion(disc["memory_id"], "codex", "agree", "via old API", round_num=1)
    assert result.get("response_id") is not None
    assert "task_ids" in result
    print("  PASS test_backward_compat_respond")


def test_backward_compat_kwargs():
    from memall.pipeline.convergence import create_discussion
    result = create_discussion(
        title="[TEST] Extra kwargs",
        participants=["codex"],
        convergence_rule="unanimous",
        timeout_hours=48,
        creator="codex",
    )
    assert result["status"] == "active"
    print("  PASS test_backward_compat_kwargs")


if __name__ == "__main__":
    import logging
    logging.disable(logging.CRITICAL)

    _cleanup_test_memories("[TEST]")
    tests = [
        test_create_discussion_simplified,
        test_confirm_discussion_auto_converges,
        test_confirm_twice,
        test_backward_compat_respond,
        test_backward_compat_kwargs,
    ]
    passed = 0
    failed = 0
    for test in tests:
        try:
            test()
            passed += 1
        except Exception as e:
            import traceback
            print("  FAIL " + test.__name__ + ": " + type(e).__name__ + ": " + str(e))
            traceback.print_exc()
            failed += 1
    print("\n{}/{} passed".format(passed, passed + failed))
    _cleanup_test_memories("[TEST]")
