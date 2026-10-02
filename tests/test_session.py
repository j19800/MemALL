"""
Test Suite — Pipeline Session
==============================
Tests session_start, session_end, session_summary.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


def test_session_start():
    """Test: session_start creates a new session."""
    from tests.test_helpers import init_temp_db, cleanup_temp_db
    from memall.pipeline.session import session_start

    db_path, patcher = init_temp_db()
    try:
        result = session_start(agent_name="test_agent")
        assert isinstance(result, dict), f"Expected dict, got {type(result)}"
        assert "session_id" in result, f"Missing session_id: {result}"
        assert result["status"] == "active", f"Expected 'active', got {result['status']}"
        assert result["agent_name"] == "test_agent"
        print("  PASS test_session_start")
    finally:
        cleanup_temp_db(db_path, patcher)


def test_session_end():
    """Test: session_end ends a session with summary."""
    from tests.test_helpers import init_temp_db, cleanup_temp_db, insert_memory
    from memall.pipeline.session import session_start, session_end
    from memall.core.db import get_conn
    from datetime import datetime, timezone

    db_path, patcher = init_temp_db()
    try:
        start_result = session_start(agent_name="test_agent")
        sid = start_result["session_id"]

        # Add some memories within session timeframe
        conn = get_conn()
        insert_memory(conn, "Session memory one", agent_name="test_agent",
                       created_at=datetime.now(timezone.utc).isoformat())
        conn.close()

        result = session_end(sid)
        assert isinstance(result, dict), f"Expected dict, got {type(result)}"
        assert result["status"] == "ended", f"Expected 'ended', got {result['status']}"
        assert result["session_id"] == sid
        print("  PASS test_session_end")
    finally:
        cleanup_temp_db(db_path, patcher)


def test_session_end_not_found():
    """Test: session_end returns error for unknown session."""
    from tests.test_helpers import init_temp_db, cleanup_temp_db
    from memall.pipeline.session import session_end

    db_path, patcher = init_temp_db()
    try:
        result = session_end("nonexistent")
        assert "error" in result, f"Expected error, got {result}"
        print("  PASS test_session_end_not_found")
    finally:
        cleanup_temp_db(db_path, patcher)


def test_session_summary_by_id():
    """Test: session_summary retrieves a session by ID."""
    from tests.test_helpers import init_temp_db, cleanup_temp_db
    from memall.pipeline.session import session_start, session_summary

    db_path, patcher = init_temp_db()
    try:
        start_result = session_start(agent_name="test_agent")
        sid = start_result["session_id"]

        result = session_summary(session_id=sid)
        assert isinstance(result, dict), f"Expected dict, got {type(result)}"
        assert result["session_id"] == sid, f"Expected {sid}, got {result}"
        print("  PASS test_session_summary_by_id")
    finally:
        cleanup_temp_db(db_path, patcher)


def test_session_summary_recent():
    """Test: session_summary returns recent sessions for an agent."""
    from tests.test_helpers import init_temp_db, cleanup_temp_db
    from memall.pipeline.session import session_start, session_summary

    db_path, patcher = init_temp_db()
    try:
        session_start(agent_name="agent_a")
        session_start(agent_name="agent_a")
        session_start(agent_name="agent_b")

        result = session_summary(agent_name="agent_a", limit=5)
        assert isinstance(result, dict), f"Expected dict, got {type(result)}"
        assert result["total"] == 2, f"Expected 2 sessions, got {result['total']}"
        print("  PASS test_session_summary_recent")
    finally:
        cleanup_temp_db(db_path, patcher)


def test_session_summary_not_found():
    """Test: session_summary returns error for non-existent session ID."""
    from tests.test_helpers import init_temp_db, cleanup_temp_db
    from memall.pipeline.session import session_summary

    db_path, patcher = init_temp_db()
    try:
        result = session_summary(session_id="does_not_exist")
        assert "error" in result, f"Expected error, got {result}"
        print("  PASS test_session_summary_not_found")
    finally:
        cleanup_temp_db(db_path, patcher)


def test_session_start_auto_close_stale():
    """Test: session_start auto-closes stale active session for same agent."""
    from tests.test_helpers import init_temp_db, cleanup_temp_db, insert_memory
    from memall.pipeline.session import session_start, session_summary
    from memall.core.db import get_conn
    from datetime import datetime, timedelta, timezone

    db_path, patcher = init_temp_db()
    try:
        # Ensure sessions table exists
        conn = get_conn()
        from memall.pipeline.session import _ensure_sessions_table
        _ensure_sessions_table(conn)

        # Create a stale session (4 hours ago)
        old_stamp = (datetime.now(timezone.utc) - timedelta(hours=4)).isoformat()
        conn.execute(
            "INSERT INTO sessions (session_id, agent_name, started_at, status) VALUES (?, ?, ?, ?)",
            ("old_sid", "test_agent", old_stamp, "active"),
        )
        # Add some memories to this session
        insert_memory(conn, "Old memory about a decision", agent_name="test_agent",
                       created_at=(datetime.now(timezone.utc) - timedelta(hours=3)).isoformat())
        insert_memory(conn, "Another old task done", agent_name="test_agent",
                       created_at=(datetime.now(timezone.utc) - timedelta(hours=2)).isoformat())
        insert_memory(conn, "Fix: resolved the issue", agent_name="test_agent",
                       created_at=(datetime.now(timezone.utc) - timedelta(hours=1)).isoformat())
        conn.close()

        # Call session_start for same agent
        result = session_start(agent_name="test_agent", auto_inject=False)

        # Verify old session is now ended
        summary = session_summary(session_id="old_sid")
        assert summary["status"] == "ended", f"Expected 'ended', got {summary}"
        assert summary["memory_count"] > 0, f"Expected >0 memories captured"

        # Verify new session is active
        assert result["status"] == "active"
        assert result["session_id"] != "old_sid"
        print("  PASS test_session_start_auto_close_stale")
    finally:
        cleanup_temp_db(db_path, patcher)


def test_session_start_auto_close_fresh_kept():
    """Test: session_start does NOT auto-close a fresh (< 2h) active session."""
    from tests.test_helpers import init_temp_db, cleanup_temp_db
    from memall.pipeline.session import session_start, session_summary, _ensure_sessions_table
    from memall.core.db import get_conn
    from datetime import datetime, timedelta, timezone

    db_path, patcher = init_temp_db()
    try:
        conn = get_conn()
        _ensure_sessions_table(conn)
        # Create a recent session (30 min ago)
        recent_stamp = (datetime.now(timezone.utc) - timedelta(hours=0.5)).isoformat()
        conn.execute(
            "INSERT INTO sessions (session_id, agent_name, started_at, status) VALUES (?, ?, ?, ?)",
            ("fresh_sid", "test_agent", recent_stamp, "active"),
        )
        conn.commit()
        conn.close()

        # session_start for same agent — should NOT close fresh session (< 2h)
        session_start(agent_name="test_agent", auto_inject=False)

        # Verify fresh session still active
        summary = session_summary(session_id="fresh_sid")
        assert summary["status"] == "active", f"Expected 'active', got {summary}"
        print("  PASS test_session_start_auto_close_fresh_kept")
    finally:
        cleanup_temp_db(db_path, patcher)


def test_session_start_auto_close_diff_agent():
    """Test: session_start does NOT close active session for a different agent."""
    from tests.test_helpers import init_temp_db, cleanup_temp_db
    from memall.pipeline.session import session_start, session_summary, _ensure_sessions_table
    from memall.core.db import get_conn
    from datetime import datetime, timedelta, timezone

    db_path, patcher = init_temp_db()
    try:
        conn = get_conn()
        _ensure_sessions_table(conn)
        old_stamp = (datetime.now(timezone.utc) - timedelta(hours=4)).isoformat()
        conn.execute(
            "INSERT INTO sessions (session_id, agent_name, started_at, status) VALUES (?, ?, ?, ?)",
            ("other_sid", "opencode", old_stamp, "active"),
        )
        conn.commit()
        conn.close()

        # session_start for a different agent — should NOT close opencode's session
        session_start(agent_name="test_agent", auto_inject=False)

        summary = session_summary(session_id="other_sid")
        assert summary["status"] == "active", \
            f"opencode session should still be active, got {summary}"
        print("  PASS test_session_start_auto_close_diff_agent")
    finally:
        cleanup_temp_db(db_path, patcher)


def test_session_start_auto_close_l4_created():
    """Test: auto-close session creates L4 summary memory when count > 3."""
    from tests.test_helpers import init_temp_db, cleanup_temp_db, insert_memory
    from memall.pipeline.session import session_start, _ensure_sessions_table
    from memall.core.db import get_conn
    from datetime import datetime, timedelta, timezone

    db_path, patcher = init_temp_db()
    try:
        conn = get_conn()
        _ensure_sessions_table(conn)
        old_stamp = (datetime.now(timezone.utc) - timedelta(hours=4)).isoformat()
        conn.execute(
            "INSERT INTO sessions (session_id, agent_name, started_at, status) VALUES (?, ?, ?, ?)",
            ("l4_sid", "test_agent", old_stamp, "active"),
        )
        # Insert >3 memories for L4 trigger
        for i in range(5):
            insert_memory(conn, f"Session memory number {i} with enough content for testing purpose",
                          agent_name="test_agent", category="general",
                          created_at=(datetime.now(timezone.utc) - timedelta(hours=3 - i)).isoformat())
        conn.close()

        result = session_start(agent_name="test_agent", auto_inject=False)

        # Check L4 memories exist for test_agent
        conn2 = get_conn()
        l4_rows = conn2.execute(
            "SELECT id, subject, content FROM memories WHERE level = 'L4' AND agent_name = 'test_agent'"
        ).fetchall()
        conn2.close()
        assert len(l4_rows) > 0, f"Expected L4 memories, got {len(l4_rows)}"
        print(f"  PASS test_session_start_auto_close_l4_created ({len(l4_rows)} L4 created)")
    finally:
        cleanup_temp_db(db_path, patcher)


def test_session_harvest_excludes_derived_l9_decisions():
    """C2 regression: an L9 derivation tagged category='decision' must NOT be
    re-consumed as a 'key decision' of a freshly harvested L4.

    The L9 content deliberately does NOT start with the ``[L9 蒸馏]`` artifact
    marker, so the post-filter in ``_harvest_session`` would *not* catch it —
    only the SQL ``level NOT IN (_DERIVED_LEVELS)`` guard added by C2 protects
    against the L4 -> L9 -> L4 recursion loop. If that guard is removed, this
    test fails.
    """
    from tests.test_helpers import init_temp_db, cleanup_temp_db, insert_memory
    from memall.pipeline.session import session_start, _ensure_sessions_table
    from memall.core.db import get_conn
    from datetime import datetime, timedelta, timezone

    db_path, patcher = init_temp_db()
    try:
        conn = get_conn()
        _ensure_sessions_table(conn)
        old_stamp = (datetime.now(timezone.utc) - timedelta(hours=4)).isoformat()
        conn.execute(
            "INSERT INTO sessions (session_id, agent_name, started_at, status) VALUES (?, ?, ?, ?)",
            ("c2_sid", "test_agent", old_stamp, "active"),
        )
        # 3 general memories to push the session count above the L4 threshold (>3)
        for i in range(3):
            insert_memory(conn, f"General working note number {i} for the project backlog",
                          agent_name="test_agent", category="general",
                          created_at=(datetime.now(timezone.utc) - timedelta(hours=3 - i)).isoformat())
        # Real observed decision (level L4) — must appear in the new L4
        insert_memory(conn, "Decided to ship the release on Friday",
                      agent_name="test_agent", category="decision", level="L4",
                      created_at=(datetime.now(timezone.utc) - timedelta(hours=2.5)).isoformat())
        # Derived L9 decision whose content does NOT match the artifact regex —
        # only the SQL level filter protects it from being re-consumed.
        insert_memory(conn, "Decided to refactor the ingestion pipeline",
                      agent_name="test_agent", category="decision", level="L9",
                      created_at=(datetime.now(timezone.utc) - timedelta(hours=2)).isoformat())
        conn.close()

        session_start(agent_name="test_agent", auto_inject=False)

        conn2 = get_conn()
        l4_rows = conn2.execute(
            "SELECT content FROM memories WHERE level = 'L4' AND agent_name = 'test_agent'"
        ).fetchall()
        conn2.close()
        assert l4_rows, f"Expected an L4 memory to be harvested, got none"
        l4_content = l4_rows[0]["content"]
        assert "ship the release on Friday" in l4_content, \
            f"Real decision should be cited in L4: {l4_content!r}"
        assert "refactor the ingestion pipeline" not in l4_content, \
            f"L9 derived decision must NOT leak into L4 key_decisions: {l4_content!r}"
        print("  PASS test_session_harvest_excludes_derived_l9_decisions")
    finally:
        cleanup_temp_db(db_path, patcher)


# ── Runner ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 60)
    print("MemALL Pipeline Session Tests")
    print("=" * 60)
    passed = 0
    failed = 0

    tests = [
        ("test_session_start", test_session_start),
        ("test_session_end", test_session_end),
        ("test_session_end_not_found", test_session_end_not_found),
        ("test_session_summary_by_id", test_session_summary_by_id),
        ("test_session_summary_recent", test_session_summary_recent),
        ("test_session_summary_not_found", test_session_summary_not_found),
        ("test_session_start_auto_close_stale", test_session_start_auto_close_stale),
        ("test_session_start_auto_close_fresh_kept", test_session_start_auto_close_fresh_kept),
        ("test_session_start_auto_close_diff_agent", test_session_start_auto_close_diff_agent),
        ("test_session_start_auto_close_l4_created", test_session_start_auto_close_l4_created),
        ("test_session_harvest_excludes_derived_l9_decisions", test_session_harvest_excludes_derived_l9_decisions),
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