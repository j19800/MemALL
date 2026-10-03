"""Search, rerank, and timeline for the thin waist (F-09 extraction).

Extracted verbatim from :mod:`memall.core.thin_waist` to isolate the heaviest
optional dependencies (``onnxruntime`` / ``tokenizers`` / ``sentence-transformers``)
from the capture/retrieve/graph core.  The public entry points
(:func:`vector_search`, :func:`hybrid_search`, :func:`timeline`) are re-exported
by ``thin_waist`` so ``from memall.core.thin_waist import hybrid_search`` keeps
working unchanged.

The shared primitives (``_pool_conn``, ``fts_query``, ``_row_to_memory``,
``_filter_by_trust_dict``) are imported *inside* the functions that need them.
That is deliberate: ``thin_waist`` imports this module, so a module-level
``from memall.core.thin_waist import ...`` would be a circular import.
"""

import logging
import sqlite3
from datetime import datetime, timezone
from typing import Optional

from memall.core.lifecycle import (
    dispatch_lifecycle,
    HOOK_PRE_SEARCH,
    HOOK_POST_SEARCH,
)

logger = logging.getLogger(__name__)


# ── Cross-encoder reranker (Phase 2) ──

_reranker = None  # cached CrossEncoder instance
_reranker_model_name = None  # track which model is loaded


# ── ONNX cross-encoder reranker (local, SSE4.2, zero-dep) ──
_reranker_onnx = None  # cached onnxruntime.InferenceSession
_reranker_onnx_tok = None  # cached tokenizers.Tokenizer


def _load_reranker_onnx():
    """Lazy-load the local ONNX cross-encoder reranker (bge-reranker-base INT8).

    Runs on SSE4.2 CPUs via onnxruntime 1.19.2 (no PyTorch / AVX2 needed).
    Returns (session, tokenizer) or (None, None) if the model files are absent
    or fail to load.
    """
    global _reranker_onnx, _reranker_onnx_tok
    if _reranker_onnx is not None:
        return _reranker_onnx, _reranker_onnx_tok
    try:
        import os
        from memall.config import get_config

        base = os.path.expanduser(
            get_config("search.reranker_onnx_dir", "~/.memall/.rerank_model")
        )
        model_path = os.path.join(base, "onnx", "model_quantized.onnx")
        tok_path = os.path.join(base, "tokenizer.json")
        if not (os.path.exists(model_path) and os.path.exists(tok_path)):
            logger.info("ONNX reranker not found at %s; skipping", base)
            return None, None
        import onnxruntime as ort
        from tokenizers import Tokenizer

        sess = ort.InferenceSession(model_path, providers=["CPUExecutionProvider"])
        tok = Tokenizer.from_file(tok_path)
        _reranker_onnx = sess
        _reranker_onnx_tok = tok
        logger.info("ONNX reranker loaded: %s", model_path)
        return sess, tok
    except Exception:
        logger.warning("failed to load ONNX reranker", exc_info=True)
        return None, None


def _onnx_rerank(results: list[dict], query: str, top_k: int) -> list[dict] | None:
    """Re-rank candidates via the local ONNX cross-encoder.

    Returns the re-ranked list (top_k) or ``None`` if the reranker is
    unavailable or inference fails (so the caller can try the next backend).
    """
    global _reranker_onnx, _reranker_onnx_tok
    sess, tok = _load_reranker_onnx()
    if sess is None or not results:
        return None
    import numpy as np
    from memall.config import get_config

    rerank_top_k = get_config("search.rerank_top_k", 30)
    candidates = results[:rerank_top_k]
    if not candidates:
        return None
    try:
        encs = tok.encode_batch(
            [(query, (r.get("content") or "")[:512]) for r in candidates],
            add_special_tokens=True,
        )
        input_ids, attn = [], []
        for e in encs:
            ids = e.ids[:512]
            mask = e.attention_mask[:512]
            input_ids.append(ids)
            attn.append(mask)
        max_len = min(max(len(i) for i in input_ids), 512)
        input_ids = [i + [0] * (max_len - len(i)) for i in input_ids]
        attn = [a + [0] * (max_len - len(a)) for a in attn]
        feeds = {
            "input_ids": np.array(input_ids, dtype=np.int64),
            "attention_mask": np.array(attn, dtype=np.int64),
        }
        logits = sess.run(None, feeds)[0]  # (n, 1) single relevance logit
        scores = np.asarray(logits, dtype=np.float32).flatten()
        for r, s in zip(candidates, scores):
            r["rerank_score"] = float(s)
        candidates.sort(key=lambda x: -x.get("rerank_score", 0))
        return candidates[:top_k]
    except Exception:
        logger.warning(
            "ONNX reranker inference failed; will try cross-encoder", exc_info=True
        )
        return None


