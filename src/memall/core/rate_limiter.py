"""In-memory sliding-window rate limiter for gateway and MCP HTTP."""

import time
import logging
from collections import defaultdict
from threading import Lock

logger = logging.getLogger(__name__)


class SlidingWindowRateLimiter:
    """Sliding-window rate limiter keyed by IP address.

    Thread-safe via Lock.  Default: 100 requests / 60 seconds window.
    """

    def __init__(self, default_limit: int = 100, window_seconds: int = 60):
        self._default_limit = default_limit
        self._window = window_seconds
        self._buckets: dict[str, list[float]] = defaultdict(list)
        self._lock = Lock()

    def _prune(self, key: str, now: float):
        """Remove timestamps outside the current window."""
        cutoff = now - self._window
        q = self._buckets[key]
        while q and q[0] < cutoff:
            q.pop(0)

    def allow(self, key: str, limit: int | None = None) -> bool:
        """Check if *key* (typically IP) is within rate limit.  Returns True if allowed."""
        now = time.time()
        with self._lock:
            self._prune(key, now)
            bucket = self._buckets[key]
            max_requests = limit if limit is not None else self._default_limit
            if len(bucket) >= max_requests:
                return False
            bucket.append(now)
            return True

    def remaining(self, key: str, limit: int | None = None) -> int:
        """Return remaining allowance for *key*."""
        now = time.time()
        with self._lock:
            self._prune(key, now)
            max_requests = limit if limit is not None else self._default_limit
            return max(0, max_requests - len(self._buckets[key]))

    def reset(self, key: str | None = None):
        """Reset counters for *key* (or all keys if None)."""
        with self._lock:
            if key:
                self._buckets.pop(key, None)
            else:
                self._buckets.clear()


# Singleton for cross-module use
_DEFAULT_LIMITER = SlidingWindowRateLimiter()


def get_rate_limiter() -> SlidingWindowRateLimiter:
    return _DEFAULT_LIMITER
