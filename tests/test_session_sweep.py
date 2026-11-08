"""Regression tests for idle_session_sweep rules added 2026-11-08.

Three failure modes measured in production after the scheduler resurrection,
each pinned here:

1. SELF-FEEDING SWEEP — _harvest_session writes L4/L6 rows with
   created_at=now and metadata.source='pipeline_harvest'.  When the sweep
   counted those as agent activity, every closed session reset the
   last-activity of all older stacked sessions of the same agent, and the
   pass stalled (431 rows left, 4 of them still tail-chasing in the live
   measurement).
2. SUPERSEDED STACKS — MemALL keeps one active session per agent by
   construction, but session_start only closes ONE stale row per start, so
   agents accumulated 20-90 stacked actives (claude 94 / workbuddy 85 /
   opencode 77).  The idle rule alone can never reach them because the
   agent keeps producing fresh memories.
3. EMPTY ZOMBIES — sessions with no memories were skipped "still warming
   up" forever; 290 such rows were >30 days old.
"""

from tests.test_helpers import init_temp_db, cleanup_temp_db, insert_memory


def _mk_session(conn, sid, agent, started_at):
    conn.execute(
        "INSERT INTO sessions (session_id, agent_name, started_at, status) VALUES (?,?,?, 'active')",
        (sid, agent, started_at),
    )


def test_sweep_closes_superseded_sessions_even_when_agent_active():
    db_path, patcher = init_temp_db()
    try:
        import json
        from datetime import datetime, timedelta, timezone
        from memall.core.db import get_conn
        from memall.pipeline.session import _ensure_sessions_table, idle_session_sweep

        conn = get_conn()
        _ensure_sessions_table(conn)
        now = datetime.now(timezone.utc)
        old = (now - timedelta(days=10)).isoformat()
        recent = (now - timedelta(hours=1)).isoformat()
        act = (now - timedelta(minutes=5)).isoformat()

        # two OLD stacked sessions for one agent
        _mk_session(conn, "stack_a", "workbuddy", old)
        _mk_session(conn, "stack_b", "workbuddy", old)
        # a CURRENT active session (superseding) + fresh activity
        _mk_session(conn, "current", "workbuddy", recent)
        insert_memory(conn, "fresh work memory content long enough to pass quality gates ok",
                      agent_name="workbuddy", created_at=act)
        conn.commit(); conn.close()

        res = idle_session_sweep(idle_minutes=120)
        conn = get_conn()
        st = {r["session_id"]: r["status"] for r in conn.execute(
            "SELECT session_id, status FROM sessions")}
        conn.close()
        assert st["stack_a"] == "ended", res          # superseded → closed despite warm agent
        assert st["stack_b"] == "ended", res
        assert st["current"] == "active", res         # the latest active must survive
        print("  PASS test_sweep_closes_superseded_sessions_even_when_agent_active")
    finally:
        cleanup_temp_db(db_path, patcher)