def _rerank(results: list[dict], query: str, top_k: int) -> list[dict]:
    """Re-rank retrieval candidates with a cross-encoder model.

    Backend order (graceful degradation):
      1. Local ONNX cross-encoder (bge-reranker-base INT8, SSE4.2, zero new
         deps) — preferred; runs on this host without PyTorch / AVX2.
      2. sentence-transformers ``CrossEncoder`` (BAAI/bge-reranker-v2-m3) —
         for hosts that have ST + PyTorch installed.
      3. Original RRF / candidate ordering — if no reranker is available.

    Falls back to the previous ordering if every stage fails.
    """
    if not results:
        return results

    # 1) Local ONNX cross-encoder (preferred, zero-dep, SSE4.2)
    try:
        onnx_res = _onnx_rerank(results, query, top_k)
        if onnx_res is not None:
            return onnx_res
    except Exception:
        logger.debug("ONNX rerank skipped", exc_info=True)

    # 2) sentence-transformers CrossEncoder (for ST-equipped hosts)
    global _reranker, _reranker_model_name
    from memall.config import get_config

    model_name = get_config("search.reranker_model", "BAAI/bge-reranker-v2-m3")
    rerank_top_k = get_config("search.rerank_top_k", 30)

    if _reranker is None or _reranker_model_name != model_name:
        _reranker = None
        _reranker_model_name = None
        try:
            from sentence_transformers import CrossEncoder

            _reranker = CrossEncoder(model_name, device="cpu")
            _reranker_model_name = model_name
            logger.info("reranker loaded: %s", model_name)
        except ImportError:
            logger.warning(
                "sentence-transformers not installed; cross-encoder reranking disabled"
            )
            return results[:top_k]
        except Exception:
            logger.warning(
                "failed to load reranker %s; using RRF results",
                model_name,
                exc_info=True,
            )
            _reranker = None
            return results[:top_k]

    # Score candidates
    try:
        candidates = results[:rerank_top_k]
        pairs = [(query, r.get("content", "")[:512]) for r in candidates]
        scores = _reranker.predict(pairs, show_progress_bar=False)
        for r, s in zip(candidates, scores):
            r["rerank_score"] = float(s)
        candidates.sort(key=lambda x: -x.get("rerank_score", 0))
        return candidates[:top_k]
    except Exception:
        logger.warning("reranker inference failed; using RRF results", exc_info=True)
        return results[:top_k]


