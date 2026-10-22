"""
Test Suite — 两处契约缺陷的回归护栏（2026-10-22）

1. **状态双轨**：``memory_status`` 与 ``metadata.status`` 曾经共用一列承载两套
   状态机——``capture()`` 把任务状态镜像进列里（迁移 023 还回填过一次），而
   pipeline 又往同一列写存储生命周期状态（archived/dormant/superseded/conflict/
   epoch）。结果是软删除的任务卡仍带着 ``status=active``，在每个 agent 的待办里
   复活；而 pipeline 写的 ``conflict`` 会抹掉卡片的真实进度。现在
   ``metadata.status`` 是唯一的任务/讨论状态轨道，``memory_status`` 只剩生命周期。
   定义见 ``memall/core/status.py``，数据修复见迁移 034。

2. **discussions 表**：迁移 019 建了 ``discussion_topics``/``discussion_responses``，
   但收敛引擎从来没用过它们——讨论是 ``L5`` + ``category='discussion'`` 的记忆，
   表态是 ``category='discussion_response'`` 记忆 + ``edges.relation_type='cites'``。
   库里两张表已经消失而 ``schema_version`` 仍记录 019 已应用（其
   ``l4_memory_id`` 指向的 #6303 也不存在了），文档还把 ``discussions`` 列为一张表。
   迁移 033 先把残留行导入真实模型再删表；文档与代码归一。
"""

import hashlib
import json
import os
import sqlite3
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from tests.test_helpers import init_temp_db, cleanup_temp_db  # noqa: E402

_TASK_BODY = (
    "[任务] 回归护栏任务卡：因为验收方需要可核验的交付物，"
    "所以正文必须写明根因、验收标准和 deadline。"
)


def _row(conn, memory_id):
    return conn.execute(
        "SELECT level, category, metadata, memory_status FROM memories WHERE id=?",
        (memory_id,),
    ).fetchone()


def _conn(db_path):
    """A raw connection that can write to ``memories``.

    The FTS5 triggers call the ``memall_split_cjk`` application function, which
    only exists on connections built through ``memall.core.db`` — a bare
    ``sqlite3.connect()`` fails on any INSERT/UPDATE touching memories.
    """
    from memall.core.db import _register_sql_functions

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    _register_sql_functions(conn)
    return conn


def _content(conn, memory_id) -> str:
    return conn.execute(
        "SELECT content FROM memories WHERE id=?", (memory_id,)
    ).fetchone()[0]


