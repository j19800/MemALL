"""
Tests for the multi-agent collaboration improvements:
  P1 — smart consensus synthesis (convergence._analyze_stances / converge_discussion)
  P2 — capability-aware participant suggestion (agent_routing.suggest_participants)
  P3 — collaboration-aware context injection (context_assembler.build_context)
  P4 — semantic cross-agent distillation (cross_agent._semantic_clusters / knowledge_distill_step)
"""

import json
import hashlib

from tests.test_helpers import init_temp_db, cleanup_temp_db, insert_memory


# ── P1: smart consensus synthesis ──────────────────────────────────


def test_analyze_stances_consensus():
    from memall.pipeline.convergence import _analyze_stances
    responses = [
        {"metadata": json.dumps({"agent_name": "claude", "stance": "confirm", "note": "方案可行"})},
        {"metadata": json.dumps({"agent_name": "codex", "stance": "confirm", "note": "同意"})},
    ]
    a = _analyze_stances(responses)
    assert a["consensus"] is True
    assert a["conflict"] is False
    assert a["counts"]["confirm"] == 2
    assert "一致通过" in a["synthesis"]
    print("  PASS test_analyze_stances_consensus")


def test_analyze_stances_conflict():
    from memall.pipeline.convergence import _analyze_stances
    responses = [
        {"metadata": json.dumps({"agent_name": "claude", "stance": "confirm", "note": "支持上线"})},
        {"metadata": json.dumps({"agent_name": "codex", "stance": "reject", "note": "风险太高"})},
    ]
    a = _analyze_stances(responses)
    assert a["consensus"] is False
    assert a["conflict"] is True
    assert "codex" in a["dissenters"]
    assert "分歧" in a["synthesis"]
    print("  PASS test_analyze_stances_conflict")


def test_converge_derives_conclusion_and_records_meta():
    db, orig = init_temp_db()
    try:
        from memall.pipeline.convergence import create_discussion, confirm_discussion, get_discussion
        from memall.core.db import get_conn

        disc = create_discussion(
            title="[TEST] 是否引入 A2A",
            background="讨论是否接入 A2A 协议",
            participants=["claude", "codex"],
            creator="workbuddy",
        )
        # first response: pending (missing codex)
        r1 = confirm_discussion(disc["memory_id"], "claude", stance="confirm", note="建议接入")
        assert r1["status"] == "pending"
        # second response: reject -> converge with conflict synthesis
        r2 = confirm_discussion(disc["memory_id"], "codex", stance="reject", note="暂不接入")
        assert r2["status"] == "converged"

        full = get_discussion(disc["memory_id"])
        meta = full["discussion"]
        assert meta["status"] == "converged"
        assert meta.get("consensus") is False
        assert meta.get("conflict") is True
        assert "codex" in (meta.get("dissenters") or [])
        # conclusion should be DERIVED (synthesis) since none was preset
        assert "分歧" in (meta.get("conclusion") or "")

        # L4 decision should carry 共识分析 section
        conn = get_conn()
        l4 = conn.execute(
            "SELECT content, metadata FROM memories WHERE id = ?", (r2["decision_id"],)
        ).fetchone()
        assert "## 共识分析" in l4["content"]
        assert "存在分歧" in l4["content"]
        l4meta = json.loads(l4["metadata"])
        assert l4meta["conflict"] is True
        assert l4meta["consensus"] is False
        conn.close()
        print("  PASS test_converge_derives_conclusion_and_records_meta")
    finally:
        cleanup_temp_db(db, orig)


def test_converge_keeps_preset_conclusion():
    db, orig = init_temp_db()
    try:
        from memall.pipeline.convergence import create_discussion, confirm_discussion, get_discussion
        disc = create_discussion(
            title="[TEST] 预设结论",
            background="x",
            recommendation="最终决定：按原计划推进",
            participants=["claude"],
            creator="workbuddy",
        )
        # inject a preset conclusion into the discussion metadata
        from memall.core.db import get_conn
        conn = get_conn()
        row = conn.execute("SELECT metadata FROM memories WHERE id=?", (disc["memory_id"],)).fetchone()
        m = json.loads(row["metadata"])
        m["conclusion"] = "预设的明确结论"
        conn.execute("UPDATE memories SET metadata=? WHERE id=?", (json.dumps(m), disc["memory_id"]))
        conn.commit(); conn.close()

        r = confirm_discussion(disc["memory_id"], "claude", stance="confirm", note="ok")
        assert r["status"] == "converged"
        full = get_discussion(disc["memory_id"])
        assert full["discussion"]["conclusion"] == "预设的明确结论"
        print("  PASS test_converge_keeps_preset_conclusion")
    finally:
        cleanup_temp_db(db, orig)


# ── P2: capability-aware participant suggestion ───────────────────


def test_suggest_participants_ranks_by_relevance():
    db, orig = init_temp_db()
    try:
        from memall.core.db import get_conn
        from memall.pipeline.agent_routing import suggest_participants

        conn = get_conn()
        # Two agents with distinct profiles
        conn.execute(
            "INSERT INTO identities (agent_name, agent_type, profile_json) VALUES (?, 'ai', ?)",
            ("db_expert", json.dumps({"l1_identity": [{"snippet": "我精通数据库优化与 SQL 调优"}]})),
        )
        conn.execute(
            "INSERT INTO identities (agent_name, agent_type, profile_json) VALUES (?, 'ai', ?)",
            ("fe_dev", json.dumps({"l1_identity": [{"snippet": "我擅长前端 React 与组件设计"}]})),
        )
        conn.commit(); conn.close()

        recs = suggest_participants(
            title="数据库慢查询怎么优化",
            background="线上 SQL 性能问题",
            exclude=["workbuddy"],
        )
        assert recs, "expected at least one recommendation"
        assert recs[0]["agent"] == "db_expert", recs
        print("  PASS test_suggest_participants_ranks_by_relevance")
    finally:
        cleanup_temp_db(db, orig)


