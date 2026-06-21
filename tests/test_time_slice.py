"""
Test Suite — time_slice_step
=============================
Tests the time_slice pipeline step: backfill, incremental mode,
temporal_weight computation, and derived slices (week/month).
"""

import os, sys, json, math
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


def _get_slice_key(dt):
    return dt.strftime("%Y-%m-%d")


def test_time_slice_backfill_empty():
    """Backfill on empty DB returns zero slices."""
    from tests.test_helpers import init_temp_db, cleanup_temp_db
    from memall.pipeline.time_slice import time_slice_step

    db_path, patcher = init_temp_db()
    try:
        result = time_slice_step()
        assert isinstance(result, dict), f"Expected dict, got {type(result)}"
        assert result["mode"] == "backfill"
        assert result["day_slices_upserted"] == 0
        assert result["temporal_weights_updated"] == 0
        print("  PASS test_time_slice_backfill_empty")
    finally:
        cleanup_temp_db(db_path, patcher)


def test_time_slice_backfill_with_data():
    """Backfill processes all existing memories correctly."""
    from tests.test_helpers import init_temp_db, cleanup_temp_db, insert_memory
    from memall.pipeline.time_slice import time_slice_step
    from memall.core.db import get_conn
    from datetime import datetime, timezone, timedelta

    db_path, patcher = init_temp_db()
    try:
        conn = get_conn()
        now = datetime.now(timezone.utc)

        # Insert memories across 3 days
        for i in range(3):
            dt = (now - timedelta(days=i)).isoformat()
            insert_memory(conn, f"memory day {i}", agent_name="alice",
                          category="test", occurred_at=dt)

        insert_memory(conn, "decision memory", agent_name="alice",
                      category="decision", occurred_at=now.isoformat())
        conn.close()

        # Pipeline state should be empty = backfill mode
        result = time_slice_step()
        assert result["mode"] == "backfill", f"Expected backfill, got {result['mode']}"
        assert result["day_slices_upserted"] >= 3, f"Expected >=3 day slices, got {result}"
        assert result["temporal_weights_updated"] >= 4, f"Expected >=4 weights, got {result}"

        # Verify time_slices exist
        conn = get_conn()
        slices = conn.execute(
            "SELECT * FROM time_slices WHERE agent_name = 'alice' AND granularity = 'day'"
        ).fetchall()
        assert len(slices) >= 3, f"Expected >=3 day slices for alice, got {len(slices)}"
        print("  PASS test_time_slice_backfill_with_data")
        conn.close()
    finally:
        cleanup_temp_db(db_path, patcher)


def test_time_slice_incremental():
    """Incremental mode only processes new memories."""
    from tests.test_helpers import init_temp_db, cleanup_temp_db, insert_memory
    from memall.pipeline.time_slice import time_slice_step
    from memall.core.db import get_conn
    from datetime import datetime, timezone

    db_path, patcher = init_temp_db()
    try:
        conn = get_conn()
        now = datetime.now(timezone.utc).isoformat()

        # First batch
        insert_memory(conn, "original memory", agent_name="bob",
                      category="test", occurred_at=now)
        conn.close()

        # First run: backfill
        r1 = time_slice_step()
        assert r1["day_slices_upserted"] >= 1, f"First run failed: {r1}"

        # Add more memories
        conn = get_conn()
        insert_memory(conn, "new memory 1", agent_name="bob",
                      category="test", occurred_at=now)
        insert_memory(conn, "new memory 2", agent_name="bob",
                      category="decision", occurred_at=now)
        conn.close()

        # Second run: should be incremental
        r2 = time_slice_step()
        assert r2["mode"] == "incremental", f"Expected incremental, got {r2['mode']}"
        assert r2["temporal_weights_updated"] >= 2, f"Expected >=2 new weights, got {r2}"
        print("  PASS test_time_slice_incremental")
    finally:
        cleanup_temp_db(db_path, patcher)


