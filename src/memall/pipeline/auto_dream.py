"""
Auto-Dream — 自主记忆固结引擎

像人类睡眠一样，定期自动固结记忆。
4 阶段管线:
1. 碎片整理: 合并重复记忆，消除冗余
2. 模式提取: 识别跨 session 的重复模式
3. 知识蒸馏: 将低层记忆提升为高层知识
4. 遗忘调度: 基于重要性自动遗忘

灵感来自: openclaw-auto-dream (553★), HippocampAI (71★)
"""

import logging
import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone, timedelta
from typing import Optional

from memall.core.db import pool_conn, get_conn, content_hash
from memall.core.thin_waist import capture, MemoryInput

logger = logging.getLogger(__name__)


def dream_consolidation_step() -> dict:
    """自主记忆固结 — 完整的 4 阶段管线。

    建议每 24 小时运行一次。
    """
    results = {}
    with pool_conn() as conn:
        # Phase 1: 碎片整理
        p1 = _defrag(conn)
        results["defrag"] = p1
        conn.commit()

        # Phase 2: 模式提取
        p2 = _extract_patterns(conn)
        results["patterns"] = p2
        conn.commit()

        # Phase 3: 知识蒸馏
        p3 = _distill_knowledge(conn)
        results["distilled"] = p3
        conn.commit()

        # Phase 4: 遗忘调度
        p4 = _schedule_forgetting(conn)
        results["forgetting"] = p4
        conn.commit()

    results["status"] = "ok"
    logger.info("Dream consolidation: defrag=%d patterns=%d distilled=%d forgotten=%d",
                p1.get("merged", 0), p2.get("found", 0),
                p3.get("created", 0), p4.get("scheduled", 0))
    return results


def _defrag(conn) -> dict:
    """Phase 1: 碎片整理 — 合并重复记忆，消除冗余。

    检测: 同 Agent + 同分类 + 内容相似度 > 0.85
    """
    merged = 0
    deleted = set()
    rows = conn.execute(
        "SELECT id, content, agent_name, category, level FROM memories "
        "WHERE level IN ('P2','L4') AND LENGTH(TRIM(content)) > 20 "
        "ORDER BY agent_name, category, created_at"
    ).fetchall()

    groups = defaultdict(list)
    for r in rows:
        key = (r["agent_name"] or "", r["category"] or "")
        groups[key].append(r)

    for key, members in groups.items():
        if len(members) < 3:
            continue
        for i, a in enumerate(members):
            if a["id"] in deleted:
                continue
            for b in members[i + 1:]:
                if b["id"] in deleted:
                    continue
                sim = _jaccard_sim(a["content"], b["content"])
                if sim > 0.85:
                    if len(a["content"]) >= len(b["content"]):
                        remove_id = b["id"]
                    else:
                        remove_id = a["id"]
                    conn.execute("DELETE FROM memory_entities WHERE memory_id = ?", (remove_id,))
                    conn.execute("DELETE FROM edges WHERE source_id = ? OR target_id = ?",
                                 (remove_id, remove_id))
                    conn.execute("DELETE FROM memories WHERE id = ?", (remove_id,))
                    deleted.add(remove_id)
                    merged += 1
                    break

    return {"merged": merged, "scanned": len(rows)}


def _extract_patterns(conn) -> dict:
    """Phase 2: 模式提取 — 识别跨 session 的重复模式。"""
    found = 0
    now = datetime.now(timezone.utc).isoformat()

    # 查找高频关键词模式
    rows = conn.execute(
        "SELECT content, agent_name, category FROM memories "
        "WHERE level IN ('L6','L7') AND LENGTH(TRIM(content)) > 20 "
        "ORDER BY created_at DESC LIMIT 500"
    ).fetchall()

    # 按 Agent 分组提取关键词
    agent_patterns = defaultdict(list)
    for r in rows:
        words = re.findall(r"[a-zA-Z一-鿿][a-zA-Z一-鿿0-9]{2,15}", r["content"])
        agent_patterns[r["agent_name"] or "system"].extend(
            w.lower() for w in words if len(w) > 2
        )

    for agent, words in agent_patterns.items():
        freq = Counter(words)
        top = [w for w, c in freq.most_common(10) if c >= 3]
        if len(top) >= 3:
            pattern_content = f"[L9 模式] {agent} 的重复模式: {', '.join(top[:5])}"
            h = content_hash(pattern_content)
            exists = conn.execute("SELECT id FROM memories WHERE content_hash = ?", (h,)).fetchone()
            if not exists:
                conn.execute(
                    "INSERT INTO memories (content, content_hash, level, agent_name, category, subject, occurred_at, created_at, updated_at) "
                    "VALUES (?, ?, 'L9', ?, 'pattern', ?, ?, ?, ?)",
                    (pattern_content, h, agent, f"Pattern: {agent}", now, now, now),
                )
                found += 1

    return {"found": found, "agents": len(agent_patterns)}


