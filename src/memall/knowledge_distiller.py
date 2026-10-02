"""
跨 Agent 学习 — 知识蒸馏 + 最佳实践自动提取。

一个 Agent 学到的高频教训/知识自动转化为全局知识，
其他 Agent 在相同场景下自动收到相关提醒。
"""

import json
import logging
from collections import Counter
from datetime import datetime, timezone, timedelta
from typing import Optional

from memall.core.db import pool_conn, get_conn, content_hash
from memall.core.thin_waist import capture, MemoryInput

logger = logging.getLogger(__name__)


class KnowledgeDistiller:
    """跨 Agent 知识蒸馏器。

    定期扫描各 Agent 的高频记忆，自动生成全局知识 (L10/L11)。
    """

    def __init__(self, min_occurrences: int = 3):
        self.min_occurrences = min_occurrences

    def distill(self) -> dict:
        """运行一次蒸馏 — 从各 Agent 提取公共知识。

        Returns:
            {global_lessons: int, global_knowledge: int, agent_count: int}
        """
        now = datetime.now(timezone.utc).isoformat()
        cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()

        with pool_conn() as conn:
            # 1. 收集各 Agent 的高频教训 (L6/L7)
            lessons = conn.execute(
                "SELECT content, agent_name, COUNT(*) as cnt, MAX(created_at) as last "
                "FROM memories "
                "WHERE level IN ('L6','L7') AND created_at >= ? "
                "GROUP BY content HAVING cnt >= ?",
                (cutoff, self.min_occurrences),
            ).fetchall()

            # 2. 收集各 Agent 的高频决策 (L4)
            decisions = conn.execute(
                "SELECT content, agent_name, COUNT(*) as cnt, MAX(created_at) as last "
                "FROM memories "
                "WHERE level = 'L4' AND created_at >= ? "
                "GROUP BY content HAVING cnt >= ?",
                (cutoff, self.min_occurrences),
            ).fetchall()

            # 3. 检查已存在的全局知识 (去重)
            existing = set()
            for row in conn.execute(
                "SELECT content_hash FROM memories WHERE level IN ('L10','L11')"
            ).fetchall():
                existing.add(row["content_hash"])

            # 4. 生成全局知识
            global_lessons = 0
            global_knowledge = 0
            agents_seen = set()

            for row in lessons:
                agents_seen.add(row["agent_name"])
                lesson_content = (
                    f"[L10 全局知识] {row['content']}\n"
                    f"(来自 {row['cnt']} 个 Agent 的 {row['cnt']} 次教训)"
                )
                # Dedup against the EXACT content that will be stored
                h = content_hash(lesson_content)
                if h in existing:
                    continue

                try:
                    capture(
                        lesson_content,
                        agent_name="system",
                        level="L10",
                        category="reflection",
                        subject=f"全局教训: {row['content'][:60]}",
                    )
                    global_lessons += 1
                    existing.add(h)
                except Exception as e:
                    logger.debug("distill lesson failed: %s", e)

            for row in decisions:
                agents_seen.add(row["agent_name"])
                decision_content = (
                    f"[L11 全局知识] {row['content']}\n"
                    f"(来自 {row['cnt']} 个 Agent 的决策共识)"
                )
                h = content_hash(decision_content)
                if h in existing:
                    continue

                try:
                    capture(
                        decision_content,
                        agent_name="system",
                        level="L11",
                        category="knowledge",
                        subject=f"全局知识: {row['content'][:60]}",
                    )
                    global_knowledge += 1
                    existing.add(h)
                except Exception as e:
                    logger.debug("distill knowledge failed: %s", e)

        logger.info(
            "KnowledgeDistiller: %d lessons, %d knowledge items from %d agents",
            global_lessons, global_knowledge, len(agents_seen),
        )
        return {
            "global_lessons": global_lessons,
            "global_knowledge": global_knowledge,
            "agent_count": len(agents_seen),
        }

    def get_agent_insights(self, agent_name: str) -> dict:
        """获取某个 Agent 的学习洞察。

        Returns:
            {topics: [...], patterns: [...], common_lessons: [...],
             knowledge_gaps: [...], top_entities: [...]}
        """
        with pool_conn() as conn:
            # 1. 高频主题
            topics = conn.execute(
                "SELECT category, COUNT(*) as cnt FROM memories "
                "WHERE LOWER(agent_name) = LOWER(?) AND category != '' "
                "GROUP BY category ORDER BY cnt DESC LIMIT 10",
                (agent_name,),
            ).fetchall()

            # 2. 高频教训关键词
            lessons = conn.execute(
                "SELECT content FROM memories "
                "WHERE LOWER(agent_name) = LOWER(?) AND level IN ('L6','L7') "
                "ORDER BY created_at DESC LIMIT 20",
                (agent_name,),
            ).fetchall()

            # 3. 已应用的全局知识
            applied = conn.execute(
                "SELECT id, subject FROM memories "
                "WHERE level IN ('L10','L11') AND subject LIKE ? "
                "ORDER BY created_at DESC LIMIT 10",
                (f"%{agent_name}%",),
            ).fetchall()

            # 4. 活跃度
            week_ago = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
            recent = conn.execute(
                "SELECT COUNT(*) FROM memories "
                "WHERE LOWER(agent_name) = LOWER(?) AND created_at >= ?",
                (agent_name, week_ago),
            ).fetchone()[0]

        return {
            "agent_name": agent_name,
            "top_categories": [{"name": r["category"], "count": r["cnt"]} for r in topics],
            "lesson_count": len(lessons),
            "applied_global_knowledge": len(applied),
            "recent_activity_7d": recent,
        }


def distill_step() -> dict:
    """管线步骤 — 运行知识蒸馏。

    供 pipeline 调用。
    """
    distiller = KnowledgeDistiller()
    return distiller.distill()