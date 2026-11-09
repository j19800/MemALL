"""KB async enrichment worker (P2-4).

Registers a non-blocking hook for KB ingest. Failures are swallowed; hook is
config-gated. Enrichment is best-effort and never blocks capture.

P2-4 真异步队列骨架（当前）：daemon-thread hook + in-process idempotency/统计。
后续可替换为持久化队列/后台 worker（幂等 key: doc_id+chunk_hash+doc_version）。
"""
from __future__ import annotations

import logging
import threading
from typing import Any

log = logging.getLogger(__name__)

_LOCK = threading.Lock()
_SEEN_KEYS: set[str] = set()
_MAX_SEEN = 4096
_STATS: dict[str, Any] = {
    "fired": 0,
    "ok": 0,
    "failed": 0,
    "skipped": 0,
    "deduped": 0,
    "last_error": "",
}


def kb_enrich_stats() -> dict[str, Any]:
    with _LOCK:
        return {**_STATS, "seen_keys": len(_SEEN_KEYS)}


def _enabled() -> bool:
    try:
        from memall.config import get_config
        return bool(get_config("kb.enrich_async", True))
    except Exception:
        return True


def _idempotency_key(meta: dict) -> str:
    if not isinstance(meta, dict):
        return ""
    parts = [
        str(meta.get("doc_id") or ""),
        str(meta.get("chunk_hash") or ""),
        str(meta.get("doc_version") or ""),
        str(meta.get("chunk_index") or ""),
    ]
    if not any(parts):
        return ""
    return "|".join(parts)


def _mark_seen(key: str) -> bool:
    """Return True if first time seen (should process)."""
    if not key:
        return True
    with _LOCK:
        if key in _SEEN_KEYS:
            return False
        if len(_SEEN_KEYS) >= _MAX_SEEN:
            # bounded memory: drop oldest-ish by clearing half (simple, deterministic enough)
            for k in list(_SEEN_KEYS)[: _MAX_SEEN // 2]:
                _SEEN_KEYS.discard(k)
        _SEEN_KEYS.add(key)
        return True


def kb_enrich_handler(meta: dict) -> None:
    """Best-effort KB enrichment. Runs in daemon thread via memory_write hook."""
    with _LOCK:
        _STATS["fired"] += 1
    if not _enabled():
        with _LOCK:
            _STATS["skipped"] += 1
        return
    try:
        if not isinstance(meta, dict):
            with _LOCK:
                _STATS["skipped"] += 1
            return
        key = _idempotency_key(meta)
        if not _mark_seen(key):
            with _LOCK:
                _STATS["deduped"] += 1
            return
        # Lightweight, non-destructive enrichment placeholder.
        # Heavy LLM/entity/embedding enrichment should be added behind explicit
        # config and a durable queue/worker with retry limits.
        _ = meta.get("doc_id"), meta.get("chunk_hash"), meta.get("source_pipeline")
        with _LOCK:
            _STATS["ok"] += 1
            _STATS["last_error"] = ""
    except Exception as e:
        with _LOCK:
            _STATS["failed"] += 1
            _STATS["last_error"] = str(e)[:300]
        log.debug("kb_enrich_handler failed: %s", e)


def register_default_kb_enrich_hook() -> bool:
    try:
        from memall.mcp.tools.memory_write import set_kb_enrich_hook
        set_kb_enrich_hook(kb_enrich_handler)
        return True
    except Exception:
        return False
