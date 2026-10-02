"""
Test Suite — 系统验收审计发现的缺陷修复（回归护栏）
==================================================
对应 2026-09-26 全系统完整度/安全性审计中确认的三处缺陷：

1. ``pipeline/entity_pipeline.py`` 使用不存在的游标列（cursor_name/cursor_value），
   真实 schema 是 (step, cursor_id, updated_at) → entity_extraction_step 每次静默失败。
2. ``mcp/federation_tools.py`` fed_deliver 以纯关键字调用 capture()，
   而 capture 的首个参数是位置参数 data → 每次调用必抛 TypeError。
3. ``mcp/tools/memory_write.py`` handle_update 无归属校验 →
   任意 agent 可篡改他人记忆并把 agent_name 改成自己（越权抢占归属）。
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


def test_entity_extraction_step_runs_with_cursor_fix():
    """entity_extraction_step 不得因游标列名错误而抛 OperationalError。"""
    from tests.test_helpers import init_temp_db, cleanup_temp_db, insert_memory
    from memall.pipeline.entity_pipeline import entity_extraction_step
    from memall.core.db import get_conn

    db_path, patcher = init_temp_db()
    try:
        conn = get_conn()
        for i in range(3):
            insert_memory(
                conn,
                f"MemALL 与 老陈 在 2026 年讨论的架构决策 #{i}，涉及 SQLite 与 知识图谱。",
                agent_name="audit_agent", category="architecture", level="L3",
            )
        conn.commit()
        conn.close()

        # 迁移后若列名写错，这里会抛 sqlite3.OperationalError
        result = entity_extraction_step()
        assert isinstance(result, dict), f"expected dict, got {type(result)}"
        assert "error" not in result, f"entity_extraction failed: {result.get('error')}"
        assert result.get("scanned", 0) >= 1, f"expected to scan >=1 memory, got {result}"

        # 游标必须能持久化并读回（证明列名与真实 schema 一致）
        again = entity_extraction_step()
        assert "error" not in again, f"second run failed: {again.get('error')}"
    finally:
        cleanup_temp_db(db_path, patcher)


def test_fed_deliver_calls_capture_correctly():
    """fed_deliver 不得因 capture() 缺位置参数而抛 TypeError。"""
    from tests.test_helpers import init_temp_db, cleanup_temp_db
    from memall.mcp.federation_tools import fed_deliver
    from memall.core.db import get_conn

    db_path, patcher = init_temp_db()
    try:
        result = fed_deliver(
            target_agent="audit_target",
            content="来自 Hub 的推送事件内容，长度足够以通过写入质量门。",
            event_type="hub_push",
        )
        assert result.get("delivered") is True, f"deliver failed: {result}"
        assert result.get("memory_id"), f"no memory_id returned: {result}"

        # 且 metadata 必须真正落到记忆上（旧代码用 metadata_json 会被静默丢弃）
        conn = get_conn()
        row = conn.execute(
            "SELECT metadata FROM memories WHERE id = ?", (result["memory_id"],)
        ).fetchone()
        conn.close()
        assert row is not None
        meta = row["metadata"] or ""
        assert "hub_push" in meta, f"metadata not persisted (metadata_json bug): {meta[:120]}"
    finally:
        cleanup_temp_db(db_path, patcher)


def test_cross_agent_update_blocked_by_default():
    """跨 agent 篡改默认必须被拒绝（所有权门控默认开启）。"""
    from tests.test_helpers import init_temp_db, cleanup_temp_db, insert_memory
    from memall.mcp.tools.memory_write import handle_update
    from memall.core.db import get_conn
    from memall import config

    db_path, patcher = init_temp_db()
    try:
        conn = get_conn()
        insert_memory(conn, "受害者记忆的正文内容，长度足够以通过写入质量门。",
                      agent_name="victim", category="general", level="P2")
        conn.commit()
        vid = conn.execute(
            "SELECT id FROM memories WHERE agent_name='victim' ORDER BY id DESC LIMIT 1"
        ).fetchone()["id"]
        conn.close()

        # 默认配置 = 开启：必须拒绝
        out = handle_update({"memory_id": vid, "agent_name": "attacker",
                             "content": "被篡改后的内容，长度足够以通过写入质量门。"})
        assert "ownership violation" in out, f"cross-agent update was NOT blocked: {out}"

        # supervisor 白名单放行（监督型 agent 代管是合法场景）
        config.get_config()
        config._config.setdefault("security", {})["supervisor_agents"] = ["attacker"]
        try:
            out2 = handle_update({"memory_id": vid, "agent_name": "attacker",
                                  "content": "监督 agent 代管更新，长度足够以通过写入质量门。"})
            assert "ownership violation" not in out2, f"supervisor should pass: {out2}"
        finally:
            config.reset_config()

        # 显式关闭时保持旧行为（向后兼容）
        config.get_config()
        config._config.setdefault("security", {})["enforce_agent_ownership"] = False
        try:
            out3 = handle_update({"memory_id": vid, "agent_name": "attacker",
                                  "content": "显式关闭时允许更新，长度足够以通过写入质量门。"})
            assert "ownership violation" not in out3, f"disabled gate should allow: {out3}"
        finally:
            config.reset_config()
    finally:
        cleanup_temp_db(db_path, patcher)
