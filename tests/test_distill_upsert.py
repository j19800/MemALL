"""
Test Suite — L9 Distillation Upsert (regression guard)
=======================================================
Guards the fix for unbounded near-identical L9 accumulation:

  Before: every pipeline cycle ran ``INSERT OR IGNORE`` keyed only on the full
  content hash.  The L9 header embeds a *changing* source count ("…领域共 10 条"
  → "11 条"), so the hash always differed and a new row was appended each cycle.

  After: exactly ONE L9 per (agent_name, category) — updated in place.

Contract under test:
  * running distillation twice with changed sources must NOT grow the L9 count;
  * the canonical row's content must be refreshed;
  * ``distill.upsert_enabled = False`` restores legacy append behaviour.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


def _l9_rows(conn, agent=None):
    sql = "SELECT id, content, content_hash FROM memories WHERE level = 'L9'"
    args = ()
    if agent:
        sql += " AND agent_name = ?"
        args = (agent,)
    sql += " ORDER BY id"
    return conn.execute(sql, args).fetchall()


def test_distill_upsert_does_not_grow_l9_count():
    """Two distillation cycles with changed sources → still exactly one L9."""
    from tests.test_helpers import init_temp_db, cleanup_temp_db, insert_memory
    from memall.pipeline.distill import distill_step
    from memall.core.db import get_conn

    db_path, patcher = init_temp_db()
    try:
        conn = get_conn()
        for i in range(3):
            insert_memory(
                conn,
                f"System architecture decision #{i}: event driven pipeline design",
                agent_name="upsert_agent",
                category="architecture",
                summary=f"Architecture note {i}",
                level="L3",
            )
        conn.close()

        first = distill_step()
        assert first["distilled"] == 1, f"cycle 1 should distill 1, got {first['distilled']}"

        conn = get_conn()
        rows_1 = _l9_rows(conn, "upsert_agent")
        assert len(rows_1) == 1, f"cycle 1 should leave 1 L9, got {len(rows_1)}"
        content_1 = rows_1[0]["content"]
        conn.close()

        # Source set changes → the header count changes → hash changes.
        conn = get_conn()
        insert_memory(
            conn,
            "System architecture decision #3: tiered SQLite storage layout",
            agent_name="upsert_agent",
            category="architecture",
            summary="Architecture note 3",
            level="L3",
        )
        conn.close()

        second = distill_step()

        conn = get_conn()
        rows_2 = _l9_rows(conn, "upsert_agent")
        conn.close()

        assert len(rows_2) == 1, (
            f"upsert must keep exactly 1 L9 per (agent, category); got {len(rows_2)} "
            "(this is the unbounded-accumulation regression)"
        )
        assert rows_2[0]["content"] != content_1, "canonical L9 content must be refreshed"
        assert second["distilled"] == 1, f"cycle 2 should report 1, got {second['distilled']}"
    finally:
        cleanup_temp_db(db_path, patcher)


def test_distill_upsert_respects_agent_and_category_boundary():
    """Upsert must not collapse L9 across different categories."""
    from tests.test_helpers import init_temp_db, cleanup_temp_db, insert_memory
    from memall.pipeline.distill import distill_step
    from memall.core.db import get_conn

    db_path, patcher = init_temp_db()
    try:
        conn = get_conn()
        for i in range(3):
            insert_memory(conn, f"Architecture note #{i} about layered storage",
                          agent_name="multi_agent", category="architecture", level="L3")
            insert_memory(conn, f"Implementation note #{i} about migration runner",
                          agent_name="multi_agent", category="implementation", level="L3")
        conn.close()

        distill_step()
        # Second cycle, same sources → unchanged hash → nothing written.
        distill_step()

        conn = get_conn()
        rows = _l9_rows(conn, "multi_agent")
        conn.close()

        cats = {r["content"] for r in rows}
        assert len(rows) == 2, f"2 categories must yield 2 L9 rows, got {len(rows)}"
        assert len(cats) == 2
    finally:
        cleanup_temp_db(db_path, patcher)


def test_l9_header_carries_agent_and_category():
    """L9 header must name the real agent + category (guards key shadowing).

    Regression: the inner de-dup loop used to rebind ``key = s[:40]``, clobbering
    the outer ``(agent_name, category)`` group key. Every distilled memory was
    then written as "[L9 蒸馏] S 在 y 领域…" with agent_name='system'.
    """
    from tests.test_helpers import init_temp_db, cleanup_temp_db, insert_memory
    from memall.pipeline.distill import distill_step
    from memall.core.db import get_conn

    db_path, patcher = init_temp_db()
    try:
        conn = get_conn()
        for i in range(3):
            insert_memory(conn, f"Storage layout decision #{i}: tiered sqlite pages",
                          agent_name="header_agent", category="storage", level="L3")
        conn.close()

        distill_step()

        conn = get_conn()
        row = conn.execute(
            "SELECT content, agent_name, category FROM memories WHERE level = 'L9'"
        ).fetchone()
        conn.close()

        assert row is not None, "L9 should exist"
        assert "header_agent" in row["content"], f"header missing agent: {row['content'][:80]}"
        assert "storage" in row["content"], f"header missing category: {row['content'][:80]}"
        assert row["agent_name"] == "header_agent", f"agent mis-attributed: {row['agent_name']}"
        assert row["category"] == "storage", f"category corrupted: {row['category']}"
    finally:
        cleanup_temp_db(db_path, patcher)


def test_distill_upsert_can_be_disabled():
    """``distill.upsert_enabled = False`` restores legacy append behaviour."""
    from tests.test_helpers import init_temp_db, cleanup_temp_db, insert_memory
    from memall.pipeline.distill import distill_step
    from memall.core.db import get_conn
    from memall import config

    db_path, patcher = init_temp_db()
    try:
        config.get_config()
        config._config.setdefault("distill", {})["upsert_enabled"] = False
        try:
            conn = get_conn()
            for i in range(3):
                insert_memory(conn, f"Legacy note #{i} about retry semantics",
                              agent_name="legacy_agent", category="behavior", level="L3")
            conn.close()

            distill_step()
            conn = get_conn()
            insert_memory(conn, "Legacy note #3 about retry backoff tuning",
                          agent_name="legacy_agent", category="behavior", level="L3")
            conn.close()
            distill_step()

            conn = get_conn()
            rows = _l9_rows(conn, "legacy_agent")
            conn.close()
            assert len(rows) == 2, f"legacy mode should append a 2nd L9, got {len(rows)}"
        finally:
            config.reset_config()
    finally:
        cleanup_temp_db(db_path, patcher)
