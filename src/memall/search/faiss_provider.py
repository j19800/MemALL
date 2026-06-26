"""FAISS vector search provider — Phase 2.

Implements ``SearchProvider`` using Facebook AI Similarity Search (FAISS).
Designed as an upgrade path when the knowledge base exceeds sqlite-vec's
brute-force limits (>100K documents).

Gracefully degrades when FAISS is not installed (returns error dicts
instead of crashing), allowing the application to function with other
providers.
"""

import logging

logger = logging.getLogger(__name__)

import json
import os
from datetime import datetime, timezone
from typing import Optional

from memall.search.base import SearchProvider

try:
    import numpy as np
except ImportError:
    np = None

try:
    import faiss
except ImportError:
    faiss = None

_INDEX_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".faiss_index")
_INDEX_FILE = os.path.join(_INDEX_DIR, "index.faiss")
_MAP_FILE = os.path.join(_INDEX_DIR, "id_map.json")
_PARAMS_FILE = os.path.join(_INDEX_DIR, "params.json")

EMBED_DIM = 768
"""Default embedding dimension (matches sentence-transformers all-MiniLM-L6-v2).
Admissible values depend on the embedding model used.  Change via config."""

BATCH_SIZE = 64


class FaissProvider(SearchProvider):
    """FAISS-based vector search provider.

    Uses IVF (Inverted File) index with Flat quantizer as the default —
    the best production trade-off between speed, memory, and recall at
    million scale.
    """

    def __init__(self, dim: int = EMBED_DIM):
        self.dim = dim
        self.index: Optional["faiss.Index"] = None
        self.id_map: dict[int, int] = {}       # faiss_id → memory_id
        self.rev_map: dict[int, int] = {}       # memory_id → faiss_id
        self._next_id: int = 0

    # ── Provider interface ──────────────────────────────────────────────

    def build_index(self, force: bool = False) -> dict:
        if faiss is None or np is None:
            return {"status": "error", "error": "faiss or numpy not installed. Run: pip install faiss-cpu numpy"}

        from memall.core.db import pool_conn

        with pool_conn() as conn:
            rows = conn.execute(
                "SELECT id, content FROM memories WHERE LENGTH(TRIM(content)) > 10 ORDER BY id"
            ).fetchall()

        if not rows:
            return {"status": "ok", "total": 0, "indexed": 0}

        texts = [r["content"][:1000] for r in rows]
        mem_ids = [r["id"] for r in rows]

        # Generate embeddings
        vecs = self._encode(texts)
        if vecs is None or vecs.shape[0] == 0:
            return {"status": "error", "error": "embedding generation failed"}

        n, d = vecs.shape
        nlist = max(1, min(int(np.sqrt(n)), n // 2))

        nprobe = min(nlist, max(1, nlist // 4))

        quantizer = faiss.IndexFlatL2(d)
        index = faiss.IndexIVFFlat(quantizer, d, nlist, faiss.METRIC_L2)
        if not index.is_trained:
            index.train(vecs)
        index.nprobe = nprobe

        index.add(vecs)

        self.index = index
        self.id_map = {i: mid for i, mid in enumerate(mem_ids)}
        self.rev_map = {mid: i for i, mid in enumerate(mem_ids)}
        self._next_id = n
        self.save()

        return {
            "status": "ok",
            "total": n,
            "indexed": n,
            "dims": d,
            "nlist": nlist,
            "nprobe": nprobe,
        }

    def search(self, query: str, top_k: int = 10) -> dict:
        if self.index is None:
            return {"query": query, "mode": "faiss", "error": "index not built. Run build_index first", "results": []}

        vec = self._encode([query[:1000]])
        if vec is None or vec.shape[0] == 0:
            return {"query": query, "mode": "faiss", "error": "query embedding failed", "results": []}

        k = min(top_k, self.index.ntotal)
        if k == 0:
            return {"query": query, "mode": "faiss", "results": [], "total": 0}

        distances, indices = self.index.search(vec, k)
        from memall.core.db import pool_conn

        results = []
        with pool_conn() as conn:
            for dist, idx in zip(distances[0], indices[0]):
                if idx < 0:
                    continue
                mem_id = self.id_map.get(int(idx))
                if mem_id is None:
                    continue
                row = conn.execute("SELECT content FROM memories WHERE id = ?", (mem_id,)).fetchone()
                if row is None:
                    continue
                score = 1.0 / (1.0 + float(dist))
                results.append({
                    "memory_id": mem_id,
                    "content": row["content"][:200],
                    "score": round(score, 4),
                    "source": "faiss",
                })

        results.sort(key=lambda x: -x["score"])
        return {"query": query, "mode": "faiss", "results": results[:top_k], "total": len(results)}

    def index_status(self) -> dict:
        from memall.core.db import pool_conn
        with pool_conn() as conn:
            total = conn.execute("SELECT COUNT(*) FROM memories WHERE LENGTH(TRIM(content)) > 10").fetchone()[0]
        indexed = self.index.ntotal if self.index else 0
        return {
            "provider": "faiss",
            "total_memories": total,
            "indexed": indexed,
            "pending": total - indexed,
            "dims": self.dim,
        }

    def add_item(self, memory_id: int, content: str) -> None:
        if self.index is None or faiss is None or np is None:
            return
        if memory_id in self.rev_map:
            return
        vec = self._encode([content[:1000]])
        if vec is None:
            return
        fid = self._next_id
        self.index.add(vec)
        self.id_map[fid] = memory_id
        self.rev_map[memory_id] = fid
        self._next_id += 1

    def remove_item(self, memory_id: int) -> None:
        if self.index is None or faiss is None:
            return
        fid = self.rev_map.pop(memory_id, None)
        if fid is not None:
            self.id_map.pop(fid, None)
            self.index.remove_ids(np.array([fid], dtype=np.int64))

    def save(self) -> None:
        if self.index is None:
            return
        os.makedirs(_INDEX_DIR, exist_ok=True)
        faiss.write_index(self.index, _INDEX_FILE)
        with open(_MAP_FILE, "w", encoding="utf-8") as f:
            json.dump({"id_map": self.id_map, "rev_map": self.rev_map, "next_id": self._next_id}, f)
        with open(_PARAMS_FILE, "w", encoding="utf-8") as f:
            json.dump({"dim": self.dim, "saved_at": datetime.now(timezone.utc).isoformat()}, f)

    @classmethod
    def load(cls) -> Optional["FaissProvider"]:
        if faiss is None:
            return None
        if not os.path.exists(_INDEX_FILE) or not os.path.exists(_MAP_FILE):
            return None
        try:
            provider = cls()
            provider.index = faiss.read_index(_INDEX_FILE)
            with open(_MAP_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            provider.id_map = {int(k): int(v) for k, v in data["id_map"].items()}
            provider.rev_map = {int(k): int(v) for k, v in data["rev_map"].items()}
            provider._next_id = int(data.get("next_id", len(provider.id_map)))
            if os.path.exists(_PARAMS_FILE):
                with open(_PARAMS_FILE, "r", encoding="utf-8") as f:
                    params = json.load(f)
                provider.dim = int(params.get("dim", EMBED_DIM))
            return provider
        except Exception:
            return None

    # ── Internal helpers ────────────────────────────────────────────────

    def _encode(self, texts: list[str]) -> Optional["np.ndarray"]:
        """Encode texts into dense vectors.

        Tries sentence-transformers first; falls back to TF-IDF+SVD for
        environments where sentence-transformers is not installed.
        """
        try:
            from sentence_transformers import SentenceTransformer
            model = SentenceTransformer("all-MiniLM-L6-v2", device="cpu")
            return model.encode(texts, normalize_embeddings=True, show_progress_bar=False).astype(np.float32)
        except ImportError:
            logger.warning("faiss(%s) sentence-transformers not available, falling back to TF-IDF+SVD", self.__class__.__name__)

        try:
            from memall.core.nlp import tfidf_svd_embed
            from memall.graph.vector_model import load_model
            saved = load_model()
            if saved is not None:
                vec = saved["vectorizer"]
                svd = saved["svd"]
                X = vec.transform(texts)
                return svd.transform(X).astype(np.float32)
            result = tfidf_svd_embed(texts, dims=self.dim)
            if result is not None:
                return result.astype(np.float32)
        except Exception:
            logger.warning("faiss(%s) TF-IDF+SVD fallback encode failed", self.__class__.__name__, exc_info=True)

        return None