def test_temporal_weight_formula():
    """temporal_weight follows the expected decay formula."""
    from tests.test_helpers import init_temp_db, cleanup_temp_db, insert_memory
    from memall.pipeline.time_slice import time_slice_step
    from memall.core.db import get_conn
    from datetime import datetime, timezone, timedelta

    db_path, patcher = init_temp_db()
    try:
        conn = get_conn()
        now = datetime.now(timezone.utc)
        # Insert a very old memory (should have low weight)
        old_dt = (now - timedelta(days=200)).isoformat()
        insert_memory(conn, "old memory", agent_name="carol",
                      category="test", occurred_at=old_dt)
        # Insert a recent memory (should have high weight)
        recent_dt = now.isoformat()
        insert_memory(conn, "recent memory", agent_name="carol",
                      category="test", occurred_at=recent_dt)
        conn.close()

        time_slice_step()

        conn = get_conn()
        rows = conn.execute(
            "SELECT id, content, metadata FROM memories WHERE agent_name = 'carol' ORDER BY id"
        ).fetchall()
        assert len(rows) == 2

        old_mem = json.loads(rows[0]["metadata"])
        new_mem = json.loads(rows[1]["metadata"])

        old_w = old_mem.get("temporal_weight", 0)
        new_w = new_mem.get("temporal_weight", 0)

        # Old memory should have significantly lower weight
        # exp(-0.01 * 200) = exp(-2) ≈ 0.135
        assert old_w < 0.5, f"Old memory weight {old_w} should be low"
        # Recent should be close to 1.0
        assert new_w > 0.9, f"Recent memory weight {new_w} should be high"
        assert new_w > old_w, f"New weight {new_w} should exceed old weight {old_w}"
        print("  PASS test_temporal_weight_formula")
        conn.close()
    finally:
        cleanup_temp_db(db_path, patcher)


def test_derive_week_month():
    """Day slices should have corresponding week/month slices derived."""
    from tests.test_helpers import init_temp_db, cleanup_temp_db, insert_memory
    from memall.pipeline.time_slice import time_slice_step
    from memall.core.db import get_conn
    from datetime import datetime, timezone

    db_path, patcher = init_temp_db()
    try:
        conn = get_conn()
        now = datetime.now(timezone.utc).isoformat()
        insert_memory(conn, "test data", agent_name="dave",
                      category="test", occurred_at=now)
        conn.close()

        time_slice_step()

        conn = get_conn()
        for g in ("day", "week", "month"):
            rows = conn.execute(
                "SELECT * FROM time_slices WHERE agent_name = 'dave' AND granularity = ?",
                (g,),
            ).fetchall()
            assert len(rows) >= 1, f"No {g} slice for dave"
        print("  PASS test_derive_week_month")
        conn.close()
    finally:
        cleanup_temp_db(db_path, patcher)


def test_self_healing():
    """Crash mid-step is safe: next run recovers (idempotent upsert)."""
    from tests.test_helpers import init_temp_db, cleanup_temp_db, insert_memory
    from memall.pipeline.time_slice import time_slice_step
    from memall.core.db import get_conn
    from datetime import datetime, timezone

    db_path, patcher = init_temp_db()
    try:
        conn = get_conn()
        now = datetime.now(timezone.utc).isoformat()
        insert_memory(conn, "data 1", agent_name="eve", category="test", occurred_at=now)
        insert_memory(conn, "data 2", agent_name="eve", category="test", occurred_at=now)
        conn.close()

        # Run once (backfill)
        time_slice_step()

        # Run again (incremental, should be idempotent)
        r1 = time_slice_step()
        r2 = time_slice_step()  # third run, no new data

        # Slices should not double-count
        conn = get_conn()
        count = conn.execute(
            "SELECT memory_count FROM time_slices WHERE agent_name = 'eve' "
            "AND granularity = 'day' LIMIT 1"
        ).fetchone()
        assert count is not None
        assert count["memory_count"] <= 3, f"Memory count inflated: {count['memory_count']}"
        print("  PASS test_self_healing")
        conn.close()
    finally:
        cleanup_temp_db(db_path, patcher)


# ── Run all ──
if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
    print("All time_slice tests PASSED")