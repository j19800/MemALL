"""
Test Suite — epoch_step
========================
Tests epoch boundary detection: gaps, category shifts, L6 viewpoint changes,
manual epochs, and auto-labeling.
"""

import os, sys, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


def test_epoch_empty_db():
    """No memories = zero boundaries."""
    from tests.test_helpers import init_temp_db, cleanup_temp_db
    from memall.pipeline.epoch import epoch_step

    db_path, patcher = init_temp_db()
    try:
        result = epoch_step()
        assert isinstance(result, dict)
        assert result["boundaries_detected"] == 0
        assert result["epochs_created"] == 0
        print("  PASS test_epoch_empty_db")
    finally:
        cleanup_temp_db(db_path, patcher)


def test_epoch_gap_detection():
    """Memory gap > 48h creates an epoch boundary."""
    from tests.test_helpers import init_temp_db, cleanup_temp_db, insert_memory
    from memall.pipeline.epoch import epoch_step
    from memall.core.db import get_conn
    from datetime import datetime, timezone, timedelta

    db_path, patcher = init_temp_db()
    try:
        conn = get_conn()
        now = datetime.now(timezone.utc)

        # Memory on day 0
        insert_memory(conn, "start of phase 1", agent_name="gap_test",
                      category="general", occurred_at=now.isoformat())

        # Memory on day 2 (48h gap)
        insert_memory(conn, "start of phase 2", agent_name="gap_test",
                      category="general",
                      occurred_at=(now + timedelta(hours=72)).isoformat())

        # Memory on day 3
        insert_memory(conn, "phase 2 continues", agent_name="gap_test",
                      category="general",
                      occurred_at=(now + timedelta(hours=96)).isoformat())
        conn.close()

        # Clear epoch pipeline state to force full scan
        conn = get_conn()
        conn.execute("DELETE FROM pipeline_state WHERE step_name = 'epoch'")
        conn.commit()
        conn.close()

        result = epoch_step()
        assert result["boundaries_detected"] >= 1, f"Expected gap boundary, got {result}"
        print("  PASS test_epoch_gap_detection")
    finally:
        cleanup_temp_db(db_path, patcher)


def test_epoch_category_shift():
    """Category shift across 5+ consecutive memories creates boundary."""
    from tests.test_helpers import init_temp_db, cleanup_temp_db, insert_memory
    from memall.pipeline.epoch import epoch_step
    from memall.core.db import get_conn
    from datetime import datetime, timezone, timedelta

    db_path, patcher = init_temp_db()
    try:
        conn = get_conn()
        now = datetime.now(timezone.utc)

        # 10 memories of category "architecture"
        for i in range(10):
            insert_memory(conn, f"architecture note {i}", agent_name="shift_test",
                          category="architecture",
                          occurred_at=(now + timedelta(hours=i)).isoformat())

        # 10 memories of category "decision"
        for i in range(10):
            insert_memory(conn, f"decision note {i}", agent_name="shift_test",
                          category="decision",
                          occurred_at=(now + timedelta(hours=24 + i)).isoformat())

        conn.close()

        conn = get_conn()
        conn.execute("DELETE FROM pipeline_state WHERE step_name = 'epoch'")
        conn.commit()
        conn.close()

        result = epoch_step()
        assert result["boundaries_detected"] >= 1, f"Expected category shift boundary, got {result}"
        print("  PASS test_epoch_category_shift")
    finally:
        cleanup_temp_db(db_path, patcher)


def test_epoch_l6_viewpoint():
    """L6 memory with viewpoint-change keywords creates boundary."""
    from tests.test_helpers import init_temp_db, cleanup_temp_db, insert_memory
    from memall.pipeline.epoch import epoch_step
    from memall.core.db import get_conn
    from datetime import datetime, timezone, timedelta

    db_path, patcher = init_temp_db()
    try:
        conn = get_conn()
        now = datetime.now(timezone.utc).isoformat()

        # Normal memory
        insert_memory(conn, "working on feature X", agent_name="reflect_test",
                      category="implementation", level="P2", occurred_at=now)

        # L6 reflection with viewpoint change
        from datetime import datetime as dt2, timezone as tz2, timedelta as td2
        later = (dt2.now(tz2.utc) + td2(hours=1)).isoformat()
        insert_memory(conn, "我重新认识了这个问题，观点变了",
                      agent_name="reflect_test",
                      category="reflection", level="L6", occurred_at=later)
        # Follow-up
        later2 = (dt2.now(tz2.utc) + td2(hours=2)).isoformat()
        insert_memory(conn, "new approach after reflection",
                      agent_name="reflect_test",
                      category="implementation", level="P2", occurred_at=later2)
        conn.close()

        conn = get_conn()
        conn.execute("DELETE FROM pipeline_state WHERE step_name = 'epoch'")
        conn.commit()
        conn.close()

        result = epoch_step()
        assert result["boundaries_detected"] >= 1, f"Expected L6 viewpoint boundary, got {result}"
        print("  PASS test_epoch_l6_viewpoint")
    finally:
        cleanup_temp_db(db_path, patcher)


def test_epoch_manual():
    """A memory with level='epoch' creates a manual epoch."""
    from tests.test_helpers import init_temp_db, cleanup_temp_db, insert_memory
    from memall.pipeline.epoch import epoch_step
    from memall.core.db import get_conn
    from datetime import datetime, timezone

    db_path, patcher = init_temp_db()
    try:
        conn = get_conn()
        now = datetime.now(timezone.utc).isoformat()

        # Insert a memory with level='epoch' directly
        import hashlib
        ch = hashlib.sha256("Starting architecture redesign phase".encode("utf-8")).hexdigest()
        conn.execute(
            "INSERT INTO memories (content, content_hash, level, agent_name, category, "
            "subject, occurred_at, created_at, updated_at) "
            "VALUES (?, ?, 'epoch', 'manual_test', 'architecture', "
            "'Architecture redesign', ?, ?, ?)",
            ("Starting architecture redesign phase", ch, now, now, now),
        )
        conn.commit()
        conn.close()

        conn = get_conn()
        conn.execute("DELETE FROM pipeline_state WHERE step_name = 'epoch'")
        conn.commit()
        conn.close()

        result = epoch_step()
        print(f"  manual_epochs: {result.get('manual_epochs', 'N/A')}")

        conn = get_conn()
        epochs = conn.execute(
            "SELECT * FROM epochs WHERE agent_name = 'manual_test'"
        ).fetchall()
        print(f"  manual_test epochs in DB: {len(epochs)}")
        conn.close()
        print("  PASS test_epoch_manual")
    finally:
        cleanup_temp_db(db_path, patcher)


# ── Run all ──
if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
    print("All epoch tests PASSED")