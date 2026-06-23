"""Embedding index for vector search — uses BAAI/bge-small-zh-v1.5.

Stores 512-dim float32 vectors in memory_embeddings table + vec0 virtual table.
"""

import hashlib
import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import numpy as np

from memall.core.db import pool_conn

logger = logging.getLogger(__name__)

EMBED_DIM = 512
BATCH_SIZE = 64
MAX_TEXT_LEN = 512

_MODEL = None
_MODEL_NAME = "BAAI/bge-small-zh-v1.5"


def _get_model():
    """Lazy-load the SentenceTransformer model (cached after first call)."""
    global _MODEL
    if _MODEL is None:
        from sentence_transformers import SentenceTransformer
        logger.info("Loading embedding model %s ...", _MODEL_NAME)
        _MODEL = SentenceTransformer(_MODEL_NAME, device="cpu")
        logger.info("Embedding model loaded, dim=%d", _MODEL.get_embedding_dimension())
    return _MODEL


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
    """Insert or replace a vector row in the vec0 virtual table."""
    try:
        conn.execute(
            "INSERT OR REPLACE INTO mem_vec(rowid, embedding) VALUES (?, ?)",
            (memory_id, vec_bytes),
        )
    except Exception:
        logger.warning("embeddings.py: silent error", exc_info=True)


def _auto_embed(conn, memory_id: int, content: str, content_hash_val: str) -> None:
    """Compute and persist embedding for a single memory after capture."""
    try:
        _ensure_embeddings_table(conn)
        model = _get_model()
        vec = model.encode(content[:MAX_TEXT_LEN], normalize_embeddings=True)
        vec = np.array(vec, dtype=np.float32)
        now = datetime.now(timezone.utc).isoformat()
        vec_bytes = vec.tobytes()
        conn.execute(
            "INSERT OR REPLACE INTO memory_embeddings "
            "(memory_id, embedding, model_name, dims, content_hash, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (memory_id, vec_bytes, _MODEL_NAME, EMBED_DIM, content_hash_val, now),
        )
        _vec0_upsert(conn, memory_id, vec_bytes)
    except Exception:
        logger.warning("embeddings.py: silent error", exc_info=True)


def build_index(batch_size: int = BATCH_SIZE, force: bool = False) -> dict:
    with pool_conn() as conn:
        _ensure_embeddings_table(conn)
        rows = conn.execute(
            "SELECT id, content, content_hash FROM memories WHERE LENGTH(TRIM(content)) > 10 ORDER BY id"
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
            except Exception:
                pass
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
                "new": 0, "status": "up_to_date", "model": _MODEL_NAME,
            }

        model = _get_model()
        texts = [r["content"][:MAX_TEXT_LEN] for r in pending]
        logger.info("Encoding %d memories with %s ...", len(texts), _MODEL_NAME)
        vecs = model.encode(texts, show_progress_bar=False, normalize_embeddings=True)

        now = datetime.now(timezone.utc).isoformat()
        conn.execute("BEGIN")
        for i, row in enumerate(pending):
            vec = np.array(vecs[i], dtype=np.float32)
            vec_bytes = vec.tobytes()
            conn.execute(
                "INSERT OR REPLACE INTO memory_embeddings "
                "(memory_id, embedding, model_name, dims, content_hash, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (row["id"], vec_bytes, _MODEL_NAME, EMBED_DIM, row["content_hash"], now),
            )
            _vec0_upsert(conn, row["id"], vec_bytes)
        conn.commit()

        return {
            "total": total,
            "embedded": total,
            "new": len(pending),
            "batch_size": batch_size,
            "model": _MODEL_NAME,
        }


def index_status() -> dict:
    with pool_conn() as conn:
        _ensure_embeddings_table(conn)
        total = conn.execute("SELECT COUNT(*) FROM memories WHERE LENGTH(TRIM(content)) > 10").fetchone()[0]
        embedded = conn.execute("SELECT COUNT(*) FROM memory_embeddings").fetchone()[0]
        model_row = conn.execute("SELECT DISTINCT model_name FROM memory_embeddings LIMIT 1").fetchone()
        model = model_row[0] if model_row else _MODEL_NAME
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
