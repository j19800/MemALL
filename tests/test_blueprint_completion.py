"""ADR 0028 blueprint-completion contracts (P1 / P3 / P4 literal items).

Covers the items that were previously only approximated or deferred:
- P1: capture auto-injects conversation.session_end provenance (opt-in);
  search-layer `continuity` flag auto-assembles a validity-aware pack.
- P3: richer interaction feedback columns + signals; utility ledger by
  slot_key/source_pipeline/scope; quality.utility.* config; utility-driven
  resolve (threshold adjust + REVIEW force, audited); acceptance key
  `quality.utility.tracking_enabled=True` makes retrieve utility-aware.
- P4: maintenance task entry (cognitive.loop.maintenance), mode=audit,
  ADR/archived_sessions append-only anchors, include_archived capability,
  audit log + before-snapshot rollback, non-destructive mark-only passes.

All new capabilities are default-OFF / config-gated / additive.
"""
import json
from datetime import datetime, timezone, timedelta

from tests.test_helpers import init_temp_db, cleanup_temp_db


def _cfg_set(dotted: str, value):
    import memall.config as cfg
    cfg.get_config()
    parts = dotted.split(".")
    node = cfg._config
    for p in parts[:-1]:
        node = node.setdefault(p, {})
    node[parts[-1]] = value


def _reset():
    from memall.config import reset_config
    reset_config()


def _now():
    return datetime.now(timezone.utc).isoformat()


# ── P3-A: interaction feedback columns + record_interaction ──

def test_p3_interaction_columns_old_db_migration():
    """Regression: _ensure_interaction_columns must work on a legacy table
    opened with a PLAIN sqlite3 connection (tuple rows), not just sqlite3.Row.
    An earlier version used r['name'] unconditionally and silently swallowed
    the TypeError, so old databases never received the P3 columns.
    """
    import os
    import sqlite3
    import tempfile
    from memall.core.db import _ensure_interaction_columns
    p = tempfile.mktemp(suffix=".db")
    c = sqlite3.connect(p)  # deliberately NO row_factory
    try:
        c.execute(
            "CREATE TABLE search_interactions (id INTEGER PRIMARY KEY, query TEXT, "
            "memory_id INTEGER, action TEXT, dwell_ms INTEGER, rank_position INTEGER, "
            "agent_name TEXT, owner TEXT, session_id TEXT, created_at TEXT)")
        c.commit()
        _ensure_interaction_columns(c)
        c.commit()
        cols = {r[1] for r in c.execute("PRAGMA table_info(search_interactions)").fetchall()}
        need = {"task_id", "accepted", "rejected", "corrected", "ignored",
                "slot_key", "source_pipeline", "scope"}
        assert need.issubset(cols), sorted(need - cols)
        # idempotent
        _ensure_interaction_columns(c)
        c.commit()
        print("PASS: test_p3_interaction_columns_old_db_migration")
    finally:
        c.close()
        os.unlink(p)


def test_p3_interaction_feedback_columns_and_ledger_dims():
    db, orig = init_temp_db()
    try:
        from memall.core.db import get_conn
        from memall.search.enhancements import record_interaction, aggregate_utility_ledger
        c = get_conn()
        cols = {r["name"] for r in c.execute("PRAGMA table_info(search_interactions)").fetchall()}
        assert {"task_id", "accepted", "rejected", "corrected", "ignored",
                "slot_key", "source_pipeline", "scope"}.issubset(cols)
        ok = record_interaction(c, query="q", memory_id=1, action="click",
                                task_id="T-1", accepted=True, corrected=True,
                                slot_key="slot:config:db", source_pipeline="capture", scope="proj:A")
        assert ok is True
        led = aggregate_utility_ledger(c, dims=["slot_key", "source_pipeline"])
        assert led["ok"] is True
        row = led["by_dimension"][0]
        assert row["slot_key"] == "slot:config:db"
        assert row["utility_score"] > 0
        assert row["feedback"]["accepted"] == 1 and row["feedback"]["corrected"] == 1
        c.close()
        print("PASS: test_p3_interaction_feedback_columns_and_ledger_dims")
    finally:
        cleanup_temp_db(db, orig)