def test_sweep_not_self_fed_by_harvest_output():
    db_path, patcher = init_temp_db()
    try:
        import json
        from datetime import datetime, timedelta, timezone
        from memall.core.db import get_conn
        from memall.pipeline.session import _ensure_sessions_table, idle_session_sweep

        conn = get_conn()
        _ensure_sessions_table(conn)
        now = datetime.now(timezone.utc)
        d35 = (now - timedelta(days=35)).isoformat()
        d34 = (now - timedelta(days=34)).isoformat()
        d33 = (now - timedelta(days=33)).isoformat()

        _mk_session(conn, "old_a", "agex", d35)
        _mk_session(conn, "old_b", "agex", d34)      # processed AFTER old_a
        # real memories inside BOTH windows, all long idle
        for i in range(6):
            insert_memory(conn, f"真实工作记忆内容第{i}条，足够长以通过质量门检查要求。",
                          agent_name="agex", created_at=d33)
        # And the harvest-style derived row an EARLIER sweep produced for
        # old_a: created NOW with source=pipeline_harvest — it falls inside
        # old_b's window and, if counted as activity, keeps old_b warm
        # forever (the self-feeding stall measured in production).
        c2 = json.dumps({"session_id": "old_a", "source": "pipeline_harvest"})
        conn.execute(
            "INSERT INTO memories (content, content_hash, level, owner, agent_name,"
            " category, subject, summary, occurred_at, created_at, updated_at,"
            " confidence, visibility, metadata, stream)"
            " VALUES (?, 'h1', 'L4','system','agex','session','x','x',?,?,?,0.5,'shared',?,'ledger')",
            ("[L4 会话] agex · 6条记忆", d34, now.isoformat(), now.isoformat(), c2),
        )
        conn.commit(); conn.close()

        res = idle_session_sweep(idle_minutes=120)
        conn = get_conn()
        st = {r["session_id"]: r["status"] for r in conn.execute(
            "SELECT session_id, status FROM sessions")}
        conn.close()
        assert st["old_a"] == "ended", res
        assert st["old_b"] == "ended", \
            f"harvest rows must not keep a session warm: {res}"
        print("  PASS test_sweep_not_self_fed_by_harvest_output")
    finally:
        cleanup_temp_db(db_path, patcher)


def test_sweep_closes_memoryless_sessions_past_30_days():
    db_path, patcher = init_temp_db()
    try:
        from datetime import datetime, timedelta, timezone
        from memall.core.db import get_conn
        from memall.pipeline.session import _ensure_sessions_table, idle_session_sweep

        conn = get_conn()
        _ensure_sessions_table(conn)
        now = datetime.now(timezone.utc)
        _mk_session(conn, "zombie_empty", "agzy", (now - timedelta(days=45)).isoformat())
        _mk_session(conn, "warmup_empty", "agnew", (now - timedelta(hours=25)).isoformat())
        conn.commit(); conn.close()

        idle_session_sweep(idle_minutes=120)
        conn = get_conn()
        st = {r["session_id"]: r["status"] for r in conn.execute(
            "SELECT session_id, status FROM sessions")}
        conn.close()
        assert st["zombie_empty"] == "ended", st
        assert st["warmup_empty"] == "active", "recent memoryless sessions may still warm up"
        print("  PASS test_sweep_closes_memoryless_sessions_past_30_days")
    finally:
        cleanup_temp_db(db_path, patcher)


def test_forget_step_survives_non_string_completed_at():
    """The 2026-11-05→11-08 live failure: a card with completed_at as a dict
    made forget_l5_archive raise TypeError; the except block then rolled back
    without an active transaction and masked the real error for days."""
    db_path, patcher = init_temp_db()
    try:
        import json
        from memall.core.db import get_conn
        from memall.pipeline.forget import forget_l5_archive

        conn = get_conn()
        meta_bad = json.dumps({"status": "done", "completed_at": {"nested": 1}})
        meta_ok = json.dumps({"status": "done",
                              "completed_at": "2020-01-01T00:00:00+00:00"})
        for i, m in enumerate((meta_bad, meta_ok)):
            conn.execute(
                "INSERT INTO memories (content, content_hash, level, owner, agent_name,"
                " category, metadata, occurred_at, created_at, updated_at)"
                " VALUES (?,?, 'L5','system','fx','task',?,"
                " '2020-01-01T00:00:00+00:00','2020-01-01T00:00:00+00:00','2020-01-01T00:00:00+00:00')",
                (f"任务卡正文内容足够长以通过质量门测试{i}", f"fh{i}", m),
            )
        conn.commit(); conn.close()

        res = forget_l5_archive(days=30)   # must not raise
        assert res["archived"] == 1, res    # the valid old one archived; bad one skipped
        print("  PASS test_forget_step_survives_non_string_completed_at")
    finally:
        cleanup_temp_db(db_path, patcher)