def _context_rerank(
    results: list[dict], query: str, top_k: int, viewer: str | None = None
) -> list[dict]:
    """Context-aware re-ranking: micro-adjust cross-encoder scores based on
    the caller's recent interaction patterns.

    Three signals (configurable weights):
      1. **Domain affinity** — if the viewer has recently searched a category,
         boost results in that category by ``affinity_boost``.
      2. **Agent affinity** — if the viewer is ``workbuddy``, boost results
         authored by workbuddy.
      3. **Freshness boost** — results created/updated within 24h get a small
         ``freshness_boost``, but cannot overtake the top 3 from reranker.

    This function is a no-op (returns results unchanged) when:
      - ``viewer`` is None/empty
      - ``search.context_rerank.enabled`` is False (config)
      - ``sentence-transformers`` cross-encoder isn't loaded (no rerank_score)

    Args:
        results: Reranked results list (each has ``rerank_score``).
        query: Original search query (for logging, unused in scoring).
        top_k: Number of results to return after re-ranking.
        viewer: Name of the calling agent/user.

    Returns:
        Re-ranked results list (sorted desc by adjusted score).
    """
    if not viewer or not results:
        return results[:top_k]

    from memall.config import get_config

    if not get_config("search.context_rerank.enabled", False):
        return results[:top_k]

    # Only adjust if cross-encoder scores exist (reranker ran)
    if "rerank_score" not in results[0]:
        return results[:top_k]

    weight = get_config("search.context_rerank.weight", 0.15)
    freshness_boost = get_config("search.context_rerank.freshness_boost", 1.1)
    affinity_boost = get_config("search.context_rerank.affinity_boost", 1.2)

    # 1. Domain affinity: find viewer's recent category distribution
    viewer_categories: dict[str, int] = {}
    try:
        from memall.core.thin_waist import _pool_conn

        with _pool_conn() as conn:
            recent_searches = conn.execute(
                "SELECT category, COUNT(*) as cnt FROM memories "
                "WHERE LOWER(agent_name) = LOWER(?) AND category != '' "
                "AND created_at > datetime('now', '-7 days') "
                "GROUP BY category ORDER BY cnt DESC LIMIT 5",
                (viewer,),
            ).fetchall()
            viewer_categories = {r["category"]: r["cnt"] for r in recent_searches}
    except sqlite3.Error:
        viewer_categories = {}

    viewer_lower = viewer.lower()

    # 2. Apply per-result boost
    from datetime import timedelta as _td

    _freshness_cutoff = (datetime.now(timezone.utc) - _td(days=7)).date().isoformat()
    for r in results:
        boost = 1.0
        cat = (r.get("category") or "").lower()

        # Domain affinity: viewer's frequent categories
        if cat in viewer_categories:
            boost += (affinity_boost - 1.0) * min(1.0, viewer_categories[cat] / 5)

        # Agent affinity: same author
        agent = (r.get("agent_name") or "").lower()
        if agent == viewer_lower:
            boost += (affinity_boost - 1.0) * 0.5

        # Freshness boost: recent memories (~7 day window)
        created = r.get("created_at") or r.get("occurred_at") or ""
        if created and created[:10] > _freshness_cutoff:
            boost += freshness_boost - 1.0

        r["context_score"] = (r.get("rerank_score", 0) or 0) * (
            1 + weight * (boost - 1.0)
        )
        r["context_boost"] = round(boost - 1.0, 3)

    # Sort by adjusted context_score
    results.sort(key=lambda x: -(x.get("context_score", 0) or 0))

    # Enforce: top 3 from cross-encoder stay in top 3 (freshness can't jump the queue)
    # Re-sort: first, pin the top 3 by rerank_score at positions 0-2
    top3_ids = {
        r["memory_id"]
        for r in sorted(results, key=lambda x: -(x.get("rerank_score", 0) or 0))[:3]
    }

    pinned = [r for r in results if r["memory_id"] in top3_ids]
    unpinned = [r for r in results if r["memory_id"] not in top3_ids]
    pinned.sort(key=lambda x: -(x.get("rerank_score", 0) or 0))
    unpinned.sort(key=lambda x: -(x.get("context_score", 0) or 0))

    combined = (pinned + unpinned)[:top_k]
    return combined


def vector_search(query: str, top_k: int = 10, provider: Optional[str] = None) -> dict:
    """Semantic vector search.

    Uses the configured search provider (default vec0 KNN via bge-small-zh-v1.5).
    Set ``provider="faiss"`` to use FAISS, ``provider="vec0"`` for explicit vec0.
    """
    from memall.config import get_config

    active = provider or get_config("search.provider", "faiss")
    from memall.search import get_provider

    p = get_provider(active)
    if p is not None:
        return p.search(query, top_k=top_k)
    from memall.graph.retrieve import retrieve as graph_retrieve

    return graph_retrieve(query, mode="vector", top_k=top_k)


