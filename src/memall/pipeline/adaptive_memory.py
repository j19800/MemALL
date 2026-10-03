"""
Adaptive Memory — 自适应记忆参数

基于使用模式自动调整:
- TTL: 高频访问延长, 低频访问缩短
- 重要性: 被引用的记忆自动提升权重
- 分类阈值: 根据历史准确率调整
"""

import logging
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from typing import Any

from memall.core.db import pool_conn

logger = logging.getLogger(__name__)


def adaptive_ttl_step() -> dict:
    """自适应 TTL 调整。

    规则:
    - 最近 7 天被访问 > 5 次的记忆: TTL 延长 2 倍
    - 被其他记忆引用的记忆: TTL 延长 1.5 倍
    - 30 天未访问且未被引用的 P2 记忆: 标记为 dormant
    """
    with pool_conn() as conn:
        now = datetime.now(timezone.utc)

        # 1. 高频访问 -> 延长 TTL
        high_access = conn.execute(
            "SELECT id, level FROM memories WHERE access_count > 5 "
            "AND updated_at > ? AND level NOT IN ('L1','L7','L9','L10','L11')",
            ((now - timedelta(days=7)).isoformat(),),
        ).fetchall()

        # 标记高频记忆 (在 metadata 中设置 access_boost)
        boosted = 0
        for r in high_access:
            conn.execute(
                "UPDATE memories SET weight = weight + 1 WHERE id = ? AND weight < 10",
                (r["id"],),
            )
            boosted += 1

        # 2. 被引用 -> 延长 TTL
        referenced = conn.execute(
            "SELECT DISTINCT e.target_id as id, m.level FROM edges e "
            "JOIN memories m ON e.target_id = m.id "
            "WHERE m.level NOT IN ('L1','L7','L9','L10','L11')"
        ).fetchall()

        for r in referenced:
            conn.execute(
                "UPDATE memories SET weight = weight + 1 WHERE id = ? AND weight < 10",
                (r["id"],),
            )

        # 3. 长期未访问 -> 降级为 dormant
        #    canonical enum 只有 P0/P1/P2 + L1..L11，P2 已是最低优先级，没有可降的
        #    level；因此“降级”用 memory_status='dormant' 表达，与
        #    auto_dream._schedule_forgetting / lifecycle._mark_dormant 一致。
        #    （旧代码写 level='P3'/'P4'，会被 memories.level 的 CHECK 拒绝并抛
        #    IntegrityError —— review F-01。）
        degraded = 0
        stale = conn.execute(
            "SELECT id FROM memories WHERE access_count = 0 "
            "AND created_at < ? AND level = 'P2' AND memory_status IS NULL",
            ((now - timedelta(days=30)).isoformat(),),
        ).fetchall()

        for r in stale:
            conn.execute(
                "UPDATE memories SET memory_status = 'dormant', updated_at = ? "
                "WHERE id = ?",
                (now.isoformat(), r["id"]),
            )
            degraded += 1

        conn.commit()

    return {
        "boosted": boosted,
        "referenced": len(referenced),
        "degraded": degraded,
        "status": "ok",
    }


def adaptive_classify_step() -> dict:
    """自适应分类优化。

    分析历史分类结果，调整分类器参数。
    """
    with pool_conn() as conn:
        # 统计各 level 的置信度分布
        quality = conn.execute(
            "SELECT level, COUNT(*) as cnt, "
            "ROUND(AVG(confidence), 2) as avg_conf, "
            "ROUND(MIN(confidence), 2) as min_conf "
            "FROM memories WHERE confidence > 0 GROUP BY level"
        ).fetchall()

    return {
        "levels_analyzed": len(quality),
        "quality": [dict(r) for r in quality],
        "status": "ok",
    }