"""
Test Suite — L10 Integration Upsert (regression guard)
=======================================================
Guards the fix for unbounded L10 整合 accumulation:

  The legacy guard compared new content only against the newest 5 rows whose
  level is exactly 'L10'.  In production the classifier had already re-levelled
  earlier integrations to 'L6', so the guard never matched and every pipeline
  cycle appended another near-identical row ("来源：2 条" → "4 条" → "6 条").

Contract under test:
  * exactly ONE L10 整合 per agent_name;
  * detection is level-agnostic (survives classifier re-levelling);
  * the canonical row's content is refreshed in place.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


def _seed_l9s(conn, agent, suffix=""):
    from tests.test_helpers import insert_memory
    insert_memory(
        conn,
        f"Architecture insight about tiered storage layout {suffix}".strip(),
        agent_name=agent, category="architecture", level="L9",
    )
    insert_memory(
        conn,
        f"Security insight about token rotation policy {suffix}".strip(),
        agent_name=agent, category="security", level="L9",
    )


def _l10_rows(conn, agent):
    return conn.execute(
        "SELECT id, content, level FROM memories "
        "WHERE agent_name = ? AND content LIKE '[L10 整合]%' ORDER BY id",
        (agent,),
    ).fetchall()


def test_integrate_upsert_keeps_single_l10_per_agent():
    """Two integration cycles with changed sources → still exactly one L10."""
    from tests.test_helpers import init_temp_db, cleanup_temp_db
    from memall.pipeline.integrate import integrate_step
    from memall.core.db import get_conn

    db_path, patcher = init_temp_db()
    try:
        conn = get_conn()
        _seed_l9s(conn, "l10_agent", "round one")
        conn.commit()
        conn.close()

        first = integrate_step()
        assert first["integrated"] == 1, f"cycle 1 should integrate 1, got {first['integrated']}"

        conn = get_conn()
        rows_1 = _l10_rows(conn, "l10_agent")
        assert len(rows_1) == 1, f"cycle 1 should leave 1 L10, got {len(rows_1)}"
        content_1 = rows_1[0]["content"]
        conn.close()

        # Sources change → merged content changes → hash changes.
        conn = get_conn()
        _seed_l9s(conn, "l10_agent", "round two with fresh material")
        conn.commit()
        conn.close()

        integrate_step()

        conn = get_conn()
        rows_2 = _l10_rows(conn, "l10_agent")
        conn.close()
        assert len(rows_2) == 1, (
            f"upsert must keep exactly 1 L10 per agent; got {len(rows_2)} "
            "(unbounded L10 accumulation regression)"
        )
        assert rows_2[0]["content"] != content_1, "canonical L10 content must be refreshed"
    finally:
        cleanup_temp_db(db_path, patcher)


def test_integrate_upsert_is_level_agnostic():
    """Classifier re-levelling the L10 must NOT defeat the upsert guard.

    This is the exact production failure: earlier integrations sat at level
    'L6', so ``level = 'L10'`` lookups missed them.
    """
    from tests.test_helpers import init_temp_db, cleanup_temp_db
    from memall.pipeline.integrate import integrate_step
    from memall.core.db import get_conn

    db_path, patcher = init_temp_db()
    try:
        conn = get_conn()
        _seed_l9s(conn, "relevel_agent", "initial")
        conn.commit()
        conn.close()

        integrate_step()

        # Simulate the classifier re-levelling the integration output.
        conn = get_conn()
        conn.execute(
            "UPDATE memories SET level = 'L6' WHERE agent_name = ? "
            "AND content LIKE '[L10 整合]%'",
            ("relevel_agent",),
        )
        conn.commit()
        conn.close()

        # New cycle with changed sources must update in place, not append.
        conn = get_conn()
        _seed_l9s(conn, "relevel_agent", "changed later on")
        conn.commit()
        conn.close()

        integrate_step()

        conn = get_conn()
        rows = _l10_rows(conn, "relevel_agent")
        conn.close()
        assert len(rows) == 1, f"re-levelled L10 must still upsert to 1 row, got {len(rows)}"
        assert rows[0]["level"] == "L10", "upsert should restore canonical level L10"
    finally:
        cleanup_temp_db(db_path, patcher)
