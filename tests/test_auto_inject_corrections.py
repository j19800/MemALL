"""Contract tests: auto_inject surfaces active corrections and session formats them."""

import json

from tests.test_helpers import init_temp_db, cleanup_temp_db


def test_auto_inject_includes_active_corrections():
    db, orig = init_temp_db()
    try:
        from memall.core.db import get_conn
        from memall.pipeline.improve import _ensure_corrections_table
        from memall.mcp.federation_tools import auto_inject, invalidate_inject_cache

        invalidate_inject_cache()
        conn = get_conn()
        _ensure_corrections_table(conn)
        conn.execute(
            "INSERT INTO corrections (agent_name, pattern_id, rule_text, summary, category, severity, scenario, source_l6_ids, status, created_at) "
            "VALUES (?, ?, ?, ?, 'personal', 'STRONG', '', '[]', 'active', ?)",
            ("alice", "p1", "先查根因再改代码", "根因优先", "2026-01-01T00:00:00+00:00"),
        )
        conn.execute(
            "INSERT INTO corrections (agent_name, pattern_id, rule_text, summary, category, severity, scenario, source_l6_ids, status, created_at) "
            "VALUES ('', ?, ?, ?, 'global', 'SUGGEST', '', '[]', 'active', ?)",
            ("g1", "跨 agent 共识规则", "全局建议", "2026-01-01T00:00:00+00:00"),
        )
        conn.commit()
        conn.close()

        result = auto_inject("alice")
        corr = result.get("active_corrections") or []
        assert isinstance(corr, list), corr
        texts = [c.get("rule_text") for c in corr]
        assert "先查根因再改代码" in texts
        assert "跨 agent 共识规则" in texts
        print("PASS: test_auto_inject_includes_active_corrections")
    finally:
        cleanup_temp_db(db, orig)


def test_session_start_formats_evolve_corrections_block():
    db, orig = init_temp_db()
    try:
        from memall.core.db import get_conn
        from memall.pipeline.improve import _ensure_corrections_table
        from memall.pipeline.session import session_start
        from memall.mcp.federation_tools import invalidate_inject_cache

        invalidate_inject_cache()
        conn = get_conn()
        _ensure_corrections_table(conn)
        conn.execute(
            "INSERT INTO corrections (agent_name, pattern_id, rule_text, summary, category, severity, scenario, source_l6_ids, status, created_at) "
            "VALUES (?, ?, ?, ?, 'personal', 'STRONG', '', '[]', 'active', ?)",
            ("bob", "p1", "验证后再提交，避免回归", "验证优先", "2026-01-01T00:00:00+00:00"),
        )
        conn.commit()
        conn.close()

        res = session_start(agent_name="bob", auto_inject=True)
        formatted = res.get("injection_formatted") or ""
        assert "[EVOLVE]" in formatted, formatted
        assert "验证后再提交" in formatted, formatted
        # payload should also carry active corrections
        inj = res.get("injection") or {}
        assert any(c.get("rule_text") == "验证后再提交，避免回归" for c in (inj.get("active_corrections") or []))
        print("PASS: test_session_start_formats_evolve_corrections_block")
    finally:
        cleanup_temp_db(db, orig)
