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
        # A recent run (daemon tick) already covered the work.  Compare in
        # Python against a UTC ISO cutoff: started_at rows mix the canonical
        # ISO format with SQLite's datetime() output, and comparing them
        # lexicographically in SQL produced false positives (every row
        # looked "recent", so the fallback never claimed).
        if min_age_seconds > 0:
            from datetime import datetime, timedelta, timezone

            cutoff = (datetime.now(timezone.utc)
                      - timedelta(seconds=int(min_age_seconds))).isoformat()
            recent = conn.execute(
                "SELECT COUNT(*) FROM pipeline_runs "
                "WHERE status IN ('ok','partial','completed') "
                "AND started_at > ?", (cutoff,)
            ).fetchone()[0]
            if recent:
                logger.debug(
                    "fallback: a pipeline ran within %ds — skipping",
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


def deliver_discussion_prompts() -> dict:
    """Tell discussion participants to respond, from the RESIDENT tick.

    ``check_pending_discussions`` only ever ran inside ``agent_round()``, whose
    only caller is ``scheduler/scheduler.py`` — and the scheduler process is not
    running.  So a goal could reach ``deliberating`` and then stall forever: the
    owner was told to open the discussion (that path is resident), but the
    participants were never told they owed a stance, so nothing converged and
    ``finalize`` kept refusing.

    Same delivery channel as the goal prompts: a session note keyed by
    discussion+agent, which the participant receives on its next tool call.
    """
    delivered, actions = 0, []
    try:
        from memall.core.db import pool_conn
        from memall.core.denylist import is_disabled_agent
        from memall.mcp.shared import push_session_note
        from memall.pipeline.convergence import check_pending_discussions

        with pool_conn() as conn:
            rows = conn.execute(
                "SELECT agent_name FROM identities WHERE status = 'active'"
            ).fetchall()
        for row in rows:
            agent = row["agent_name"]
            if not agent or is_disabled_agent(agent):
                continue
            try:
                pending = check_pending_discussions(agent) or []
            except Exception:
                logger.debug("discussion check failed for %s", agent, exc_info=True)
                continue
            for item in pending:
                did = item.get("discussion_id")
                subj = (item.get("subject") or "")[:80]
                msg = (f"[讨论 #{did}] 《{subj}》你还没有表态。"
                       f"请调用 memall_discussion action=respond，"
                       f"discussion_id={did}，stance=agree|disagree|abstain，"
                       f"arguments 写你的理由；表态后本次提醒自动失效。")
                try:
                    if push_session_note(agent, msg,
                                         dedup_key=f"discussion:{did}:{agent}"):
                        delivered += 1
                        actions.append({"agent": agent, "discussion_id": did})
                except Exception:
                    logger.debug("discussion note failed for %s", agent,
                                 exc_info=True)
    except Exception:
        logger.debug("deliver_discussion_prompts failed", exc_info=True)
    return {"delivered": delivered, "actions": actions}


def deliver_goal_prompts() -> dict:
    """Drive the goal lifecycle: tell each agent the ONE next thing it must do.

    This is what makes collaboration automatic for session-based agents.  They
    are not resident, so the system cannot call them; instead the driver
    computes whose turn it is and leaves a session note, which the agent
    receives on its next tool call (memall/mcp/shared + adapter injection).
    Idempotent: the note is keyed by goal+action, so a repeated tick with no
    state change does not pile up duplicates.
    """
    try:
        from memall.core.db import get_conn
        from memall.pipeline.goals import goal_tick

        tick = goal_tick()
    except Exception:
        logger.debug("goal tick failed", exc_info=True)
        return {"delivered": 0, "error": "tick failed"}

    delivered = 0
    try:
        from memall.mcp.shared import push_session_note
    except Exception:
        return {"delivered": 0, "actions": tick.get("actions", []),
                "note_api": "unavailable"}

    pending_prompts = []
    for act in tick.get("actions", []):
        agent = (act.get("agent") or "").strip()
        if not agent:
            continue
        msg = act.get("message") or ""
        if not msg:
            continue
        try:
            # keyed delivery: the same goal+action is not re-queued while the
            # previous prompt is still pending
            key = f"goal:{act.get('goal_id')}:{act.get('action')}"
            if push_session_note(agent, msg, dedup_key=key):
                delivered += 1
                pending_prompts.append(act)
        except Exception:
            logger.debug("goal prompt delivery failed for %s", agent, exc_info=True)

    # A note only reaches an agent that makes a tool call LATER.  With no live
    # session nothing moves, which is what made the loop "半自动".  Try to wake
    # the agent's own CLI so the hand-off actually advances.  Off by default
    # (agents.wake.enabled=false) and dry-run by default: this starts a process
    # that can edit code.
    woke = {"woke": [], "skipped": []}
    if pending_prompts:
        try:
            from memall.pipeline.agent_waker import wake_for_actions

            woke = wake_for_actions(pending_prompts)
        except Exception:
            logger.debug("agent wake failed", exc_info=True)

    return {"delivered": delivered, "goals": tick.get("goals", 0),
            "actions": tick.get("actions", []), "wake": woke}


def run_fallback_once(force: bool = False) -> dict:
    """Run distill + integrate if there is work. Returns a small report."""
    # The goal driver runs on EVERY tick regardless of distillation work:
    # a pending hand-off is never "no work".
    goal_result = deliver_goal_prompts()
    # Participants owe a stance for the same reason the owner owes an
    # action: the resident tick is the only delivery path that runs.
    discussion_result = deliver_discussion_prompts()

    events = pending_event_count()
    distillable = has_distillable_input()
    if not force and not events and not distillable:
        return {"ran": False, "reason": "no work", "events": events,
                "goals": goal_result, "discussions": discussion_result}
    # ``force`` also bypasses the "a pipeline ran recently" guard (used by
    # the acceptance check and by manual ops); it still never runs
    # concurrently with another live run.
    if not claim_run(min_age_seconds=0 if force else 900):
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