def test_create_discussion_suggest_populates_metadata():
    db, orig = init_temp_db()
    try:
        from memall.pipeline.convergence import create_discussion
        from memall.core.db import get_conn
        conn = get_conn()
        conn.execute(
            "INSERT INTO identities (agent_name, agent_type, profile_json) VALUES (?, 'ai', ?)",
            ("doc_writer", json.dumps({"l1_identity": [{"snippet": "我负责技术文档撰写"}]})),
        )
        conn.commit(); conn.close()

        disc = create_discussion(
            title="[TEST] 文档协作",
            background="需要写一份技术文档",
            creator="workbuddy",
            suggest=True,
        )
        assert "suggested_participants" in disc
        assert "doc_writer" in disc["suggested_participants"]
        print("  PASS test_create_discussion_suggest_populates_metadata")
    finally:
        cleanup_temp_db(db, orig)


# ── P3: collaboration-aware context injection ─────────────────────


def test_build_context_includes_active_discussion():
    db, orig = init_temp_db()
    try:
        from memall.core.context_assembler import build_context
        from memall.core.db import get_conn

        agent = "codex"
        conn = get_conn()
        meta = json.dumps({
            "status": "active",
            "participants": [agent],
            "options": [], "open_questions": [], "action_items": [],
            "conclusion": "", "converged_at": "", "convergence_reason": "",
        })
        content = "[讨论] [TEST] 待 codex 回应"
        ch = hashlib.sha256(content.encode()).hexdigest()
        conn.execute(
            "INSERT INTO memories (content, content_hash, level, owner, agent_name, subject, category, confidence, visibility, metadata, occurred_at, created_at, updated_at) "
            "VALUES (?,?, 'L5','system','workbuddy','[讨论] [TEST] 待 codex 回应','discussion',0.5,'private',?,?,?,?)",
            (content, ch, meta, "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z"),
        )
        conn.commit(); conn.close()

        result = build_context(agent, max_tokens=2000)
        assert "[协作-待回应]" in result["context"], result["context"][:500]
        print("  PASS test_build_context_includes_active_discussion")
    finally:
        cleanup_temp_db(db, orig)


# ── P4: semantic cross-agent distillation ─────────────────────────


def test_cross_agent_keyword_fallback_when_no_embeddings():
    db, orig = init_temp_db()
    try:
        import memall.pipeline.cross_agent as ca
        import memall.graph.embeddings as emb
        # Force embeddings unavailable so it uses the keyword path.
        # Patch the symbol _semantic_clusters actually imports from.
        emb._embed_texts = lambda texts, normalize=True: None

        lessons = [
            {"content": "数据库连接池泄漏导致性能下降", "agent_name": "a", "confidence": 0.8, "weight": 1},
            {"content": "数据库连接超时需要优化索引", "agent_name": "b", "confidence": 0.8, "weight": 1},
            {"content": "前端组件渲染卡顿需要懒加载", "agent_name": "c", "confidence": 0.8, "weight": 1},
        ]
        clusters = ca._semantic_clusters(lessons)
        assert clusters is None  # embeddings unavailable -> caller falls back
        kw = ca._keyword_clusters(lessons)
        assert "数据库" in kw and len(kw["数据库"]) == 2
        print("  PASS test_cross_agent_keyword_fallback_when_no_embeddings")
    finally:
        cleanup_temp_db(db, orig)


def test_cross_agent_distill_generates_l10_l11():
    db, orig = init_temp_db()
    try:
        import memall.pipeline.cross_agent as ca
        import memall.graph.embeddings as emb
        from memall.core.db import get_conn
        emb._embed_texts = lambda texts, normalize=True: None  # force keyword path

        conn = get_conn()
        # Seed 6 L6 lessons about 数据库 (>=5 triggers L11) and one about 前端.
        # Content must exceed 20 chars to pass knowledge_distill_step's
        # LENGTH(TRIM(content)) > 20 filter (Chinese counts as characters).
        for i, txt in enumerate([
            "数据库连接池泄漏导致性能下降，需要引入连接池监控与回收机制",
            "数据库连接超时需要优化索引，避免慢查询拖垮整个服务",
            "慢查询需要加复合索引，并且要定期分析执行计划",
            "数据库主从延迟要监控，否则读取到的是过期数据",
            "数据库分库分表方案评估，需要结合业务峰值来做",
            "数据库备份策略要完善，做到可恢复可验证，并定期演练恢复流程",
            "前端组件渲染卡顿需要懒加载，减少首屏资源体积",
        ]):
            ch = hashlib.sha256(txt.encode()).hexdigest()
            conn.execute(
                "INSERT INTO memories (content, content_hash, level, owner, agent_name, category, confidence, occurred_at, created_at, updated_at, metadata) "
                "VALUES (?,?, 'L6','system','agentX','lesson',0.8,?,?,?,'{}')",
                (txt, ch, "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z"),
            )
        conn.commit(); conn.close()

        res = ca.knowledge_distill_step()
        assert res["cluster_method"] == "keyword"
        assert res["l10_created"] >= 1, res
        assert res["l11_created"] >= 1, res
        print("  PASS test_cross_agent_distill_generates_l10_l11")
    finally:
        cleanup_temp_db(db, orig)
