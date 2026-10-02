"""
Test Suite — Owner / Creator semantic fix (Invariant #1, memory #823)
=====================================================================

Invariant #1: owner != creator, and owner is ALWAYS a human.  creator is the
agent that triggered the write.  These tests guard the A1 fix:

  * a missing owner falls back to the configured human owner, NOT the agent;
  * a caller-supplied human owner is never silently rewritten to the agent;
  * creator is persisted as the writing agent;
  * the 025 migration backfills legacy rows and is idempotent.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from memall.core.db import get_conn
from memall.config import get_config, reset_config


def _expected_human_owner():
    # Reset cache so the test sees the pristine default ('老陈').
    reset_config()
    return get_config("identity.human_owner", "老陈") or "老陈"


def test_missing_owner_falls_back_to_human_not_agent():
    from tests.test_helpers import init_temp_db, cleanup_temp_db
    from memall.core.thin_waist import capture

    db_path, orig = init_temp_db()
    try:
        human = _expected_human_owner()
        mid = capture(
            "A memory written without an explicit owner must still belong to a human.",
            agent_name="claude",
        )
        conn = get_conn()
        row = conn.execute(
            "SELECT owner, creator, agent_name FROM memories WHERE id = ?", (mid,)
        ).fetchone()
        conn.close()
        assert row is not None, "capture did not persist the memory"
        assert row["owner"] == human, f"owner should be human '{human}', got '{row['owner']}'"
        assert row["creator"] == "claude", f"creator should be 'claude', got '{row['creator']}'"
        print("  PASS test_missing_owner_falls_back_to_human_not_agent")
    finally:
        cleanup_temp_db(db_path, orig)


def test_explicit_human_owner_not_degraded():
    from tests.test_helpers import init_temp_db, cleanup_temp_db
    from memall.core.thin_waist import capture

    db_path, orig = init_temp_db()
    try:
        human = _expected_human_owner()
        mid = capture(
            "A memory where the caller explicitly names the human owner.",
            agent_name="claude",
            owner=human,
        )
        conn = get_conn()
        row = conn.execute(
            "SELECT owner, creator, agent_name FROM memories WHERE id = ?", (mid,)
        ).fetchone()
        conn.close()
        assert row is not None
        assert row["owner"] == human, f"explicit owner must survive, got '{row['owner']}'"
        assert row["creator"] == "claude", f"creator should be 'claude', got '{row['creator']}'"
        print("  PASS test_explicit_human_owner_not_degraded")
    finally:
        cleanup_temp_db(db_path, orig)


def test_creator_persisted_for_different_agent():
    from tests.test_helpers import init_temp_db, cleanup_temp_db
    from memall.core.thin_waist import capture

    db_path, orig = init_temp_db()
    try:
        human = _expected_human_owner()
        mid = capture(
            "Another memory, this one written by a different agent on behalf of the human.",
            agent_name="workbuddy",
            owner=human,
        )
        conn = get_conn()
        row = conn.execute(
            "SELECT owner, creator FROM memories WHERE id = ?", (mid,)
        ).fetchone()
        conn.close()
        assert row["owner"] == human
        assert row["creator"] == "workbuddy", f"creator should be 'workbuddy', got '{row['creator']}'"
        print("  PASS test_creator_persisted_for_different_agent")
    finally:
        cleanup_temp_db(db_path, orig)


def test_migration_backfills_and_is_idempotent():
    from tests.test_helpers import init_temp_db, cleanup_temp_db
    import importlib.util
    import sqlite3

    db_path, orig = init_temp_db()
    try:
        human = _expected_human_owner()
        conn = get_conn()
        # Register the agents the seeded rows refer to, mirroring production where
        # every writing agent is present in identities (agent_type='ai').
        conn.execute("INSERT OR IGNORE INTO identities (agent_name, agent_type) VALUES ('claude', 'ai')")
        conn.execute("INSERT OR IGNORE INTO identities (agent_name, agent_type) VALUES ('workbuddy', 'ai')")
        conn.commit()
        # Seed two legacy-shaped rows: one where owner was collapsed onto the
        # agent, one with a correct human owner.
        conn.execute(
            "INSERT INTO memories (content, content_hash, level, owner, agent_name, creator, "
            "occurred_at, created_at, updated_at, metadata) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            ("legacy collapsed owner", "h1", "P2", "claude", "claude", "",
             "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z", "{}"),
        )
        conn.execute(
            "INSERT INTO memories (content, content_hash, level, owner, agent_name, creator, "
            "occurred_at, created_at, updated_at, metadata) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            ("legacy human owner", "h2", "P2", human, "workbuddy", "",
             "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z", "{}"),
        )
        # Third row: owned by an AI agent but NOT collapsed onto the writing agent
        # (e.g. an agent portrait distilled by one agent, attributed to another).
        conn.execute(
            "INSERT INTO memories (content, content_hash, level, owner, agent_name, creator, "
            "occurred_at, created_at, updated_at, metadata) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            ("legacy ai-owned portrait", "h3", "P2", "claude", "workbuddy", "",
             "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z", "{}"),
        )
        conn.commit()

        # Load and run the migration module directly (twice → idempotency check).
        spec = importlib.util.spec_from_file_location(
            "m025", os.path.join(os.path.dirname(__file__), "..", "src", "memall",
                                 "migrations", "025_add_creator_column.py")
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        res1 = mod.apply(conn)
        res2 = mod.apply(conn)  # idempotent re-run must not raise / must not corrupt
        conn.commit()

        rows = {r["content"]: r for r in conn.execute(
            "SELECT content, owner, creator, agent_name FROM memories").fetchall()}

        collapsed = rows["legacy collapsed owner"]
        assert collapsed["creator"] == "claude", f"creator should be 'claude', got {collapsed['creator']!r}"
        assert collapsed["owner"] == human, f"collapsed owner should become human, got {collapsed['owner']!r}"

        kept = rows["legacy human owner"]
        assert kept["creator"] == "workbuddy"
        assert kept["owner"] == human, "pre-existing human owner must be preserved"

        ai_owned = rows["legacy ai-owned portrait"]
        assert ai_owned["creator"] == "workbuddy"
        assert ai_owned["owner"] == human, f"AI-owned memory must be reassigned to human, got {ai_owned['owner']!r}"

        # Human identity must exist.
        ident = conn.execute(
            "SELECT agent_name, agent_type FROM identities WHERE agent_name = ?", (human,)
        ).fetchone()
        assert ident is not None and ident["agent_type"] == "human", f"human identity missing: {ident}"

        # No memory should be owned by an AI agent.
        bad = conn.execute(
            "SELECT COUNT(*) FROM memories WHERE owner IN "
            "(SELECT agent_name FROM identities WHERE agent_type='ai')"
        ).fetchone()[0]
        assert bad == 0, f"{bad} memories still owned by an AI agent"
        conn.close()
        print(f"  PASS test_migration_backfills_and_is_idempotent (run1={res1}, run2={res2})")
    finally:
        cleanup_temp_db(db_path, orig)


# ── Runner ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 60)
    print("MemALL Owner/Creator Semantics Tests")
    print("=" * 60)
    passed = 0
    failed = 0
    tests = [
        ("test_missing_owner_falls_back_to_human_not_agent", test_missing_owner_falls_back_to_human_not_agent),
        ("test_explicit_human_owner_not_degraded", test_explicit_human_owner_not_degraded),
        ("test_creator_persisted_for_different_agent", test_creator_persisted_for_different_agent),
        ("test_migration_backfills_and_is_idempotent", test_migration_backfills_and_is_idempotent),
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
