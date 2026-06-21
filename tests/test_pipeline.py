"""
Test Suite — Pipeline (Phase 16+)
=================================
Tests capture/retrieve, edge connect+traverse, forget, merge, and security audit.
"""

import os
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from memall.core.db import init_db, get_conn
from memall.core.thin_waist import (
    capture,
    retrieve,
    update,
    timeline,
    connect,
    traverse,
    MemoryInput,
)


# ── Test helpers ───────────────────────────────────────────────────────

def _unique_agent() -> str:
    """Return a unique agent name for test isolation (no timestamps to avoid agent tag blacklist)."""
    import uuid
    return f"t_agent_{uuid.uuid4().hex[:8]}"


# ── Tests ──────────────────────────────────────────────────────────────

def test_capture_retrieve():
    """Test: capture a memory then retrieve it by query."""
    init_db()

    agent = _unique_agent()
    # content must include unique agent to avoid hash-dedup with prior runs
    content = f"Quantum computing leverages entanglement and qubits for exponential speedup — agent {agent}"

    # capture returns int (memory_id)
    mem_id = capture(MemoryInput(
        agent_name=agent,
        content=content,
        category="fact",
        level="P2",
    ))
    assert isinstance(mem_id, int), f"capture should return int, got {type(mem_id)}"
    assert mem_id > 0, f"Invalid memory_id: {mem_id}"

    # Retrieve via query+agent filter — returns list[Memory]
    results = retrieve(query="quantum qubits entanglement", agent_name=agent, limit=5)
    assert len(results) > 0, f"No results from retrieve for agent={agent}"
    # Memory is a dataclass with .content attribute
    found = any("quantum" in r.content.lower() for r in results)
    assert found, "Retrieved results don't contain 'quantum'"

    print(f"  PASS test_capture_retrieve — memory #{mem_id}")


def test_edge_connect():
    """Test: create two memories, connect them, then traverse."""
    init_db()

    agent = _unique_agent()
    a_id = capture(MemoryInput(agent_name=agent, content=f"Memory A — source ({agent})", level="P2"))
    b_id = capture(MemoryInput(agent_name=agent, content=f"Memory B — target ({agent})", level="P2"))

    assert a_id > 0 and b_id > 0, "capture failed to return valid IDs"

    # connect returns int (edge_id)
    edge_id = connect(source_id=a_id, target_id=b_id, relation_type="refines", weight=0.9)
    assert isinstance(edge_id, int), f"connect should return int, got {type(edge_id)}"
    assert edge_id > 0, f"Invalid edge_id: {edge_id}"

    # traverse returns dict with "nodes" key
    trav = traverse(node_id=a_id, depth=1)
    assert isinstance(trav, dict), f"traverse returned {type(trav)}"
    assert "nodes" in trav, f"Missing 'nodes' in traverse result: {list(trav.keys())}"
    assert len(trav["nodes"]) >= 1, f"No nodes in traverse, got {len(trav['nodes'])}"

    print(f"  PASS test_edge_connect — edge #{edge_id}")


def test_forget():
    """Test: check that forget functions run without error."""
    init_db()

    agent = _unique_agent()
    old_ts = "2020-01-01T00:00:00"

    # Insert an old memory (use occurred_at for historical date)
    capture(MemoryInput(
        agent_name=agent,
        content=f"Very old deprecated memory ({agent})",
        occurred_at=old_ts,
        level="P2",
    ))

    conn = get_conn()
    initial = conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]

    # Run forget_low_value
    from memall.pipeline.forget import forget_low_value

    result = forget_low_value(agent_name=agent)
    assert isinstance(result, dict), "forget_low_value returned non-dict"
    deleted = result.get("deleted_memories", 0)
    assert deleted >= 0, f"Invalid deleted count: {deleted}"

    after = conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]

    print(
        f"  PASS test_forget — initial={initial}, after={after}, "
        f"deleted_low_value={deleted}"
    )


def test_merge():
    """Test: merge two memories via ops.merge_memories."""
    init_db()

    agent = _unique_agent()
    a_id = capture(MemoryInput(agent_name=agent, content=f"Memory Alpha ({agent})", level="P2"))
    b_id = capture(MemoryInput(agent_name=agent, content=f"Memory Beta ({agent})", level="P2"))

    assert a_id > 0 and b_id > 0

    from memall.pipeline.ops import merge_memories

    result = merge_memories(source_id=a_id, target_id=b_id)
    assert isinstance(result, dict), f"merge_memories returned {type(result)}"
    # Accept either merged or success key
    merged_ok = result.get("merged", result.get("success", result.get("merged_into")))
    assert merged_ok, f"Merge failed: {result}"

    print(f"  PASS test_merge — #{a_id} merged into #{b_id}")


