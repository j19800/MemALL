"""
LLM Memory Enhancement — 用 LLM 增强记忆系统

能力:
1. 自动摘要: 将多条记忆压缩为 L9 摘要
2. 关联发现: 发现记忆之间的隐含关联
3. 冲突解决: 自动解决矛盾记忆
4. 知识提取: 从非结构化文本中提取结构化知识
"""

import json
import logging
import re
from collections import Counter
from datetime import datetime, timezone
from typing import Optional

from memall.core.db import pool_conn, get_conn, content_hash
from memall.core.thin_waist import capture, MemoryInput

logger = logging.getLogger(__name__)


# ── 文本摘要（无需 LLM，使用提取式摘要）─

def summarize_memories(memory_ids: list[int]) -> Optional[str]:
    """对一组记忆生成提取式摘要。

    使用关键词频率 + 关键句提取，不需要 LLM。
    """
    conn = get_conn()
    try:
        placeholders = ",".join("?" * len(memory_ids))
        rows = conn.execute(
            f"SELECT id, content, subject, level, category FROM memories WHERE id IN ({placeholders})",
            memory_ids,
        ).fetchall()

        if not rows:
            return None

        contents = [r["content"] for r in rows if r["content"]]
        subjects = list(dict.fromkeys(r["subject"] for r in rows if r["subject"]))
        categories = list(dict.fromkeys(r["category"] for r in rows if r["category"]))

        # 关键词频率
        words: Counter = Counter()
        for c in contents:
            tokens = re.findall(r"[a-zA-Z一-鿿][a-zA-Z一-鿿0-9]{1,20}", c[:300])
            words.update(t.lower() for t in tokens)
        stopwords = {"the", "and", "for", "are", "but", "not", "you", "all", "can",
                     "this", "that", "from", "with", "what", "which", "when",
                     "这些", "那些", "这个", "那个", "什么", "怎么", "如何", "可以"}
        top_keywords = [w for w, _ in words.most_common(8) if w not in stopwords][:5]

        # 关键句提取
        key_sentences = []
        seen = set()
        for c in contents:
            first = c.strip()[:200].split("\n")[0][:200]
            dedup_key = first[:30]
            if dedup_key not in seen and len(first) > 15:
                key_sentences.append(first)
                seen.add(dedup_key)
                if len(key_sentences) >= 3:
                    break

        summary = (
            f"摘要 ({len(rows)} 条记录)\n"
            f"主题: {'; '.join(subjects[:3])}\n"
            f"分类: {', '.join(categories[:3])}\n"
            f"关键词: {', '.join(top_keywords)}\n"
            f"要点:\n" + "\n".join(f"  • {s}" for s in key_sentences)
        )
        return summary

    finally:
        conn.close()


def auto_summarize_step() -> dict:
    """自动摘要管线步骤: 对未处理的 L4/L6 记忆生成 L9 摘要。"""
    with pool_conn() as conn:
        # 按 (agent, category) 分组
        rows = conn.execute(
            "SELECT id, content, agent_name, category, level FROM memories "
            "WHERE level IN ('L4','L6') AND LENGTH(TRIM(content)) > 20 "
            "ORDER BY agent_name, category"
        ).fetchall()

        groups = {}
        for r in rows:
            key = (r["agent_name"] or "system", r["category"] or "general")
            if key not in groups:
                groups[key] = []
            groups[key].append(r["id"])

        summarized = 0
        for (agent, category), ids in groups.items():
            if len(ids) < 3:
                continue  # 至少 3 条才做摘要

            summary = summarize_memories(ids[:10])
            if not summary:
                continue

            # 检查是否已存在相同摘要
            h = content_hash(summary)
            existing = conn.execute(
                "SELECT id FROM memories WHERE content_hash = ?", (h,)
            ).fetchone()
            if existing:
                continue

            # 存储为 L9
            now = datetime.now(timezone.utc).isoformat()
            conn.execute(
                "INSERT INTO memories (content, content_hash, level, agent_name, category, subject, occurred_at, created_at, updated_at) "
                "VALUES (?, ?, 'L9', ?, ?, ?, ?, ?, ?)",
                (summary, h, agent, category,
                 f"Auto-summary: {category} ({len(ids)} items)",
                 now, now, now),
            )
            l9_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

            # 创建 refines 边
            for mid in ids[:10]:
                conn.execute(
                    "INSERT OR IGNORE INTO edges (source_id, target_id, relation_type, weight, created_at, metadata) "
                    "VALUES (?, ?, 'refines', 1.0, ?, '{}')",
                    (l9_id, mid, now),
                )
            summarized += 1

        conn.commit()
        return {"summarized": summarized, "groups_found": len(groups), "status": "ok"}


# ── 关联发现 ──────────────────────────────────────────

def discover_associations(memory_id: int) -> list[dict]:
    """发现与指定记忆关联的其他记忆。

    基于: 同 Agent + 同分类 + 时间接近 + 实体重叠
    """
    with pool_conn() as conn:
        mem = conn.execute(
            "SELECT id, content, agent_name, category, level, created_at FROM memories WHERE id = ?",
            (memory_id,),
        ).fetchone()
        if not mem:
            return []

        associations = []

        # 1. 同 Agent + 同分类 + 时间接近
        related = conn.execute(
            "SELECT id, content, subject, level, created_at FROM memories "
            "WHERE LOWER(agent_name) = LOWER(?) AND category = ? AND id != ? "
            "AND ABS(julianday(created_at) - julianday(?)) < 7 "
            "ORDER BY created_at DESC LIMIT 5",
            (mem["agent_name"], mem["category"], memory_id, mem["created_at"]),
        ).fetchall()

        for r in related:
            associations.append({
                "memory_id": r["id"],
                "subject": r["subject"] or r["content"][:60],
                "level": r["level"],
                "created_at": r["created_at"],
                "reason": "same_agent_category",
            })

        # 2. 实体重叠
        entities = conn.execute(
            "SELECT entity_id FROM memory_entities WHERE memory_id = ?", (memory_id,)
        ).fetchall()
        if entities:
            eids = [r["entity_id"] for r in entities]
            placeholders = ",".join("?" * len(eids))
            entity_rel = conn.execute(
                f"SELECT DISTINCT m.id, m.subject, m.level, m.created_at FROM memories m "
                f"JOIN memory_entities me ON m.id = me.memory_id "
                f"WHERE me.entity_id IN ({placeholders}) AND m.id != ? "
                f"ORDER BY m.created_at DESC LIMIT 5",
                (*eids, memory_id),
            ).fetchall()

            for r in entity_rel:
                if not any(a["memory_id"] == r["id"] for a in associations):
                    associations.append({
                        "memory_id": r["id"],
                        "subject": r["subject"] or r["content"][:60],
                        "level": r["level"],
                        "created_at": r["created_at"],
                        "reason": "shared_entities",
                    })

        return associations


def discover_associations_step() -> dict:
    """管线步骤: 为新记忆发现关联。"""
    with pool_conn() as conn:
        rows = conn.execute(
            "SELECT id FROM memories ORDER BY id DESC LIMIT 100"
        ).fetchall()

        associations_found = 0
        for r in rows:
            assoc = discover_associations(r["id"])
            if assoc:
                associations_found += len(assoc)

        return {"associations_found": associations_found, "status": "ok"}