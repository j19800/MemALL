"""Vector search using BAAI/bge-small-zh-v1.5 embeddings + sqlite-vec KNN.

vec0 virtual table stores float[512] vectors and supports
KNN queries via ``WHERE embedding MATCH ? ORDER BY distance LIMIT ?``.

Graceful fallback: if the SentenceTransformer model isn't available,
falls back to keyword-only mode.
"""

import logging

import numpy as np

from memall.core.db import pool_conn
from memall.core.nlp import tfidf_svd_embed
from memall.graph.embeddings import _load_embeddings_matrix, EMBED_DIM

logger = logging.getLogger(__name__)

# Level-based score multiplier for search ranking
# P0-P2: working memories → full weight
# L1-L3: identity/metadata → moderate
# L4-L5: summaries/decisions → moderate
# L6-L10: reflections/distillations → lower (let P0-P2 surface)
_LEVEL_BOOST = {
    "P0": 1.0, "P1": 1.0, "P2": 1.0,
    "L1": 0.7, "L2": 0.7, "L3": 0.7,
    "L4": 0.6, "L5": 0.6,
    "L6": 0.4, "L7": 0.4, "L8": 0.4, "L9": 0.3, "L10": 0.3, "L11": 0.3,
}
_DEFAULT_BOOST = 0.4


def _apply_level_boost(conn, raw_results: list) -> list[dict]:
    """Apply level-based score weighting to raw keyword results.

    P0-P2 get priority, L6/L9/L10 are deboosted so working memories
    surface before distilled content in default searches.
    """
    results = []
    for r in raw_results:
        mid = r[0]
        row = conn.execute("SELECT level FROM memories WHERE id = ?", (mid,)).fetchone()
        boost = _LEVEL_BOOST.get(row["level"] if row else "", _DEFAULT_BOOST)
        score = round(boost, 4)
        results.append({
            "memory_id": mid,
            "content": r[1][:200],
            "category": r[2],
            "score": score,
            "source": "keyword",
        })
    results.sort(key=lambda x: -x["score"])
    return results

_EMBED_MODEL = None


def _get_embed_model():
    """Lazy-load the embedding model (cached after first call)."""
    global _EMBED_MODEL
    if _EMBED_MODEL is None:
        try:
            from sentence_transformers import SentenceTransformer
            _EMBED_MODEL = SentenceTransformer("BAAI/bge-small-zh-v1.5", device="cpu")
            logger.info("Embedding model loaded for search")
        except Exception as e:
            logger.warning("Embedding model unavailable: %s", e)
            return None
    return _EMBED_MODEL


def _query_embed(query: str) -> np.ndarray | None:
    """Encode query using bge-small-zh model for consistent vector space."""
    model = _get_embed_model()
    if model is None:
        return None
    try:
        vec = model.encode(query[:EMBED_DIM], normalize_embeddings=True)
        return np.array(vec, dtype=np.float32)
    except Exception:
        return None


def _vec0_knn(conn, query_vec: np.ndarray, top_k: int) -> list[dict]:
    """KNN search via vec0 virtual table."""
    vec_bytes = np.array(query_vec, dtype=np.float32).tobytes()
    try:
        rows = conn.execute(
            "SELECT rowid, distance FROM mem_vec WHERE embedding MATCH ? ORDER BY distance LIMIT ?",
            (vec_bytes, top_k),
        ).fetchall()
        results = []
        for r in rows:
            score = 1.0 / (1.0 + r["distance"])
            results.append({
                "memory_id": r["rowid"],
                "distance": float(r["distance"]),
                "score": round(score, 4),
            })
        return results
    except Exception:
        return []


def _keyword_search(conn, query: str, top_k: int):
    like = f"%{query}%"
    rows = conn.execute(
        "SELECT id, content, category FROM memories WHERE content LIKE ? ORDER BY occurred_at DESC LIMIT ?",
        (like, top_k),
    ).fetchall()
    return [(r["id"], r["content"], r["category"], 0.0) for r in rows]


def _get_one_hop(conn, mem_ids: list, limit: int = 5000):
    if not mem_ids:
        return set()
    placeholders = ",".join("?" * len(mem_ids))
    edges = conn.execute(
        f"SELECT source_id, target_id FROM edges WHERE source_id IN ({placeholders}) OR target_id IN ({placeholders}) LIMIT ?",
        tuple(mem_ids * 2 + [limit]),
    ).fetchall()
    neighbors = set()
    for e in edges:
        neighbors.add(e["source_id"])
        neighbors.add(e["target_id"])
    return neighbors


def retrieve(query: str, mode: str = "hybrid", top_k: int = 10) -> dict:
    with pool_conn() as conn:
        if mode == "keyword":
            raw = _keyword_search(conn, query, top_k * 3)  # more candidates for level reordering
            return {"query": query, "mode": "keyword", "results": _apply_level_boost(conn, raw)[:top_k], "total": len(raw)}

        # Encode query using embedding model
        query_vec = _query_embed(query)
        if query_vec is None:
            # Model unavailable — fall back to keyword
            return retrieve(query, mode="keyword", top_k=top_k)

        # vec0 KNN
        vec0_results = _vec0_knn(conn, query_vec, top_k * 3 if mode == "hybrid" else top_k)

        if not vec0_results:
            # vec0 unavailable or empty
            return retrieve(query, mode="keyword", top_k=top_k)

        candidates = []
        for vr in vec0_results:
            row = conn.execute(
                "SELECT id, content, category, level FROM memories WHERE id = ?",
                (vr["memory_id"],),
            ).fetchone()
            if row:
                boost = _LEVEL_BOOST.get(row["level"] if row else "", _DEFAULT_BOOST)
                score = vr["score"] * boost
                candidates.append({
                    "memory_id": vr["memory_id"],
                    "content": row["content"][:200],
                    "category": row["category"],
                    "score": round(score, 4),
                    "source": "vector",
                })

        if mode == "vector":
            return {"query": query, "mode": "vector", "results": candidates[:top_k], "total": len(candidates)}

        # Hybrid: vector + graph expansion
        vector_ids = [c["memory_id"] for c in candidates]
        neighbors = _get_one_hop(conn, vector_ids)
        seen_ids = set(vector_ids)
        for nid in neighbors:
            if nid not in seen_ids:
                row = conn.execute("SELECT id, content FROM memories WHERE id = ?", (nid,)).fetchone()
                if row:
                    edge_count = conn.execute(
                        "SELECT COUNT(*) FROM edges WHERE (source_id = ? AND target_id IN ({})) OR (target_id = ? AND source_id IN ({}))".format(
                            ",".join("?" * len(vector_ids)), ",".join("?" * len(vector_ids)),
                        ),
                        tuple([nid] + vector_ids + [nid] + vector_ids),
                    ).fetchone()[0]
                    score = 0.1 * min(1.0, edge_count / 3.0)
                    candidates.append({
                        "memory_id": nid,
                        "content": row["content"][:200],
                        "score": round(score, 4),
                        "source": "graph_expansion",
                    })
                    seen_ids.add(nid)

        sorted_candidates = sorted(candidates, key=lambda x: -x["score"])[:top_k]
        return {
            "query": query, "mode": "hybrid",
            "results": sorted_candidates,
            "total": len(candidates),
            "vector_hits": len(vector_ids),
            "graph_expansions": len(neighbors - set(vector_ids)),
        }