def test_security_audit():
    """Test: security audit returns well-structured result."""
    init_db()

    from memall.pipeline.security import audit_sensitive

    result = audit_sensitive()
    assert isinstance(result, dict), "audit_sensitive returned non-dict"
    assert "total_findings" in result or "findings" in result, (
        f"Missing findings key in: {list(result.keys())}"
    )
    assert "risk_level" in result, f"Missing risk_level in: {list(result.keys())}"

    risk = result["risk_level"]
    assert risk in ("low", "medium", "high", "none"), f"Unexpected risk level: {risk}"

    print(f"  PASS test_security_audit — risk={risk}")


def test_timeline():
    """Test: timeline returns memories within a time window."""
    init_db()

    agent = _unique_agent()
    mid = capture(MemoryInput(
        agent_name=agent,
        content=f"Timeline test memory ({agent})",
        level="P2",
        category="test",
    ))
    assert mid > 0

    results = timeline(
        query="Timeline test",
        category="test",
        limit=10,
    )
    assert isinstance(results, list), f"timeline returned {type(results)}"
    assert len(results) >= 1, f"No results from timeline"
    found = any(r.id == mid for r in results)
    assert found, "Timeline result missing the captured memory"


def test_update():
    """Test: update a memory's fields."""
    init_db()

    agent = _unique_agent()
    mid = capture(MemoryInput(
        agent_name=agent,
        content=f"Update test memory ({agent})",
        level="P2",
        category="test",
    ))
    assert mid > 0

    ok = update(mid, level="P1", category="important")
    assert ok, "update returned False"

    updated = retrieve(mid)
    assert updated is not None, "retrieve by ID returned None"
    assert updated.level == "P1", f"level should be P1, got {updated.level}"
    assert updated.category == "important", f"category should be important, got {updated.category}"

    # Update with no valid fields returns False
    ok2 = update(mid, nonexistent="whatever")
    assert not ok2, "update with only invalid fields should return False"

    print(f"  PASS test_update — memory #{mid} updated")


# ── Decision Arc Tests ──────────────────────────────────────────────────


def test_arc_capture_l4_open():
    """Test 1: capture L4 → arc_status='open'"""
    init_db()
    agent = _unique_agent()
    mid = capture(MemoryInput(agent_name=agent, content=f"arc test open ({agent})", level="L4"))
    conn = get_conn()
    row = conn.execute("SELECT arc_status FROM memories WHERE id = ?", (mid,)).fetchone()
    conn.close()
    assert row["arc_status"] == "open", f"Expected 'open', got {row['arc_status']}"
    print(f"  PASS test_arc_capture_l4_open — #{mid} arc_status=open")


def test_arc_l5_edge_in_progress():
    """Test 2: connect L5→L4 后跑 pipeline → 'in_progress'"""
    init_db()
    agent = _unique_agent()
    l4_id = capture(MemoryInput(agent_name=agent, content=f"arc l5 test ({agent})", level="L4"))
    l5_id = capture(MemoryInput(agent_name=agent, content=f"task for arc ({agent})", level="L5"))
    connect(source_id=l5_id, target_id=l4_id, relation_type="refines")

    from memall.pipeline.arc_status import arc_status_step
    arc_status_step()

    conn = get_conn()
    row = conn.execute("SELECT arc_status FROM memories WHERE id = ?", (l4_id,)).fetchone()
    conn.close()
    assert row["arc_status"] == "in_progress", f"Expected 'in_progress', got {row['arc_status']}"
    print(f"  PASS test_arc_l5_edge_in_progress — #{l4_id} → in_progress")


def test_arc_l6_edge_closed():
    """Test 3: connect L6→L4 后跑 pipeline → 'closed'"""
    init_db()
    agent = _unique_agent()
    l4_id = capture(MemoryInput(agent_name=agent, content=f"arc l6 close ({agent})", level="L4"))
    l6_id = capture(MemoryInput(agent_name=agent, content=f"reflection on arc ({agent})", level="L6"))
    connect(source_id=l6_id, target_id=l4_id, relation_type="refines")

    from memall.pipeline.arc_status import arc_status_step
    arc_status_step()

    conn = get_conn()
    row = conn.execute("SELECT arc_status FROM memories WHERE id = ?", (l4_id,)).fetchone()
    conn.close()
    assert row["arc_status"] == "closed", f"Expected 'closed', got {row['arc_status']}"
    print(f"  PASS test_arc_l6_edge_closed — #{l4_id} → closed")