def _load_migration(stem: str):
    """Load a migration module the way memall.migrations does (by file path)."""
    import importlib.util

    path = os.path.join(
        os.path.dirname(__file__), "..", "src", "memall", "migrations", f"{stem}.py"
    )
    spec = importlib.util.spec_from_file_location(f"test_migration_{stem}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _capture_task(content=_TASK_BODY, **overrides):
    from memall.core.thin_waist import capture

    data = {
        "content": content,
        "level": "L5",
        "category": "task",
        "agent_name": "track_agent",
        "subject": "[任务] 状态单轨验证",
        "project": "memall",
    }
    data.update(overrides)
    return capture(data)


# ── 1. 状态单轨 ────────────────────────────────────────────────────────


def test_capture_no_longer_mirrors_task_status_into_memory_status():
    """任务状态只住 metadata.status；列里不能再出现 active/done。"""
    from memall.core.status import LIFECYCLE_STATUSES

    db_path, patcher = init_temp_db()
    try:
        conn = _conn(db_path)
        mid = _capture_task()
        row = _row(conn, mid)
        meta = json.loads(row["metadata"])
        assert meta["status"] == "active", meta
        assert row["memory_status"] is None, (
            "capture() must not write the workflow status into the lifecycle "
            f"column, got {row['memory_status']!r}"
        )
        values = {
            r[0]
            for r in conn.execute(
                "SELECT DISTINCT memory_status FROM memories "
                "WHERE memory_status IS NOT NULL"
            )
        }
        assert values <= set(LIFECYCLE_STATUSES), values
        conn.close()
    finally:
        cleanup_temp_db(db_path, patcher)


def test_capture_rejects_status_outside_the_vocabulary():
    """词表外的状态会在所有读取侧隐身，必须在写入口直接拒绝。"""
    import pytest

    db_path, patcher = init_temp_db()
    try:
        with pytest.raises(ValueError):
            _capture_task(metadata={"status": "in_progress"})
        with pytest.raises(ValueError):
            _capture_task(metadata={"status": {"value": "nonsense", "_meta": {"v": 1}}})
        # 合法值必须放行
        assert _capture_task(metadata={"status": "done"})
    finally:
        cleanup_temp_db(db_path, patcher)


def test_update_accepts_pipeline_vocabulary_and_flattens_envelopes():
    """update() 的词表必须覆盖 task_lifecycle/convergence 实际写的值。"""
    db_path, patcher = init_temp_db()
    try:
        from memall.core.thin_waist import update

        mid = _capture_task()
        for status in ("blocked", "resolved", "converged", "done", "active"):
            assert update(mid, metadata={"status": status}), status
        # 信封写法会被摊平成标量，否则 json_extract 读不到
        assert update(mid, metadata={
            "status": {"value": "active", "_meta": {"version": 1}}
        })
        conn = _conn(db_path)
        raw = conn.execute(
            "SELECT metadata FROM memories WHERE id=?", (mid,)
        ).fetchone()[0]
        conn.close()
        assert json.loads(raw)["status"] == "active", raw
        # 列仍然只承载生命周期
        conn = _conn(db_path)
        assert conn.execute(
            "SELECT memory_status FROM memories WHERE id=?", (mid,)
        ).fetchone()[0] is None
        conn.close()
    finally:
        cleanup_temp_db(db_path, patcher)


def test_soft_deleted_task_is_not_pending_for_any_reader():
    """幽灵待办：memory_status=archived 而 metadata.status=active 的卡不得注入。"""
    db_path, patcher = init_temp_db()
    try:
        mid = _capture_task(content=_TASK_BODY + " 幽灵待办验证。")
        conn = _conn(db_path)
        conn.execute(
            "UPDATE memories SET memory_status='archived' WHERE id=?", (mid,)
        )
        conn.commit()
        conn.close()

        from memall.pipeline.task_lifecycle import list_active_tasks

        assert [t["task_id"] for t in list_active_tasks("track_agent")] == []

        from memall.mcp.federation_tools import _pending_l5_for

        conn = _conn(db_path)
        pending, global_rows = _pending_l5_for(conn, "track_agent")
        conn.close()
        assert pending == [] and global_rows == []

        from memall.core.context_assembler import _build_tier1

        conn = _conn(db_path)
        lines = _build_tier1("track_agent", conn)
        conn.close()
        assert not any(line.startswith("[Task") for line in lines), lines
    finally:
        cleanup_temp_db(db_path, patcher)


def test_enveloped_status_still_counts_as_active_for_python_readers():
    """历史信封值不该让卡片在 auto_inject 里隐身。"""
    db_path, patcher = init_temp_db()
    try:
        mid = _capture_task(content=_TASK_BODY + " 信封兼容验证。")
        conn = _conn(db_path)
        conn.execute(
            "UPDATE memories SET metadata=? WHERE id=?",
            (
                json.dumps({
                    "status": {"value": "active", "_meta": {"version": 1}},
                    "category_hint": "task",
                }, ensure_ascii=False),
                mid,
            ),
        )
        conn.commit()
        conn.row_factory = sqlite3.Row
        from memall.mcp.federation_tools import _pending_l5_for

        pending, _ = _pending_l5_for(conn, "track_agent")
        conn.close()
        assert [p["id"] for p in pending] == [mid], pending
    finally:
        cleanup_temp_db(db_path, patcher)


def test_migration_034_repairs_legacy_double_track():
    """存量漂移必须被迁移 034 修回单轨：镜像值出列、信封摊平、缺省补 active。"""
    db_path, patcher = init_temp_db()
    try:
        conn = _conn(db_path)

        def seed(tag, meta, memory_status):
            content = f"历史漂移卡 {tag}：因为迁移前状态写在两轨上，所以要归一。"
            ch = hashlib.sha256(content.encode("utf-8")).hexdigest()
            conn.execute(
                "INSERT INTO memories (content, content_hash, level, owner, agent_name,"
                " category, occurred_at, created_at, updated_at, metadata, memory_status)"
                " VALUES (?,?,'L5','x','legacy_agent','task',datetime('now'),"
                " datetime('now'), datetime('now'), ?,?)",
                (content, ch, json.dumps(meta, ensure_ascii=False), memory_status),
            )
            return ch

        hashes = {
            "mirror_active": seed("A", {}, "active"),
            "mirror_done": seed("B", {}, "done"),
            "envelope": seed("C", {"status": {"value": "active", "_meta": {"version": 1}}}, "conflict"),
            "statusless": seed("D", {}, None),
            "soft_deleted": seed("E", {}, "archived"),
            "ghost": seed("F", {"status": "active"}, "archived"),
        }
        conn.commit()

        mod = _load_migration("034_split_status_tracks")
        mod.apply(conn)

        got = {
            r[0]: (
                json.loads(r[1])["status"] if r[1] else None,
                r[2],
            )
            for r in conn.execute(
                "SELECT content_hash, metadata, memory_status FROM memories"
            )
        }
        # A: 状态从列里搬进 metadata，列清空
        assert got[hashes["mirror_active"]] == ("active", None), got
        # B: 已完成不得复活成待办
        assert got[hashes["mirror_done"]] == ("done", None), got
        # C: 信封摊平为标量，生命周期标记（conflict）保留在列里
        assert got[hashes["envelope"]] == ("active", "conflict"), got
        # D: 无状态的开放任务卡补默认 active（AGENTS.md 缺省视为 active）
        assert got[hashes["statusless"]] == ("active", None), got
        # E: 软删除在两轨上都表达"离开在线集合"
        assert got[hashes["soft_deleted"]] == ("archived", "archived"), got

        # 034 只做归轨：F 这种"列已归档、metadata 还写着 active"的矛盾留给 035，
        # 所以不变量里只剩这一项非零——它正是幽灵待办的定义。
        invariant = mod.validate(conn)
        assert invariant == {
            "lifecycle_track_violations": 0,
            "enveloped_workflow_statuses": 0,
            "archived_but_pending_cards": 1,
        }, invariant

        # 幂等：重跑不改变结果
        mod.apply(conn)
        assert mod.validate(conn) == invariant
        conn.close()
    finally:
        cleanup_temp_db(db_path, patcher)


def test_migration_035_reconciles_ghost_and_keeps_forget_semantics():
    """生命周期轨优先修幽灵卡；forget 的"metadata 归档、行仍在线"不得被反向改动。"""
    db_path, patcher = init_temp_db()
    try:
        conn = _conn(db_path)

        def seed(tag, meta, memory_status):
            content = f"轨道矛盾卡 {tag}：两轨说法不一致时必须有一轨让位。"
            ch = hashlib.sha256(content.encode("utf-8")).hexdigest()
            conn.execute(
                "INSERT INTO memories (content, content_hash, level, owner, agent_name,"
                " category, occurred_at, created_at, updated_at, metadata, memory_status)"
                " VALUES (?,?,'L5','x','legacy_agent','task',datetime('now'),"
                " datetime('now'), datetime('now'), ?,?)",
                (content, ch, json.dumps(meta, ensure_ascii=False), memory_status),
            )
            return ch

        ghost = seed("ghost", {"status": "active"}, "archived")
        forgotten = seed("forgotten", {"status": "archived"}, None)
        blocked = seed("blocked", {"status": "blocked"}, "conflict")
        conn.commit()

        mod34 = _load_migration("034_split_status_tracks")
        mod35 = _load_migration("035_reconcile_archived_cards")
        mod35.apply(conn)

        got = {
            r[0]: (json.loads(r[1])["status"], r[2])
            for r in conn.execute(
                "SELECT content_hash, metadata, memory_status FROM memories"
            )
        }
        # 列说已删除，metadata 不能再声称待办
        assert got[ghost] == ("archived", "archived"), got
        # forget 的归档只写在 metadata，行必须仍然在线（否则历史检索会丢）
        assert got[forgotten] == ("archived", None), got
        # conflict 不是删除，工作流状态原样保留
        assert got[blocked] == ("blocked", "conflict"), got

        assert mod34.validate(conn) == {
            "lifecycle_track_violations": 0,
            "enveloped_workflow_statuses": 0,
            "archived_but_pending_cards": 0,
        }
        # 幂等
        mod35.apply(conn)
        assert mod34.validate(conn)["archived_but_pending_cards"] == 0
        conn.close()
    finally:
        cleanup_temp_db(db_path, patcher)


# ── 2. discussions 表示模型 ────────────────────────────────────────────


def test_fresh_schema_has_no_discussion_tables():
    """讨论只有一个表示：memories(L5/discussion) + edges(cites)。死表不得再出现。"""
    db_path, patcher = init_temp_db()
    try:
        conn = _conn(db_path)
        names = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE name LIKE 'discussion%'"
            )
        }
        conn.close()
        assert names == set(), names
    finally:
        cleanup_temp_db(db_path, patcher)