def hybrid_search(
    query: str,
    top_k: int = 10,
    rrf_k: Optional[int] = None,
    category: Optional[str] = None,
    level: Optional[str] = None,
    owner: Optional[str] = None,
    rerank: Optional[bool] = None,
    viewer: Optional[str] = None,
) -> dict:
    """RRF (Reciprocal Rank Fusion) hybrid search combining FTS5 + vec0.

    1. FTS5 keyword search → ranked results
    2. vec0 KNN vector search → ranked results
    3. RRF merge: score = 1/(rrf_k + rank_fts) + 1/(rrf_k + rank_vec)
    4. (optional) Cross-encoder reranking of top candidates
    5. (optional) Context-aware re-ranking using viewer profile

    Optional metadata filters (``category``, ``level``, ``owner``) are applied
    before the RRF merge, reducing candidate pool size.

    Reranking is enabled when ``rerank=True`` (or, when ``rerank`` is left as
    ``None``, by the ``search.rerank_enabled`` config flag). The top
    ``search.rerank_top_k`` candidates are re-scored by a cross-encoder:
    the local ONNX bge-reranker-base (SSE4.2, zero new deps) is tried first,
    then sentence-transformers ``CrossEncoder`` (needs PyTorch) as a fallback,
    then the original RRF ordering if no reranker is available.

    Returns dict with ``results`` (each includes memory_id, content, subject,
    category, level, owner, agent_name, rrf_score, fts_rank, vec_rank),
    ``total``, and per-source hit counts.
    """
    from memall.core.thin_waist import _pool_conn, fts_query, _filter_by_trust_dict
    from memall.graph.retrieve import _query_embed, _vec0_knn
    from memall.config import get_config

    if rrf_k is None:
        rrf_k = get_config("search.rrf_k", 60)

    dispatch_lifecycle(
        HOOK_PRE_SEARCH,
        query=query,
        top_k=top_k,
        rrf_k=rrf_k,
        category=category,
        level=level,
        owner=owner,
    )

    def _apply_meta_filters(rows: list) -> list:
        filtered = rows
        if category:
            filtered = [r for r in filtered if r.get("category") == category]
        if level:
            filtered = [r for r in filtered if r.get("level") == level]
        if owner:
            filtered = [r for r in filtered if r.get("owner") == owner]
        return filtered

    with _pool_conn() as conn:
        # FTS5 results
        fts_q = fts_query(query)
        fts_rows = []
        if fts_q:
            fts_rowids = conn.execute(
                "SELECT rowid FROM memories_fts WHERE memories_fts MATCH ? "
                "ORDER BY rank LIMIT ?",
                (fts_q, top_k * 2),
            ).fetchall()
            if fts_rowids:
                ids = [r["rowid"] for r in fts_rowids]
                placeholders = ",".join("?" * len(ids))
                fts_rows = conn.execute(
                    f"SELECT id, content, subject, category, level, owner, agent_name, created_at FROM memories WHERE id IN ({placeholders})",
                    ids,
                ).fetchall()
        fts_rows = _apply_meta_filters(fts_rows)

        # vec0 KNN results
        query_vec = _query_embed(query)
        vec_rows = []
        if query_vec is not None:
            vec_results = _vec0_knn(conn, query_vec, top_k * 2)
            if vec_results:
                mids = [vr["memory_id"] for vr in vec_results]
                vp = ",".join("?" * len(mids))
                vec_rows = conn.execute(
                    f"SELECT id, content, subject, category, level, owner, agent_name, created_at FROM memories WHERE id IN ({vp})",
                    mids,
                ).fetchall()
        vec_rows = _apply_meta_filters(vec_rows)

        if not fts_rows and not vec_rows:
            return {
                "query": query,
                "mode": "hybrid_rrf",
                "results": [],
                "total": 0,
            }

        # RRF merge
        scores: dict[int, dict] = {}
        for rank, r in enumerate(fts_rows):
            scores[r["id"]] = {
                "memory_id": r["id"],
                "content": r["content"][:200],
                "subject": r["subject"] or "",
                "category": r["category"] or "",
                "level": r["level"] or "",
                "owner": r["owner"] or "",
                "agent_name": r["agent_name"] or "",
                "created_at": r["created_at"] or "",
                "rrf_score": 1.0 / (rrf_k + rank + 1),
                "fts_rank": rank + 1,
                "vec_rank": None,
            }
        for rank, r in enumerate(vec_rows):
            if r["id"] in scores:
                scores[r["id"]]["rrf_score"] += 1.0 / (rrf_k + rank + 1)
                scores[r["id"]]["vec_rank"] = rank + 1
            else:
                scores[r["id"]] = {
                    "memory_id": r["id"],
                    "content": r["content"][:200],
                    "subject": r["subject"] or "",
                    "category": r["category"] or "",
                    "level": r["level"] or "",
                    "owner": r["owner"] or "",
                    "agent_name": r["agent_name"] or "",
                    "created_at": r["created_at"] or "",
                    "rrf_score": 1.0 / (rrf_k + rank + 1),
                    "fts_rank": None,
                    "vec_rank": rank + 1,
                }

        sorted_results = sorted(scores.values(), key=lambda x: -x["rrf_score"])

        # Visibility filter: apply before returning results
        if viewer and sorted_results:
            visibility_scores = _filter_by_trust_dict(sorted_results, viewer)
            sorted_results = [
                r for r in sorted_results if visibility_scores.get(r["memory_id"], True)
            ]

        # Rerank stage (P0-2): ONNX cross-encoder → CrossEncoder → RRF fallback.
        # rerank=None means "decide from config" so enabling is one-line config.
        if rerank is None:
            from memall.config import get_config

            rerank = get_config("search.rerank_enabled", True)
        if rerank:
            sorted_results = _rerank(sorted_results, query, top_k)
            # Context-aware re-ranking (micro-adjustment after cross-encoder)
            sorted_results = _context_rerank(
                sorted_results, query, top_k, viewer=viewer
            )
        else:
            sorted_results = sorted_results[:top_k]

        dispatch_lifecycle(
            HOOK_POST_SEARCH,
            query=query,
            results=sorted_results,
            total=len(scores),
            fts_hits=len(fts_rows),
            vec_hits=len(vec_rows),
        )
        return {
            "query": query,
            "mode": "hybrid_rerank" if rerank else "hybrid_rrf",
            "results": sorted_results,
            "total": len(scores),
            "fts_hits": len(fts_rows),
            "vec_hits": len(vec_rows),
        }


