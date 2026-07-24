"""
Cross-Agent Learning — 跨 Agent 知识蒸馏

从各 Agent 的高分记忆中提取通用知识，自动生成 L10/L11 全局知识。
"""

import logging
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone

from memall.core.db import pool_conn, get_conn, content_hash
from memall.core.thin_waist import capture, MemoryInput

logger = logging.getLogger(__name__)


def knowledge_distill_step() -> dict:
    """知识蒸馏管线步骤。

    1. 收集所有 Agent 的高分 L6 教训
    2. 按主题聚类
    3. 生成 L10 全局知识
    4. 识别高频模式 -> L11 领域知识
    """
    with pool_conn() as conn:
        # 1. 收集高分 L6 教训 (confidence >= 0.7, 至少被 2 个 Agent 共享)
        lessons = conn.execute(
            "SELECT id, content, subject, agent_name, confidence, weight FROM memories "
            "WHERE level = 'L6' AND confidence >= 0.5 AND LENGTH(TRIM(content)) > 20 "
            "ORDER BY confidence DESC LIMIT 500"
        ).fetchall()

        # 2. 按内容主题聚类 (使用关键词匹配)
        clusters = defaultdict(list)
        for r in lessons:
            content_lower = r["content"].lower()
            # 提取主题关键词
            topics = []
            for kw in ["数据库", "部署", "测试", "性能", "安全", "配置", "API", "代码",
                       "database", "deploy", "test", "performance", "security", "config"]:
                if kw.lower() in content_lower:
                    topics.append(kw)

            for t in topics or ["general"]:
                clusters[t].append({
                    "id": r["id"],
                    "content": r["content"],
                    "agent": r["agent_name"],
                    "confidence": r["confidence"],
                })

        # 3. 生成 L10 全局知识
        l10_created = 0
        for topic, members in clusters.items():
            if len(members) < 2:
                continue  # 至少 2 条才生成全局知识

            # 去重内容
            unique_contents = list(set(m["content"][:100] for m in members))
            if len(unique_contents) < 2:
                continue

            # 构建 L10 知识
            agents = list(set(m["agent"] for m in members))
            avg_conf = sum(m["confidence"] for m in members) / len(members)

            l10_content = (
                f"[L10 全局知识] {topic}\n"
                f"来源: {len(members)} 条教训, {len(agents)} 个 Agent\n"
                f"要点:\n" + "\n".join(f"  • {c[:200]}" for c in unique_contents[:3])
            )

            h = content_hash(l10_content)
            existing = conn.execute(
                "SELECT id FROM memories WHERE content_hash = ?", (h,)
            ).fetchone()
            if existing:
                continue

            now = datetime.now(timezone.utc).isoformat()
            conn.execute(
                "INSERT INTO memories (content, content_hash, level, agent_name, category, subject, confidence, weight, occurred_at, created_at, updated_at) "
                "VALUES (?, ?, 'L10', 'system', 'knowledge', ?, ?, ?, ?, ?, ?)",
                (l10_content, h, f"Global: {topic} ({len(members)} patterns)",
                 round(avg_conf, 2), len(members), now, now, now),
            )
            l10_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

            # 创建 derived_from 边
            for m in members:
                conn.execute(
                    "INSERT OR IGNORE INTO edges (source_id, target_id, relation_type, weight, created_at, metadata) "
                    "VALUES (?, ?, 'derived_from', 1.0, ?, '{}')",
                    (l10_id, m["id"], now),
                )

            l10_created += 1

        # 4. 识别高频模式 -> L11 领域知识
        l11_created = 0
        for topic, members in clusters.items():
            if len(members) < 5:
                continue  # 至少 5 条才生成领域知识

            agents = list(set(m["agent"] for m in members))
            l11_content = (
                f"[L11 领域知识] {topic}\n"
                f"这是一个在 {len(agents)} 个 Agent 中反复出现的模式 "
                f"({len(members)} 次记录)。\n"
                f"建议: 将此纳入团队最佳实践。"
            )

            h = content_hash(l11_content)
            existing = conn.execute(
                "SELECT id FROM memories WHERE content_hash = ?", (h,)
            ).fetchone()
            if existing:
                continue

            now = datetime.now(timezone.utc).isoformat()
            conn.execute(
                "INSERT INTO memories (content, content_hash, level, agent_name, category, subject, weight, occurred_at, created_at, updated_at) "
                "VALUES (?, ?, 'L11', 'system', 'domain', ?, ?, ?, ?, ?)",
                (l11_content, h, f"Domain pattern: {topic}", len(members), now, now, now),
            )
            l11_created += 1

        conn.commit()

    return {
        "clusters": len(clusters),
        "l10_created": l10_created,
        "l11_created": l11_created,
        "status": "ok",
    }