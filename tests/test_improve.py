"""
Test Suite — Improve Step (Phase 22: Self-Improvement)
=======================================================
Tests correction rule extraction from L6 reflections,
per-agent vs global rules, dedup, and session injection.
"""

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
ROOT = os.path.dirname(os.path.dirname(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def _make_l6(conn, content: str, agent_name: str = "test_agent") -> None:
    """Helper: insert an L6 memory with standard fields."""
    from datetime import datetime, timezone
    from tests.test_helpers import insert_memory
    insert_memory(conn, content, agent_name=agent_name, level="L6",
                  created_at=datetime.now(timezone.utc).isoformat())


def test_improve_personal_created():
    """Test: L6 with known patterns creates personal corrections."""
    from tests.test_helpers import init_temp_db, cleanup_temp_db
    from memall.pipeline.improve import improve_step, get_active_corrections
    from memall.core.db import get_conn

    db_path, patcher = init_temp_db()
    try:
        conn = get_conn()
        _make_l6(conn, "涉及完成标记：不应改 level，应 metadata.done=true。要记住这个原则。",
                 agent_name="agent_a")
        _make_l6(conn, "改完了但数据没变——下次先查根因再动手，不凭直觉。",
                 agent_name="agent_a")
        conn.close()

        result = improve_step(agent_name="agent_a")

        assert result["scanned_l6"] >= 2, f"Expected >=2, got {result['scanned_l6']}"
        corr = get_active_corrections("agent_a")
        assert len(corr) > 0, f"Expected >0 corrections, got 0"
        print(f"  PASS test_improve_personal_created ({len(corr)} rules)")
    finally:
        cleanup_temp_db(db_path, patcher)


def test_improve_global_created():
    """Test: same pattern across >=2 agents creates global rule."""
    from tests.test_helpers import init_temp_db, cleanup_temp_db
    from memall.pipeline.improve import improve_step, get_active_corrections
    from memall.core.db import get_conn

    db_path, patcher = init_temp_db()
    try:
        conn = get_conn()
        for i, agent in enumerate(["agent_a", "agent_b", "agent_c"]):
            _make_l6(conn, f"反思{i}: session_end 几乎不被调用，不要依赖结束回调。这是个大坑。",
                     agent_name=agent)
        conn.close()

        result = improve_step(agent_name=None)  # Scan all agents

        assert result["scanned_l6"] >= 3, f"Expected >=3 L6, got {result['scanned_l6']}"

        corr = get_active_corrections("agent_a")
        global_rules = [c for c in corr if c["category"] == "global"]
        assert len(global_rules) >= 1, \
            f"Expected >=1 global rule, got {len(global_rules)}"
        print(f"  PASS test_improve_global_created ({len(global_rules)} global, {len(corr)} total)")
    finally:
        cleanup_temp_db(db_path, patcher)


def test_improve_dedup():
    """Test: running improve_step twice doesn't create duplicate rules."""
    from tests.test_helpers import init_temp_db, cleanup_temp_db
    from memall.pipeline.improve import improve_step, get_active_corrections
    from memall.core.db import get_conn

    db_path, patcher = init_temp_db()
    try:
        conn = get_conn()
        _make_l6(conn, "先查根因再改代码，不凭直觉下结论。很重要。",
                 agent_name="agent_a")
        _make_l6(conn, "验证是关键，不验证直接改容易出问题。",
                 agent_name="agent_a")
        conn.close()

        # Run twice
        improve_step(agent_name="agent_a")
        improve_step(agent_name="agent_a")

        corr = get_active_corrections("agent_a")
        rule_texts = [c["rule_text"] for c in corr]
        assert len(rule_texts) == len(set(rule_texts)), \
            f"Duplicate rules detected: {rule_texts}"
        print(f"  PASS test_improve_dedup ({len(corr)} unique rules)")
    finally:
        cleanup_temp_db(db_path, patcher)


def test_improve_no_l6():
    """Test: no L6 memories returns zero counts."""
    from tests.test_helpers import init_temp_db, cleanup_temp_db
    from memall.pipeline.improve import improve_step

    db_path, patcher = init_temp_db()
    try:
        result = improve_step(agent_name="agent_a")
        assert result["scanned_l6"] == 0, f"Expected 0, got {result['scanned_l6']}"
        print("  PASS test_improve_no_l6")
    finally:
        cleanup_temp_db(db_path, patcher)


def test_injection_corrections():
    """Test: session_start with auto_inject includes [CORRECTIONS] section."""
    from unittest.mock import patch
    from tests.test_helpers import init_temp_db, cleanup_temp_db
    from memall.pipeline.session import session_start, _ensure_sessions_table
    from memall.core.db import get_conn

    db_path, patcher = init_temp_db()
    try:
        # Insert L6 to trigger correction rules
        conn = get_conn()
        _make_l6(conn, "涉及完成任务时，应标记 done 而不是改 level。",
                 agent_name="test_agent")
        _make_l6(conn, "完成标记要记住：不能改 level。done 才是对的。",
                 agent_name="test_agent")
        _ensure_sessions_table(conn)
        conn.close()

        # Run improve first to create corrections
        from memall.pipeline.improve import improve_step
        improve_step(agent_name="test_agent")

        # Mock auto_inject to return dict with recent_reflections so we can
        # verify the [CORRECTIONS] section is rendered in formatted injection
        mock_inject = {
            "identity_traits": {},
            "recent_reflections": [
                {"summary": "涉及完成任务时，应标记 done 而不是改 level", "category": "rule"},
                {"summary": "完成标记要记住：不能改 level", "category": "rule"},
            ],
        }
        with patch("memall.mcp.federation_tools.auto_inject", return_value=mock_inject):
            result = session_start(agent_name="test_agent", auto_inject=True)

        formatted = result.get("injection_formatted", "")
        assert isinstance(formatted, str) and "[CONTEXT]" in formatted, \
            f"Injection missing [CONTEXT] section:\n{formatted[:300]}"
        assert "[CORRECTIONS]" in formatted, \
            f"Injection missing [CORRECTIONS] section (was restored):\n{formatted[:500]}"
        print("  PASS test_injection_corrections (injection format ok, [CORRECTIONS] present)")
    finally:
        cleanup_temp_db(db_path, patcher)


def test_improve_pipeline_wired():
    """Test: run_pipeline with include_improve=True calls improve_step."""
    from tests.test_helpers import init_temp_db, cleanup_temp_db
    from memall.pipeline.pipeline import run_pipeline
    from memall.core.db import get_conn

    db_path, patcher = init_temp_db()
    try:
        conn = get_conn()
        _make_l6(conn, "session_end 几乎不被调用，结束逻辑应挂在 session_start 中。",
                 agent_name="agent_x")
        _make_l6(conn, "先查记录再改，不凭直觉下结论。每次都要。",
                 agent_name="agent_x")
        conn.close()

        result = run_pipeline(
            dry_run=False,
            include_persona=False,
            include_reflect=False,
            include_distill=False,
            include_integrate=False,
            include_improve=True,
        )
        assert "improve" in result.get("results", {}), \
            f"improve not in results: {result.keys()}"
        imp = result["results"]["improve"]
        assert isinstance(imp, dict), f"Expected dict, got {type(imp)}"
        print(f"  PASS test_improve_pipeline_wired ({imp})")
    finally:
        cleanup_temp_db(db_path, patcher)


# ── Runner ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 60)
    print("MemALL Improve Step Tests (Phase 22)")
    print("=" * 60)
    passed = 0
    failed = 0

    tests = [
        ("test_improve_personal_created", test_improve_personal_created),
        ("test_improve_global_created", test_improve_global_created),
        ("test_improve_dedup", test_improve_dedup),
        ("test_improve_no_l6", test_improve_no_l6),
        ("test_improve_pipeline_wired", test_improve_pipeline_wired),
        ("test_injection_corrections", test_injection_corrections),
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
def test_improve_global_correction_merges_on_repeat():
    """Repeated improve_step must merge global evidence without duplicates/inflated hits."""
    from tests.test_helpers import init_temp_db, cleanup_temp_db
    from memall.pipeline.improve import improve_step, get_active_corrections
    from memall.core.db import get_conn
    import json

    db_path, patcher = init_temp_db()
    try:
        conn = get_conn()
        for i, agent in enumerate(["agent_a", "agent_b", "agent_c"]):
            _make_l6(conn, f"反思{i}: session_end 几乎不被调用，不要依赖结束回调。这是个大坑。",
                     agent_name=agent)
        conn.close()

        r1 = improve_step(agent_name=None)
        conn = get_conn()
        row1 = conn.execute(
            "SELECT id, hit_count, source_l6_ids, status FROM corrections "
            "WHERE agent_name = '' AND category = 'global' AND pattern_id = 'session_end_unreliable'"
        ).fetchone()
        conn.close()
        assert row1 is not None, "expected global correction created"
        ids1 = json.loads(row1["source_l6_ids"] or "[]")
        assert row1["status"] == "active"

        # Run again with same evidence set; must not duplicate or inflate.
        r2 = improve_step(agent_name=None)
        conn = get_conn()
        globals_rows = conn.execute(
            "SELECT id, hit_count, source_l6_ids, status FROM corrections "
            "WHERE agent_name = '' AND category = 'global' AND pattern_id = 'session_end_unreliable'"
        ).fetchall()
        conn.close()
        assert len(globals_rows) == 1, f"expected single global row, got {len(globals_rows)}"
        row2 = globals_rows[0]
        ids2 = json.loads(row2["source_l6_ids"] or "[]")
        assert sorted(set(ids2)) == sorted(set(ids1)) == sorted(ids2)
        assert int(row2["hit_count"]) == len(ids2)
        print("  PASS test_improve_global_correction_merges_on_repeat")
    finally:
        cleanup_temp_db(db_path, patcher)

def test_improve_personal_correction_merges_unique_hits():
    """Repeated improve_step must not inflate personal hit_count for same L6 evidence."""
    from tests.test_helpers import init_temp_db, cleanup_temp_db
    from memall.pipeline.improve import improve_step
    from memall.core.db import get_conn
    import json

    db_path, patcher = init_temp_db()
    try:
        conn = get_conn()
        _make_l6(conn, "先查根因再改代码，不凭直觉下结论。很重要。", agent_name="agent_a")
        _make_l6(conn, "验证是关键，不验证直接改容易出问题。先查再改。", agent_name="agent_a")
        conn.close()

        improve_step(agent_name="agent_a")
        conn = get_conn()
        row1 = conn.execute(
            "SELECT hit_count, source_l6_ids FROM corrections "
            "WHERE agent_name = 'agent_a' AND category = 'personal' AND pattern_id = 'verify_before_act'"
        ).fetchone()
        conn.close()
        assert row1 is not None, "expected personal correction for verify_before_act"
        ids1 = json.loads(row1["source_l6_ids"] or "[]")

        improve_step(agent_name="agent_a")
        conn = get_conn()
        row2 = conn.execute(
            "SELECT hit_count, source_l6_ids FROM corrections "
            "WHERE agent_name = 'agent_a' AND category = 'personal' AND pattern_id = 'verify_before_act'"
        ).fetchone()
        conn.close()
        ids2 = json.loads(row2["source_l6_ids"] or "[]")
        assert sorted(set(ids2)) == sorted(set(ids1)) == sorted(ids2)
        assert int(row2["hit_count"]) == len(ids2)
        print("  PASS test_improve_personal_correction_merges_unique_hits")
    finally:
        cleanup_temp_db(db_path, patcher)


# ── TD-EVOL-TRACK-01: correction effectiveness/lifecycle tracking ──

def test_correction_tracking_defaults_off_no_writes():
    """Default config: injection/adoption recorders are strict no-ops (counters stay 0)."""
    from tests.test_helpers import init_temp_db, cleanup_temp_db
    db_path, patcher = init_temp_db()
    try:
        import memall.config as cfg
        from memall.core.db import get_conn
        from memall.pipeline.improve import (
            improve_step, record_corrections_injection, record_correction_adoption,
            correction_effectiveness_report,
        )

        cfg.reset_config(); cfg.get_config(); cfg._last_mtime_check = float("inf")

        conn = get_conn()
        _make_l6(conn, "改完了但数据没变——下次先查根因再动手，不凭直觉。", agent_name="agent_t")
        _make_l6(conn, "又忘了验证就宣布完成，必须先跑测试再汇报。", agent_name="agent_t")
        conn.close()

        improve_step(agent_name="agent_t")
        conn = get_conn()
        row = conn.execute(
            "SELECT pattern_id, COALESCE(injected_count,-1) ic, COALESCE(adopted_count,-1) ac "
            "FROM corrections WHERE agent_name='agent_t' LIMIT 1").fetchone()
        conn.close()
        assert row is not None, "expected a correction created for agent_t"
        assert row["ic"] == 0 and row["ac"] == 0

        n = record_corrections_injection("agent_t", [row["pattern_id"]])
        ok = record_correction_adoption("agent_t", row["pattern_id"])
        assert n == 0 and ok is False, "default-off tracking must not write"

        conn = get_conn()
        row2 = conn.execute(
            "SELECT COALESCE(injected_count,-1) ic FROM corrections WHERE agent_name='agent_t' LIMIT 1").fetchone()
        conn.close()
        assert row2["ic"] == 0

        rep = correction_effectiveness_report("agent_t")
        assert rep["total_injected"] == 0
        assert rep["adoption_rate"] is None
        assert rep["tracking"] == {"injection_enabled": False, "adoption_enabled": False}
        print("  PASS test_correction_tracking_defaults_off_no_writes")
    finally:
        cleanup_temp_db(db_path, patcher)


def test_correction_injection_adoption_tracking_enabled_counts():
    """With gates on: injection counts per agent+global scope; adoption increments once per event; report reflects rates."""
    from tests.test_helpers import init_temp_db, cleanup_temp_db
    db_path, patcher = init_temp_db()
    try:
        import memall.config as cfg
        from memall.core.db import get_conn
        from memall.pipeline.improve import (
            improve_step, record_corrections_injection, record_correction_adoption,
            correction_effectiveness_report,
        )

        cfg.reset_config(); cfg.get_config(); cfg._last_mtime_check = float("inf")
        cfg._config.setdefault("evolution", {})["correction_injection_tracking_enabled"] = True
        cfg._config["evolution"]["correction_adoption_tracking_enabled"] = True

        conn = get_conn()
        _make_l6(conn, "改完了但数据没变——下次先查根因再动手，不凭直觉。", agent_name="agent_u")
        _make_l6(conn, "又忘了验证就宣布完成，必须先跑测试再汇报。", agent_name="agent_u")
        conn.close()

        improve_step(agent_name="agent_u")
        conn = get_conn()
        row = conn.execute(
            "SELECT pattern_id, category FROM corrections WHERE LOWER(agent_name)='agent_u' LIMIT 1").fetchone()
        conn.close()
        assert row is not None

        updated = record_corrections_injection("agent_u", [row["pattern_id"], row["pattern_id"]])
        assert updated >= 1
        conn = get_conn()
        r1 = conn.execute(
            "SELECT COALESCE(injected_count,0) ic, last_injected_at FROM corrections WHERE pattern_id=? "
            "AND LOWER(agent_name)=LOWER('agent_u')", (row["pattern_id"],)).fetchone()
        conn.close()
        assert r1["ic"] == 1 and r1["last_injected_at"]

        ok = record_correction_adoption("agent_u", row["pattern_id"])
        assert ok is True
        conn = get_conn()
        r2 = conn.execute(
            "SELECT COALESCE(adopted_count,0) ac, last_adopted_at FROM corrections WHERE pattern_id=? "
            "AND LOWER(agent_name)=LOWER('agent_u')", (row["pattern_id"],)).fetchone()
        conn.close()
        assert r2["ac"] == 1 and r2["last_adopted_at"]

        rep = correction_effectiveness_report("agent_u")
        assert rep["tracking"]["injection_enabled"] is True
        assert rep["total_injected"] >= 1
        assert rep["total_adopted"] >= 1
        assert rep["adoption_rate"] is not None and 0 <= rep["adoption_rate"]
        assert rep["top_injected"], "top_injected should list injected active rules"
        print("  PASS test_correction_injection_adoption_tracking_enabled_counts")
    finally:
        cleanup_temp_db(db_path, patcher)


def test_corrections_schema_migration_adds_tracking_columns():
    """Old corrections tables without tracking columns gain them via _ensure_corrections_table."""
    from tests.test_helpers import init_temp_db, cleanup_temp_db
    db_path, patcher = init_temp_db()
    try:
        from memall.core.db import get_conn
        from memall.pipeline.improve import _ensure_corrections_table
        conn = get_conn()
        conn.execute("DROP TABLE IF EXISTS corrections")
        conn.execute("""
            CREATE TABLE corrections (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                agent_name TEXT NOT NULL DEFAULT '',
                pattern_id TEXT NOT NULL,
                rule_text TEXT NOT NULL,
                summary TEXT DEFAULT '',
                category TEXT NOT NULL DEFAULT 'personal',
                source_l6_ids TEXT NOT NULL DEFAULT '[]',
                status TEXT NOT NULL DEFAULT 'active',
                hit_count INTEGER DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT
            )
        """)
        conn.commit()
        _ensure_corrections_table(conn)
        cols = {r[1] for r in conn.execute("PRAGMA table_info(corrections)").fetchall()}
        conn.close()
        for c in ("severity", "scenario", "injected_count", "last_injected_at",
                  "adopted_count", "last_adopted_at"):
            assert c in cols, f"missing migrated column {c}"
        print("  PASS test_corrections_schema_migration_adds_tracking_columns")
    finally:
        cleanup_temp_db(db_path, patcher)


def test_mcp_correction_report_action_contract():
    """TD-EVOL-TRACK-01: memall_system action=correction_report is read-only,
    validator-registered, and returns the effectiveness report shape."""
    from tests.test_helpers import init_temp_db, cleanup_temp_db
    db_path, patcher = init_temp_db()
    try:
        import json as _json
        from memall.mcp.adapter import handle_call

        out = _json.loads(handle_call("memall_system", {"action": "correction_report"}))
        assert out.get("ok") is True, out
        rep = out.get("report") or {}
        for k in ("by_status", "total_rules", "total_injected", "total_adopted",
                  "adoption_rate", "top_injected", "tracking"):
            assert k in rep, f"missing {k} in report"

        out2 = _json.loads(handle_call("memall_system", {"action": "correction_report", "agent_name": "nobody"}))
        assert out2.get("ok") is True
        assert out2["report"]["total_rules"] == 0

        # Validator accepts the new action (no unknown-action rejection).
        from memall.mcp.validator import validate_tool_input
        valid, data, err = validate_tool_input("memall_system", {"action": "correction_report", "agent_name": "a"})
        assert valid is True, err
        assert data.get("agent_name") == "a"
        print("  PASS test_mcp_correction_report_action_contract")
    finally:
        cleanup_temp_db(db_path, patcher)


# ── TD-EVOL-TRACK-01: automatic adoption/violation judge ──

def _seed_correction(conn, agent_name, pattern_id="verify_before_act",
                     status="active", category="personal"):
    from datetime import datetime, timezone
    from memall.pipeline.improve import _ensure_corrections_table
    _ensure_corrections_table(conn)
    conn.execute(
        "INSERT INTO corrections (agent_name, pattern_id, rule_text, summary, category, "
        "source_l6_ids, status, severity, hit_count, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (agent_name, pattern_id, "修改前先查相关记录。", "先查再动手", category,
         "[]", status, "STRONG", 1,
         datetime.now(timezone.utc).isoformat(), datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()


def _corr_row(conn, pattern_id, agent_name):
    return conn.execute(
        "SELECT COALESCE(injected_count,0) ic, COALESCE(adopted_count,0) ac, "
        "COALESCE(last_injected_at,'') inj FROM corrections "
        "WHERE pattern_id=? AND agent_name=?", (pattern_id, agent_name)).fetchone()


def test_corrections_auto_eval_default_off_is_noop():
    """evolution.correction_auto_adoption_eval defaults to False: evaluator is a pure no-op."""
    from tests.test_helpers import init_temp_db, cleanup_temp_db
    db_path, patcher = init_temp_db()
    try:
        import memall.config as cfg
        from memall.core.db import get_conn
        from memall.pipeline.improve import evaluate_session_corrections

        cfg.reset_config(); cfg.get_config(); cfg._last_mtime_check = float("inf")
        out = evaluate_session_corrections("agent_v", "2026-01-01T00:00:00+00:00",
                                           "2026-12-01T00:00:00+00:00", "s1")
        assert out == {"evaluated": False, "reason": "disabled"}, out
        print("  PASS test_corrections_auto_eval_default_off_is_noop")
    finally:
        cleanup_temp_db(db_path, patcher)


def test_corrections_auto_eval_three_state_verdicts():
    """adoption requires injection evidence in window; violation keywords block adoption;
    no injection evidence yields no_signal; writes follow the adoption gate."""
    from tests.test_helpers import init_temp_db, cleanup_temp_db
    db_path, patcher = init_temp_db()
    try:
        import memall.config as cfg
        from memall.core.db import get_conn
        from memall.pipeline.improve import evaluate_session_corrections

        cfg.reset_config(); cfg.get_config(); cfg._last_mtime_check = float("inf")
        cfg._config.setdefault("evolution", {})["correction_auto_adoption_eval"] = True
        cfg._config["evolution"]["correction_adoption_tracking_enabled"] = True

        from datetime import datetime, timezone, timedelta
        now_dt = datetime.now(timezone.utc)
        win_start = (now_dt - timedelta(hours=2)).isoformat()
        win_end = (now_dt + timedelta(hours=1)).isoformat()
        inj_ts = now_dt.isoformat()

        conn = get_conn()
        # global rules (agent-agnostic) exercised from agent_a's window
        _seed_correction(conn, "", "verify_before_act", category="global")
        _seed_correction(conn, "", "human_first", category="global")
        _seed_correction(conn, "", "mark_done_not_alter_level", category="global")
        # injection evidence inside the session window
        conn.execute("UPDATE corrections SET last_injected_at=?, "
                     "injected_count=1 WHERE pattern_id IN ('verify_before_act','human_first')",
                     (inj_ts,))
        conn.commit()
        # mark_done rule: no injection evidence -> no_signal expected
        # session corpus: violation for human_first, benign for verify_before_act
        _make_l6(conn, "把归档任务处理好了，导出功能正常。", agent_name="agent_a")
        _make_l6(conn, "运维直接改了数据来关闭告警工单。", agent_name="agent_a")
        conn.close()

        out = evaluate_session_corrections(
            "agent_a", win_start, win_end, "s-eval")
        assert out["evaluated"] is True, out
        by = {r["pattern_id"]: r["verdict"] for r in out["rules"]}
        assert by["verify_before_act"] == "adoption", by
        assert by["human_first"] == "violation", by
        assert by["mark_done_not_alter_level"] == "no_signal", by
        assert (out["adopted"], out["violations"], out["no_signal"], out["written"]) == (1, 1, 1, 1)
        assert out.get("dry_run") in (False, None)

        conn = get_conn()
        r_v = _corr_row(conn, "verify_before_act", "")
        r_h = _corr_row(conn, "human_first", "")
        r_m = _corr_row(conn, "mark_done_not_alter_level", "")
        conn.close()
        assert r_v["ac"] == 1, "adoption must increment adopted_count"
        assert r_h["ac"] == 0, "violation must not increment adopted_count"
        assert r_m["ac"] == 0 and r_m["inj"] == "", "no_signal must not touch anything"
        print("  PASS test_corrections_auto_eval_three_state_verdicts")
    finally:
        cleanup_temp_db(db_path, patcher)


def test_corrections_auto_eval_dry_run_without_adoption_gate():
    """Eval on but adoption gate off: verdicts produced, ZERO writes (gray-release preview)."""
    from tests.test_helpers import init_temp_db, cleanup_temp_db
    db_path, patcher = init_temp_db()
    try:
        import memall.config as cfg
        from memall.core.db import get_conn
        from memall.pipeline.improve import evaluate_session_corrections

        cfg.reset_config(); cfg.get_config(); cfg._last_mtime_check = float("inf")
        cfg._config.setdefault("evolution", {})["correction_auto_adoption_eval"] = True
        # adoption gate stays default False

        from datetime import datetime, timezone, timedelta
        now_dt = datetime.now(timezone.utc)
        win_start = (now_dt - timedelta(hours=2)).isoformat()
        win_end = (now_dt + timedelta(hours=1)).isoformat()

        conn = get_conn()
        _seed_correction(conn, "", "verify_before_act", category="global")
        conn.execute("UPDATE corrections SET last_injected_at=?, "
                     "injected_count=1 WHERE pattern_id='verify_before_act'",
                     (now_dt.isoformat(),))
        conn.commit()
        _make_l6(conn, "接口联调完成，文档已同步更新。", agent_name="agent_d")
        conn.close()

        out = evaluate_session_corrections(
            "agent_d", win_start, win_end, "s-dry")
        assert out["evaluated"] is True
        assert out["adopted"] == 1 and out["written"] == 0
        assert out["dry_run"] is True

        conn = get_conn()
        r = _corr_row(conn, "verify_before_act", "")
        conn.close()
        assert r["ac"] == 0, "dry run must not write counters"
        print("  PASS test_corrections_auto_eval_dry_run_without_adoption_gate")
    finally:
        cleanup_temp_db(db_path, patcher)


def test_session_end_reports_corrections_eval_when_enabled():
    """session_end surfaces additive corrections_eval summary when the judge is on."""
    from tests.test_helpers import init_temp_db, cleanup_temp_db
    db_path, patcher = init_temp_db()
    try:
        import memall.config as cfg
        from datetime import datetime, timezone
        from memall.core.db import get_conn
        from memall.pipeline.session import session_start, session_end

        cfg.reset_config(); cfg.get_config(); cfg._last_mtime_check = float("inf")
        cfg._config.setdefault("evolution", {})["correction_auto_adoption_eval"] = True
        cfg._config["evolution"]["correction_adoption_tracking_enabled"] = True

        conn = get_conn()
        _seed_correction(conn, "agent_e", "verify_before_act")
        conn.close()

        st = session_start(agent_name="agent_e")
        sid = st["session_id"]
        conn = get_conn()
        # injection evidence must fall inside the session window
        conn.execute("UPDATE corrections SET last_injected_at=? WHERE pattern_id='verify_before_act'",
                     (datetime.now(timezone.utc).isoformat(),))
        conn.commit()
        _make_l6(conn, "发布流程梳理完成，清单逐项处理妥当。", agent_name="agent_e")
        conn.close()
        en = session_end(sid)
        assert en.get("corrections_eval"), en
        assert en["corrections_eval"]["adopted"] >= 1
        assert en["corrections_eval"]["written"] >= 1
        conn = get_conn()
        r = _corr_row(conn, "verify_before_act", "agent_e")
        conn.close()
        assert r["ac"] >= 1
        print("  PASS test_session_end_reports_corrections_eval_when_enabled")
    finally:
        cleanup_temp_db(db_path, patcher)


def test_session_end_corrections_eval_absent_when_disabled():
    """Default config: session_end result contains no corrections_eval key (no behavior change)."""
    from tests.test_helpers import init_temp_db, cleanup_temp_db
    db_path, patcher = init_temp_db()
    try:
        import memall.config as cfg
        from memall.pipeline.session import session_start, session_end

        cfg.reset_config(); cfg.get_config(); cfg._last_mtime_check = float("inf")
        st = session_start(agent_name="agent_f")
        en = session_end(st["session_id"])
        assert "corrections_eval" not in en, en
        print("  PASS test_session_end_corrections_eval_absent_when_disabled")
    finally:
        cleanup_temp_db(db_path, patcher)
