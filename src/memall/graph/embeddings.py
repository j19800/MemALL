"""Embedding index for vector search — uses BAAI/bge-small-zh-v1.5.

Stores 512-dim float32 vectors in memory_embeddings table + vec0 virtual table.
"""

import hashlib
import logging
import os
import sqlite3
import threading
from datetime import datetime, timezone

import numpy as np

from memall.core.db import pool_conn

logger = logging.getLogger(__name__)

EMBED_DIM = 512
BATCH_SIZE = 64
MAX_TEXT_LEN = 512

_MODEL = None
_MODEL_NAME = "BAAI/bge-small-zh-v1.5"

# Declare optional dependency: sentence-transformers for text embedding
# NOTE: import is deferred to _get_model() because sentence_transformers pulls in
# torch which can hang on some Windows configurations during module-level import.
_HAS_ST: bool | None = None  # None = untried, False = unavailable, True = available


def _check_st_available(timeout: float = 5.0) -> bool:
    """Check if sentence-transformers is importable (cached after first call).

    Uses a daemon thread with timeout because ``import sentence_transformers``
    can hang indefinitely on some Windows configurations (torch init).
    """
    global _HAS_ST
    if _HAS_ST is not None:
        return _HAS_ST
    result = [False]
    done = threading.Event()

    def _probe():
        try:
            import sentence_transformers  # noqa: F401
            result[0] = True
        except ImportError:
            pass
        done.set()

    t = threading.Thread(target=_probe, daemon=True)
    t.start()
    if not done.wait(timeout=timeout):
        logger.warning(
            "sentence-transformers import timed out (%.1fs); vector search unavailable",
            timeout,
        )
        _HAS_ST = False
        return False

    _HAS_ST = result[0]
    if not _HAS_ST:
        logger.info(
            "sentence-transformers not installed; vector search unavailable. "
            "Install with: pip install sentence-transformers"
        )
    return _HAS_ST


def _get_model():
    """Lazy-load the SentenceTransformer model (cached after first call)."""
    if not _check_st_available():
        raise ImportError(
            "sentence-transformers is required for embedding. "
            "Install with: pip install sentence-transformers"
        )
    global _MODEL
    if _MODEL is None:
        from sentence_transformers import SentenceTransformer
        logger.info("Loading embedding model %s ...", _MODEL_NAME)
        _MODEL = SentenceTransformer(_MODEL_NAME, device="cpu")
        logger.info("Embedding model loaded, dim=%d", _MODEL.get_embedding_dimension())
    return _MODEL


# ── Torch-free fallback embedder (TF-IDF → SVD) ──────────────────────────
# Used when sentence-transformers cannot be imported (e.g. torch DLL init
# failure on this host). Produces deterministic, query/index-consistent
# EMBED_DIM vectors with a persisted TfidfVectorizer + TruncatedSVD.
# Chinese text is tokenized with char unigrams + bigrams (no jieba dep).

import re as _re  # noqa: E402
import pickle as _pickle  # noqa: E402

_CJK_RE = _re.compile(r"[\u4e00-\u9fff]+")
_ASCII_RE = _re.compile(r"[A-Za-z0-9]+")


def _tokenize_mixed(text: str) -> list:
    """Mixed CN/EN tokenizer: ASCII words + CJK char unigrams/bigrams."""
    toks = []
    for m in _ASCII_RE.findall(text.lower()):
        toks.append(m)
    for seg in _CJK_RE.findall(text):
        for i in range(len(seg)):
            toks.append(seg[i])
            if i + 1 < len(seg):
                toks.append(seg[i:i + 2])
    return toks


_TFIDF_MODEL = None
_TFIDF_MODEL_PATH = None


def _tfidf_model_path() -> str:
    global _TFIDF_MODEL_PATH
    if _TFIDF_MODEL_PATH is None:
        base = os.path.join(os.path.expanduser("~/.memall"), ".vector_model")
        os.makedirs(base, exist_ok=True)
        _TFIDF_MODEL_PATH = os.path.join(base, "tfidf_svd_vecsearch.pkl")
    return _TFIDF_MODEL_PATH