def test_discussion_lives_in_memories_and_cites_edges():
    """create_discussion/respond 的落库形状就是契约本身。"""
    db_path, patcher = init_temp_db()
    try:
        from memall.pipeline.convergence import create_discussion, confirm_discussion

        created = create_discussion(
            title="单轨契约验证：讨论落库形状",
            background="因为表态靠 edges 关联，所以要断言 cites 边存在。",
            participants=["track_agent", "track_agent_b"],
            creator="track_agent",
        )
        mid = created.get("memory_id") or created.get("id")
        assert mid, created

        conn = _conn(db_path)
        row = _row(conn, mid)
        assert (row["level"], row["category"]) == ("L5", "discussion"), dict(row)
        meta = json.loads(row["metadata"])
        assert meta["status"] == "active"
        assert row["memory_status"] is None, "讨论卡同样不占生命周期轨"

        confirm = confirm_discussion(mid, agent_name="track_agent", stance="agree")
        edges = conn.execute(
            "SELECT target_id FROM edges WHERE source_id=? AND relation_type='cites'",
            (mid,),
        ).fetchall()
        assert len(edges) == 1, edges

        # 表态行住在 P2——两个读取侧（收敛与路由）都按这个 level 选
        resp = _row(conn, edges[0]["target_id"])
        assert (resp["level"], resp["category"]) == ("P2", "discussion_response"), dict(resp)

        # 法定人数：两名参与方只有一位表态时不得收敛
        assert confirm.get("status") == "pending", confirm
        assert json.loads(_row(conn, mid)["metadata"])["status"] == "active"

        confirm_discussion(mid, agent_name="track_agent_b", stance="agree")
        after = _row(conn, mid)
        assert json.loads(after["metadata"])["status"] == "converged", after["metadata"]
        conn.close()
    finally:
        cleanup_temp_db(db_path, patcher)


