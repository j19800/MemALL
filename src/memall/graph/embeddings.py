import logging
import sqlite3
import json
import hashlib
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

try:
    import numpy as np
except ImportError:
    np = None  # type: ignore

from memall.core.db import pool_conn
from memall.core.nlp import tfidf_svd_embed
from memall.graph.vector_model import save_model

EMBED_DIM = 256
BATCH_SIZE = 64
MAX_TEXT_LEN = 1000

# Regex to detect CJK characters in text
_CJK_CHARS_RE = re.compile(r'[\u4e00-\u9fff\u3400-\u4dbf\uf900-\ufaff]')


def _cjk_aware_tokenizer(text: str):
    """Tokenize text for TF-IDF with jieba Chinese word segmentation.

    Replaces the old unigram+bigram approach with proper word segmentation,
    reducing vocabulary noise and improving semantic signal density.
    """
    tokens = []
    for segment in re.findall(r'[一-鿿㐀-䶿豈-﫿]+|[a-zA-Z][a-zA-Z0-9_]*|\d+', text.lower()):
        if re.match(r'[\u4e00-\u9fff]', segment):
            import jieba
            for w in jieba.lcut(segment):
                w = w.strip()
                if w:
                    tokens.append(w)
        elif len(segment) > 1 or segment.isdigit():
            tokens.append(segment)
    return tokens


