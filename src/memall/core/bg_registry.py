"""Bounded executor for the fire-and-forget background tasks.

Audit P0-3: every capture used to spawn bare threads
(``threading.Thread(...).start()``) for embedding/dream_scan, the
lightweight pipeline and the discussion check — no pool, no cap, no join.
Writing 100 memories in a batch meant 100+ threads, each loading the ONNX
model and contending for the SQLite write lock: occasional
``database is locked`` and a memory spike.  The acceptance criterion is
"peak threads <= resident + 4".

This module is the single place background work is scheduled:

  * a bounded ThreadPoolExecutor (default 3 workers, configurable) — the
    queue absorbs bursts instead of spawning a thread per task;
  * the pool is registered so test harnesses can drain it before switching
    databases (the "no such table" / "database is locked" cross-test
    artifacts came from a still-running thread resolving the global
    connection pool against a freshly created file);
  * a master switch lets suites disable background work entirely.

``spawn()`` keeps the old call-site shape (a function + args) and returns
the Future, so callers do not need to know a pool is involved.
"""
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from typing import List, Optional

_lock = threading.Lock()
_threads: List[threading.Thread] = []

_enabled = True
_executor: Optional[ThreadPoolExecutor] = None
_max_workers = 3
_executor_lock = threading.Lock()


def set_max_workers(n: int) -> None:
    """Set the concurrency cap (a new pool is created on next use)."""
    global _max_workers, _executor
    n = max(1, int(n))
    with _executor_lock:
        if n != _max_workers:
            _max_workers = n
            old, _executor = _executor, None
            if old is not None:
                try:
                    old.shutdown(wait=False)
                except Exception:
                    pass


def max_workers() -> int:
    return _max_workers


def set_enabled(enabled: bool) -> None:
    """Master switch. Test suites disable it so fire-and-forget work is
    never scheduled — scheduling anyway (and only skipping the tracking)
    would leave the task running against a database the suite is about to
    repoint, which is the collision this module exists to prevent.
    """
    global _enabled
    _enabled = bool(enabled)


def _pool() -> ThreadPoolExecutor:
    global _executor
    with _executor_lock:
        if _executor is None:
            _executor = ThreadPoolExecutor(
                max_workers=_max_workers, thread_name_prefix="memall-bg")
        return _executor


def spawn(target, args=(), kwargs=None, name=None) -> "Future | None":
    """Schedule *target(*args) on the bounded pool.

    Returns the Future (or None while disabled).
    """
    if not _enabled:
        return None
    try:
        return _pool().submit(target, *args, **(kwargs or {}))
    except RuntimeError:
        # pool shut down concurrently — rebuild once
        global _executor
        with _executor_lock:
            _executor = None
        return _pool().submit(target, *args, **(kwargs or {}))


def register(thread: threading.Thread) -> threading.Thread:
    """Track an already-built (not yet started) thread and start it.

    Kept for call sites that need a real Thread (the plugin scheduler
    returns the thread to the caller); new code should use spawn().
    """
    if not _enabled:
        return thread
    with _lock:
        _threads[:] = [t for t in _threads if t.is_alive()]
        _threads.append(thread)
    thread.start()
    return thread


def live_count() -> int:
    """Number of background tasks currently running."""
    with _lock:
        threads_alive = sum(1 for t in _threads if t.is_alive())
    running = 0
    try:
        from concurrent.futures import as_completed  # noqa: F401
        pool = _pool()
        for _ in pool._threads:  # type: ignore[attr-defined]
            if _ is not None and _.is_alive():
                running += 1
    except Exception:
        pass
    return threads_alive + running


def wait_for_background(timeout: float = 15.0) -> int:
    """Block until background work finishes or *timeout* seconds elapse.

    Returns the number of still-running tasks on timeout (0 = quiet).
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with _lock:
            _threads[:] = [t for t in _threads if t.is_alive()]
            alive = list(_threads)
        pending = alive
        try:
            pool = _pool()
            for t in list(getattr(pool, "_threads", [])):
                if t is not None and t.is_alive():
                    pending.append(t)
        except Exception:
            pass
        if not pending:
            return 0
        for t in pending:
            t.join(min(0.2, max(0.0, deadline - time.monotonic())))
    return live_count()


def shutdown(wait: bool = False) -> None:
    """Shut the pool down (process teardown / tests)."""
    global _executor
    with _executor_lock:
        pool, _executor = _executor, None
    if pool is not None:
        try:
            pool.shutdown(wait=wait)
        except Exception:
            pass
