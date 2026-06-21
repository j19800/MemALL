"""Vector search using sqlite-vec (vec0) KNN instead of in-memory numpy.

vec0 is a virtual table that stores float[256] vectors and supports
KNN queries via ``WHERE embedding MATCH ? ORDER BY distance LIMIT ?``.

Fallback to the old numpy matrix load when vec0 is unavailable.
"""

import struct

import numpy as np

from memall.core.db import pool_conn
from memall.core.nlp import tfidf_svd_embed
from memall.graph.embeddings import _load_embeddings_matrix, EMBED_DIM
from memall.graph.vector_model import load_model


def _query_embed(query: str) -> np.ndarray | None:
    """Encode query using saved vectorizer model for consistent vector space."""
    model = load_model()
    if model is None:
        return None
    vec = model["vectorizer"]
    svd = model["svd"]
    X = vec.transform([query[:1000]])
    try:
        qv = svd.transform(X)[0]
        if len(qv) < EMBED_DIM:
            padded = np.zeros(EMBED_DIM, dtype=np.float32)
            padded[:len(qv)] = qv
            return padded
        return qv
    except Exception:
        return np.zeros(EMBED_DIM)


def _vec0_knn(conn, query_vec: np.ndarray, top_k: int) -> list[dict]:
    """KNN search via vec0 virtual table.

    Returns list of {memory_id, distance, score}.
    Score is inverted distance: 1 / (1 + distance).
    """
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
        return []  # vec0 unavailable, caller falls back to numpy


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


def _numpy_vector_search(
    conn, query_vec: np.ndarray, top_k: int,
) -> tuple[list[int], list[str], np.ndarray, list[int]]:
    """Fallback: load ALL embeddings into numpy and compute cosine similarity."""
    vecs, mem_ids, contents = _load_embeddings_matrix(conn)
    if len(vecs) == 0:
        return [], [], np.array([]), []
    norms = np.linalg.norm(vecs, axis=1)
    if norms.sum() == 0:
        sims = np.zeros(vecs.shape[0])
    else:
        sims = (vecs @ query_vec) / (norms * np.linalg.norm(query_vec) + 1e-10)
    return mem_ids, contents, sims, list(range(len(vecs)))


def retrieve(query: str, mode: str = "hybrid", top_k: int = 10) -> dict:
    with pool_conn() as conn:
        if mode == "keyword":
            raw = _keyword_search(conn, query, top_k)
            results = [{"memory_id": r[0], "content": r[1][:200], "category": r[2], "score": round(float(r[3]), 4), "source": "keyword"} for r in raw]
            return {"query": query, "mode": "keyword", "results": results[:top_k], "total": len(results)}

        # Encode query
        query_vec = _query_embed(query)
        if query_vec is None:
            all_texts = [query[:1000], ""]
            embeddings = tfidf_svd_embed(all_texts, dims=EMBED_DIM)
            query_vec = embeddings[0] if embeddings is not None else np.zeros(EMBED_DIM)

        # Try vec0 KNN first
        vec0_results = _vec0_knn(conn, query_vec, top_k * 3 if mode == "hybrid" else top_k)

        if vec0_results:
            # vec0 path — results have memory_id + score
            candidates = []
            for vr in vec0_results:
                row = conn.execute(
                    "SELECT id, content, category FROM memories WHERE id = ?",
                    (vr["memory_id"],),
                ).fetchone()
                if row:
                    candidates.append({
                        "memory_id": vr["memory_id"],
                        "content": row["content"][:200],
                        "category": row["category"],
                        "score": vr["score"],
                        "source": "vector",
                    })

            if mode == "vector":
                return {"query": query, "mode": "vector", "results": candidates[:top_k], "total": len(candidates)}

            # Hybrid: vec0 + graph expansion
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

        # Fallback: numpy path (vec0 unavailable)
        mem_ids, contents, sims, all_indices = _numpy_vector_search(conn, query_vec, top_k)
        if len(mem_ids) == 0:
            return {"query": query, "mode": mode, "error": "no embeddings, run memall index build first", "results": []}

        if mode == "vector":
            top_indices = np.argsort(-sims)[:top_k]
            results = []
            for idx in top_indices:
                if sims[idx] <= 0:
                    continue
                results.append({
                    "memory_id": mem_ids[idx],
                    "content": contents[idx][:200],
                    "score": round(float(sims[idx]), 4),
                    "source": "vector",
                })
            return {"query": query, "mode": "vector", "results": results, "total": len(results)}

        # Hybrid (numpy fallback)
        hybrid_top_k = top_k * 3
        top_indices = np.argsort(-sims)[:hybrid_top_k]
        candidates = {}
        for idx in top_indices:
            if sims[idx] <= 0:
                continue
            mid = mem_ids[idx]
            candidates[mid] = {"memory_id": mid, "content": contents[idx][:200], "score": float(sims[idx]), "source": "vector"}

        vector_ids = list(candidates.keys())
        neighbors = _get_one_hop(conn, vector_ids)
        for nid in neighbors:
            if nid not in candidates:
                row = conn.execute("SELECT id, content FROM memories WHERE id = ?", (nid,)).fetchone()
                if row:
                    candidates[nid] = {"memory_id": nid, "content": row["content"][:200], "score": 0.1, "source": "graph_expansion"}

        expanded_scores = {}
        for cid, info in candidates.items():
            score = info["score"]
            if info["source"] == "graph_expansion":
                edge_count = conn.execute(
                    "SELECT COUNT(*) FROM edges WHERE (source_id = ? AND target_id IN ({})) OR (target_id = ? AND source_id IN ({}))".format(
                        ",".join("?" * len(vector_ids)), ",".join("?" * len(vector_ids)),
                    ),
                    tuple([cid] + vector_ids + [cid] + vector_ids),
                ).fetchone()[0]
                score = 0.1 * min(1.0, edge_count / 3.0)
            expanded_scores[cid] = score

        sorted_candidates = sorted(expanded_scores.items(), key=lambda x: -x[1])[:top_k]
        results = [candidates[cid] for cid, _ in sorted_candidates]
        return {
            "query": query, "mode": "hybrid",
            "results": results, "total": len(candidates),
            "vector_hits": len(vector_ids),
            "graph_expansions": len(neighbors - set(vector_ids)),
        }


def provider_search(query: str, top_k: int = 10, provider_name: str = "faiss") -> dict:
    """Run vector search via a registered search provider (FAISS, etc.).

    This is the Phase 2 entry point for FAISS-based search.
    Falls back to the built-in TF-IDF+SVD retriever if the provider
    is unavailable or returns an error.
    """
    from memall.search import get_provider
    p = get_provider(provider_name)
    if p is not None:
        result = p.search(query, top_k=top_k)
        if result.get("error") is None:
            return result
    return retrieve(query, mode="vector", top_k=top_k)
