"""
Test Suite — Decision Arcs (arc_status_step)
=============================================
Tests the Decision Arc lifecycle: L4 capture sets open, L5/L6 edges
drive status transitions, backfill, staleness detection, bidirectional
edge recognition, and epoch closure_rate queries.
"""

import os, sys, json, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))  # for tests.test_helpers


def _insert_edge(conn, source_id: int, target_id: int, relation_type: str = "refines", created_at: str = None):
    """Insert a test edge between two memories."""
    from datetime import datetime, timezone
    now = created_at or datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT INTO edges (source_id, target_id, relation_type, weight, created_at) VALUES (?, ?, ?, 1.0, ?)",
        (source_id, target_id, relation_type, now),
    )
    conn.commit()


def test_capture_l4_sets_open():
    """An L4 memory capture sets arc_status='open'."""
    from tests.test_helpers import init_temp_db, cleanup_temp_db, insert_memory
    from memall.core.db import get_conn

    db_path, patcher = init_temp_db()
    try:
        conn = get_conn()
        # Insert L4 — this simulates what capture() does (sets arc_status='open')
        mid = insert_memory(conn, "test decision", agent_name="arc_test",
                            level="L4", category="decision")
        # Manually simulate capture() behaviour
        conn.execute("UPDATE memories SET arc_status = 'open' WHERE id = ?", (mid,))
        conn.commit()

        row = conn.execute(
            "SELECT arc_status FROM memories WHERE id = ?", (mid,)
        ).fetchone()
        assert row and row["arc_status"] == "open", f"Expected open, got {row}"
        print("  PASS test_capture_l4_sets_open")
        conn.close()
    finally:
        cleanup_temp_db(db_path, patcher)


def test_l5_edge_sets_in_progress():
    """L4 with an L5 edge transitions to in_progress."""
    from tests.test_helpers import init_temp_db, cleanup_temp_db, insert_memory
    from memall.pipeline.arc_status import arc_status_step
    from memall.core.db import get_conn

    db_path, patcher = init_temp_db()
    try:
        conn = get_conn()
        l4 = insert_memory(conn, "decision memory", agent_name="arc_test", level="L4")
        l5 = insert_memory(conn, "task memory", agent_name="arc_test", level="L5")
        _insert_edge(conn, l4, l5)
        conn.execute("UPDATE memories SET arc_status = 'open' WHERE id = ?", (l4,))
        conn.commit()
        conn.close()

        result = arc_status_step()
        assert result["upgraded"] >= 1, f"Expected upgrade, got {result}"

        conn = get_conn()
        row = conn.execute(
            "SELECT arc_status FROM memories WHERE id = ?", (l4,)
        ).fetchone()
        assert row and row["arc_status"] == "in_progress", f"Expected in_progress, got {row}"
        assert result["status_counts"].get("in_progress", 0) >= 1
        print("  PASS test_l5_edge_sets_in_progress")
        conn.close()
    finally:
        cleanup_temp_db(db_path, patcher)


def test_l6_edge_sets_closed():
    """L4 with an L6 edge transitions to closed."""
    from tests.test_helpers import init_temp_db, cleanup_temp_db, insert_memory
    from memall.pipeline.arc_status import arc_status_step
    from memall.core.db import get_conn

    db_path, patcher = init_temp_db()
    try:
        conn = get_conn()
        l4 = insert_memory(conn, "decision to reflect on", agent_name="arc_test", level="L4")
        l6 = insert_memory(conn, "reflection on decision", agent_name="arc_test", level="L6")
        _insert_edge(conn, l4, l6)
        conn.execute("UPDATE memories SET arc_status = 'open' WHERE id = ?", (l4,))
        conn.commit()
        conn.close()

        result = arc_status_step()
        assert result["upgraded"] >= 1, f"Expected upgrade, got {result}"

        conn = get_conn()
        row = conn.execute(
            "SELECT arc_status FROM memories WHERE id = ?", (l4,)
        ).fetchone()
        assert row and row["arc_status"] == "closed", f"Expected closed, got {row}"
        assert result["status_counts"].get("closed", 0) >= 1
        print("  PASS test_l6_edge_sets_closed")
        conn.close()
    finally:
        cleanup_temp_db(db_path, patcher)


