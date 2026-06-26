"""Provider registry — selects the active search backend from config.

Usage::

    from memall.search import get_provider
    provider = get_provider()
    provider.build_index()
    results = provider.search("南海归墟", top_k=10)
"""

from typing import Optional

from memall.search.base import SearchProvider

_providers: dict[str, type[SearchProvider]] = {}
_active: Optional[SearchProvider] = None


def register_provider(name: str, cls: type[SearchProvider]) -> None:
    """Register a search provider class under a short name."""
    _providers[name] = cls


def list_providers() -> dict[str, type[SearchProvider]]:
    """Return dict of registered provider names → classes."""
    return dict(_providers)


def get_provider(name: Optional[str] = None) -> Optional[SearchProvider]:
    """Get (or create) the active search provider.

    Args:
        name: Provider name.  If None, reads from ``memall.config``
              key ``search.provider`` (default: ``"faiss"``).

    Returns:
        An instance of the selected ``SearchProvider``, or ``None``
        if the provider is not registered.
    """
    global _active

    if _active is not None and name is None:
        return _active

    if name is None:
        try:
            from memall.config import get_config
            name = get_config("search.provider", "faiss")
        except Exception:
            name = "faiss"

    cls = _providers.get(name)
    if cls is None:
        return None

    try:
        _active = cls.load()
    except Exception:
        _active = None

    if _active is None:
        try:
            _active = cls()
        except Exception:
            return None

    return _active


def reset_provider() -> None:
    """Reset the cached provider instance (useful for testing/config reload)."""
    global _active
    _active = None


# ── Built-in providers ──────────────────────────────────────────────────

def _register_builtin() -> None:
    from memall.search.faiss_provider import FaissProvider
    register_provider("faiss", FaissProvider)


_register_builtin()