_LEGACY_SQL = """
CREATE TABLE discussion_topics (
    topic_id TEXT PRIMARY KEY,
    l4_memory_id INTEGER,
    title TEXT, background TEXT, options TEXT, participants TEXT,
    open_questions TEXT, status TEXT,
    rounds_without_new_args INTEGER DEFAULT 0,
    timeout_hours INTEGER DEFAULT 24,
    created_at TEXT, updated_at TEXT, closed_at TEXT
);
CREATE TABLE discussion_responses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    topic_id TEXT, agent_name TEXT, stance TEXT, arguments TEXT,
    response_round INTEGER, responded_at TEXT,
    UNIQUE(topic_id, agent_name, response_round)
);
INSERT INTO discussion_topics VALUES
  ('dt_old1', NULL, '已完结的历史讨论', '因为要验证导入，所以留一行。',
   '["甲方案","乙方案"]', '["track_agent","track_agent_b"]', '[]',
   'converged', 2, 24, '2026-06-16T10:00:00+00:00',
   '2026-06-16T12:00:00+00:00', '2026-06-16T12:00:00+00:00'),
  ('dt_old2', NULL, '无人回应的历史讨论', '超时未收敛。',
   '[]', '["track_agent"]', '[]',
   'active', 0, 24, '2026-06-16T09:00:00+00:00',
   '2026-06-16T09:00:00+00:00', NULL);
INSERT INTO discussion_responses
  (topic_id, agent_name, stance, arguments, response_round, responded_at)
  VALUES ('dt_old1','track_agent','agree','因为方案甲更省。',1,'2026-06-16T11:00:00+00:00'),
         ('dt_old1','track_agent_b','disagree','乙方案风险高。',1,'2026-06-16T11:30:00+00:00');
"""


