"""Lifecycle pipeline — scheduled background memory maturation.

Four phases run in sequence during the daily lifecycle task:

1. Embedding-based clustering of L4/L6 by (agent, category)
2. Cluster → L9 distillation (extractive summarization)
3. Mark source memories as ``memory_status='superseded'``
4. Mark stale memories as ``memory_status='dormant'``
"""

import hashlib
import json
import logging
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

# ── Public entry point ──────────────────────────────────────────────────────


def lifecycle_step() -> dict:
    """Run the full memory lifecycle pipeline.

    Returns a dict with counts for each phase::

        {"clusters": int, "distilled": int, "superseded": int,
         "dormant": int, "status": "ok"|"error"}
    """
    from memall.core.db import get_conn

    conn = get_conn()
    try:
        conn.execute("BEGIN")
        clusters = _cluster_by_embedding(conn)
        distilled = _distill_clusters(conn, clusters)
        superseded = _mark_superseded(conn, clusters)
        dormant = _mark_dormant(conn)
        conn.execute("COMMIT")
        return {
            "clusters": len(clusters),
            "distilled": distilled,
            "superseded": superseded,
            "dormant": dormant,
            "status": "ok",
        }
    except Exception as e:
        conn.execute("ROLLBACK")
        logger.error("lifecycle_step failed: %s", e, exc_info=True)
        return {"status": "error", "error": str(e)}
    finally:
        conn.close()


# ── Phase 1: Embedding-based clustering ─────────────────────────────────


def _cluster_by_embedding(conn) -> list[dict]:
    """Cluster L4/L6 memories by (agent, category) using cosine > 0.85 threshold.

    Returns list of cluster dicts::

        {"agent_name": str, "category": str, "member_ids": list[int],
         "size": int, "similarity": float}
    """
    from memall.graph.embeddings import _check_st_available

    if not _check_st_available():
        logger.info("lifecycle: sentence-transformers unavailable, skipping embedding clustering")
        return []

    rows = conn.execute(
        "SELECT id, content, agent_name, category FROM memories "
        "WHERE level IN ('L4','L6') AND LENGTH(TRIM(content)) > 20 "
        "AND memory_status IS NULL ORDER BY id"
    ).fetchall()

    if not rows:
        return []

    groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for r in rows:
        key = ((r["agent_name"] or "").lower(), r["category"] or "")
        groups[key].append({"id": r["id"], "content": r["content"]})

    from memall.graph.embeddings import _get_model
    import numpy as np

    model = _get_model()
    all_clusters: list[dict] = []

    for (agent, cat), members in groups.items():
        if len(members) < 4:
            continue

        texts = [m["content"][:768] for m in members]
        try:
            embeddings = model.encode(texts, normalize_embeddings=True)
        except Exception:
            logger.warning("lifecycle: encode failed for %s/%s, skipping", agent, cat, exc_info=True)
            continue

        sim_matrix = np.dot(embeddings, embeddings.T)

        connected_components = _connected_components(sim_matrix, threshold=0.85)

        for comp in connected_components:
            cluster_members = [members[i] for i in comp]
            if len(cluster_members) < 2:
                continue

            ids = [m["id"] for m in cluster_members]
            # Average pairwise similarity within the cluster
            pairs = [(i, j) for i in comp for j in comp if i < j]
            avg_sim = float(np.mean([sim_matrix[i][j] for i, j in pairs])) if pairs else 0.0

            all_clusters.append({
                "agent_name": agent,
                "category": cat,
                "member_ids": ids,
                "size": len(cluster_members),
                "similarity": round(avg_sim, 4),
            })

    return all_clusters


def _connected_components(sim_matrix, threshold: float = 0.85) -> list[list[int]]:
    """Find connected components in a similarity matrix above threshold."""
    import numpy as np

    n = len(sim_matrix)
    visited = [False] * n
    components: list[list[int]] = []

    for i in range(n):
        if visited[i]:
            continue
        comp = [i]
        visited[i] = True
        # BFS
        queue = [i]
        while queue:
            cur = queue.pop(0)
            for j in range(n):
                if not visited[j] and sim_matrix[cur][j] > threshold:
                    comp.append(j)
                    visited[j] = True
                    queue.append(j)
        components.append(comp)

    return components


# ── Phase 2: Cluster → L9 distillation ──────────────────────────────────


