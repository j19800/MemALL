"""
Auto-Capture — 自动记忆捕获管道

自动从 MCP 工具调用、对话、代码变更中捕获关键信息，
无需手动调用 capture()。

工作流程:
1. 监听 MCP 工具调用 (hooks)
2. 自动分析内容意图
3. 智能分配 level/category/subject
4. 存入记忆系统
"""

import logging
import json
from datetime import datetime, timezone
from typing import Optional

from memall.core.lifecycle import (
    HookRegistry, HookDef,
    HOOK_POST_TOOL_USE, HOOK_POST_CAPTURE,
)

logger = logging.getLogger(__name__)

# ── 自动捕获 —— 注册 hook ──────────────────────────────

def _auto_capture_hook(tool_name: str, arguments: dict, result: str = "", **kwargs) -> None:
    """自动捕获: 从 MCP 工具调用中提取关键信息并存入记忆。"""
    from memall.intelligent_capture import intelligent_capture

    # 只处理 write 类工具的输出
    if tool_name not in ("memall_write", "memall_read"):
        return

    # 提取关键信息
    content = arguments.get("content", "")
    action = arguments.get("action", "")

    if not content and action in ("capture", "smart_store", "quick"):
        return  # 已经是 capture 调用，不重复捕获

    if not content:
        return

    # 自动捕获结果
    agent = arguments.get("agent_name", "system")

    # 跳过质量门控较低的内容
    if len(content.strip()) < 20:
        return

    try:
        mem_id = intelligent_capture(content, agent_name=agent)
        if mem_id:
            logger.debug("auto_capture: captured #%d from %s (%s)", mem_id, tool_name, action)
    except Exception as e:
        logger.debug("auto_capture: skipped (%s)", e)


# ── 注册自动捕获 hook ─────────────────────────────────

HookRegistry.register(HookDef(
    hook_point=HOOK_POST_TOOL_USE,
    matcher="memall_*",
    handler=_auto_capture_hook,
    description="Auto-capture memory from MCP tool calls",
))


# ── 批量导入工具 ────────────────────────────────────────

def import_from_mem0(jsonl_path: str, agent_name: str = "imported") -> dict:
    """从 Mem0 导出的 JSONL 文件导入记忆。

    Mem0 导出格式: {"role": "user"/"assistant", "content": "...", ...}
    """
    import json
    from pathlib import Path
    from memall.intelligent_capture import intelligent_capture

    path = Path(jsonl_path)
    if not path.exists():
        return {"error": f"File not found: {jsonl_path}"}

    imported = 0
    skipped = 0
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
                content = data.get("content", data.get("text", ""))
                if not content or len(content) < 20:
                    skipped += 1
                    continue
                intelligent_capture(content, agent_name=agent_name)
                imported += 1
            except (json.JSONDecodeError, Exception):
                skipped += 1

    return {"imported": imported, "skipped": skipped, "source": "mem0"}


def import_from_jsonl(jsonl_path: str, agent_name: str = "imported") -> dict:
    """从标准 JSONL 文件导入记忆 (每行: {"content": "...", "level": "...", ...})。

    支持可选字段: level, category, subject, project, agent_name
    """
    import json
    from pathlib import Path
    from memall.core.thin_waist import capture, MemoryInput
    from memall.intelligent_capture import intelligent_capture, detect_intent, _INTENT_LEVEL, _INTENT_CATEGORY

    path = Path(jsonl_path)
    if not path.exists():
        return {"error": f"File not found: {jsonl_path}"}

    imported = 0
    skipped = 0
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
                content = data.get("content", data.get("text", ""))
                if not content or len(content) < 10:
                    skipped += 1
                    continue

                # 使用提供的字段或自动检测
                level = data.get("level", "")
                category = data.get("category", "")
                subject = data.get("subject", "")
                agent = data.get("agent_name", agent_name)

                if level or category or subject:
                    capture(MemoryInput(
                        content=content,
                        agent_name=agent,
                        level=level or "P2",
                        category=category or "general",
                        subject=subject or content[:60],
                        metadata=data.get("metadata", "{}"),
                    ))
                else:
                    intelligent_capture(content, agent_name=agent)

                imported += 1
            except (json.JSONDecodeError, Exception) as e:
                skipped += 1

    return {"imported": imported, "skipped": skipped, "source": "jsonl"}


def import_from_csv(csv_path: str, agent_name: str = "imported") -> dict:
    """从 CSV 文件导入记忆 (列: content, level, category, subject, project)。"""
    import csv
    from pathlib import Path
    from memall.core.thin_waist import capture, MemoryInput
    from memall.intelligent_capture import intelligent_capture

    path = Path(csv_path)
    if not path.exists():
        return {"error": f"File not found: {csv_path}"}

    imported = 0
    skipped = 0
    with open(path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            content = row.get("content", row.get("text", ""))
            if not content or len(content) < 10:
                skipped += 1
                continue

            level = row.get("level", "")
            category = row.get("category", "")
            subject = row.get("subject", "")
            agent = row.get("agent_name", agent_name)

            if level or category or subject:
                capture(MemoryInput(
                    content=content,
                    agent_name=agent,
                    level=level or "P2",
                    category=category or "general",
                    subject=subject or content[:60],
                ))
            else:
                intelligent_capture(content, agent_name=agent)

            imported += 1

    return {"imported": imported, "skipped": skipped, "source": "csv"}