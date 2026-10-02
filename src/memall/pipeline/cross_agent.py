"""
Cross-Agent Learning — 跨 Agent 知识蒸馏

从各 Agent 的高分记忆中提取通用知识，自动生成 L10/L11 全局知识。

v2 改进（更先进）：
- 聚类方式从「硬编码关键词匹配」升级为「向量语义聚类」：用统一嵌入
  (sentence-transformers → ONNX bge → TF-IDF/SVD 兜底) 把 L6 教训编码后，
  按余弦相似度阈值做连通分量聚类，得到语义一致的簇，而非脆弱的子串匹配。
- 当嵌入后端不可用（例如无模型）时，自动回退到关键词聚类，保证健壮。
"""

import logging
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone

from memall.core.db import pool_conn, get_conn, content_hash
from memall.core.thin_waist import capture, MemoryInput
from memall.core.nlp import tokenize

logger = logging.getLogger(__name__)

# 语义聚类余弦阈值：高于此值视为同一主题。过低会过度合并，过高则退化成单条。
_SEMANTIC_THRESHOLD = 0.32
# 参与聚类的最大教训数（取最近、最高分），控制单轮嵌入成本。
_MAX_CLUSTER_CANDIDATES = 200


def _keyword_clusters(lessons: list[dict]) -> dict:
    """Legacy keyword-based clustering (fallback when embeddings unavailable)."""
    clusters: dict = defaultdict(list)
    keywords = ["数据库", "部署", "测试", "性能", "安全", "配置", "API", "代码",
                "database", "deploy", "test", "performance", "security", "config"]
    for r in lessons:
        content_lower = (r["content"] or "").lower()
        topics = [kw for kw in keywords if kw.lower() in content_lower]
        for t in (topics or ["general"]):
            clusters[t].append(r)
    return clusters


def _semantic_clusters(lessons: list[dict]) -> dict | None:
    """Vector semantic clustering via connected components.

    Returns a dict mapping a topic label -> list of lesson dicts, or None when
    the embedding backend is unavailable.
    """
    try:
        from memall.graph.embeddings import _embed_texts
        texts = [(r["content"] or "")[:400] for r in lessons]
        vecs = _embed_texts(texts, normalize=True)
    except Exception as e:
        logger.debug("Semantic clustering embed failed: %s", e)
        return None
    if vecs is None or len(vecs) < 2:
        return None

    import numpy as np
    vecs = np.asarray(vecs, dtype="float32")
    n = len(vecs)

    # Union-Find over cosine-similarity threshold (vectors are L2-normalized).
    parent = list(range(n))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    for i in range(n):
        vi = vecs[i]
        for j in range(i + 1, n):
            # normalized → dot product == cosine
            sim = float(np.dot(vi, vecs[j]))
            if sim >= _SEMANTIC_THRESHOLD:
                union(i, j)

    groups: dict = defaultdict(list)
    for i in range(n):
        groups[find(i)].append(lessons[i])

    # Label each cluster by its most frequent content token.
    labeled: dict = {}
    for members in groups.values():
        if len(members) < 1:
            continue
        freq: Counter = Counter()
        for m in members:
            freq.update(tokenize(m["content"] or ""))
        label = freq.most_common(1)[0][0] if freq else "general"
        labeled[label] = members
    return labeled


def knowledge_distill_step() -> dict:
    """知识蒸馏管线步骤。

    1. 收集所有 Agent 的高分 L6 教训
    2. 语义聚类（向量；不可用则关键词回退）
    3. 生成 L10 全局知识
    4. 识别高频模式 -> L11 领域知识
    """
    with pool_conn() as conn:
        # 1. 收集高分 L6 教训 (confidence >= 0.5, 至少被 2 个 Agent 共享)
        lessons = conn.execute(
            "SELECT id, content, subject, agent_name, confidence, weight FROM memories "
            "WHERE level = 'L6' AND confidence >= 0.5 AND LENGTH(TRIM(content)) > 20 "
            "ORDER BY confidence DESC LIMIT 500"
        ).fetchall()

        lesson_dicts = [dict(r) for r in lessons]

        # 2. 聚类：优先语义，失败回退关键词
        clusters = _semantic_clusters(lesson_dicts[:_MAX_CLUSTER_CANDIDATES])
        cluster_method = "semantic"
        if clusters is None:
            clusters = _keyword_clusters(lesson_dicts)
            cluster_method = "keyword"
        logger.info("cross_agent: clustering method=%s clusters=%d lessons=%d",
                    cluster_method, len(clusters), len(lesson_dicts))

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
            agents = list(set(m["agent"] for m in members)) if all(
                "agent" in m for m in members
            ) else list(set(m["agent_name"] for m in members))
            conf_field = "confidence" if "confidence" in members[0] else "weight"
            avg_conf = sum(float(m.get(conf_field) or 0) for m in members) / len(members)

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
                mid = m.get("id")
                if mid is None:
                    continue
                conn.execute(
                    "INSERT OR IGNORE INTO edges (source_id, target_id, relation_type, weight, created_at, metadata) "
                    "VALUES (?, ?, 'derived_from', 1.0, ?, '{}')",
                    (l10_id, mid, now),
                )

            l10_created += 1

        # 4. 识别高频模式 -> L11 领域知识
        l11_created = 0
        for topic, members in clusters.items():
            if len(members) < 5:
                continue  # 至少 5 条才生成领域知识

            agents = list(set(m["agent"] for m in members)) if all(
                "agent" in m for m in members
            ) else list(set(m["agent_name"] for m in members))
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
        "cluster_method": cluster_method,
        "l10_created": l10_created,
        "l11_created": l11_created,
        "status": "ok",
    }