def test_p3_mcp_record_interaction_feedback_roundtrip():
    db, orig = init_temp_db()
    try:
        from memall.mcp.tools.retrieve import handle_record_interaction
        from memall.mcp.validator import validate_tool_input
        out = json.loads(handle_record_interaction({
            "query": "q", "memory_id": 1, "interaction_type": "click",
            "task_id": "T-2", "rejected": True, "slot_key": "s", "source_pipeline": "sp", "scope": "sc",
        }))
        assert out.get("recorded") is True
        ok, data, err = validate_tool_input("memall_write", {
            "action": "record_interaction", "query": "q", "memory_id": 1,
            "accepted": True, "corrected": True, "slot_key": "s", "scope": "sc", "task_id": "T-3",
        })
        assert ok is True, err
        assert data.get("accepted") is True and data.get("task_id") == "T-3"
        print("PASS: test_p3_mcp_record_interaction_feedback_roundtrip")
    finally:
        cleanup_temp_db(db, orig)


# ── P3-C: utility-driven resolve (config-gated, audited) ──

def test_p3_utility_resolve_policy_default_off_is_noop():
    db, orig = init_temp_db()
    try:
        from memall.core.db import get_conn
        from memall.core.thin_waist import _utility_resolve_policy
        _reset()
        pol = _utility_resolve_policy(get_conn(), "slot:config:db")
        assert pol["enabled"] is False
        assert pol["_meta"]["applied"] is False
        print("PASS: test_p3_utility_resolve_policy_default_off_is_noop")
    finally:
        _reset()
        cleanup_temp_db(db, orig)


def test_p3_utility_resolve_low_utility_forces_review_and_is_audited():
    db, orig = init_temp_db()
    try:
        from memall.core.db import get_conn
        from memall.core.models import MemoryInput
        from memall.core.thin_waist import capture, _resolve_decision, _utility_resolve_policy
        from memall.search.enhancements import record_interaction
        _cfg_set("quality.utility.tracking_enabled", True)
        _cfg_set("quality.utility.resolve_enabled", True)
        _cfg_set("quality.utility.slot_weight_factor", 0.15)
        # negative utility for the slot
        c = get_conn()
        for _ in range(3):
            record_interaction(c, query="q", memory_id=999, action="click", rejected=True,
                               slot_key="slot:config:db", source_pipeline="capture", scope="p")
        pol = _utility_resolve_policy(c, "slot:config:db")
        assert pol["enabled"] is True
        assert pol["utility_score"] < 0
        assert pol["force_review"] is True
        # two same-slot memories; rules would MERGE (identical-ish text) but policy forces REVIEW
        capture(MemoryInput(content="项目决定使用 PostgreSQL 作为主库，团队熟悉且事务稳定，配置已确认长期有效。",
                            level="L4", category="decision", agent_name="t", subject="db",
                            metadata=json.dumps({"machine_hints": {"slot_key": "slot:config:db"}})))
        res = _resolve_decision(c, MemoryInput(
            content="项目决定使用 PostgreSQL 作为主库，团队熟悉且事务稳定，配置已确认长期有效，补充一句说明。",
            level="L4", category="decision", agent_name="t", subject="db",
            metadata=json.dumps({"machine_hints": {"slot_key": "slot:config:db"}})), "h", _now())
        assert res["decision"] == "REVIEW", res
        assert res["utility_policy"]["applied"] is True  # audited
        assert res["utility_policy"]["force_review"] is True
        c.close()
        print("PASS: test_p3_utility_resolve_low_utility_forces_review_and_is_audited")
    finally:
        _reset()
        cleanup_temp_db(db, orig)


def test_p3_utility_resolve_metadata_audit_record_persisted():
    db, orig = init_temp_db()
    try:
        from memall.core.db import get_conn
        from memall.core.models import MemoryInput
        from memall.core.thin_waist import capture
        _cfg_set("quality.utility.tracking_enabled", True)
        _cfg_set("quality.utility.resolve_enabled", True)
        mid = capture(MemoryInput(
            content="项目决定使用 MySQL 作为主库，团队熟悉且事务稳定，配置已确认长期有效可审计。",
            level="L4", category="decision", agent_name="t", subject="db",
            metadata=json.dumps({"machine_hints": {"slot_key": "slot:config:db"}})))
        c = get_conn()
        row = c.execute("SELECT metadata FROM memories WHERE id=?", (int(mid),)).fetchone()
        c.close()
        meta = json.loads(row[0]) if isinstance(row[0], str) else dict(row[0] or {})
        up = (meta.get("resolve") or {}).get("_meta", {}).get("utility_policy")
        assert up is not None, meta
        assert "applied" in up
        print("PASS: test_p3_utility_resolve_metadata_audit_record_persisted")
    finally:
        _reset()
        cleanup_temp_db(db, orig)