def test_migration_033_imports_stranded_rows_then_drops_tables():
    """残留的旧表数据必须导入真实模型后才删除，且不得变成幽灵待办。"""
    db_path, patcher = init_temp_db()
    try:
        conn = _conn(db_path)
        conn.executescript(_LEGACY_SQL)
        conn.commit()

        mod = _load_migration("033_retire_discussion_tables")
        mod.apply(conn)

        conn.row_factory = sqlite3.Row
        topics = {
            json.loads(r["metadata"])["source_topic_id"]: r
            for r in conn.execute(
                "SELECT id, level, category, metadata, memory_status, created_at"
                " FROM memories WHERE json_extract(metadata,'$.migrated_from')"
                " = 'discussion_topics'"
            )
        }
        assert set(topics) == {"dt_old1", "dt_old2"}, list(topics)

        converged = topics["dt_old1"]
        meta = json.loads(converged["metadata"])
        assert (converged["level"], converged["category"]) == ("L5", "discussion")
        assert meta["status"] == "converged", meta
        assert meta["participants"] == ["track_agent", "track_agent_b"]
        assert meta["options"] == ["甲方案", "乙方案"]
        assert "[讨论] 已完结的历史讨论" in _content(conn, converged["id"])
        assert "== 解决方案 ==" in _content(conn, converged["id"])

        stale = topics["dt_old2"]
        stale_meta = json.loads(stale["metadata"])
        # 四个月前无人回应的讨论：可读，但不得再进任何待办/收敛队列
        assert stale_meta["status"] == "archived", stale_meta
        assert stale["memory_status"] == "archived", dict(stale)

        responses = conn.execute(
            "SELECT id, level, category, metadata FROM memories WHERE category="
            " 'discussion_response' AND json_extract(metadata,'$.migrated_from')"
            " = 'discussion_responses'"
        ).fetchall()
        assert len(responses) == 2, [dict(r) for r in responses]
        stances = {json.loads(r["metadata"])["stance"] for r in responses}
        assert stances == {"confirm", "reject"}, stances
        for r in responses:
            assert r["level"] == "P2", dict(r)
            assert json.loads(r["metadata"])["discussion_id"] == converged["id"]
            linked = conn.execute(
                "SELECT 1 FROM edges WHERE source_id=? AND target_id=?"
                " AND relation_type='cites'",
                (converged["id"], r["id"]),
            ).fetchone()
            assert linked, (converged["id"], r["id"])

        leftover = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE name LIKE 'discussion%'"
            )
        }
        assert leftover == set(), leftover

        from memall.pipeline.convergence import list_active_discussions

        assert list_active_discussions() == [], "退役讨论不得回流待办"

        # 幂等：表已删除，重跑无事发生
        mod.apply(conn)
        assert conn.execute(
            "SELECT COUNT(*) FROM memories WHERE category='discussion_response'"
        ).fetchone()[0] == 2

        # 真正的幂等：把同样的旧表再灌一次（content_hash 上没有唯一索引，
        # 单靠 INSERT OR IGNORE 挡不住重复），行数必须不变
        conn.executescript(_LEGACY_SQL)
        conn.commit()
        mod.apply(conn)
        assert conn.execute(
            "SELECT COUNT(*) FROM memories WHERE category IN ('discussion',"
            " 'discussion_response')"
        ).fetchone()[0] == 4, "migration 033 must not double-import the same history"
        conn.close()
    finally:
        cleanup_temp_db(db_path, patcher)


def test_architecture_doc_matches_the_real_storage_model():
    """ARCHITECTURE.md 不能再声明一个不存在的 discussions 表。"""
    doc = os.path.join(
        os.path.dirname(__file__), "..", "src", "memall", "ARCHITECTURE.md"
    )
    with open(doc, encoding="utf-8") as fh:
        text = fh.read()
    assert "`discussions`" not in text, (
        "ARCHITECTURE.md lists a discussions table that no migration creates and "
        "no code reads — describe the real model (L5 memories + cites edges) instead"
    )
    assert "category='discussion'" in text or "cites" in text, (
        "the discussion storage contract must be documented where the schema is"
    )
