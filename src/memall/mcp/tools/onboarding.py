import json
from memall.onboarding import (
    _get_status, _save_step, _complete, _reset,
)
from memall.core.thin_waist import retrieve, capture as _twaist_capture


def handle(arguments: dict) -> str:
    action = arguments["action"]
    user_id = arguments.get("user_id", "default")

    if action == "status":
        from memall.onboarding import STEPS
        result = _get_status(user_id)
        result["steps"] = STEPS
        result["total_steps"] = len(STEPS)
        return json.dumps(result, ensure_ascii=False, default=str)

    elif action == "start":
        status = _get_status(user_id)
        if status.get("completed"):
            return json.dumps({
                "status": "already_completed",
                "message": "新手引导已完成。使用 reset 重新开始。",
                "onboarding_status": status,
            }, ensure_ascii=False, default=str)

        current_step = status.get("current_step", 1)
        from memall.onboarding import STEPS
        step_info = STEPS[current_step - 1]

        return json.dumps({
            "status": "in_progress",
            "current_step": current_step,
            "total_steps": len(STEPS),
            "step_title": step_info["title"],
            "step_prompt": step_info["prompt"],
            "step_detail": step_info.get("detail", ""),
            "step_tools": step_info.get("tools", []),
            "message": f"请提交步骤 {current_step} 的输入数据，使用 action=submit_step",
            "next_action": {
                "tool": "memall_onboarding",
                "arguments": {"action": "submit_step", "user_id": user_id, "step": current_step, "input_data": "..."}
            }
        }, ensure_ascii=False, default=str)

    elif action == "submit_step":
        step = arguments.get("step", 1)
        input_data = arguments.get("input_data", {}) or {}

        if step == 1:
            agent_name = (input_data.get("agent_name") or "").strip() or "default_agent"
            _save_step(user_id, 2, agent_name=agent_name, collected={"agent_name": agent_name})
            from memall.onboarding import STEPS as _STEPS
            next_info = _STEPS[1]
            return json.dumps({
                "status": "step_completed",
                "step": 1,
                "step_title": "创建 Agent 身份",
                "agent_name": agent_name,
                "message": f"Agent '{agent_name}' 已注册。",
                "next_step": 2,
                "next_step_title": next_info["title"],
                "next_step_detail": next_info.get("detail", ""),
                "next_step_tools": next_info.get("tools", []),
                "next_step_hint": "请提交步骤 2 的输入（input_data: {content: '你想记住的第一条记忆'}）"
            }, ensure_ascii=False)

        elif step == 2:
            content = (input_data.get("content") or "").strip()
            if not content:
                return json.dumps({"error": "step=2 需要 input_data.content 字段"}, ensure_ascii=False)
            status = _get_status(user_id)
            agent_name = status.get("agent_name", "default_agent")
            from memall.core.models import MemoryInput as _MemInput
            mid = _twaist_capture(_MemInput(
                content=content, owner=user_id, agent_name=agent_name, category="general"
            ))
            _save_step(user_id, 3, collected={"first_memory_id": mid})
            from memall.onboarding import STEPS as _STEPS
            next_info = _STEPS[2]
            return json.dumps({
                "status": "step_completed",
                "step": 2,
                "step_title": "首次存储记忆",
                "memory_id": mid,
                "message": f"记忆已存储（ID: {mid}）",
                "next_step": 3,
                "next_step_title": next_info["title"],
                "next_step_detail": next_info.get("detail", ""),
                "next_step_tools": next_info.get("tools", []),
                "next_step_hint": "请提交步骤 3 的输入（input_data: {keyword: '你想搜索的关键词'}）"
            }, ensure_ascii=False)

        elif step == 3:
            keyword = (input_data.get("keyword") or "").strip()
            if not keyword:
                return json.dumps({"error": "step=3 需要 input_data.keyword 字段"}, ensure_ascii=False)
            results = retrieve(keyword, limit=5)
            if isinstance(results, list) and results:
                sample = [{
                    "id": r.id, "content": r.content[:150], "category": r.category, "level": r.level
                } for r in results[:5]]
            else:
                sample = []
            _save_step(user_id, 4)
            return json.dumps({
                "status": "step_completed",
                "step": 3,
                "step_title": "搜索试用",
                "keyword": keyword,
                "found_count": len(sample),
                "results": sample,
                "next_step": 4,
                "next_step_hint": "继续步骤 4（系统状态）"
            }, ensure_ascii=False, default=str)

        elif step == 4:
            from memall.core.db import db_stats
            try:
                stats = db_stats()
                db_summary = {
                    "db_path": stats.get("db_path", ""),
                    "total_memories": stats.get("tables", {}).get("memories", 0),
                    "total_edges": stats.get("tables", {}).get("edges", 0),
                }
            except Exception as e:
                db_summary = {"error": str(e)}
            _save_step(user_id, 5)
            return json.dumps({
                "status": "step_completed",
                "step": 4,
                "step_title": "系统状态",
                "db_summary": db_summary,
                "next_step": 5,
                "next_step_hint": "继续步骤 5（完成）"
            }, ensure_ascii=False, default=str)

        elif step == 5:
            _complete(user_id)
            final_status = _get_status(user_id)
            collected = final_status.get("collected_data", {})
            welcome_mid = collected.get("welcome_memory_id", "")
            from memall.onboarding import _TOOL_RECOMMENDATIONS as _TOOL_RECS
            return json.dumps({
                "status": "completed",
                "step": 5,
                "step_title": "完成",
                "message": "新手引导完成！你现在可以使用 MemALL 的全部 MCP 工具。",
                "agent_name": final_status.get("agent_name", ""),
                "completed_at": final_status.get("completed_at", ""),
                "welcome_memory_id": welcome_mid,
                "tool_groups": _TOOL_RECS,
                "next_steps": [
                    "capture — 存储新记忆",
                    "retrieve — 搜索已有记忆",
                    "connect — 建立记忆关联",
                    "memall_session_start — 开始会话追踪",
                    "memall_fed_publish — 跨 Agent 联邦发布",
                ]
            }, ensure_ascii=False, default=str)

        else:
            return json.dumps({"error": f"step 必须在 1-5 范围，当前: {step}"}, ensure_ascii=False)

    elif action == "reset":
        _reset(user_id)
        return json.dumps({
            "status": "reset",
            "message": "引导状态已重置。重新调用 action=start 开始。",
            "user_id": user_id,
        }, ensure_ascii=False)

    elif action == "skip":
        _complete(user_id)
        return json.dumps({
            "status": "skipped",
            "message": "已跳过新手引导。",
        }, ensure_ascii=False)

    else:
        return json.dumps({"error": f"unknown action: {action}"})