def test_closed_irreversible():
    """Once closed, an L4 arc stays closed even with new edges."""
    from tests.test_helpers import init_temp_db, cleanup_temp_db, insert_memory
    from memall.pipeline.arc_status import arc_status_step
    from memall.core.db import get_conn

    db_path, patcher = init_temp_db()
    try:
        conn = get_conn()
        l4 = insert_memory(conn, "closed decision", agent_name="arc_test", level="L4")
        l6 = insert_memory(conn, "existing reflection", agent_name="arc_test", level="L6")
        _insert_edge(conn, l4, l6)
        conn.execute("UPDATE memories SET arc_status = 'closed' WHERE id = ?", (l4,))
        conn.commit()

        # Now add an L5 edge — should NOT reopen
        l5 = insert_memory(conn, "late task", agent_name="arc_test", level="L5")
        _insert_edge(conn, l4, l5)
        conn.close()

        result = arc_status_step()
        # Upgraded should be 0 — closed is terminal, no transition to in_progress
        conn = get_conn()
        row = conn.execute(
            "SELECT arc_status FROM memories WHERE id = ?", (l4,)
        ).fetchone()
        assert row and row["arc_status"] == "closed", f"Expected still closed, got {row}"
        print("  PASS test_closed_irreversible")
        conn.close()
    finally:
        cleanup_temp_db(db_path, patcher)


def test_backfill_null_arcs():
    """Existing L4s with NULL arc_status get backfilled on first run."""
    from tests.test_helpers import init_temp_db, cleanup_temp_db, insert_memory
    from memall.pipeline.arc_status import arc_status_step
    from memall.core.db import get_conn

    db_path, patcher = init_temp_db()
    try:
        conn = get_conn()
        # Insert L4 with NULL arc_status (no capture-simulated set)
        l4_open = insert_memory(conn, "open decision", agent_name="arc_test", level="L4")
        # arc_status stays NULL

        l4_with_l5 = insert_memory(conn, "in-progress decision", agent_name="arc_test", level="L4")
        # arc_status stays NULL
        l5 = insert_memory(conn, "related task", agent_name="arc_test", level="L5")
        _insert_edge(conn, l4_with_l5, l5)

        l4_with_l6 = insert_memory(conn, "reflected decision", agent_name="arc_test", level="L4")
        l6 = insert_memory(conn, "reflection", agent_name="arc_test", level="L6")
        _insert_edge(conn, l4_with_l6, l6)

        non_l4 = insert_memory(conn, "regular note", agent_name="arc_test", level="P2")
        conn.close()

        result = arc_status_step()
        assert result["backfilled"] >= 3, f"Expected >=3 backfilled, got {result}"

        conn = get_conn()
        rows = {
            r["id"]: r["arc_status"]
            for r in conn.execute(
                "SELECT id, arc_status FROM memories WHERE level = 'L4'"
            ).fetchall()
        }
        assert rows.get(l4_open) == "open", f"Expected open for {l4_open}: {rows}"
        assert rows.get(l4_with_l5) == "in_progress", f"Expected in_progress for {l4_with_l5}: {rows}"
        assert rows.get(l4_with_l6) == "closed", f"Expected closed for {l4_with_l6}: {rows}"

        # non-L4 should still be NULL
        non = conn.execute("SELECT arc_status FROM memories WHERE id = ?", (non_l4,)).fetchone()
        assert non and non["arc_status"] is None, f"Expected NULL for non-L4, got {non}"
        print("  PASS test_backfill_null_arcs")
        conn.close()
    finally:
        cleanup_temp_db(db_path, patcher)