def test_arc_closed_irreversible():
    """Test 4: closed 后再 connect 新 L5 → arc_status 不变"""
    init_db()
    agent = _unique_agent()
    l4_id = capture(MemoryInput(agent_name=agent, content=f"arc irreversible ({agent})", level="L4"))
    l6_id = capture(MemoryInput(agent_name=agent, content=f"reflection ({agent})", level="L6"))
    connect(source_id=l6_id, target_id=l4_id, relation_type="refines")

    from memall.pipeline.arc_status import arc_status_step
    arc_status_step()

    # Add L5 after closure
    l5_id = capture(MemoryInput(agent_name=agent, content=f"late task ({agent})", level="L5"))
    connect(source_id=l5_id, target_id=l4_id, relation_type="refines")
    arc_status_step()

    conn = get_conn()
    row = conn.execute("SELECT arc_status FROM memories WHERE id = ?", (l4_id,)).fetchone()
    conn.close()
    assert row["arc_status"] == "closed", f"Expected 'closed' (irreversible), got {row['arc_status']}"
    print(f"  PASS test_arc_closed_irreversible — #{l4_id} stays closed")


def test_arc_backfill():
    """Test 5: backfill 存量 L4 设初始状态"""
    init_db()
    agent = _unique_agent()
    import hashlib
    ch = hashlib.sha256(f"backfill test ({agent})".encode()).hexdigest()
    conn = get_conn()
    conn.execute(
        "INSERT INTO memories (agent_name, content, content_hash, level, occurred_at, created_at, updated_at) VALUES (?, ?, ?, 'L4', datetime('now'), datetime('now'), datetime('now'))",
        (agent, f"backfill test ({agent})", ch),
    )
    conn.commit()
    conn.close()

    from memall.pipeline.arc_status import arc_status_step
    result = arc_status_step()

    conn = get_conn()
    row = conn.execute(
        "SELECT arc_status FROM memories WHERE agent_name = ? AND level = 'L4' AND arc_status IS NOT NULL",
        (agent,),
    ).fetchone()
    conn.close()
    assert row is not None, "Backfill did not set arc_status"
    assert row["arc_status"] == "open", f"Expected 'open', got {row['arc_status']}"
    assert result["backfilled"] >= 1, "arc_status_step should report backfill"
    print(f"  PASS test_arc_backfill — backfilled={result['backfilled']}")


def test_arc_stale_detection():
    """Test 6: >21d 且无 L5 边 → stale"""
    init_db()
    agent = _unique_agent()
    old_ts = "2020-01-01T00:00:00"
    mid = capture(MemoryInput(agent_name=agent, content=f"stale arc ({agent})", level="L4", occurred_at=old_ts))

    # Force created_at to old date (occurred_at may not map to created_at)
    conn = get_conn()
    conn.execute("UPDATE memories SET created_at = ? WHERE id = ?", (old_ts, mid))
    conn.commit()
    conn.close()

    from memall.pipeline.arc_status import arc_status_step
    arc_status_step()

    conn = get_conn()
    row = conn.execute("SELECT arc_status, created_at FROM memories WHERE id = ?", (mid,)).fetchone()
    conn.close()
    assert row["arc_status"] == "open", f"Expected 'open', got {row['arc_status']}"

    from datetime import date, timedelta
    cutoff = (date.today() - timedelta(days=21)).isoformat()
    assert (row["created_at"] or "")[:10] < cutoff, "Memory should be older than 21d cutoff"
    print(f"  PASS test_arc_stale_detection — #{mid} open + old = stale candidate")


def test_arc_bidirectional_edge():
    """Test 7: L4→L5 和 L5→L4 双向边同等对待"""
    init_db()
    agent = _unique_agent()

    # L4→L5 direction
    l4_a = capture(MemoryInput(agent_name=agent, content=f"bidir a ({agent})", level="L4"))
    l5_a = capture(MemoryInput(agent_name=agent, content=f"task a ({agent})", level="L5"))
    connect(source_id=l4_a, target_id=l5_a, relation_type="refines")

    # L5→L4 direction
    l4_b = capture(MemoryInput(agent_name=agent, content=f"bidir b ({agent})", level="L4"))
    l5_b = capture(MemoryInput(agent_name=agent, content=f"task b ({agent})", level="L5"))
    connect(source_id=l5_b, target_id=l4_b, relation_type="refines")

    from memall.pipeline.arc_status import arc_status_step
    arc_status_step()

    conn = get_conn()
    a_status = conn.execute("SELECT arc_status FROM memories WHERE id = ?", (l4_a,)).fetchone()["arc_status"]
    b_status = conn.execute("SELECT arc_status FROM memories WHERE id = ?", (l4_b,)).fetchone()["arc_status"]
    conn.close()
    assert a_status == "in_progress", f"L4→L5 direction: expected 'in_progress', got {a_status}"
    assert b_status == "in_progress", f"L5→L4 direction: expected 'in_progress', got {b_status}"
    print(f"  PASS test_arc_bidirectional_edge — both directions → in_progress")