def _load_tfidf_model():
    global _TFIDF_MODEL
    if _TFIDF_MODEL is not None:
        return _TFIDF_MODEL
    p = _tfidf_model_path()
    if os.path.exists(p):
        try:
            with open(p, "rb") as f:
                _TFIDF_MODEL = _pickle.load(f)
                return _TFIDF_MODEL
        except Exception:
            _TFIDF_MODEL = None
    return None


def _save_tfidf_model(model):
    p = _tfidf_model_path()
    try:
        with open(p, "wb") as f:
            _pickle.dump(model, f, protocol=_pickle.HIGHEST_PROTOCOL)
    except Exception:
        logger.warning("failed to persist tfidf vecsearch model", exc_info=True)


def _tfidf_embed(texts, normalize: bool = True):
    """TF-IDF + TruncatedSVD embedding (torch-free). Trains on first batch."""
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.decomposition import TruncatedSVD

    texts = [t or "" for t in texts]
    model = _load_tfidf_model()
    if model is None:
        if len(texts) < 2:
            return None  # cannot train SVD on a single doc
        vec = TfidfVectorizer(
            tokenizer=_tokenize_mixed, token_pattern=None,
            max_features=4000, stop_words=None,
        )
        X = vec.fit_transform(texts)
        n = X.shape[0]
        k = min(EMBED_DIM, n, X.shape[1])
        if k < 2:
            return None
        svd = TruncatedSVD(n_components=k, random_state=42)
        svd.fit(X)
        model = (vec, svd)
        _TFIDF_MODEL = model
        _save_tfidf_model(model)
    vec, svd = model
    X = vec.transform(texts)
    out = svd.transform(X).astype(np.float32)
    if normalize:
        norms = np.linalg.norm(out, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        out = out / norms
    return out


def _embed_texts(texts, normalize: bool = True):
    """Unified embedder: sentence-transformers if available, else TF-IDF/SVD.

    Returns an (n, EMBED_DIM) float32 array, or None when no backend can
    produce vectors for the given inputs (callers should degrade gracefully).
    """
    if _check_st_available():
        try:
            model = _get_model()
            vecs = model.encode(texts, show_progress_bar=False, normalize_embeddings=normalize)
            return np.asarray(vecs, dtype=np.float32)
        except Exception:
            logger.warning("sentence-transformers encode failed; falling back to TF-IDF/SVD", exc_info=True)
    return _tfidf_embed(texts, normalize=normalize)


def _active_model_name() -> str:
    """Report the backend actually used for embeddings (for diagnostics)."""
    return _MODEL_NAME if _check_st_available() else "tfidf-svd-fallback"


def _ensure_embeddings_table(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS memory_embeddings (
            memory_id INTEGER PRIMARY KEY,
            embedding BLOB NOT NULL,
            model_name TEXT NOT NULL DEFAULT 'bge-small-zh',
            dims INTEGER NOT NULL DEFAULT 512,
            content_hash TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
    """)
    conn.commit()


def _content_hash(content: str) -> str:
    return hashlib.md5(content.encode("utf-8")).hexdigest()


def _vec0_upsert(conn, memory_id: int, vec_bytes: bytes) -> None:
    """Insert or replace a vector row in the vec0 virtual table.

    Uses DELETE + INSERT instead of INSERT OR REPLACE because the vec0
    virtual table may not handle OR REPLACE correctly on some platforms.
    """
    conn.execute("DELETE FROM mem_vec WHERE rowid = ?", (memory_id,))
    conn.execute(
        "INSERT INTO mem_vec(rowid, embedding) VALUES (?, ?)",
        (memory_id, vec_bytes),
    )


def _auto_embed(conn, memory_id: int, content: str, content_hash_val: str) -> None:
    """Compute and persist embedding for a single memory after capture.

    Raises on failure so callers can track embedding status.
    """
    _ensure_embeddings_table(conn)
    emb = _embed_texts([content[:MAX_TEXT_LEN]], normalize=True)
    if emb is None:
        # No embedding backend available (e.g. torch DLL failure AND TF-IDF
        # model not yet trained). Skip; build_index will fill it later.
        return
    vec = emb[0]
    now = datetime.now(timezone.utc).isoformat()
    vec_bytes = vec.tobytes()
    conn.execute(
        "INSERT OR REPLACE INTO memory_embeddings "
        "(memory_id, embedding, model_name, dims, content_hash, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (memory_id, vec_bytes, _active_model_name(), EMBED_DIM, content_hash_val, now),
    )
    _vec0_upsert(conn, memory_id, vec_bytes)


def build_index(batch_size: int = BATCH_SIZE, force: bool = False) -> dict:
    with pool_conn() as conn:
        _ensure_embeddings_table(conn)
        rows = conn.execute(
            "SELECT id, content, content_hash FROM memories WHERE LENGTH(TRIM(content)) > 10 ORDER BY id LIMIT 100000"
        ).fetchall()
        total = len(rows)
        if total == 0:
            return {"total": 0, "embedded": 0, "new": 0, "status": "no_data"}

        existing = set()
        if not force:
            for r in conn.execute("SELECT memory_id, content_hash FROM memory_embeddings").fetchall():
                existing.add((r[0], r["content_hash"]))

        if force:
            pending = list(rows)
            conn.execute("DELETE FROM memory_embeddings")
            try:
                conn.execute("DELETE FROM mem_vec")
            except sqlite3.Error:
                logger.warning("embeddings: failed to clear vec0 index", exc_info=True)
            conn.commit()
        else:
            pending = []
            for r in rows:
                ch = r["content_hash"]
                if (r["id"], ch) not in existing:
                    pending.append(r)

        if not pending:
            return {
                "total": total, "embedded": total,
                "new": 0, "status": "up_to_date", "model": _active_model_name(),
            }

        texts = [r["content"][:MAX_TEXT_LEN] for r in pending]
        logger.info("Encoding %d memories with embedding backend ...", len(texts))
        vecs = _embed_texts(texts, normalize=True)
        if vecs is None:
            logger.error("Embedding backend unavailable; cannot build vector index")
            return {
                "total": total, "embedded": total,
                "new": 0, "status": "embedder_unavailable", "model": _active_model_name(),
            }

        now = datetime.now(timezone.utc).isoformat()
        conn.execute("BEGIN")
        for i, row in enumerate(pending):
            vec = np.array(vecs[i], dtype=np.float32)
            vec_bytes = vec.tobytes()
            conn.execute(
                "INSERT OR REPLACE INTO memory_embeddings "
                "(memory_id, embedding, model_name, dims, content_hash, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (row["id"], vec_bytes, _active_model_name(), EMBED_DIM, row["content_hash"], now),
            )
            _vec0_upsert(conn, row["id"], vec_bytes)
        conn.commit()

        return {
            "total": total,
            "embedded": total,
            "new": len(pending),
            "batch_size": batch_size,
            "model": _active_model_name(),
        }


def index_status() -> dict:
    with pool_conn() as conn:
        _ensure_embeddings_table(conn)
        total = conn.execute("SELECT COUNT(*) FROM memories WHERE LENGTH(TRIM(content)) > 10").fetchone()[0]
        embedded = conn.execute("SELECT COUNT(*) FROM memory_embeddings").fetchone()[0]
        model_row = conn.execute("SELECT DISTINCT model_name FROM memory_embeddings LIMIT 1").fetchone()
        model = model_row[0] if model_row else _active_model_name()
        dims = EMBED_DIM
        return {"total_memories": total, "embedded": embedded, "pending": total - embedded, "model": model, "dims": dims}


def _load_embeddings_matrix(conn) -> tuple:
    _ensure_embeddings_table(conn)
    rows = conn.execute(
        "SELECT me.memory_id, me.embedding, m.content "
        "FROM memory_embeddings me JOIN memories m ON me.memory_id = m.id ORDER BY me.memory_id"
    ).fetchall()
    if not rows:
        return (), [], []
    mem_ids = [r["memory_id"] for r in rows]
    contents = [r["content"] for r in rows]
    raw_vecs = [np.frombuffer(r["embedding"], dtype=np.float32) for r in rows]
    k = EMBED_DIM
    uniform_vecs = []
    for v in raw_vecs:
        if len(v) < k:
            padded = np.zeros(k, dtype=np.float32)
            padded[:len(v)] = v
            uniform_vecs.append(padded)
        else:
            uniform_vecs.append(v[:k])
    vecs = np.array(uniform_vecs)
    return vecs, mem_ids, contents