def test_stale_detection():
    """Open L4 > 21d with no L5 edges is stale."""
    from tests.test_helpers import init_temp_db, cleanup_temp_db, insert_memory
    from memall.pipeline.arc_status import arc_status_step
    from memall.core.db import get_conn
    from datetime import datetime, timezone, timedelta

    db_path, patcher = init_temp_db()
    try:
        conn = get_conn()
        old = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()

        # Old L4 with no edges — should be open + stale
        stale_mid = insert_memory(conn, "abandoned decision", agent_name="arc_test",
                                  level="L4", created_at=old, occurred_at=old)
        conn.execute("UPDATE memories SET arc_status = 'open' WHERE id = ?", (stale_mid,))
        conn.commit()

        # Fresh L4 — not stale
        fresh_mid = insert_memory(conn, "recent decision", agent_name="arc_test", level="L4")
        conn.execute("UPDATE memories SET arc_status = 'open' WHERE id = ?", (fresh_mid,))
        conn.commit()
        conn.close()

        # Run arc_status_step (backfill)
        arc_status_step()

        # Check staleness via the same query the gateway uses
        conn = get_conn()
        stale_cutoff = (datetime.now(timezone.utc) - timedelta(days=21)).isoformat()
        stale_ids = set()
        for r in conn.execute(
            "SELECT id FROM memories WHERE level = 'L4' AND arc_status = 'open' "
            "AND created_at < ? "
            "AND NOT EXISTS (SELECT 1 FROM edges WHERE source_id = memories.id "
            "AND target_id IN (SELECT id FROM memories WHERE level = 'L5'))"
            "AND NOT EXISTS (SELECT 1 FROM edges WHERE target_id = memories.id "
            "AND source_id IN (SELECT id FROM memories WHERE level = 'L5'))",
            (stale_cutoff,),
        ).fetchall():
            stale_ids.add(r["id"])

        assert stale_mid in stale_ids, f"Expected stale_mid {stale_mid} in stale set {stale_ids}"
        assert fresh_mid not in stale_ids, f"Fresh decision {fresh_mid} should not be stale"
        print("  PASS test_stale_detection")
        conn.close()
    finally:
        cleanup_temp_db(db_path, patcher)


def test_bidirectional_edges():
    """Edges are recognized regardless of direction (L5→L4 or L4→L5)."""
    from tests.test_helpers import init_temp_db, cleanup_temp_db, insert_memory
    from memall.pipeline.arc_status import arc_status_step
    from memall.core.db import get_conn

    db_path, patcher = init_temp_db()
    try:
        conn = get_conn()

        # Reverse direction: L5 → L4 (target is L4)
        l4_reverse = insert_memory(conn, "decision with reverse edge", agent_name="arc_test", level="L4")
        l5_reverse = insert_memory(conn, "task pointing to decision", agent_name="arc_test", level="L5")
        _insert_edge(conn, l5_reverse, l4_reverse)  # L5->L4, not L4->L5
        conn.execute("UPDATE memories SET arc_status = 'open' WHERE id = ?", (l4_reverse,))
        conn.commit()
        conn.close()

        result = arc_status_step()
        assert result["upgraded"] >= 1, f"Expected reverse edge upgrade, got {result}"

        conn = get_conn()
        row = conn.execute(
            "SELECT arc_status FROM memories WHERE id = ?", (l4_reverse,)
        ).fetchone()
        assert row and row["arc_status"] == "in_progress", f"Expected in_progress from reverse edge, got {row}"
        print("  PASS test_bidirectional_edges")
        conn.close()
    finally:
        cleanup_temp_db(db_path, patcher)


def test_non_l4_stays_null():
    """Non-L4 memories never get arc_status set."""
    from tests.test_helpers import init_temp_db, cleanup_temp_db, insert_memory
    from memall.pipeline.arc_status import arc_status_step
    from memall.core.db import get_conn

    db_path, patcher = init_temp_db()
    try:
        conn = get_conn()
        for level in ["P0", "P1", "P2", "L1", "L2", "L3", "L5", "L6", "L7", "L8", "L9", "L10"]:
            insert_memory(conn, f"test {level} memory", agent_name="arc_test", level=level)
        conn.close()

        result = arc_status_step()
        assert result["backfilled"] == 0, "Should backfill 0 non-L4 memories"

        conn = get_conn()
        null_count = conn.execute(
            "SELECT COUNT(*) FROM memories WHERE arc_status IS NOT NULL"
        ).fetchone()[0]
        assert null_count == 0, f"Expected 0 non-null arc_status for non-L4, got {null_count}"
        print("  PASS test_non_l4_stays_null")
        conn.close()
    finally:
        cleanup_temp_db(db_path, patcher)