# ── P3-D: acceptance key quality.utility.tracking_enabled ──

def test_p3_acceptance_tracking_enabled_makes_rerank_active():
    """ADR 0028 P3 acceptance: `quality.utility.tracking_enabled=True` ALONE
    (no per-call flag) makes retrieval utility-aware; off = no effect.

    Regression guard: an earlier implementation still required the caller to
    pass utility_rerank=True, which did NOT satisfy this clause. This test
    asserts the config-only path at BOTH the enhancement layer and the real
    search entrances (unified_search / handle_unified_search).
    """
    db, orig = init_temp_db()
    try:
        from memall.core.db import get_conn
        from memall.core.models import MemoryInput
        from memall.core.thin_waist import capture, unified_search
        from memall.mcp.tools.retrieve import handle_unified_search
        from memall.search.enhancements import apply_enhancements, record_interaction
        c = get_conn()
        mid = int(capture(MemoryInput(
            content="PostgreSQL database configuration decision content long enough to pass gate checks properly",
            level="L4", category="general", agent_name="tester", project="p")))
        for _ in range(4):
            record_interaction(c, query="PostgreSQL", memory_id=mid, action="click", agent_name="tester")
        results = [{"memory_id": mid, "content": "a", "rrf_score": 0.3},
                   {"memory_id": 999, "content": "b", "rrf_score": 0.4}]

        # OFF (default): even with an explicit flag, no signals.
        _reset()
        off = apply_enhancements([dict(r) for r in results], query="q", conn=c,
                                 agent_name="tester", utility_rerank=True)
        assert all("utility_rerank_signal" not in r for r in off["results"])

        # Config key ON, NO per-call flag anywhere.
        _cfg_set("quality.utility.tracking_enabled", True)
        layer = apply_enhancements([dict(r) for r in results], query="q", conn=c,
                                   agent_name="tester")  # deliberately NO utility_rerank
        assert any("utility_rerank_signal" in r for r in layer["results"]), \
            "config-only must activate utility rerank at enhancement layer"

        # Real search entrances, still NO flag in the arguments.
        u = json.loads(unified_search({"query": "PostgreSQL", "mode": "fts5",
                                       "agent_name": "tester"}))
        assert u.get("results"), "query should match the captured memory"
        assert any("utility_rerank_signal" in r for r in u["results"]), \
            "unified_search JSON must carry utility signals from config alone"
        mu = json.loads(handle_unified_search({"query": "PostgreSQL", "mode": "fts5",
                                               "agent_name": "tester"}))
        assert any("utility_rerank_signal" in r for r in (mu.get("results") or [])), \
            "MCP handle_unified_search must carry utility signals from config alone"

        # Off again: no signals, no order influence.
        _cfg_set("quality.utility.tracking_enabled", False)
        back = json.loads(unified_search({"query": "PostgreSQL", "mode": "fts5",
                                          "agent_name": "tester"}))
        assert all("utility_rerank_signal" not in r for r in back.get("results", []))
        c.close()
        print("PASS: test_p3_acceptance_tracking_enabled_makes_rerank_active")
    finally:
        _reset()
        cleanup_temp_db(db, orig)


# ── P1: conversation.session_end inject + continuity flag ──

def test_p1_conversation_session_end_injection_opt_in():
    db, orig = init_temp_db()
    try:
        from memall.core.db import get_conn
        from memall.core.models import MemoryInput
        from memall.core.thin_waist import capture
        # default off: no rewrite
        _reset()
        m1 = capture(MemoryInput(content="会话结束连续性记忆内容足够长用于质量门禁校验通过写入路径。",
                                 level="L4", category="continuity", agent_name="t",
                                 metadata=json.dumps({"session_end_context": True, "session_id": "S1"})))
        c = get_conn()
        meta1 = json.loads(c.execute("SELECT metadata FROM memories WHERE id=?", (int(m1),)).fetchone()[0])
        assert meta1.get("source_pipeline") != "conversation.session_end"
        c.close()
        # opt-in: rewritten
        _cfg_set("session.conversation_session_end_inject", True)
        m2 = capture(MemoryInput(content="会话结束连续性记忆内容足够长用于质量门禁校验通过写入路径第二条。",
                                 level="L4", category="continuity", agent_name="t",
                                 metadata=json.dumps({"session_end_context": True, "session_id": "S2",
                                                      "project": "P", "thread_id": ""})))
        c = get_conn()
        meta2 = json.loads(c.execute("SELECT metadata FROM memories WHERE id=?", (int(m2),)).fetchone()[0])
        c.close()
        assert meta2.get("source_pipeline") == "conversation.session_end", meta2
        assert meta2.get("session_id") == "S2"
        assert meta2.get("conversation_session_end") is True
        print("PASS: test_p1_conversation_session_end_injection_opt_in")
    finally:
        _reset()
        cleanup_temp_db(db, orig)