def _distill_clusters(conn, clusters: list[dict]) -> int:
    """Generate L9 memories from each cluster.

    Returns count of L9 memories created.
    """
    count = 0
    for cl in clusters:
        if not cl["member_ids"]:
            continue

        placeholders = ",".join("?" * len(cl["member_ids"]))
        rows = conn.execute(
            f"SELECT id, content, subject, agent_name, category FROM memories WHERE id IN ({placeholders})",
            cl["member_ids"],
        ).fetchall()
        if not rows:
            continue

        merged = _build_l9_content(rows)
        content_hash = hashlib.sha256(merged.encode()).hexdigest()

        # Dedup by content hash
        existing = conn.execute(
            "SELECT id FROM memories WHERE content_hash = ?", (content_hash,)
        ).fetchone()
        if existing:
            continue

        agent = rows[0]["agent_name"] or "?"
        category = rows[0]["category"] or "?"
        now = datetime.now(timezone.utc).isoformat()

        conn.execute(
            "INSERT INTO memories "
            "(subject, content, content_hash, level, category, agent_name, "
            "metadata, occurred_at, created_at, updated_at, memory_status) "
            "VALUES (?, ?, ?, 'L9', ?, ?, ?, ?, ?, ?, NULL)",
            (
                f"Lifecycle distill: {category} ({len(rows)} items)",
                merged,
                content_hash,
                category or "",
                agent,
                json.dumps({"layer_source": {"value": "lifecycle_auto", "version": 1}}),
                now, now, now,
            ),
        )
        l9_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

        # Create refines edges and update supersedes on sources
        for r in rows:
            conn.execute(
                "INSERT OR IGNORE INTO edges (source_id, target_id, relation_type, weight, created_at, metadata) "
                "VALUES (?, ?, 'refines', 1.0, ?, '{}')",
                (l9_id, r["id"], now),
            )
            # Append to supersedes JSON in source memory metadata
            existing_meta = conn.execute(
                "SELECT metadata FROM memories WHERE id = ?", (r["id"],)
            ).fetchone()
            meta = json.loads(existing_meta["metadata"]) if existing_meta and existing_meta["metadata"] else {}
            sup = meta.get("supersedes", [])
            if l9_id not in sup:
                sup.append(l9_id)
                meta["supersedes"] = sup
                conn.execute(
                    "UPDATE memories SET metadata = ? WHERE id = ?",
                    (json.dumps(meta, ensure_ascii=False), r["id"]),
                )

        count += 1

    return count


def _build_l9_content(rows: list) -> str:
    """Build merged L9 content string from cluster member rows.

    Mirrors the format used by ``distill_step()`` in ``memall.pipeline.distill``
    so that agents recognise the output.
    """
    agent = rows[0]["agent_name"] or "?"
    category = rows[0]["category"] or "?"
    contents = [r["content"] for r in rows]

    # Word frequency keywords
    words: Counter = Counter()
    for c in contents:
        tokens = re.findall(r"[\w一-鿿]{2,}", c[:200])
        words.update(tokens)
    top_keywords = ", ".join(w for w, _ in words.most_common(5))

    # Themes from unique subjects
    subjects = list(dict.fromkeys(r["subject"] for r in rows if r["subject"]))
    themes = "; ".join(subjects[:3]) if subjects else category

    # Key sentences: first meaningful sentence per member, dedup by first 40 chars, max 3
    key_sentences: list[str] = []
    seen: set[str] = set()
    for c in contents:
        first = c.strip()[:200].split("\n")[0][:200]
        dedup_key = first[:40]
        if dedup_key not in seen and len(first) > 10:
            key_sentences.append(first)
            seen.add(dedup_key)
            if len(key_sentences) >= 3:
                break

    return (
        f"[L9 蒸馏] {agent} 在 {category} 领域共 {len(contents)} 条\n"
        f"关键词：{top_keywords}\n"
        f"主题：{themes}\n"
        f"要点：{' | '.join(key_sentences)}"
    )


# ── Phase 3: Mark superseded ────────────────────────────────────────────


def _mark_superseded(conn, clusters: list[dict]) -> int:
    """Set ``memory_status='superseded'`` on L4/L6 memories in clusters.

    Only marks memories that have no existing ``memory_status`` set.
    Returns count of memories updated.
    """
    all_ids: list[int] = []
    for cl in clusters:
        all_ids.extend(cl["member_ids"])

    if not all_ids:
        return 0

    placeholders = ",".join("?" * len(all_ids))
    conn.execute(
        f"UPDATE memories SET memory_status = 'superseded' "
        f"WHERE id IN ({placeholders}) AND level IN ('L4','L6') AND memory_status IS NULL",
        all_ids,
    )
    return conn.execute("SELECT changes()").fetchone()[0]


# ── Phase 4: Mark dormant ───────────────────────────────────────────────


def _mark_dormant(conn, threshold: float = 0.2) -> int:
    """Set ``memory_status='dormant'`` on stale, low-confidence memories.

    Targets memories that:
    - Are not permanent (skip L1, L7, L9+)
    - Have confidence below threshold
    - Have never been accessed
    - Have no existing memory_status

    Returns count of memories marked.
    """
    conn.execute(
        "UPDATE memories SET memory_status = 'dormant' "
        "WHERE level NOT IN ('L1','L7','L9','L10','L11') "
        "AND confidence < ? AND access_count = 0 AND memory_status IS NULL",
        (threshold,),
    )
    return conn.execute("SELECT changes()").fetchone()[0]