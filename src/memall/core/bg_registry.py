"""Registry of fire-and-forget background tasks.

capture() and the plugins spawn daemon threads that outlive the call which
started them (lightweight pipeline, embedding, discussion check).  When a
test switches the active database between calls, a thread still running
from the PREVIOUS call resolves the global connection pool lazily and hits
the half-initialised NEW file — the classic cross-test artifact behind
"no such table: memories" and "database is locked" in check_fixes.

Producers register their threads here; test harnesses call
``wait_for_background()`` before repointing the DB.  A thread started
before the switch is still allowed to finish against the database it
resolved at spawn time — that is exactly why both spawn sites capture
``DB_PATH`` before launching.
"""
import threading
import time
from typing import List

_lock = threading.Lock()
_threads: List[threading.Thread] = []


def set_enabled(enabled: bool) -> None:
    """Master switch. Test suites disable it so fire-and-forget threads are
    never spawned — spawning anyway (and only skipping the tracking) would
    leave the thread running against a database the suite is about to
    repoint, which is the collision this module exists to prevent.
    """
    global _enabled
    _enabled = bool(enabled)


_enabled = True


def spawn(target, args=(), kwargs=None, name=None) -> "threading.Thread | None":
    """Start a daemon thread *target(*args) and track it.

    Returns None (nothing started) while disabled.
    """
    if not _enabled:
        return None
    t = threading.Thread(target=target, args=args, kwargs=kwargs or {},
                         name=name, daemon=True)
    with _lock:
        # opportunistic GC of finished threads
        _threads[:] = [x for x in _threads if x.is_alive()]
        _threads.append(t)
    t.start()
    return t


def register(thread: threading.Thread) -> threading.Thread:
    """Track an already-built (not yet started) thread and start it."""
    if not _enabled:
        return thread
    with _lock:
        _threads[:] = [t for t in _threads if t.is_alive()]
        _threads.append(thread)
    return thread


def live_count() -> int:
    with _lock:
        return sum(1 for t in _threads if t.is_alive())


def wait_for_background(timeout: float = 15.0) -> int:
    """Block until tracked threads finish or *timeout* seconds elapse.

    Returns the number of threads still alive on timeout (0 = quiet).
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with _lock:
            _threads[:] = [t for t in _threads if t.is_alive()]
            alive = list(_threads)
        if not alive:
            return 0
        for t in alive:
            t.join(min(0.2, max(0.0, deadline - time.monotonic())))
    with _lock:
        return sum(1 for t in _threads if t.is_alive())
