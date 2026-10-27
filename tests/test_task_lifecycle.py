"""Unit tests for task_lifecycle (acknowledge / resolve / block) — task #1000409.

Covers the state machine + caller gate + reminder closure that the
memall_write task_resolve / task_block actions route through.
"""

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from tests.test_helpers import init_temp_db, cleanup_temp_db

import pytest

_db_path = None
_original_db_path = None


@pytest.fixture(autouse=True, scope="module")
def _temp_db():
    global _db_path, _original_db_path
    _db_path, _original_db_path = init_temp_db()
    yield
    cleanup_temp_db(_db_path, _original_db_path)


def _make_task(agent_name="testagent"):
    from memall.core.thin_waist import capture

    tid = capture(
        "Test task content for lifecycle",
        level="L5",
        category="task",
        project="g",
        agent_name=agent_name,
        owner="tester",
        metadata=json.dumps({"status": "active", "assignee": agent_name}),
    )
    assert isinstance(tid, int), f"capture failed: {tid}"
    return tid


def test_acknowledge_task_sets_timestamp_keeps_active():
    from memall.pipeline.task_lifecycle import acknowledge_task
    from memall.core.db import get_conn

    tid = _make_task()
    res = acknowledge_task(tid, caller="testagent")
    assert "acknowledged_at" in res
    assert res["task_id"] == tid

    conn = get_conn()
    try:
        meta = json.loads(conn.execute(
            "SELECT metadata FROM memories WHERE id=?", (tid,)).fetchone()[0])
        assert meta.get("status") == "active"
        assert "acknowledged_at" in meta
    finally:
        conn.close()


def test_resolve_task_from_active():
    from memall.pipeline.task_lifecycle import resolve_task
    from memall.core.db import get_conn

    tid = _make_task()
    res = resolve_task(tid, result="done evidence", caller="testagent")
    assert res["task_id"] == tid
    assert "resolved_at" in res

    conn = get_conn()
    try:
        meta = json.loads(conn.execute(
            "SELECT metadata FROM memories WHERE id=?", (tid,)).fetchone()[0])
        assert meta.get("status") == "resolved"
        assert meta.get("resolution") == "done evidence"
        arc = conn.execute(
            "SELECT arc_status FROM memories WHERE id=?", (tid,)).fetchone()[0]
        assert arc == "closed"
    finally:
        conn.close()


def test_resolve_task_from_blocked():
    from memall.pipeline.task_lifecycle import resolve_task, block_task
    from memall.core.db import get_conn

    tid = _make_task()
    block_task(tid, reason="need human", caller="testagent")
    res = resolve_task(tid, result="unblocked", caller="testagent")
    assert res["task_id"] == tid

    conn = get_conn()
    try:
        meta = json.loads(conn.execute(
            "SELECT metadata FROM memories WHERE id=?", (tid,)).fetchone()[0])
        assert meta.get("status") == "resolved"
    finally:
        conn.close()


def test_resolve_rejects_already_resolved():
    from memall.pipeline.task_lifecycle import resolve_task

    tid = _make_task()
    resolve_task(tid, result="first", caller="testagent")
    res = resolve_task(tid, result="second", caller="testagent")
    assert "warning" in res


def test_block_task_from_active():
    from memall.pipeline.task_lifecycle import block_task
    from memall.core.db import get_conn

    tid = _make_task()
    res = block_task(tid, reason="waiting on API", caller="testagent")
    assert res["task_id"] == tid
    assert "blocked_at" in res

    conn = get_conn()
    try:
        meta = json.loads(conn.execute(
            "SELECT metadata FROM memories WHERE id=?", (tid,)).fetchone()[0])
        assert meta.get("status") == "blocked"
        assert meta.get("blocked_reason") == "waiting on API"
    finally:
        conn.close()


def test_block_requires_reason_via_handler():
    from memall.mcp.adapter import handle_call
    from memall.mcp.session_identity import bind_handshake_agent, reset

    tid = _make_task()
    reset()
    bind_handshake_agent("testagent")
    res = handle_call("memall_write", {
        "action": "task_block", "task_id": tid})
    import json as _json
    data = _json.loads(res)
    assert "error" in data


def test_caller_gate_forbidden_resolve():
    from memall.pipeline.task_lifecycle import resolve_task

    tid = _make_task("agentA")
    res = resolve_task(tid, caller="agentB")
    assert res.get("status") == "forbidden"
    assert "assignee" in res


def test_caller_gate_forbidden_block():
    from memall.pipeline.task_lifecycle import block_task

    tid = _make_task("agentA")
    res = block_task(tid, reason="x", caller="agentB")
    assert res.get("status") == "forbidden"


