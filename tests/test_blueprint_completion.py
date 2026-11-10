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
    db, orig = init_temp_db()
    try:
        from memall.core.db import get_conn
        from memall.search.enhancements import apply_enhancements, record_interaction
        c = get_conn()
        for _ in range(3):
            record_interaction(c, query="q", memory_id=1, action="click", agent_name="t")
        results = [{"memory_id": 1, "content": "a", "rrf_score": 0.3},
                   {"memory_id": 2, "content": "b", "rrf_score": 0.4}]
        # OFF (default): no signals
        _reset()
        off = apply_enhancements([dict(r) for r in results], query="q", conn=c,
                                 agent_name="t", utility_rerank=True)
        assert all("utility_rerank_signal" not in r for r in off["results"])
        # Blueprint key ON: signals appear
        _cfg_set("quality.utility.tracking_enabled", True)
        # Search rerank uses search.utility_rerank config for min_interactions;
        # align its window with the tracking switch for this acceptance test.
        on = apply_enhancements([dict(r) for r in results], query="q", conn=c,
                                agent_name="t", utility_rerank=True)
        assert any("utility_rerank_signal" in r for r in on["results"])
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
    import os, tempfile
    db, orig = init_temp_db()
    tmp_arch = tempfile.mkdtemp()
    try:
        from memall.core.db import get_conn
        from memall.pipeline import maintenance as m
        # redirect archive/audit to a temp dir to avoid polluting the repo
        _cfg_set("maintenance.archive_dir", tmp_arch)
        _cfg_set("maintenance.audit_log", os.path.join(tmp_arch, "audit.jsonl"))
        _cfg_set("maintenance.enabled", True)
        _cfg_set("maintenance.conflict_review", True)
        _cfg_set("maintenance.snapshot_before_write", True)
        c = get_conn()
        c.execute(
            "INSERT INTO memories (content, content_hash, level, agent_name, category, subject, occurred_at, created_at, updated_at, memory_status, metadata) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            ("冲突复核候选记忆内容足够长用于测试维护任务非破坏性标记。", "h_maint_1", "L4", "t", "general",
             "conflict", _now(), _now(), _now(), "CONFLICT", "{}"),
        )
        c.commit()
        # run one pass
        res = m.run_maintenance(actions=["conflict_review"], dry_run=False)
        assert res["ok"] is True and res["total_marked"] >= 1, res
        # content untouched, metadata marker added, still readable
        row = c.execute("SELECT content, metadata, memory_status FROM memories WHERE content_hash='h_maint_1'").fetchone()
        assert row[0].startswith("冲突复核候选")
        meta = json.loads(row[1])
        assert meta.get("cognitive_maintenance")
        assert meta["cognitive_maintenance"][-1]["source_pipeline"] == "cognitive.loop.maintenance"
        # audit log appended with before-snapshot
        au = m.read_audit(limit=20)
        marks = [e for e in au["entries"] if e.get("action") == "conflict_review"]
        assert marks and marks[0].get("before", {}).get("id")
        # rollback restores exact prior metadata/status
        before = marks[0]["before"]
        rb = m.rollback_from_audit(marks[0])
        assert rb["ok"] is True
        row2 = c.execute("SELECT metadata, memory_status FROM memories WHERE content_hash='h_maint_1'").fetchone()
        assert row2[0] == before.get("metadata") or json.loads(row2[0]) == {}
        c.close()
        # archive anchor is append-only and non-deleting
        ar = m.archive_session("S-arch-1", payload={"note": "test"}, agent_name="t")
        assert ar["ok"] is True
        assert os.path.exists(ar["path"])
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
