"""Per-session "already returned" tracking for search results.

Motivation (borrowed from dsh-meow-memory's ``sessions/<id>.json`` seen-set):
an agent that searches the same topic twice within one session gets the exact
same top-k both times, and the second call teaches it nothing.  Surfacing
*novelty* on repeat queries is what makes repeated retrieval useful instead of
wasteful.

Design — soft demotion, never hard exclusion
--------------------------------------------
meow-memory's hard-won rule (design notes §4): the top hits must NOT be
filtered by seen-ness, because "if you exclude from the start, the most
relevant entries are never retrievable and search drifts further off with
every query".  So:

* the first ``protect_top`` results of every response are kept as-is, seen or
  not — relevance is never sacrificed for novelty;
* results *beyond* that window are stably partitioned unseen-first, so repeat
  queries surface new material lower down;
* nothing is ever removed, only reordered.

Storage
-------
In-process bounded LRU keyed by ``(agent_name, session_id)``, with a TTL —
deliberately not a DB table.  Seen-ness is a *session-scoped* UI/flow concern;
persisting it would grow a table per conversation and leak cross-session
knowledge the agent did not ask for.  A restart simply forgets seen-ness,
which is the correct semantic for a session-local aid.
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict
from typing import Dict, List, Optional, Sequence

_lock = threading.Lock()
# (agent, session) -> (epoch_monotonic, set(memory_ids))
_seen: "OrderedDict[tuple, tuple]" = OrderedDict()

_MAX_SESSIONS = 512


def _ttl_seconds() -> float:
    from memall.config import get_config

    minutes = get_config("search.seen_tracking.session_ttl_minutes", 60)
    try:
        return max(60.0, float(minutes) * 60.0)
    except (TypeError, ValueError):
        return 3600.0


def _enabled() -> bool:
    from memall.config import get_config

    return bool(get_config("search.seen_tracking.enabled", False))


def _protect_top() -> int:
    from memall.config import get_config

    try:
        return max(0, int(get_config("search.seen_tracking.protect_top", 5)))
    except (TypeError, ValueError):
        return 5


def _key(agent_name: str, session_id: str) -> tuple:
    return ((agent_name or "").strip().lower(), (session_id or "").strip())


def _evict_locked(now: float) -> None:
    """Drop expired entries, then oldest-beyond-cap. Caller holds _lock."""
    ttl = _ttl_seconds()
    for k in [k for k, (ts, _) in _seen.items() if now - ts > ttl]:
        _seen.pop(k, None)
    while len(_seen) > _MAX_SESSIONS:
        _seen.popitem(last=False)


def record(agent_name: str, session_id: str, memory_ids: Sequence) -> None:
    """Mark *memory_ids* as returned to this session."""
    if not session_id or not memory_ids:
        return
    key = _key(agent_name, session_id)
    now = time.monotonic()
    with _lock:
        entry = _seen.get(key)
        ids = set(entry[1]) if entry else set()
        for mid in memory_ids:
            try:
                ids.add(int(mid))
            except (TypeError, ValueError):
                continue
        _seen[key] = (now, ids)
        _seen.move_to_end(key)
        _evict_locked(now)


def seen_ids(agent_name: str, session_id: str) -> set:
    """Memory ids already returned to this session (empty when unknown/expired)."""
    if not session_id:
        return set()
    key = _key(agent_name, session_id)
    now = time.monotonic()
    with _lock:
        entry = _seen.get(key)
        if not entry:
            return set()
        ts, ids = entry
        if now - ts > _ttl_seconds():
            _seen.pop(key, None)
            return set()
        _seen.move_to_end(key)
        return set(ids)


def reorder_novelty(
    items: List[dict],
    agent_name: str,
    session_id: str,
    id_field: str = "memory_id",
) -> List[dict]:
    """Stably partition *items* unseen-first, protecting the top slice.

    Returns the list unchanged when tracking is off, no session is given, or
    nothing has been seen yet.  The first ``protect_top`` positions are never
    reordered — that is the anti-drift rule from the module docstring.
    """
    if not _enabled() or not session_id or len(items) <= 1:
        return items
    seen = seen_ids(agent_name, session_id)
    if not seen:
        return items

    protect = _protect_top()
    head, tail = items[:protect], items[protect:]
    novel = [it for it in tail if it.get(id_field) not in seen]
    old = [it for it in tail if it.get(id_field) in seen]
    return head + novel + old


def reset(agent_name: str = "", session_id: str = "") -> int:
    """Clear seen state (all, or one session). Returns entries removed."""
    with _lock:
        if not session_id:
            n = len(_seen)
            _seen.clear()
            return n
        keys = [k for k in _seen if k == _key(agent_name, session_id)]
        for k in keys:
            _seen.pop(k, None)
        return len(keys)


__all__ = ["record", "seen_ids", "reorder_novelty", "reset"]