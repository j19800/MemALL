"""
Auto-Capture — 自动记忆捕获管道

自动从 MCP 工具调用、对话、代码变更中捕获关键信息，
无需手动调用 capture()。

工作流程:
1. 监听 MCP 工具调用 (post_tool_use hook)
2. 按 action 提取高价值内容 (decision / reflection / stance / discussion)
3. 智能分配 level/category/subject (intelligent_capture)
4. 存入记忆系统

提取策略
--------
不是"任何调用都存"——那会把检索日志淹没。只提取**带结论或判断**的
内容，且每个来源都有明确的去重键：

| 来源 | 提取字段 | 目标层级 |
|---|---|---|
| ask          | result.answer + question     | L4 决策推演 |
| reflect      | arguments.context            | L6 反思 |
| discussion   | arguments.arguments (立场)   | L4 讨论结论 |
| capture 家族  | 不捕获（已显式入库）          | — |
| 纯检索类      | 不捕获（无结论）              | — |
"""

import logging
import json

from memall.core.lifecycle import (
    HookRegistry, HookDef,
    HOOK_POST_TOOL_USE,
)

logger = logging.getLogger(__name__)

# ── 内容长度下限 ───────────────────────────────────────────
# 短于此的内容不构成可复用知识（intelligent_capture 另有 15 字门槛）。
_MIN_CONTENT_LEN = 20