def _ensure_embeddings_table(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS memory_embeddings (
            memory_id INTEGER PRIMARY KEY,
            embedding BLOB NOT NULL,
            model_name TEXT NOT NULL DEFAULT 'tfidf-256',
            dims INTEGER NOT NULL DEFAULT 256,
            content_hash TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
    """)
    conn.commit()


def _content_hash(content: str) -> str:
    return hashlib.md5(content.encode("utf-8")).hexdigest()


def _vec0_upsert(conn, memory_id: int, vec_bytes: bytes) -> None:
    """Insert or replace a vector row in the vec0 virtual table."""
    try:
        conn.execute(
            "INSERT OR REPLACE INTO mem_vec(rowid, embedding) VALUES (?, ?)",
            (memory_id, vec_bytes),
        )
    except Exception:
        logger.warning("embeddings.py: silent error", exc_info=True)


def _auto_embed(conn, memory_id: int, content: str, content_hash_val: str) -> None:
    """Compute and persist embedding for a single memory after capture.

    Uses recent existing embeddings as SVD context, then stores only the
    new memory's vector.  Silently no-ops if there aren't enough texts for SVD.
    """
    try:
        _ensure_embeddings_table(conn)
        recent_ctx = conn.execute(
            "SELECT m.content FROM memory_embeddings me "
            "JOIN memories m ON me.memory_id = m.id "
            "WHERE m.id != ? ORDER BY me.memory_id DESC LIMIT 30",
            (memory_id,),
        ).fetchall()
        ctx_texts = [content[:MAX_TEXT_LEN]]
        ctx_texts.extend(r["content"][:MAX_TEXT_LEN] for r in reversed(recent_ctx))
        vecs = tfidf_svd_embed(ctx_texts, dims=EMBED_DIM)
        if vecs is not None and len(vecs) > 0:
            now = datetime.now(timezone.utc).isoformat()
            vec = np.array(vecs[0], dtype=np.float32)
            # Pad to EMBED_DIM if SVD returned fewer dimensions (low text count / vocab)
            if vec.ndim == 1 and vec.shape[0] < EMBED_DIM:
                vec = np.pad(vec, (0, EMBED_DIM - vec.shape[0]), mode='constant')
            vec_bytes = vec.tobytes()
            conn.execute(
                "INSERT OR REPLACE INTO memory_embeddings "
                "(memory_id, embedding, model_name, dims, content_hash, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (memory_id, vec_bytes,
                 "tfidf-256", EMBED_DIM, content_hash_val, now),
            )
            _vec0_upsert(conn, memory_id, vec_bytes)
    except Exception:
        logger.warning("embeddings.py: silent error", exc_info=True)


def build_index(batch_size: int = BATCH_SIZE, force: bool = False) -> dict:
    if np is None:
        return {"status": "error", "error": "numpy is not installed. Run: pip install numpy"}
    with pool_conn() as conn:
        _ensure_embeddings_table(conn)
        rows = conn.execute(
            "SELECT id, content, content_hash FROM memories WHERE LENGTH(TRIM(content)) > 10 ORDER BY id"
        ).fetchall()
        total = len(rows)
        if total == 0:
            return {"total": 0, "embedded": 0, "new": 0, "status": "no_data"}

        # Auto-detect stale model: if vocab < 500, force rebuild
        if not force:
            from memall.graph.vector_model import load_model, has_model
            if has_model():
                model = load_model()
                if model is not None:
                    vec = model["vectorizer"]
                    try:
                        vocab_size = len(vec.get_feature_names_out())
                    except Exception:
                        vocab_size = 0
                    if vocab_size < 500:
                        logger.info("Embedding model stale (vocab=%d < 500), auto-forcing full rebuild", vocab_size)
                        force = True

        existing = set()
        for r in conn.execute("SELECT memory_id, content_hash FROM memory_embeddings").fetchall():
            existing.add((r[0], r["content_hash"]))

        if force:
            pending = list(rows)
            # Clear old embeddings so regenerate is clean
            conn.execute("DELETE FROM memory_embeddings")
            try:
                conn.execute("DELETE FROM mem_vec")
            except Exception:
                pass  # vec0 table may not exist yet
            conn.commit()
        else:
            pending = []
            for r in rows:
                ch = r["content_hash"]
                if (r["id"], ch) not in existing:
                    pending.append(r)

        if not pending:
            return {"total": total, "embedded": total - len(existing), "new": 0, "status": "up_to_date", "model": "tfidf-256"}

        now = datetime.now(timezone.utc).isoformat()
        texts = [r["content"][:MAX_TEXT_LEN] for r in pending]

        try:
            from sklearn.decomposition import TruncatedSVD
            from sklearn.feature_extraction.text import TfidfVectorizer

            # Train on all texts for consistent vector space
            train_texts = [r["content"][:MAX_TEXT_LEN] for r in rows]
            vec = TfidfVectorizer(max_features=5000, stop_words=None, tokenizer=_cjk_aware_tokenizer)
            X = vec.fit_transform(train_texts)
            n_total = X.shape[0]
            k = min(EMBED_DIM, n_total, X.shape[1])
            if k < 2:
                vecs = np.zeros((len(texts), EMBED_DIM))
            else:
                svd = TruncatedSVD(n_components=k, random_state=42)
                svd.fit_transform(X)  # Fit SVD on all data
                # Now transform only pending texts
                X_pending = vec.transform(texts)
                vecs = svd.transform(X_pending)
                try:
                    save_model(vec, svd)
                except Exception:
                    logger.warning("embeddings.py: silent error", exc_info=True)
        except Exception:
            vecs = np.zeros((len(texts), EMBED_DIM))

        conn.execute("BEGIN")
        for i, (row, vec) in enumerate(zip(pending, vecs)):
            vec = np.array(vec, dtype=np.float32)
            if vec.ndim == 1 and vec.shape[0] < EMBED_DIM:
                vec = np.pad(vec, (0, EMBED_DIM - vec.shape[0]), mode='constant')
            vec_bytes = vec.tobytes()
            conn.execute(
                "INSERT OR REPLACE INTO memory_embeddings (memory_id, embedding, model_name, dims, content_hash, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (row["id"], vec_bytes, "tfidf-256", EMBED_DIM, row["content_hash"], now),
            )
            _vec0_upsert(conn, row["id"], vec_bytes)
        conn.commit()

        return {
            "total": total,
            "embedded": total - len(pending) + len(pending),
            "new": len(pending),
            "batch_size": batch_size,
            "model": "tfidf-256",
        }


def index_status() -> dict:
    with pool_conn() as conn:
        _ensure_embeddings_table(conn)
        total = conn.execute("SELECT COUNT(*) FROM memories WHERE LENGTH(TRIM(content)) > 10").fetchone()[0]
        embedded = conn.execute("SELECT COUNT(*) FROM memory_embeddings").fetchone()[0]
        model_row = conn.execute("SELECT DISTINCT model_name FROM memory_embeddings LIMIT 1").fetchone()
        model = model_row[0] if model_row else "N/A"
        dims = conn.execute("SELECT DISTINCT dims FROM memory_embeddings LIMIT 1").fetchone()
        dims = dims[0] if dims else 0
        return {"total_memories": total, "embedded": embedded, "pending": total - embedded, "model": model, "dims": dims}


def _load_embeddings_matrix(conn) -> tuple:
    if np is None:
        return (), [], []
    _ensure_embeddings_table(conn)
    rows = conn.execute("SELECT me.memory_id, me.embedding, m.content FROM memory_embeddings me JOIN memories m ON me.memory_id = m.id ORDER BY me.memory_id").fetchall()
    if not rows:
        return (), [], []
    mem_ids = [r["memory_id"] for r in rows]
    contents = [r["content"] for r in rows]
    raw_vecs = [np.frombuffer(r["embedding"], dtype=np.float32) for r in rows]
    # Ensure uniform dimension: pad shorter, truncate longer (defense against legacy data)
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
