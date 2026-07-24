"""
智能检索 — 基于意图自动选择最佳检索策略。

- 事实查询 ("FastAPI 是什么") → 精确匹配 + KG
- 决策查询 ("为什么选 FastAPI") → 语义检索 + 时间线
- 时间查询 ("今天做了什么") → 时间线检索
- 更新查询 ("上次改了啥") → 最近更新
- 模糊查询 ("关于 Python 的事情") → 混合检索
"""

import re
from datetime import datetime, timezone, timedelta
from typing import Optional

from memall.core.thin_waist import retrieve, hybrid_search, timeline
from memall.core.context_assembler import build_context
from memall.core.entity_extractor import extract_entities


# ── 查询意图检测 ──

_WHY_PATTERN = re.compile(r"(?:为什么|为何|怎么|how|why|原因|理由)", re.I)
_WHAT_PATTERN = re.compile(r"(?:什么是|是什么|what is|what are|定义|概念)", re.I)
_WHEN_PATTERN = re.compile(r"(?:今天|昨天|最近|最近几天|最近一周|最近一个月|what did|what happened|今天.*了)", re.I)
_UPDATE_PATTERN = re.compile(r"(?:上次|最后|最近.*更新|最新|latest|last|recent|更新了|改了)", re.I)
_DECISION_PATTERN = re.compile(r"(?:决定|选择|采用|决策|decide|chose|方案|选型)", re.I)
_LESSON_PATTERN = re.compile(r"(?:教训|坑|问题|错误|失败|bug|lesson|learned)", re.I)


def detect_search_intent(query: str) -> str:
    """检测搜索意图类型。

    Returns:
        intent: fact | decision | lesson | time | update | general
    """
    if _WHY_PATTERN.search(query):
        return "decision"
    if _WHAT_PATTERN.search(query):
        return "fact"
    if _WHEN_PATTERN.search(query):
        return "time"
    if _UPDATE_PATTERN.search(query):
        return "update"
    if _DECISION_PATTERN.search(query):
        return "decision"
    if _LESSON_PATTERN.search(query):
        return "lesson"
    return "general"


def _search_fact(query: str, top_k: int = 5) -> list:
    """事实查询: 精确匹配 + 实体检索 + KG。"""
    results = []
    # 1. 实体检索
    entities = extract_entities(query)
    if entities:
        from memall.core.db import pool_conn
        names = [e["name"] for e in entities]
        with pool_conn() as conn:
            placeholders = ",".join("?" * len(names))
            rows = conn.execute(
                f"SELECT DISTINCT m.id, m.content, m.subject, m.level, m.category, m.created_at "
                f"FROM memories m "
                f"JOIN memory_entities me ON m.id = me.memory_id "
                f"JOIN entities e ON me.entity_id = e.id "
                f"WHERE LOWER(e.name) IN ({','.join('?' for _ in names)}) "
                f"ORDER BY m.created_at DESC LIMIT ?",
                *(names + [top_k]),
            ).fetchall()
            results = [dict(r) for r in rows]

    # 2. 混合检索补充
    if len(results) < top_k:
        hybrid = _search_general(query, top_k)
        seen = {r["id"] for r in results}
        for r in hybrid:
            if r.get("id") not in seen:
                results.append(r)
                seen.add(r.get("id"))

    return results[:top_k]


def _search_decision(query: str, top_k: int = 5) -> list:
    """决策查询: 语义检索 + 时间线，优先 L4/L6。"""
    from memall.core.db import pool_conn
    with pool_conn() as conn:
        rows = conn.execute(
            "SELECT id, content, subject, level, category, created_at FROM memories "
            "WHERE level IN ('L4','L6') AND (content LIKE ? OR subject LIKE ?) "
            "ORDER BY created_at DESC LIMIT ?",
            (f"%{query}%", f"%{query}%", top_k),
        ).fetchall()
    return [dict(r) for r in rows]


def _search_time(query: str, top_k: int = 5) -> list:
    """时间查询: 最近记忆。"""
    days = 1
    if "最近几天" in query or "最近" in query:
        days = 7
    elif "最近一周" in query:
        days = 7
    elif "最近一个月" in query:
        days = 30

    from memall.core.thin_waist import timeline as tl
    items = tl(days=days)
    results = []
    for item in items:
        if hasattr(item, "id"):
            results.append({"id": item.id, "content": item.content,
                          "subject": getattr(item, "subject", ""),
                          "level": getattr(item, "level", ""),
                          "category": getattr(item, "category", ""),
                          "created_at": getattr(item, "created_at", "")})
    return results[:top_k]


def _search_update(query: str, top_k: int = 5) -> list:
    """更新查询: 最近修改的记忆。"""
    from memall.core.db import pool_conn
    with pool_conn() as conn:
        rows = conn.execute(
            "SELECT id, content, subject, level, category, created_at, updated_at FROM memories "
            "WHERE updated_at > created_at OR content LIKE ? "
            "ORDER BY updated_at DESC LIMIT ?",
            (f"%{query}%", top_k),
        ).fetchall()
    return [dict(r) for r in rows]


def _search_lesson(query: str, top_k: int = 5) -> list:
    """教训查询: 优先 L6 反思 + L7 教训。"""
    from memall.core.db import pool_conn
    with pool_conn() as conn:
        rows = conn.execute(
            "SELECT id, content, subject, level, category, created_at FROM memories "
            "WHERE level IN ('L6','L7') AND (content LIKE ? OR subject LIKE ?) "
            "ORDER BY created_at DESC LIMIT ?",
            (f"%{query}%", f"%{query}%", top_k),
        ).fetchall()
    return [dict(r) for r in rows]


def _search_general(query: str, top_k: int = 5) -> list:
    """通用查询: 混合检索 (FTS5 + vec0)。"""
    results = retrieve(query, limit=top_k)
    if not isinstance(results, list):
        return []
    return [dict(r) if hasattr(r, "keys") else r for r in results]


_INTENT_HANDLERS = {
    "fact": _search_fact,
    "decision": _search_decision,
    "lesson": _search_lesson,
    "time": _search_time,
    "update": _search_update,
    "general": _search_general,
}


def intelligent_search(query: str, top_k: int = 5) -> dict:
    """智能检索 — 自动选择最佳检索策略。

    Args:
        query: 搜索查询
        top_k: 最大结果数

    Returns:
        {results: [...], intent: str, strategy: str}
    """
    intent = detect_search_intent(query)
    handler = _INTENT_HANDLERS.get(intent, _search_general)
    results = handler(query, top_k)

    return {
        "results": results,
        "intent": intent,
        "strategy": handler.__name__,
        "total": len(results),
    }