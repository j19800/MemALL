"""Tests for narrative → L9 knowledge promotion (Phase 3 P2).

Verifies promote_narratives_to_l9():
  (a) runs without error on a temp DB;
  (b) creates L9 memories for qualifying narratives;
  (c) is idempotent (re-running does not duplicate);
  (d) skips non-qualifying narratives (no decision/insight signal).
"""

import json

from tests.test_helpers import init_temp_db, cleanup_temp_db


def _insert_narrative(conn, agent, ntype, text, summary, span_start, span_end, generated_at):
    conn.execute(
        "INSERT INTO narratives (agent_name, narrative_type, span_start, span_end, "
        "narrative_text, events, summary, generated_at, memory_count) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (agent, ntype, span_start, span_end, text, "[]", summary, generated_at, 3),
    )
    conn.commit()


def _l9_promotions(conn):
    rows = conn.execute(
        "SELECT id, content, subject, metadata, agent_name, level, category "
        "FROM memories WHERE level = 'L9'"
    ).fetchall()
    return [dict(r) for r in rows]


def test_promote_qualifying_narrative_creates_l9():
    db, orig = init_temp_db()
    try:
        from memall.core.db import get_conn
        from memall.pipeline.narrative_l9 import promote_narratives_to_l9

        conn = get_conn()
        _insert_narrative(
            conn,
            agent="dsh",
            ntype="weekly",
            text="从 2026-10-01 到 2026-10-07，dsh 共产生了 12 条记忆。我们决定采用路线 B，确认了身份绑定方案。",
            summary="决定采用路线 B 的身份绑定方案",
            span_start="2026-10-01T00:00:00+00:00",
            span_end="2026-10-07T00:00:00+00:00",
            generated_at="2026-10-07T00:00:00+00:00",
        )
        conn.close()

        result = promote_narratives_to_l9()
        assert result["errors"] == 0
        assert result["scanned"] >= 1
        assert result["promoted"] >= 1

        conn = get_conn()
        rows = _l9_promotions(conn)
        conn.close()
        assert len(rows) == 1
        meta = json.loads(rows[0]["metadata"])
        assert meta["source"] == "narrative_l9_promotion"
        assert meta["narrative_type"] == "weekly"
        assert rows[0]["agent_name"] == "dsh"
        assert rows[0]["category"] == "knowledge"
        print("  PASS test_promote_qualifying_narrative_creates_l9")
    finally:
        cleanup_temp_db(orig)


def test_promote_is_idempotent():
    db, orig = init_temp_db()
    try:
        from memall.core.db import get_conn
        from memall.pipeline.narrative_l9 import promote_narratives_to_l9

        conn = get_conn()
        _insert_narrative(
            conn,
            agent="opencode",
            ntype="weekly",
            text="经过讨论，我们决定：总线采用独立常驻进程部署，这是本次架构评审的最终结论。",
            summary="架构评审决定：总线采用独立常驻进程形态部署，不塞进 stdio MCP server。",
            span_start="2026-10-10T00:00:00+00:00",
            span_end="2026-10-17T00:00:00+00:00",
            generated_at="2026-10-17T00:00:00+00:00",
        )
        conn.close()

        r1 = promote_narratives_to_l9()
        assert r1["promoted"] == 1

        r2 = promote_narratives_to_l9()
        assert r2["promoted"] == 0
        assert r2["skipped"] >= 1

        conn = get_conn()
        rows = _l9_promotions(conn)
        conn.close()
        assert len(rows) == 1
        print("  PASS test_promote_is_idempotent")
    finally:
        cleanup_temp_db(orig)


def test_non_qualifying_narrative_skipped():
    db, orig = init_temp_db()
    try:
        from memall.core.db import get_conn
        from memall.pipeline.narrative_l9 import promote_narratives_to_l9

        conn = get_conn()
        _insert_narrative(
            conn,
            agent="qoder",
            ntype="weekly",
            text="过去一周主要在日常记录与例行整理，没有特别事项。",
            summary="日常记录为主",
            span_start="2026-10-20T00:00:00+00:00",
            span_end="2026-10-27T00:00:00+00:00",
            generated_at="2026-10-27T00:00:00+00:00",
        )
        conn.close()

        result = promote_narratives_to_l9()
        assert result["promoted"] == 0
        assert result["skipped"] >= 1

        conn = get_conn()
        rows = _l9_promotions(conn)
        conn.close()
        assert len(rows) == 0
        print("  PASS test_non_qualifying_narrative_skipped")
    finally:
        cleanup_temp_db(orig)


def test_pipeline_step_registered():
    """narrative_l9 must be callable through the pipeline step loader."""
    from memall.pipeline.pipeline import _load_step_fn

    fn = _load_step_fn("memall.pipeline.narrative_l9", "promote_narratives_to_l9")
    assert callable(fn)
    print("  PASS test_pipeline_step_registered")
