import json
from datetime import datetime, timezone
from memall.core.db import get_conn
from memall.core.thin_waist import capture, retrieve
from memall.core.models import MemoryInput

STEPS = [
    {
        "id": 1,
        "title": "创建 Agent 身份",
        "prompt": "请输入你的 Agent 名称（如 SOLO、OpenCode、Cursor）：",
        "detail": "每个接入 MemALL 的 Agent 都有一个独立身份。名称将用于记忆归属、人格画像和联邦查询。",
        "tools": ["capture", "retrieve"],
    },
    {
        "id": 2,
        "title": "首次存储记忆",
        "prompt": "输入你想记住的第一条内容（如项目决策、架构设计、经验总结）：",
        "detail": "记忆会自动生成标题、分类、摘要。支持 5 级重要性（P0-P4）、项目和分类标签。",
        "tools": ["capture", "memall_write action=smart_store", "memall_write action=store_batch"],
    },
    {
        "id": 3,
        "title": "搜索试用",
        "prompt": "输入一个关键词试试搜索功能（支持全文搜索 + 语义向量搜索）：",
        "detail": "搜索支持关键词匹配、语义相似度（TF-IDF+SVD）、按项目/分类/Agent 过滤。",
        "tools": ["retrieve", "memall_read action=vector_search", "timeline"],
    },
    {
        "id": 4,
        "title": "系统状态",
        "prompt": "",
        "detail": "查看系统概览：记忆总数、Agent 数量、关系网络、联邦状态、安全评分。",
        "tools": ["memall_system action=db", "memall_system action=security", "memall_system action=adaptive"],
    },
    {
        "id": 5,
        "title": "完成 🎉",
        "prompt": "",
        "detail": "恭喜完成新手引导！系统已自动存储一条 Welcome 记忆供检索参考。以下是推荐的工作流：",
        "tools": [],
    },
]

# 工具推荐分组（按使用场景）
_TOOL_RECOMMENDATIONS = [
    {"group": "快速上手", "tools": ["capture → retrieve → connect → traverse → timeline"]},
    {"group": "智能存储", "tools": ["memall_write → smart_store → 去重存储", "memall_write → store_batch → 批量存储", "memall_write → update → 更新", "memall_read → vector_search → 语义搜索"]},
    {"group": "Agent 人格", "tools": ["memall_persona → persona → 认知画像", "memall_persona → persona_profile → 三层画像", "memall_persona → ask → 数字孪生问答"]},
    {"group": "会话追踪", "tools": ["memall_system → session_start → 开始会话", "memall_system → session_end → 结束并提取事实", "memall_read → session_summary → 会话摘要"]},
    {"group": "知识图谱", "tools": ["memall_read → graph → 图谱探索", "memall_federation → fed_query → 跨 Agent 查询", "memall_federation → fed_publish → 联邦发布"]},
    {"group": "系统运维", "tools": ["memall_write → forget → 自动遗忘", "memall_system → security → 安全审计", "memall_system → adaptive → 自适应管线", "memall_system → db → 数据库维护"]},
]


def _store_welcome_memory(user_id: str, agent_name: str) -> int:
    """存储一条 Welcome 记忆，含工具概览，帮助新用户快速了解系统能力。"""
    content = (
        f"[欢迎使用 MemALL]\n"
        f"Agent: {agent_name}\n"
        f"用户: {user_id}\n\n"
        f"MemALL 是一个多 Agent 协作记忆系统，当前版本支持 28 个 MCP 工具。\n"
        f"以下是根据使用场景推荐的工具路径：\n"
    )
    for rec in _TOOL_RECOMMENDATIONS:
        content += f"\n▸ {rec['group']}\n"
        for t in rec['tools']:
            content += f"  • {t}\n"
    content += (
        f"\n使用 capture 工具存储新记忆，retrieve 搜索已有记忆，connect 建立记忆关联。\n"
        f"更多信息请参考 MemALL_Agent_Integration_Guide.docx"
    )
    from memall.core.models import MemoryInput
    mid = capture(MemoryInput(
        content=content,
        owner=user_id,
        agent_name=agent_name,
        subject=f"[实现] {agent_name}: 欢迎使用 MemALL — 工具概览",
        category="implementation",
        project="onboarding",
    ))
    return mid


