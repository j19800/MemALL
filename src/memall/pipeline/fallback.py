"""MCP-process fallback timer: the self-evolution steps (L9/L10).

Audit P0-1: the capture-triggered *lightweight* pipeline explicitly skips
distill and integrate (``run_lightweight_pipeline`` passes
``include_distill=False, include_integrate=False``) because they are
expensive.  The only periodic full-pipeline run lives in the daemon
(``scheduler/scheduler.py::_tick``, every 6h).  A deployment that only runs
``memall serve`` — the normal MCP client setup — therefore never produced
an L9 (distilled knowledge) or L10 (cross-domain integration) memory: the
11-layer model silently stopped at L7.

This module adds a low-frequency watchdog inside the MCP process:

  * every ``interval`` seconds (default 300s) it checks for work;
  * "work" = unconsumed ``pipeline_events`` (memories written but not yet
    processed) or L9/L10 input rows that have not been distilled;
  * it runs distill + integrate ONLY then, so an idle store costs nothing;
  * it never runs concurrently with the daemon: the claim is recorded in
    ``pipeline_runs`` with the current pid/host, and a run already in
    progress (or one started recently by another process) blocks the
    claim.

The watchdog is a daemon thread; it stops with the process.
"""
import logging
import os
import socket
import threading
import time
from typing import Optional

logger = logging.getLogger("memall.pipeline.fallback")


def _host() -> str:
    try:
        return socket.gethostname()
    except Exception:
        return "unknown"


def pending_event_count() -> int:
    try:
        from memall.core.db import get_conn
        conn = get_conn()
        try:
            return conn.execute(
                "SELECT COUNT(*) FROM pipeline_events WHERE processed_at IS NULL"
            ).fetchone()[0]
        finally:
            conn.close()
    except Exception:
        logger.debug("fallback: pending count unavailable", exc_info=True)
        return 0


def has_distillable_input() -> bool:
    """Are there rows that would produce new L9/L10 memories?

    L9 comes from L4/L5 (knowledge) rows, L10 from L9 rows that span
    several categories.  Counting them is cheap and avoids running the
    expensive steps on an idle store.
    """
    try:
        from memall.core.db import get_conn
        conn = get_conn()
        try:
            l4 = conn.execute(
                "SELECT COUNT(*) FROM memories WHERE level IN ('L4','L5') "
                "AND stream = 'knowledge' "
                "AND COALESCE(memory_status, '') != 'archived' "
                "AND COALESCE(metadata, '') NOT LIKE '%\"source\": \"ask\"%' "
                "AND COALESCE(metadata, '') NOT LIKE '%\"source\": \"reflect\"%' "
                "AND COALESCE(metadata, '') NOT LIKE '%\"source\": \"discussion\"%'"
            ).fetchone()[0]
            l9 = conn.execute(
                "SELECT COUNT(*) FROM memories WHERE level = 'L9' "
                "AND COALESCE(memory_status, '') != 'archived'"
            ).fetchone()[0]
            return bool(l4 or l9)
        finally:
            conn.close()
    except Exception:
        logger.debug("fallback: distillable check unavailable", exc_info=True)
        return False


def claim_run(min_age_seconds: int = 900) -> bool:
    """Try to claim the right to run the fallback.

    Refuses when another pipeline run is currently in progress, or when one
    finished less than *min_age_seconds* ago (the daemon's own 6h run
    covers the same work; running both would duplicate L9/L10 output).
    The claim is written as a ``pipeline_runs`` row so it is visible in the
    history like any other run.
    """
    try:
        from memall.core.db import get_conn
        from memall.pipeline.pipeline import _create_pipeline_run
    except Exception:
        return False
    conn = None
    try:
        conn = get_conn()
        now = time.time()
        # A run in progress (any process, any host) blocks us.
        running = conn.execute(
            "SELECT COUNT(*) FROM pipeline_runs WHERE status = 'running'"
        ).fetchone()[0]
        if running:
            logger.debug("fallback: a pipeline run is in progress — skipping")
            return False
        # A recent run (daemon tick) already covered the work.
        recent = conn.execute(
            "SELECT COUNT(*) FROM pipeline_runs WHERE status IN ('ok','partial') "
            "AND started_at > datetime('now', ?)",
            (f"-{int(min_age_seconds)} seconds",),
        ).fetchone()[0]
        if recent:
            logger.debug("fallback: a pipeline ran %ds ago — skipping",
                         min_age_seconds)
            return False
        conn.close()
        conn = None
        _create_pipeline_run()
        return True
    except Exception:
        logger.debug("fallback: claim failed", exc_info=True)
        return False
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def run_fallback_once(force: bool = False) -> dict:
    """Run distill + integrate if there is work. Returns a small report."""
    events = pending_event_count()
    distillable = has_distillable_input()
    if not force and not events and not distillable:
        return {"ran": False, "reason": "no work", "events": events}
    if not claim_run():
        return {"ran": False, "reason": "not claimed", "events": events}
    try:
        from memall.pipeline.pipeline import run_pipeline
        result = run_pipeline(
            include_distill=True,
            include_integrate=True,
            include_persona=False,
            include_cluster=False,
            include_narrative=False,
            include_suggest=False,
            include_bridge=False,
            include_improve=False,
            include_embed_index=False,
            include_archive=False,
        )
        logger.info(
            "fallback: distill+integrate done (%s, %.1fs)",
            result.get("status"), result.get("elapsed", 0),
        )
        return {"ran": True, "status": result.get("status"),
                "events": events, "elapsed": result.get("elapsed")}
    except Exception as e:
        logger.warning("fallback: distill+integrate failed: %s", e)
        return {"ran": False, "reason": str(e)[:120], "events": events}


class FallbackTimer:
    """Low-frequency watchdog thread for the MCP process."""

    def __init__(self, interval: int = 300):
        self.interval = max(60, int(interval))
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self.last_result: dict = {}

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop, name="memall-fallback-timer", daemon=True)
        self._thread.start()
        logger.info("fallback timer started (every %ds)", self.interval)

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        while not self._stop.is_set():
            # Sleep first: the process has just started; the daemon (when
            # present) may be about to run its own cycle.
            if self._stop.wait(self.interval):
                break
            try:
                self.last_result = run_fallback_once()
            except Exception:
                logger.debug("fallback tick failed", exc_info=True)


_timer: Optional[FallbackTimer] = None


def start_fallback_timer(interval: int = 300) -> FallbackTimer:
    """Start (once) the in-process fallback timer."""
    global _timer
    if _timer is None:
        _timer = FallbackTimer(interval=interval)
    _timer.start()
    return _timer


def stop_fallback_timer() -> None:
    if _timer is not None:
        _timer.stop()


def backlog_report() -> dict:
    """Diagnostics for the daily report: is work piling up unnoticed?"""
    events = pending_event_count()
    threshold = 500
    return {
        "unconsumed_events": events,
        "threshold": threshold,
        "warning": events > threshold,
        "message": (
            f"{events} unconsumed pipeline events (>{threshold}) — the "
            "lightweight pipeline is not keeping up"
            if events > threshold else
            f"{events} unconsumed pipeline events"
        ),
    }