def test_p1_search_continuity_flag_auto_assembles_pack():
    db, orig = init_temp_db()
    try:
        from memall.core.db import get_conn
        from memall.core.thin_waist import unified_search
        from memall.core.models import MemoryInput
        from memall.core.thin_waist import capture
        capture(MemoryInput(content="用户偏好使用深色主题界面，已在多个会话中确认一致有效。",
                            level="L4", category="general", agent_name="t", project="p",
                            metadata=json.dumps({"slot_key": "slot:profile:theme",
                                                 "resolve": {"decision": "NOOP"}})))
        # default off: no continuity payload
        _reset()
        off = json.loads(unified_search({"query": "深色主题", "mode": "fts5", "continuity": True,
                                         "agent_name": "t", "project": "p"}))
        assert "continuity" not in off
        # opt-in: continuity pack attached, validity-aware + KB excluded
        _cfg_set("search.continuity.enabled", True)
        on = json.loads(unified_search({"query": "深色主题", "mode": "fts5", "continuity": True,
                                        "agent_name": "t", "project": "p"}))
        ce = on.get("continuity")
        assert isinstance(ce, dict) and ce.get("ok") is True, on
        pack = ce.get("continuity_pack") or {}
        assert pack.get("kb_excluded") is True
        assert "active_state" in pack
        print("PASS: test_p1_search_continuity_flag_auto_assembles_pack")
    finally:
        _reset()
        cleanup_temp_db(db, orig)


# ── P4: maintenance / audit / archive / rollback ──

def test_p4_maintenance_default_off_and_status():
    import os
    db, orig = init_temp_db()
    try:
        from memall.mcp.tools import _handle_system
        _reset()
        r = json.loads(_handle_system({"action": "maintenance", "sub_action": "run"}))
        assert r.get("ran") is False and "enabled=False" in str(r.get("reason"))
        s = json.loads(_handle_system({"action": "maintenance", "sub_action": "status"}))
        assert s.get("enabled") is False
        assert "policies" in s
        print("PASS: test_p4_maintenance_default_off_and_status")
    finally:
        _reset()
        cleanup_temp_db(db, orig)


