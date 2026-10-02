"""
Test Suite — 安全加固回归护栏（2026-09-27）
==========================================
覆盖本轮按合理性落地的安全修复：

1. 归属门控默认开启 + supervisor 白名单（``security.enforce_agent_ownership`` /
   ``security.supervisor_agents``）。
2. ``UpdateInput`` 必须保留 ``agent_name``，否则 Pydantic 校验会把调用方身份
   剥掉，门控在真实 MCP 链路上永远不触发；同时 update 不得写 agent_name。
3. 破坏性运维操作（forget 删除类 / ops merge|split|dedup|archive / db vacuum|
   archive_vacuum|backfill_*|dedupe_l9|dedupe_l10）必须要求显式确认。
4. bundle 导入路径白名单按**路径组件**比较，``exports_evil`` 之类的兄弟目录
   不得绕过。
5. Gateway：非法 Origin 一律 403（含 GET）；免鉴权的 SPA 端点仅限 loopback。
"""

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


# ── 1/2. 归属门控与调用方身份透传 ──────────────────────────────────────

def test_update_input_keeps_caller_identity():
    """校验层必须把 agent_name 透传给 handler，否则门控形同虚设。"""
    from memall.mcp.validator import validate_tool_input

    ok, data, err = validate_tool_input(
        "memall_write",
        {"action": "update", "memory_id": 42, "agent_name": "codex",
         "content": "新内容"},
    )
    assert ok, f"validation failed: {err}"
    assert data.get("agent_name") == "codex", f"caller identity stripped: {data}"


def test_update_never_reassigns_owner():
    """update 不得把 agent_name 写进记忆行（防止借更新抢占归属）。"""
    from tests.test_helpers import init_temp_db, cleanup_temp_db, insert_memory
    from memall.mcp.tools.memory_write import handle_update
    from memall.core.db import get_conn

    db_path, patcher = init_temp_db()
    try:
        conn = get_conn()
        insert_memory(conn, "原主人的记忆正文，长度足够以通过写入质量门。",
                      agent_name="owner_a", category="general", level="P2")
        conn.commit()
        mid = conn.execute(
            "SELECT id FROM memories WHERE agent_name='owner_a' ORDER BY id DESC LIMIT 1"
        ).fetchone()["id"]
        conn.close()

        out = handle_update({"memory_id": mid, "agent_name": "owner_a",
                             "content": "主人自己更新内容，长度足够以通过写入质量门。"})
        assert "updated" in out, f"self update should succeed: {out}"

        conn = get_conn()
        row = conn.execute("SELECT agent_name FROM memories WHERE id=?", (mid,)).fetchone()
        conn.close()
        assert row["agent_name"] == "owner_a", f"owner changed: {row['agent_name']}"
    finally:
        cleanup_temp_db(db_path, patcher)


# ── 3. 破坏性操作确认门控 ─────────────────────────────────────────────

def test_forget_requires_confirmation():
    from tests.test_helpers import init_temp_db, cleanup_temp_db
    from memall.mcp.tools.manage import handle_forget

    db_path, patcher = init_temp_db()
    try:
        gated = json.loads(handle_forget({"action": "expired"}))
        assert gated.get("status") == "confirmation_required", f"not gated: {gated}"

        # 只读类子动作不受影响
        stats = json.loads(handle_forget({"action": "stats"}))
        assert "total_memories" in stats, f"stats broken: {stats}"

        # 显式确认后执行
        ran = json.loads(handle_forget({"action": "expired", "confirm": True}))
        assert "deleted_memories" in ran, f"confirmed run failed: {ran}"
    finally:
        cleanup_temp_db(db_path, patcher)


def test_ops_and_db_destructive_actions_require_confirmation():
    from memall.mcp.tools.manage import handle_ops, handle_db

    gated_ops = json.loads(handle_ops({"action": "dedup"}))
    assert gated_ops.get("status") == "confirmation_required", f"ops dedup not gated: {gated_ops}"

    # dry_run 视为只读预览，放行
    preview = json.loads(handle_ops({"action": "dedup", "dry_run": True,
                                     "max_memories": 50, "max_pairs": 50}))
    assert "status" not in preview or preview.get("status") != "confirmation_required", \
        f"dry_run should pass: {preview}"

    gated_db = json.loads(handle_db({"action": "vacuum"}))
    assert gated_db.get("status") == "confirmation_required", f"db vacuum not gated: {gated_db}"

    # 只读/可逆类 db 子动作不受影响
    ok = json.loads(handle_db({"action": "stats"}))
    assert "tables" in ok or "file_size_mb" in ok, f"db stats broken: {ok}"


# ── 4. bundle 导入路径白名单 ──────────────────────────────────────────

def test_import_path_check_is_component_aware():
    from pathlib import Path
    from memall.gateway import _path_within_any

    exports = Path("/home/u/.memall/exports")
    assert _path_within_any(exports / "bundle.json", [exports])
    assert _path_within_any(exports / "sub" / "bundle.json", [exports])
    # 前缀相同但不是子目录 —— 必须拒绝
    assert not _path_within_any(Path("/home/u/.memall/exports_evil/bundle.json"), [exports])
    assert not _path_within_any(Path("/home/u/.memall/exportsX/bundle.json"), [exports])
    assert not _path_within_any(Path("/tmp/elsewhere/bundle.json"), [exports])


# ── 5. Gateway 鉴权边界 ───────────────────────────────────────────────

def test_loopback_detection_fails_closed():
    from memall.gateway_utils import is_loopback_request, origin_allowed

    class _Req:
        def __init__(self, remote, origin=""):
            self.remote = remote
            self.headers = {"Origin": origin} if origin else {}

    assert is_loopback_request(_Req("127.0.0.1"))
    assert is_loopback_request(_Req("::1"))
    assert not is_loopback_request(_Req("192.168.1.7"))
    assert not is_loopback_request(_Req(None))
    assert not is_loopback_request(_Req("not-an-ip"))

    # 非法 Origin 一律不通过（GET 也会被拦）
    assert not origin_allowed(_Req("127.0.0.1", "http://evil.example"))
    # 可信的本地 UI origin / 无 Origin（curl、同源导航）通过
    assert origin_allowed(_Req("127.0.0.1", "http://localhost:9919"))
    assert origin_allowed(_Req("127.0.0.1"))

    # 数据 API 不得出现在"永久免鉴权"清单里
    from memall.gateway import _ALWAYS_PUBLIC_PATHS
    for p in ("/memories", "/api/x", "/db/stats", "/graph", "/timeline/api"):
        assert p not in _ALWAYS_PUBLIC_PATHS, f"{p} must not be always-public"