def timeline(
    query: Optional[str] = None,
    hours: int = 24,
    category: Optional[str] = None,
    project: Optional[str] = None,
    limit: int = 50,
    start: Optional[str] = None,
    end: Optional[str] = None,
    days: Optional[int] = None,
) -> list:
    from memall.core.thin_waist import _pool_conn, fts_query, _row_to_memory

    with _pool_conn() as conn:
        from datetime import timedelta

        cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()

        where = []
        params: list = []

        if start:
            where.append("occurred_at >= ?")
            params.append(start)
        if end:
            where.append("occurred_at <= ?")
            params.append(end)
        if not start and not end:
            if days:
                cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
            else:
                cutoff = (
                    datetime.now(timezone.utc) - timedelta(hours=hours)
                ).isoformat()
            where.append("occurred_at >= ?")
            params.append(cutoff)

        if category:
            where.append("category = ?")
            params.append(category)
        if project:
            where.append("project = ?")
            params.append(project)
        if query:
            q = fts_query(query)
            if q:
                where.append(
                    "id IN (SELECT rowid FROM memories_fts WHERE memories_fts MATCH ?)"
                )
                params.append(q)

        params.append(limit)
        sql = f"SELECT * FROM memories WHERE {' AND '.join(where)} ORDER BY occurred_at DESC LIMIT ?"
        rows = conn.execute(sql, params).fetchall()
        return [_row_to_memory(r) for r in rows]