"""Search provider abstraction layer for MemALL.

Provides a pluggable ``SearchProvider`` interface that all vector search
backends implement, enabling seamless switching between:

- TF-IDF + SVD  (legacy, built-in)
- sqlite-vec    (Phase 1, lightweight SQLite extension)
- FAISS         (Phase 2, production-scale)

Use ``get_provider()`` to obtain the active provider based on config.
"""
from memall.search.registry import get_provider, register_provider, list_providers
from memall.search.base import SearchProvider
from memall.search.intent_router import classify, resolve_mode, SearchIntent

__all__ = [
    "SearchProvider", "get_provider", "register_provider", "list_providers",
    "classify", "resolve_mode", "SearchIntent",
]
