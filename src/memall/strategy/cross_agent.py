"""
智能跨 Agent 记忆 — 让 Agent 之间自动共享相关知识。

核心能力:
1. 上下文感知共享: 相同 project/任务的 Agent 自动共享相关记忆
2. 智能路由: 当 Agent A 需要某方面知识时，自动查找其他 Agent 的经验
3. 知识桥接: 自动发现不同 Agent 间的关联，构建跨 Agent 知识图
4. 隐私保护: 只共享高层次的总结/教训，不共享原始对话
"""

import logging
import json
from collections import defaultdict, Counter
from datetime import datetime, timezone, timedelta
from typing import Optional

from memall.core.db import pool_conn, get_conn, content_hash

logger = logging.getLogger(__name__)


def intelligent_retrieve(agent_name: str, query: str = "", top_k: int = 10) -> list:
    """智能检索 — 不仅查自己的记忆，还自动发现相关 Agent 的知识。

    流程:
    1. 查自己的记忆（标准检索）
    2. 解析 query 中的实体和主题
    3. 查其他 Agent 的公开 / 共享记忆中匹配的部分
    4. 按相关性融合排序
    """
    from memall.core.thin_waist import retrieve

    results = []

    # 1. 自己的记忆
    own = retrieve(query, viewer=agent_name, limit=top_k)
    if isinstance(own, list):
        for r in own:
            item = _mem_to_dict(r)
            item["_source"] = "own"
            results.append(item)

    # 2. 跨 Agent 知识（L6 教训 + L7 偏好 + L9/L10/L11 全局知识）
    if query:
        with pool_conn() as conn:
            kw = query[:50]
            shared = conn.execute(
                "SELECT id, content, subject, level, agent_name, created_at FROM memories "
                "WHERE level IN ('L6','L7','L9','L10','L11') "
                "AND (content LIKE ? OR subject LIKE ?) "
                "AND LOWER(agent_name) != LOWER(?) "
                "ORDER BY level DESC, created_at DESC LIMIT 10",
                (f"%{kw}%", f"%{kw}%", agent_name),
            ).fetchall()
            for r in shared:
                results.append({
                    "id": r["id"],
                    "content": r["content"],
                    "subject": r["subject"],
                    "level": r["level"],
                    "agent_name": r["agent_name"],
                    "created_at": r["created_at"],
                    "_source": "cross_agent_knowledge",
                })

    # 3. 同主题 Agent 的教训+偏好（L6/L7，按 project 和 category 匹配）
    with pool_conn() as conn:
        my_projects = conn.execute(
            "SELECT DISTINCT project FROM memories "
            "WHERE LOWER(agent_name) = LOWER(?) AND project != '' "
            "LIMIT 5",
            (agent_name,),
        ).fetchall()

        for p in my_projects:
            project = p["project"]
            peers = conn.execute(
                "SELECT id, content, subject, level, agent_name, created_at FROM memories "
                "WHERE level IN ('L6','L7') AND project = ? "
                "AND LOWER(agent_name) != LOWER(?) "
                "ORDER BY confidence DESC, created_at DESC LIMIT 5",
                (project, agent_name),
            ).fetchall()
            for r in peers:
                results.append({
                    "id": r["id"],
                    "content": r["content"],
                    "subject": r["subject"],
                    "level": r["level"],
                    "agent_name": r["agent_name"],
                    "created_at": r["created_at"],
                    "_source": f"peer_{project}",
                })

        # 4. 同 category 的教训+偏好（按主题匹配，不限于同 project）
        my_cats = conn.execute(
            "SELECT DISTINCT category FROM memories "
            "WHERE LOWER(agent_name) = LOWER(?) AND category != '' AND category != 'general' "
            "LIMIT 5",
            (agent_name,),
        ).fetchall()

        for c in my_cats:
            cat = c["category"]
            peers = conn.execute(
                "SELECT id, content, subject, level, agent_name, created_at FROM memories "
                "WHERE level IN ('L6','L7') AND category = ? "
                "AND LOWER(agent_name) != LOWER(?) "
                "ORDER BY confidence DESC, created_at DESC LIMIT 3",
                (cat, agent_name),
            ).fetchall()
            for r in peers:
                results.append({
                    "id": r["id"],
                    "content": r["content"],
                    "subject": r["subject"],
                    "level": r["level"],
                    "agent_name": r["agent_name"],
                    "created_at": r["created_at"],
                    "_source": f"peer_{cat}",
                })

    # 去重
    seen = set()
    deduped = []
    for r in results:
        mid = r.get("id")
        if mid and mid not in seen:
            seen.add(mid)
            deduped.append(r)

    return deduped[:top_k]