def _distill_knowledge(conn) -> dict:
    """Phase 3: 知识蒸馏 — 将低层记忆提升为高层知识。"""
    created = 0
    now = datetime.now(timezone.utc).isoformat()

    # 收集 L6 教训，按主题聚类
    lessons = conn.execute(
        "SELECT id, content, agent_name FROM memories "
        "WHERE level = 'L6' AND LENGTH(TRIM(content)) > 20 "
        "ORDER BY confidence DESC LIMIT 200"
    ).fetchall()

    # 提取关键词聚类
    clusters = defaultdict(list)
    for r in lessons:
        topics = set()
        for kw in ["数据库", "部署", "测试", "性能", "安全", "配置", "API", "代码",
                   "database", "deploy", "test", "performance", "security"]:
            if kw.lower() in r["content"].lower():
                topics.add(kw)
        for t in topics or {"general"}:
            clusters[t].append(r)

    for topic, members in clusters.items():
        if len(members) < 3:
            continue
        agents = list(set(m["agent_name"] for m in members))
        content = (
            f"[L10 知识] {topic}\n"
            f"来自 {len(agents)} 个 Agent 的 {len(members)} 条教训\n"
            f"共识: 这是一个反复出现的问题领域\n"
            f"建议: 建立相关的最佳实践和检查清单"
        )
        h = content_hash(content)
        exists = conn.execute("SELECT id FROM memories WHERE content_hash = ?", (h,)).fetchone()
        if not exists:
            conn.execute(
                "INSERT INTO memories (content, content_hash, level, agent_name, category, subject, weight, occurred_at, created_at, updated_at) "
                "VALUES (?, ?, 'L10', 'system', 'knowledge', ?, ?, ?, ?, ?)",
                (content, h, f"Knowledge: {topic}", len(members), now, now, now),
            )
            created += 1

    return {"created": created, "clusters": len(clusters)}


def _schedule_forgetting(conn) -> dict:
    """Phase 4: 遗忘调度 — 基于重要性自动遗忘。"""
    scheduled = 0

    # 标记低价值 P2 记忆为遗忘候选
    conn.execute(
        "UPDATE memories SET memory_status = 'dormant' "
        "WHERE level = 'P2' AND confidence < 0.3 AND access_count = 0 "
        "AND memory_status IS NULL AND created_at < datetime('now', '-7 days')"
    )
    scheduled += conn.execute("SELECT changes()").fetchone()[0]

    # 标记孤立记忆（无边、无引用、低置信度）
    conn.execute(
        "UPDATE memories SET memory_status = 'dormant' "
        "WHERE id NOT IN (SELECT source_id FROM edges UNION SELECT target_id FROM edges) "
        "AND level IN ('P2','P3','P4') AND confidence < 0.4 "
        "AND memory_status IS NULL AND created_at < datetime('now', '-14 days')"
    )
    scheduled += conn.execute("SELECT changes()").fetchone()[0]

    return {"scheduled": scheduled}


def _jaccard_sim(a: str, b: str) -> float:
    """计算 Jaccard 相似度。"""
    set_a = set(re.findall(r"[a-zA-Z一-鿿]+", a.lower()))
    set_b = set(re.findall(r"[a-zA-Z一-鿿]+", b.lower()))
    if not set_a or not set_b:
        return 0.0
    intersection = set_a & set_b
    union = set_a | set_b
    return len(intersection) / len(union)