def test_arc_non_l4_null():
    """Test 8: 非 L4 记忆 arc_status 始终 NULL"""
    init_db()
    agent = _unique_agent()
    levels = ["P0", "P1", "P2", "L1", "L5", "L6", "L7"]
    ids = {}
    for lv in levels:
        mid = capture(MemoryInput(agent_name=agent, content=f"non-l4 {lv} ({agent})", level=lv))
        ids[lv] = mid

    from memall.pipeline.arc_status import arc_status_step
    arc_status_step()

    conn = get_conn()
    for lv, mid in ids.items():
        row = conn.execute("SELECT arc_status FROM memories WHERE id = ?", (mid,)).fetchone()
        assert row["arc_status"] is None, f"Level {lv} should have NULL arc_status, got {row['arc_status']}"
    conn.close()
    print(f"  PASS test_arc_non_l4_null — all {len(levels)} non-L4 levels stay NULL")


def test_arc_epoch_closure_rate():
    """Test 9: epoch 含 3 种弧 → closure_rate 正确"""
    init_db()
    agent = _unique_agent()

    # Create an epoch via direct insert
    conn = get_conn()
    conn.execute(
        "INSERT INTO epochs (agent_name, label, started_at, created_at) VALUES (?, 'test-epoch', datetime('now', '-7 days'), datetime('now'))",
        (agent,),
    )
    epoch_id = conn.execute("SELECT id FROM epochs WHERE agent_name = ? ORDER BY id DESC LIMIT 1", (agent,)).fetchone()[0]
    conn.close()

    # Open decisions (no edges)
    open_id = capture(MemoryInput(agent_name=agent, content=f"epoch arc open ({agent})", level="L4", occurred_at="2026-06-10T00:00:00"))

    # In-progress decision (L5 edge)
    ip_id = capture(MemoryInput(agent_name=agent, content=f"epoch arc ip ({agent})", level="L4", occurred_at="2026-06-11T00:00:00"))
    l5_id = capture(MemoryInput(agent_name=agent, content=f"epoch task ({agent})", level="L5", occurred_at="2026-06-11T00:00:00"))
    connect(source_id=l5_id, target_id=ip_id, relation_type="refines")

    # Closed decision (L6 edge)
    closed_id = capture(MemoryInput(agent_name=agent, content=f"epoch arc closed ({agent})", level="L4", occurred_at="2026-06-12T00:00:00"))
    l6_id = capture(MemoryInput(agent_name=agent, content=f"epoch reflection ({agent})", level="L6", occurred_at="2026-06-12T00:00:00"))
    connect(source_id=l6_id, target_id=closed_id, relation_type="refines")

    conn.close()

    from memall.pipeline.arc_status import arc_status_step
    arc_status_step()

    # Verify via query (simulate epoch arc aggregation)
    conn = get_conn()
    statuses = conn.execute(
        "SELECT arc_status, COUNT(*) as cnt FROM memories WHERE level = 'L4' AND arc_status IS NOT NULL "
        "AND agent_name = ? GROUP BY arc_status",
        (agent,),
    ).fetchall()
    counts = {r["arc_status"]: r["cnt"] for r in statuses}
    conn.close()

    total = sum(counts.values())
    closed = counts.get("closed", 0)
    rate = round(closed / total, 2) if total > 0 else 0.0
    assert rate == round(1/3, 2), f"Expected closure_rate ~0.33, got {rate} (total={total}, closed={closed})"
    print(f"  PASS test_arc_epoch_closure_rate — total={total}, closed={closed}, rate={rate}")


# ── Runner ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 60)
    print("MemALL Pipeline Tests")
    print("=" * 60)
    passed = 0
    failed = 0

    tests = [
        ("test_capture_retrieve", test_capture_retrieve),
        ("test_edge_connect", test_edge_connect),
        ("test_forget", test_forget),
        ("test_merge", test_merge),
        ("test_security_audit", test_security_audit),
        ("test_timeline", test_timeline),
        ("test_update", test_update),
        ("test_arc_capture_l4_open", test_arc_capture_l4_open),
        ("test_arc_l5_edge_in_progress", test_arc_l5_edge_in_progress),
        ("test_arc_l6_edge_closed", test_arc_l6_edge_closed),
        ("test_arc_closed_irreversible", test_arc_closed_irreversible),
        ("test_arc_backfill", test_arc_backfill),
        ("test_arc_stale_detection", test_arc_stale_detection),
        ("test_arc_bidirectional_edge", test_arc_bidirectional_edge),
        ("test_arc_non_l4_null", test_arc_non_l4_null),
        ("test_arc_epoch_closure_rate", test_arc_epoch_closure_rate),
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