def _mem_to_dict(m):
    """Convert Memory object to dict."""
    if hasattr(m, '__dict__'):
        return m.__dict__
    if isinstance(m, dict):
        return m
    return {"id": m, "content": str(m)}


def cross_agent_bridge_step() -> dict:
    """跨 Agent 知识桥接管线步骤。

    自动发现不同 Agent 间的关联，创建知识桥接边。
    """
    bridges_created = 0
    with pool_conn() as conn:
        # 1. 查找共享相同 project 的 Agent 对
        agent_pairs = conn.execute(
            "SELECT DISTINCT a.agent_name as a1, b.agent_name as a2, a.project "
            "FROM (SELECT DISTINCT agent_name, project FROM memories WHERE project != '') a "
            "JOIN (SELECT DISTINCT agent_name, project FROM memories WHERE project != '') b "
            "ON a.project = b.project AND LOWER(a.agent_name) < LOWER(b.agent_name)"
        ).fetchall()

        for pair in agent_pairs:
            a1, a2, project = pair["a1"], pair["a2"], pair["project"]

            # 检查是否已存在桥接边
            existing = conn.execute(
                "SELECT id FROM edges WHERE source_id IN (SELECT id FROM memories WHERE agent_name = ? LIMIT 1) "
                "AND relation_type = 'bridges_to' LIMIT 1",
                (a1,),
            ).fetchone()

            if existing:
                continue

            # 创建桥接 L9 知识
            now = datetime.now(timezone.utc).isoformat()
            content = (
                f"[L9 桥接] {a1} ↔ {a2}\n"
                f"共同项目: {project}\n"
                f"自动发现: 两个 Agent 在 {project} 项目中有平行工作。\n"
                f"建议: 参考对方在此项目中的经验和教训。"
            )
            h = content_hash(content)
            conn.execute(
                "INSERT OR IGNORE INTO memories (content, content_hash, level, agent_name, category, subject, occurred_at, created_at, updated_at) "
                "VALUES (?, ?, 'L9', 'system', 'bridge', ?, ?, ?, ?)",
                (content, h, f"Bridge: {a1} ↔ {a2} ({project})", now, now, now),
            )
            bridges_created += 1

        conn.commit()

    return {"bridges_created": bridges_created, "status": "ok"}


def recommend_peers(agent_name: str, top_k: int = 5) -> list[dict]:
    """推荐与指定 Agent 最相关的其他 Agent。

    基于: 共同 project、共同 category、实体重叠。
    """
    with pool_conn() as conn:
        # 1. 找同 project 的 Agent
        peers = conn.execute(
            "SELECT DISTINCT b.agent_name, COUNT(*) as common_projects "
            "FROM (SELECT DISTINCT project FROM memories WHERE LOWER(agent_name) = LOWER(?) AND project != '') a "
            "JOIN (SELECT DISTINCT agent_name, project FROM memories WHERE project != '') b "
            "ON a.project = b.project AND LOWER(b.agent_name) != LOWER(?) "
            "GROUP BY b.agent_name ORDER BY common_projects DESC LIMIT ?",
            (agent_name, agent_name, top_k),
        ).fetchall()

        return [{
            "agent_name": r["agent_name"],
            "common_projects": r["common_projects"],
            "reason": "same_project",
        } for r in peers]