def test_trusted_internal_caller_skips_gate():
    from memall.pipeline.task_lifecycle import resolve_task
    from memall.core.db import get_conn

    tid = _make_task("agentA")
    # caller=None is the trusted-internal path (scheduler/migration/guards)
    res = resolve_task(tid, result="internal", caller=None)
    assert res["task_id"] == tid

    conn = get_conn()
    try:
        meta = json.loads(conn.execute(
            "SELECT metadata FROM memories WHERE id=?", (tid,)).fetchone()[0])
        assert meta.get("status") == "resolved"
    finally:
        conn.close()


def test_close_task_reminders():
    from memall.pipeline.task_lifecycle import close_task_reminders
    from memall.core.db import get_conn

    tid = _make_task()
    conn = get_conn()
    try:
        conn.execute(
            "INSERT INTO memories (content, content_hash, level, category, "
            "agent_name, metadata) VALUES (?,?,?,?,?,?)",
            ("reminder", "hash_reminder_1", "P2", "task_pending",
             "testagent", json.dumps({"task_id": tid})))
        conn.commit()
    finally:
        conn.close()

    conn = get_conn()
    try:
        count = close_task_reminders(conn, tid)
        assert count == 1
        rem = conn.execute(
            "SELECT COUNT(*) FROM memories WHERE category='task_pending' "
            "AND json_extract(metadata, '$.task_id')=?", (tid,)).fetchone()[0]
        assert rem == 0
    finally:
        conn.close()


def test_resolve_closes_reminders():
    from memall.pipeline.task_lifecycle import resolve_task
    from memall.core.db import get_conn

    tid = _make_task()
    conn = get_conn()
    try:
        conn.execute(
            "INSERT INTO memories (content, content_hash, level, category, "
            "agent_name, metadata) VALUES (?,?,?,?,?,?)",
            ("reminder", "hash_reminder_2", "P2", "task_pending",
             "testagent", json.dumps({"task_id": tid})))
        conn.commit()
    finally:
        conn.close()

    resolve_task(tid, result="done", caller="testagent")

    conn = get_conn()
    try:
        rem = conn.execute(
            "SELECT COUNT(*) FROM memories WHERE category='task_pending' "
            "AND json_extract(metadata, '$.task_id')=?", (tid,)).fetchone()[0]
        assert rem == 0
    finally:
        conn.close()


def test_block_closes_reminders():
    from memall.pipeline.task_lifecycle import block_task
    from memall.core.db import get_conn

    tid = _make_task()
    conn = get_conn()
    try:
        conn.execute(
            "INSERT INTO memories (content, content_hash, level, category, "
            "agent_name, metadata) VALUES (?,?,?,?,?,?)",
            ("reminder", "hash_reminder_3", "P2", "task_pending",
             "testagent", json.dumps({"task_id": tid})))
        conn.commit()
    finally:
        conn.close()

    block_task(tid, reason="blocked", caller="testagent")

    conn = get_conn()
    try:
        rem = conn.execute(
            "SELECT COUNT(*) FROM memories WHERE category='task_pending' "
            "AND json_extract(metadata, '$.task_id')=?", (tid,)).fetchone()[0]
        assert rem == 0
    finally:
        conn.close()


def test_acknowledge_forbidden_for_stranger():
    from memall.pipeline.task_lifecycle import acknowledge_task

    tid = _make_task("agentA")
    res = acknowledge_task(tid, caller="agentB")
    assert res.get("status") == "forbidden"


def test_block_from_claimed():
    from memall.pipeline.task_lifecycle import block_task, claim_task
    from memall.core.db import get_conn

    tid = _make_task()
    # claim first
    claim_task(tid, "testagent")
    res = block_task(tid, reason="stuck", caller="testagent")
    assert res["task_id"] == tid

    conn = get_conn()
    try:
        meta = json.loads(conn.execute(
            "SELECT metadata FROM memories WHERE id=?", (tid,)).fetchone()[0])
        assert meta.get("status") == "blocked"
    finally:
        conn.close()


def test_resolve_from_claimed():
    from memall.pipeline.task_lifecycle import resolve_task, claim_task
    from memall.core.db import get_conn

    tid = _make_task()
    claim_task(tid, "testagent")
    res = resolve_task(tid, result="done", caller="testagent")
    assert res["task_id"] == tid

    conn = get_conn()
    try:
        meta = json.loads(conn.execute(
            "SELECT metadata FROM memories WHERE id=?", (tid,)).fetchone()[0])
        assert meta.get("status") == "resolved"
    finally:
        conn.close()
    """,
    "file_path": "E:\\MemALL\\tests\\test_task_lifecycle.py",
}