def test_p4_maintenance_run_is_non_destructive_audited_and_rollbackable():
    """ADR 0028 P4 acceptance: after a maintenance run, `metadata.resolve.status`
    and `memory_status` are updated BY RULE; archived rows stay (no silent
    deletion); audit before-snapshot allows exact rollback.

    Regression guard: an earlier implementation only appended a marker and
    changed neither status, which did NOT satisfy the acceptance clause. The
    test uses before-values DISTINCT from the rule targets so an unchanged
    value cannot accidentally pass.
    """
    import os, tempfile
    db, orig = init_temp_db()
    tmp_arch = tempfile.mkdtemp()
    try:
        from memall.core.db import get_conn
        from memall.pipeline import maintenance as m
        _cfg_set("maintenance.archive_dir", tmp_arch)
        _cfg_set("maintenance.audit_log", os.path.join(tmp_arch, "audit.jsonl"))
        _cfg_set("maintenance.enabled", True)
        _cfg_set("maintenance.conflict_review", True)
        _cfg_set("maintenance.expire", True)
        _cfg_set("maintenance.snapshot_before_write", True)
        c = get_conn()
        # h1: CONFLICT row whose resolve.status is 'ACCEPTED' (distinct from the
        # rule target 'REVIEW') -> conflict_review must rewrite it to REVIEW.
        c.execute(
            "INSERT INTO memories (content, content_hash, level, agent_name, category, subject, occurred_at, created_at, updated_at, memory_status, metadata) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            ("冲突复核候选记忆内容足够长用于测试维护任务按规则更新状态字段。", "h_maint_1", "L4", "t", "general",
             "conflict", _now(), _now(), _now(), "CONFLICT",
             json.dumps({"resolve": {"status": "ACCEPTED", "decision": "CONFLICT"}})),
        )
        # h2: expired ACTIVE row -> expire must set memory_status='archived'.
        c.execute(
            "INSERT INTO memories (content, content_hash, level, agent_name, category, subject, occurred_at, created_at, updated_at, invalid_at, memory_status, metadata) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            ("过期候选记忆内容足够长用于测试维护任务归档状态按规则更新。", "h_maint_2", "L4", "t", "general",
             "expired", _now(), _now(), _now(), "2020-01-01T00:00:00Z", "ACTIVE", "{}"),
        )
        c.commit()

        r1 = m.run_maintenance(actions=["conflict_review"], dry_run=False)
        assert r1["ok"] is True and r1["total_marked"] == 1, r1
        row = c.execute("SELECT content, metadata, memory_status FROM memories WHERE content_hash='h_maint_1'").fetchone()
        assert row[0].startswith("冲突复核候选")  # content untouched (non-destructive)
        meta = json.loads(row[1])
        assert meta.get("cognitive_maintenance")[-1]["source_pipeline"] == "cognitive.loop.maintenance"
        assert meta["resolve"]["status"] == "REVIEW", meta  # BY RULE

        r2 = m.run_maintenance(actions=["expire"], dry_run=False)
        assert r2["ok"] is True and r2["total_marked"] == 1, r2
        row2 = c.execute("SELECT memory_status FROM memories WHERE content_hash='h_maint_2'").fetchone()
        assert row2[0] == "archived", row2  # BY RULE, not deleted

        # audit log appended with before-snapshot + rules_applied
        au = m.read_audit(limit=30)
        marks = [e for e in au["entries"] if e.get("action") == "conflict_review"]
        assert marks and marks[0].get("before", {}).get("id")
        assert marks[0].get("after", {}).get("id")

        # rollback restores the EXACT prior resolve.status (distinct value).
        rb = m.rollback_from_audit(marks[0])
        assert rb["ok"] is True
        row3 = c.execute("SELECT metadata, memory_status FROM memories WHERE content_hash='h_maint_1'").fetchone()
        meta3 = json.loads(row3[0])
        assert meta3["resolve"]["status"] == "ACCEPTED", meta3  # restored
        assert "cognitive_maintenance" not in meta3  # marker rolled back too
        assert row3[1] == "CONFLICT"  # memory_status restored

        # archived row still exists (no silent physical deletion)
        exists = c.execute("SELECT COUNT(*) FROM memories WHERE content_hash='h_maint_2'").fetchone()[0]
        assert exists == 1
        c.close()
        # archive anchor is append-only and non-deleting
        ar = m.archive_session("S-arch-1", payload={"note": "test"}, agent_name="t")
        assert ar["ok"] is True and os.path.exists(ar["path"])
        print("PASS: test_p4_maintenance_run_is_non_destructive_audited_and_rollbackable")
    finally:
        _reset()
        import shutil
        shutil.rmtree(tmp_arch, ignore_errors=True)
        cleanup_temp_db(db, orig)


def test_p4_mode_audit_retrieve_surface():
    db, orig = init_temp_db()
    try:
        from memall.mcp.tools.retrieve import handle_retrieve
        from memall.mcp.validator import validate_tool_input
        out = json.loads(handle_retrieve({"mode": "audit", "agent_name": "t", "limit": 5}))
        assert out.get("mode") == "audit" and out.get("ok") is True
        for k in ("conflicts", "history", "evidence", "maintenance"):
            assert k in out
        ok, data, err = validate_tool_input("memall_read", {"action": "retrieve", "mode": "audit", "limit": 5})
        assert ok is True, err
        assert data.get("mode") == "audit"
        print("PASS: test_p4_mode_audit_retrieve_surface")
    finally:
        cleanup_temp_db(db, orig)


def test_p4_include_archived_capability_is_present():
    """Blueprint: archived rows readable via include_archived=True (existing thin_waist capability reused)."""
    db, orig = init_temp_db()
    try:
        from memall.core.db import get_conn
        from memall.core.thin_waist import retrieve
        c = get_conn()
        c.execute(
            "INSERT INTO memories (content, content_hash, level, agent_name, category, subject, occurred_at, created_at, updated_at, memory_status) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            ("归档证据记忆内容足够长用于验证 include_archived 显式可读路径。", "h_arch_1", "L4", "t", "general",
             "archived subject", _now(), _now(), _now(), "archived"),
        )
        c.commit()
        c.close()
        # default excludes
        r0 = retrieve(query="归档证据", viewer="t", include_content=True)
        assert r0 is None or len(r0) == 0
        # include_archived=True exposes it
        r1 = retrieve(query="归档证据", viewer="t", include_content=True, include_archived=True)
        assert r1 and len(r1) >= 1
        print("PASS: test_p4_include_archived_capability_is_present")
    finally:
        cleanup_temp_db(db, orig)


