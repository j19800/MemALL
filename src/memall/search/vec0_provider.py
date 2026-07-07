"""sqlite-vec vector search provider.

Implements ``SearchProvider`` wrapping the existing vec0 virtual table
infrastructure (``memall.graph.embeddings`` / ``retrieve``).  The index
lives in the SQLite database itself — no external files needed.
"""

import logging

logger = logging.getLogger(__name__)

import json
import sqlite3
from typing import Optional

import numpy as np

from memall.search.base import SearchProvider
from memall.graph.embeddings import build_index as _build_embeddings, index_status as _embeddings_status

_EMBED_MODEL = "bge-small-zh-v1.5"


class Vec0Provider(SearchProvider):
    """sqlite-vec vector search provider.

    The index is stored entirely in the ``mem_vec`` virtual table within
    the main MemALL SQLite database — no external files or state to manage.
    """

    def __init__(self):
        self._ready = True  # vec0 is always available when the DB is

    # ── Provider interface ─────────────────────────────────────────────────

    def build_index(self, force: bool = False) -> dict:
        try:
            return _build_embeddings(force=force)
        except Exception as e:
            logger.error("Vec0 build_index failed: %s", e)
            return {"status": "error", "error": str(e)}

    def search(self, query: str, top_k: int = 10) -> dict:
        from memall.graph.retrieve import _query_embed, _vec0_knn
        from memall.core.db import pool_conn

        query_vec = _query_embed(query[:768])
        if query_vec is None:
            return {"query": query, "mode": "vec0", "error": "embedding failed", "results": [], "total": 0}

        with pool_conn() as conn:
            vec_results = _vec0_knn(conn, query_vec, top_k)

        if not vec_results:
            return {"query": query, "mode": "vec0", "results": [], "total": 0}

        # Fetch full memory data for each result
        mem_ids = [r["memory_id"] for r in vec_results]
        results = []
        with pool_conn() as conn:
            for vr in vec_results:
                row = conn.execute(
                    "SELECT id, content, subject, category, level, agent_name FROM memories WHERE id = ?",
                    (vr["memory_id"],),
                ).fetchone()
                if row:
                    results.append({
                        "memory_id": row["id"],
                        "content": row["content"][:500],
                        "subject": row["subject"] or "",
                        "category": row["category"] or "",
                        "level": row["level"] or "",
                        "agent_name": row["agent_name"] or "",
                        "score": vr["score"],
                        "source": "vec0",
                    })

        return {
            "query": query,
            "mode": "vec0",
            "results": results,
            "total": len(results),
        }

    def index_status(self) -> dict:
        try:
            return _embeddings_status()
        except Exception as e:
            logger.error("Vec0 index_status failed: %s", e)
            return {"status": "error", "error": str(e)}

    def add_item(self, memory_id: int, content: str) -> None:
        """Encode and insert a single memory into the vec0 index.

        Delegates to the per-memory insertion path in embeddings.
        """
        from memall.core.db import pool_conn
        from memall.graph.embeddings import _get_model, _vec0_upsert, EMBED_DIM

        model = _get_model()
        if model is None:
            logger.warning("Vec0 add_item: embed model unavailable")
            return

        vec = model.encode([content[:768]], normalize_embeddings=True)[0]
        vec_bytes = np.array(vec, dtype=np.float32).tobytes()

        with pool_conn() as conn:
            _vec0_upsert(conn, memory_id, vec_bytes)

    def remove_item(self, memory_id: int) -> None:
        from memall.core.db import pool_conn

        with pool_conn() as conn:
            try:
                conn.execute("DELETE FROM mem_vec WHERE rowid = ?", (memory_id,))
            except sqlite3.Error:
                logger.warning("Vec0 remove_item failed for memory_id=%s", memory_id, exc_info=True)

    def save(self) -> None:
        pass  # vec0 lives in the DB — no persist step needed

    @classmethod
    def load(cls) -> Optional["Vec0Provider"]:
        return Vec0Provider()