def _ensure_table(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS onboarding_status (
            user_id TEXT PRIMARY KEY DEFAULT 'default',
            current_step INTEGER DEFAULT 1,
            completed INTEGER DEFAULT 0,
            started_at TEXT,
            completed_at TEXT,
            agent_name TEXT DEFAULT '',
            collected_data TEXT DEFAULT '{}'
        )
    """)
    conn.commit()


def _get_status(user_id: str = "default") -> dict:
    conn = get_conn()
    try:
        _ensure_table(conn)
        row = conn.execute("SELECT * FROM onboarding_status WHERE user_id = ?", (user_id,)).fetchone()
        if not row:
            return {"user_id": user_id, "current_step": 1, "completed": False, "agent_name": "", "collected_data": {}}
        return {
            "user_id": row["user_id"],
            "current_step": row["current_step"],
            "completed": bool(row["completed"]),
            "started_at": row["started_at"],
            "completed_at": row["completed_at"],
            "agent_name": row["agent_name"] or "",
            "collected_data": json.loads(row["collected_data"]) if row["collected_data"] and row["collected_data"] != "{}" else {},
        }
    finally:
        conn.close()


def _save_step(user_id: str, step: int, agent_name: str = "", collected: dict = None):
    conn = get_conn()
    try:
        _ensure_table(conn)
        existing = conn.execute("SELECT * FROM onboarding_status WHERE user_id = ?", (user_id,)).fetchone()
        now = datetime.now(timezone.utc).isoformat()
        if existing:
            data = json.loads(existing["collected_data"]) if existing["collected_data"] else {}
            if collected:
                data.update(collected)
            conn.execute(
                "UPDATE onboarding_status SET current_step=?, agent_name=COALESCE(?, agent_name), collected_data=?, started_at=COALESCE(?, started_at) WHERE user_id=?",
                (step, agent_name or None, json.dumps(data, ensure_ascii=False), now if not existing["started_at"] else None, user_id),
            )
        else:
            conn.execute(
                "INSERT INTO onboarding_status (user_id, current_step, started_at, agent_name, collected_data) VALUES (?, ?, ?, ?, ?)",
                (user_id, step, now, agent_name or "", json.dumps(collected or {}, ensure_ascii=False)),
            )
        conn.commit()
    finally:
        conn.close()


def _complete(user_id: str):
    conn = get_conn()
    try:
        _ensure_table(conn)
        now = datetime.now(timezone.utc).isoformat()
        conn.execute("""
            INSERT INTO onboarding_status (user_id, current_step, completed, started_at, completed_at)
            VALUES (?, 5, 1, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                completed=1, current_step=5, completed_at=excluded.completed_at
        """, (user_id, now, now))

        # 自动存储 Welcome 记忆（仅在首次完成时）
        row = conn.execute(
            "SELECT agent_name, collected_data FROM onboarding_status WHERE user_id = ?",
            (user_id,)
        ).fetchone()
        if row:
            agent_name = row["agent_name"] or "default_agent"
            try:
                welcome_mid = _store_welcome_memory(user_id, agent_name)
                # 回写 welcome_memory_id
                data = json.loads(row["collected_data"]) if row["collected_data"] else {}
                data["welcome_memory_id"] = welcome_mid
                conn.execute(
                    "UPDATE onboarding_status SET collected_data=? WHERE user_id=?",
                    (json.dumps(data, ensure_ascii=False), user_id),
                )
            except Exception as e:
                # Welcome 记忆失败不应阻塞引导完成
                pass
        conn.commit()
    finally:
        conn.close()


def _reset(user_id: str):
    conn = get_conn()
    try:
        conn.execute("DELETE FROM onboarding_status WHERE user_id=?", (user_id,))
        conn.commit()
    finally:
        conn.close()


def _input(prompt: str) -> str:
    try:
        return input(prompt).strip()
    except (EOFError, KeyboardInterrupt):
        return ""


def start(user_id: str = "default", step: int = None) -> dict:
    _ensure_table(get_conn())
    status = _get_status(user_id)
    if status["completed"]:
        return {"status": "already_completed", "message": "新手引导已完成。使用 reset 重新开始。"}

    current = status["current_step"] if step is None else step
    collected = status.get("collected_data", {})

    print(f"\n=== MemALL 新手引导（步骤 {current}/5）===\n")

    for step_id in range(current, 6):
        step_info = STEPS[step_id - 1]
        print(f"\n--- 步骤 {step_id}: {step_info['title']} ---")

        if step_id == 1:
            agent_name = _input(step_info["prompt"])
            if not agent_name:
                agent_name = "default_agent"
            collected["agent_name"] = agent_name
            _save_step(user_id, 2, agent_name=agent_name, collected=collected)
            print(f"  ✓ Agent '{agent_name}' 已注册。\n")

        elif step_id == 2:
            prompt_text = step_info["prompt"]
            content = _input(prompt_text)
            if content:
                agent_name = collected.get("agent_name", "")
                mid = capture(MemoryInput(content=content, owner=user_id, agent_name=agent_name, category="general"))
                collected["first_memory_id"] = mid
                _save_step(user_id, 3, collected=collected)
                print(f"  ✓ 记忆已存储（ID: {mid}）。\n")
            else:
                _save_step(user_id, 3, collected=collected)
                print("  ✓ 跳过。\n")

        elif step_id == 3:
            keyword = _input(step_info["prompt"])
            if keyword:
                results = retrieve(keyword, limit=5)
                if isinstance(results, list) and results:
                    print(f"  找到 {len(results)} 条相关记忆：")
                    for r in results[:5]:
                        print(f"    - #{r.id}: {r.content[:120]}")
                else:
                    print("  未找到相关记忆。你可以稍后添加更多内容。")
                _save_step(user_id, 4)
            else:
                _save_step(user_id, 4)
            print()

        elif step_id == 4:
            from memall.cli.main import cmd_status
            print("  系统状态：")
            cmd_status(None)
            _save_step(user_id, 5)
            print()

        elif step_id == 5:
            _complete(user_id)
            print(f"  ✓ 新手引导完成！")
            agent_name = collected.get("agent_name", "")
            mem_id = collected.get("first_memory_id", "")
            welcome_id = collected.get("welcome_memory_id", "")
            print(f"\n  摘要：")
            print(f"    Agent: {agent_name or 'default_agent'}")
            print(f"    首条记忆: {'#' + str(mem_id) if mem_id else '无'}")
            print(f"    Welcome 记忆: {'#' + str(welcome_id) if welcome_id else '无'}")
            print(f"    完成时间: {datetime.now(timezone.utc).isoformat()}")
            print(f"\n  📋 推荐工具路径：")
            for rec in _TOOL_RECOMMENDATIONS:
                print(f"    ▸ {rec['group']}")
                for t in rec['tools']:
                    print(f"      {t}")
            print(f"\n  后续命令（CLI）：")
            print(f"    memall capture  ...  存储新记忆")
            print(f"    memall search   ...  搜索记忆")
            print(f"    memall pipeline ...  运行管线")
            print(f"    memall status   ...  查看系统状态")

    return {"status": "completed", "steps_completed": 5}


def status(user_id: str = "default") -> dict:
    return _get_status(user_id)


def reset(user_id: str = "default") -> dict:
    _reset(user_id)
    return {"status": "reset", "message": "引导状态已重置。"}


def complete(user_id: str = "default") -> dict:
    _complete(user_id)
    return {"status": "completed", "message": "引导已标记为完成。"}