# ── Independent-review regressions: config switch must NOT change recall ──

def _seed_identity(c, viewer="viewer1"):
    try:
        c.execute("INSERT OR REPLACE INTO identities (agent_name, agent_type, trusted_by) VALUES (?,?,?)",
                  (viewer, "human", ""))
        c.commit()
    except Exception:
        pass


def test_p3_config_only_switch_never_changes_recall_set():
    """Review finding: config ON made an fts5 recall of 2 rows become 0
    because viewer identity was used as an author content filter. The master
    switch must affect ORDERING only, never the recall set.
    """
    db, orig = init_temp_db()
    try:
        from memall.core.db import get_conn
        from memall.core.models import MemoryInput
        from memall.core.thin_waist import capture, unified_search
        from memall.search.enhancements import record_interaction
        c = get_conn()
        mids = []
        for txt in [
            "PostgreSQL database configuration decision content long enough to pass gate checks properly",
            "PostgreSQL replication configuration decision content long enough to pass gate checks properly",
        ]:
            mids.append(int(capture(MemoryInput(
                content=txt, level="L4", category="general",
                agent_name="author1", visibility="public", project="p"))))
        _seed_identity(c, "viewer1")
        for _ in range(4):
            record_interaction(c, query="PostgreSQL", memory_id=mids[0],
                               action="click", agent_name="viewer1")
        c.close()

        base = {"query": "PostgreSQL", "mode": "fts5", "agent_name": "viewer1"}
        _reset()
        off = json.loads(unified_search(dict(base)))
        n_off = len(off.get("results") or [])
        _cfg_set("quality.utility.tracking_enabled", True)
        on = json.loads(unified_search(dict(base)))
        n_on = len(on.get("results") or [])
        assert n_off == 2 and n_on == 2, \
            f"config must not change recall: off={n_off} on={n_on}"
        assert any("utility_rerank_signal" in r for r in on.get("results") or [])
        # Explicit author filter still works under config ON.
        filt = json.loads(unified_search({**base, "agent_name_filter": "author1"}))
        assert len(filt.get("results") or []) == 2
        print("PASS: test_p3_config_only_switch_never_changes_recall_set")
    finally:
        _reset()
        cleanup_temp_db(db, orig)


def test_p3_handle_retrieve_config_only_recall_and_per_result_signals():
    """Review finding: handle_retrieve dropped recall under config ON and
    lacked per-result utility signals. Both paths (plain + enhanced) must
    return the SAME recall set, and enhanced results must carry signals.
    """
    db, orig = init_temp_db()
    try:
        from memall.core.db import get_conn
        from memall.core.models import MemoryInput
        from memall.core.thin_waist import capture
        from memall.mcp.tools.retrieve import handle_retrieve
        from memall.search.enhancements import record_interaction
        c = get_conn()
        mids = []
        for txt in [
            "PostgreSQL database configuration decision content long enough to pass gate checks properly",
            "PostgreSQL replication configuration decision content long enough to pass gate checks properly",
        ]:
            mids.append(int(capture(MemoryInput(
                content=txt, level="L4", category="general",
                agent_name="author1", visibility="public", project="p"))))
        _seed_identity(c, "viewer1")
        for _ in range(4):
            record_interaction(c, query="PostgreSQL", memory_id=mids[0],
                               action="click", agent_name="viewer1")
        c.close()
        args = {"query": "PostgreSQL", "agent_name": "viewer1"}

        def recall(raw):
            o = json.loads(raw)
            if isinstance(o, list):
                return len(o), any("utility_rerank_signal" in r for r in o)
            rs = o.get("results") or []
            return len(rs), any("utility_rerank_signal" in r for r in rs)

        _reset()
        n_off, _ = recall(handle_retrieve(dict(args)))
        _cfg_set("quality.utility.tracking_enabled", True)
        n_on, has_sig = recall(handle_retrieve(dict(args)))
        assert n_off == 2 and n_on == 2, f"recall changed: off={n_off} on={n_on}"
        assert has_sig is True, "enhanced handle_retrieve must carry per-result utility signals"
        _cfg_set("quality.utility.tracking_enabled", False)
        n_back, _ = recall(handle_retrieve(dict(args)))
        assert n_back == 2
        print("PASS: test_p3_handle_retrieve_config_only_recall_and_per_result_signals")
    finally:
        _reset()
        cleanup_temp_db(db, orig)