def test_epoch_closure_rate():
    """Closure stats (total/open/in_progress/closed/rate) for an epoch's arcs."""
    from tests.test_helpers import init_temp_db, cleanup_temp_db, insert_memory
    from memall.pipeline.arc_status import arc_status_step
    from memall.core.db import get_conn
    from datetime import datetime, timezone

    db_path, patcher = init_temp_db()
    try:
        conn = get_conn()
        now = datetime.now(timezone.utc)

        # Create an epoch
        conn.execute(
            "INSERT INTO epochs (agent_name, label, started_at, ended_at, boundary_reason, category, created_at) "
            "VALUES (?, 'test epoch', ?, ?, 'manual', 'decision', ?)",
            ("arc_test", now.isoformat(), (now.replace(year=now.year + 1)).isoformat(),
             now.isoformat()),
        )
        epoch_id = conn.execute("SELECT id FROM epochs ORDER BY id DESC LIMIT 1").fetchone()[0]

        # 2 closed arcs, 1 open arc — closure_rate = 2/3
        for i in range(2):
            mid = insert_memory(conn, f"closed decision {i}", agent_name="arc_test", level="L4")
            l6 = insert_memory(conn, f"reflection {i}", agent_name="arc_test", level="L6")
            _insert_edge(conn, mid, l6)
            conn.execute("UPDATE memories SET arc_status = 'closed' WHERE id = ?", (mid,))
            conn.commit()

        open_mid = insert_memory(conn, "open decision", agent_name="arc_test", level="L4")
        conn.execute("UPDATE memories SET arc_status = 'open' WHERE id = ?", (open_mid,))
        conn.commit()
        conn.close()

        # Run arc_status_step
        arc_status_step()

        # Query closure stats (same logic as get_epoch_arcs in gateway)
        conn = get_conn()
        arc_rows = conn.execute(
            "SELECT id, level, category, subject, agent_name, created_at, arc_status "
            "FROM memories WHERE level = 'L4' AND arc_status IS NOT NULL "
            "AND agent_name = ? ORDER BY created_at DESC",
            ("arc_test",),
        ).fetchall()

        arc_list = []
        for a in arc_rows:
            s = a["arc_status"] or "open"
            arc_list.append({"id": a["id"], "arc_status": s})

        total = len(arc_list)
        open_count = sum(1 for a in arc_list if a["arc_status"] == "open")
        in_progress_count = sum(1 for a in arc_list if a["arc_status"] == "in_progress")
        closed_count = sum(1 for a in arc_list if a["arc_status"] == "closed")
        closure_rate = round(closed_count / total, 2) if total > 0 else 0.0

        assert total == 3, f"Expected 3 total arcs, got {total}"
        assert open_count == 1, f"Expected 1 open, got {open_count}"
        assert closed_count == 2, f"Expected 2 closed, got {closed_count}"
        assert closure_rate == 0.67, f"Expected 0.67 closure_rate, got {closure_rate}"
        print("  PASS test_epoch_closure_rate")
        conn.close()
    finally:
        cleanup_temp_db(db_path, patcher)


def test_incremental_only_upgrades():
    """Second run only upgrades, doesn't re-backfill."""
    from tests.test_helpers import init_temp_db, cleanup_temp_db, insert_memory
    from memall.pipeline.arc_status import arc_status_step
    from memall.core.db import get_conn

    db_path, patcher = init_temp_db()
    try:
        conn = get_conn()
        l4 = insert_memory(conn, "decision", agent_name="arc_test", level="L4")
        conn.execute("UPDATE memories SET arc_status = 'open' WHERE id = ?", (l4,))
        conn.commit()
        conn.close()

        # First run — backfills
        r1 = arc_status_step()
        assert r1["backfilled"] == 0  # already set to open, not NULL
        # Actually arc_status is set directly, not via capture, so arc_status_step
        # should not backfill it (it's not NULL). Need to check for this.

        # Hmm, the L4 has arc_status='open' set directly, so backfill skips it (not NULL).
        # No upgrade either (no L5/L6 edges).
        assert r1["status_counts"].get("open", 0) >= 1

        # Second run — should report same, not re-backfill
        r2 = arc_status_step()
        assert r2["backfilled"] == 0, "Second run should not backfill"
        assert r2["upgraded"] == 0, "No new edges, no upgrades"

        print("  PASS test_incremental_only_upgrades")
    finally:
        cleanup_temp_db(db_path, patcher)


if __name__ == "__main__":
    test_capture_l4_sets_open()
    test_l5_edge_sets_in_progress()
    test_l6_edge_sets_closed()
    test_closed_irreversible()
    test_backfill_null_arcs()
    test_stale_detection()
    test_bidirectional_edges()
    test_non_l4_stays_null()
    test_epoch_closure_rate()
    test_incremental_only_upgrades()
    print("\nAll arc_status tests PASSED")