def _as_text(value) -> str:
    """Coerce an argument value to trimmed text (lists/dicts → JSON)."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    try:
        return json.dumps(value, ensure_ascii=False).strip()
    except Exception:
        return str(value).strip()


def _extract_ask(arguments: dict, result: dict) -> tuple[str, dict]:
    """Digital-twin stance answer — a reasoned conclusion worth keeping."""
    question = _as_text(arguments.get("question"))
    answer = _as_text(result.get("answer"))
    if not question or not answer:
        return "", {}
    subject = _as_text(arguments.get("subject") or arguments.get("agent_name"))
    content = f"问：{question}\n答：{answer}"
    return content, {
        "subject": f"推演: {question[:40]}",
        "category": "decision",
        "level": "L4",
        "metadata": {"source": "ask", "subject_of": subject,
                     "mode": arguments.get("mode", "stance")},
    }


def _extract_reflect(arguments: dict, result: dict) -> tuple[str, dict]:
    """A stance taken on an existing L6 reflection — feedback on a lesson."""
    memory_id = arguments.get("memory_id")
    context = _as_text(arguments.get("context"))
    verb = _as_text(arguments.get("action") or arguments.get("reflect_sub_action"))
    if not memory_id or not context:
        return "", {}
    verb_cn = {"agree": "认同", "disagree": "反对", "probe": "存疑"}.get(verb, verb or "反馈")
    content = f"对反思 #{memory_id} {verb_cn}：{context}"
    return content, {
        "subject": f"反思反馈: #{memory_id}",
        "category": "reflection",
        "level": "L6",
        "metadata": {"source": "reflect", "target_memory_id": memory_id,
                     "verb": verb},
    }


def _extract_discussion(arguments: dict, result: dict) -> tuple[str, dict]:
    """A stance recorded in a multi-agent discussion."""
    stance = _as_text(arguments.get("stance"))
    reasoning = _as_text(arguments.get("arguments"))
    discussion_id = arguments.get("discussion_id")
    agent = _as_text(arguments.get("agent_name"))
    if not stance or not reasoning:
        return "", {}
    stance_cn = {"agree": "支持", "disagree": "反对", "abstain": "弃权",
                 "pass": "弃权", "confirm": "确认", "reject": "否决"}.get(
                     stance.lower(), stance)
    content = f"[{stance_cn}] {reasoning}"
    return content, {
        "subject": f"讨论立场: #{discussion_id}",
        "category": "discussion",
        "level": "L4",
        "metadata": {"source": "discussion", "discussion_id": discussion_id,
                     "stance": stance, "participant": agent},
    }


# action → extractor.  Anything not listed is deliberately ignored: pure
# reads (search/retrieve/timeline/...) carry no conclusion, and the capture
# family has already written its content explicitly.
_EXTRACTORS = {
    ("memall_persona", "ask"): _extract_ask,
    ("memall_system", "reflect"): _extract_reflect,
    ("memall_discussion", "respond"): _extract_discussion,
}


def _auto_capture_hook(tool_name: str, arguments: dict, result: str = "", **kwargs) -> None:
    """自动捕获: 从 MCP 工具调用中提取关键结论并存入记忆。"""
    if not tool_name or not arguments:
        return

    action = arguments.get("action", "")
    extractor = _EXTRACTORS.get((tool_name, action))
    if extractor is None:
        return

    # Only capture successful calls — an error payload has nothing to keep.
    try:
        parsed = json.loads(result) if result else {}
    except Exception:
        return
    if not isinstance(parsed, dict) or parsed.get("error"):
        return

    try:
        content, hints = extractor(arguments, parsed)
    except Exception:
        logger.debug("auto_capture: extractor failed for %s/%s",
                     tool_name, action, exc_info=True)
        return

    if len(content) < _MIN_CONTENT_LEN:
        return

    agent = _as_text(arguments.get("agent_name")) or "system"

    # ── 去重 ──
    # auto-capture 是"顺手存"，不是显式写入。同一结论反复出现（同一个问题
    # 问两遍、同一个立场重申一次）不应该堆出多条近似记忆，否则检索时同一
    # 结论会占满 top-k。用来源 + 目标 ID + 内容前缀做键，命中则跳过。
    if _already_captured(tool_name, action, content, hints):
        return

    try:
        from memall.intelligent_capture import intelligent_capture

        mem_id = intelligent_capture(
            content,
            agent_name=agent,
            subject=hints.get("subject", ""),
            category=hints.get("category", "general"),
            level=hints.get("level", "P2"),
            metadata=json.dumps(hints.get("metadata", {}), ensure_ascii=False),
            owner="",
        )
        if mem_id:
            logger.debug("auto_capture: captured #%d from %s/%s",
                         mem_id, tool_name, action)
    except Exception as e:
        logger.debug("auto_capture: skipped (%s)", e)


# ── 去重 ──────────────────────────────────────────────────
# 进程内缓存，键 = (来源 action, 目标 ID, 内容前 64 字)。capture() 本身有
# 精确 hash 去重，但 auto-capture 的内容每次都会带上时间戳或措辞差异，
# hash 去重拦不住，所以这里在写入前先用一个粗键挡掉明显的重复。
_recent_keys: dict[tuple, str] = {}
_RECENT_KEY_TTL = 3600  # seconds


def _already_captured(tool_name: str, action: str, content: str,
                      hints: dict) -> bool:
    from datetime import datetime, timezone

    meta = hints.get("metadata") or {}
    target = (meta.get("target_memory_id")
              or meta.get("discussion_id")
              or meta.get("subject_of")
              or "")
    key = (action, str(target), content[:64])
    now = datetime.now(timezone.utc).isoformat()

    # Expire stale keys so the dict cannot grow without bound.
    if len(_recent_keys) > 500:
        for k in [k for k, ts in _recent_keys.items()
                  if (datetime.now(timezone.utc)
                      - datetime.fromisoformat(ts)).total_seconds() > _RECENT_KEY_TTL]:
            _recent_keys.pop(k, None)

    if key in _recent_keys:
        return True
    _recent_keys[key] = now
    return False


# ── 注册自动捕获 hook ─────────────────────────────────────

HookRegistry.register(HookDef(
    hook_point=HOOK_POST_TOOL_USE,
    matcher="memall_*",
    handler=_auto_capture_hook,
    description="Auto-capture conclusions from ask/reflect/discussion tool calls",
))


# ── 批量导入工具 ────────────────────────────────────────

def import_from_mem0(jsonl_path: str, agent_name: str = "imported") -> dict:
    """从 Mem0 导出的 JSONL 文件导入记忆。

    Mem0 导出格式: {"role": "user"/"assistant", "content": "...", ...}
    """
    from pathlib import Path
    from memall.intelligent_capture import intelligent_capture

    path = Path(jsonl_path)
    if not path.is_file():
        return {"error": f"file not found: {jsonl_path}"}

    imported = 0
    errors = 0
    with open(path, "r", encoding="utf-8") as fh:
        for line_no, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
                content = rec.get("content") or rec.get("memory") or ""
                if not content or not str(content).strip():
                    continue
                intelligent_capture(str(content), agent_name=agent_name)
                imported += 1
            except Exception:
                errors += 1
                logger.warning("auto_capture: mem0 line %d import failed",
                               line_no, exc_info=True)

    return {"imported": imported, "errors": errors, "source": str(path)}


def import_from_jsonl(jsonl_path: str, agent_name: str = "imported") -> dict:
    """从 MemALL 自己的 JSONL 导出文件导入记忆。

    每行: {"content": "...", "level": "...", "category": "...", ...}
    """
    from pathlib import Path
    from memall.intelligent_capture import intelligent_capture

    path = Path(jsonl_path)
    if not path.is_file():
        return {"error": f"file not found: {jsonl_path}"}

    imported = 0
    errors = 0
    with open(path, "r", encoding="utf-8") as fh:
        for line_no, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
                content = rec.get("content") or ""
                if not content or not str(content).strip():
                    continue
                overrides = {k: rec[k] for k in
                             ("level", "category", "subject", "project", "owner")
                             if rec.get(k)}
                intelligent_capture(str(content), agent_name=agent_name, **overrides)
                imported += 1
            except Exception as e:
                errors += 1
                logger.warning("auto_capture: jsonl line %d import failed: %s",
                               line_no, e, exc_info=True)

    return {"imported": imported, "errors": errors, "source": str(path)}


def import_from_csv(csv_path: str, agent_name: str = "imported") -> dict:
    """从 CSV 导入记忆。需包含 content 列；可选 level/category/subject/project。"""
    import csv
    from pathlib import Path
    from memall.intelligent_capture import intelligent_capture

    path = Path(csv_path)
    if not path.is_file():
        return {"error": f"file not found: {csv_path}"}

    imported = 0
    errors = 0
    with open(path, "r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        if not reader.fieldnames or "content" not in reader.fieldnames:
            return {"error": "CSV must contain a 'content' column"}
        for row_no, row in enumerate(reader, 2):
            try:
                content = (row.get("content") or "").strip()
                if not content:
                    continue
                overrides = {k: (row.get(k) or "").strip() for k in
                             ("level", "category", "subject", "project", "owner")}
                intelligent_capture(content, agent_name=agent_name,
                                    **{k: v for k, v in overrides.items() if v})
                imported += 1
            except Exception as e:
                errors += 1
                logger.warning("auto_capture: csv row %d import failed: %s",
                               row_no, e, exc_info=True)

    return {"imported": imported, "errors": errors, "source": str(path)}
