"""
Scheduler Plugin — Periodic task scheduler for automated forget, audit, etc.
"""

import logging
logger = logging.getLogger(__name__)

import json
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional

from memall.config import get_config


# ── Debounce state for on_capture → lightweight pipeline ──
_last_light_pipeline: float = 0  # timestamp of last trigger
_debounce_seconds: int = 60      # don't re-trigger within this window
_pipeline_lock = threading.Lock()


class TaskScheduler:
    """Lightweight periodic task scheduler running in a background thread.

    Tasks are Python callables executed on a fixed interval. The scheduler
    thread polls every second for due tasks.

    Usage:
        sched = TaskScheduler()
        sched.add_task("daily_forget", run_daily_forget, 86400)
        sched.start()
        # ... later ...
        sched.stop()
    """

    def __init__(self) -> None:
        self._tasks: Dict[str, Dict[str, Any]] = {}
        self._thread: Optional[threading.Thread] = None
        self._running: bool = False
        self._lock = threading.Lock()

    def add_task(
        self,
        name: str,
        func: Callable,
        interval_seconds: int,
        run_immediately: bool = False,
    ) -> bool:
        """Register a new periodic task.

        Args:
            name: Unique task name.
            func: Callable to execute. Should accept no arguments.
            interval_seconds: Seconds between executions.
            run_immediately: If True, also run once now (synchronously).

        Returns:
            True if added, False if name already exists.
        """
        with self._lock:
            if name in self._tasks:
                print(f"[Scheduler] Task '{name}' already exists", file=sys.stderr)
                return False

            self._tasks[name] = {
                "func": func,
                "interval": interval_seconds,
                "last_run": None,
                "next_run": datetime.now(timezone.utc)
                + timedelta(seconds=0 if run_immediately else interval_seconds),
                "run_count": 0,
                "error_count": 0,
            }

            if run_immediately:
                self._run_task(name)
                task = self._tasks[name]
                task["next_run"] = datetime.now(timezone.utc) + timedelta(
                    seconds=interval_seconds
                )

            return True

    def remove_task(self, name: str) -> bool:
        """Remove a task by name.

        Returns:
            True if removed, False if not found.
        """
        with self._lock:
            if name in self._tasks:
                del self._tasks[name]
                return True
            return False

    def list_tasks(self) -> List[Dict[str, Any]]:
        """Return a list of all tasks with status information."""
        result: List[Dict[str, Any]] = []
        with self._lock:
            for name, task in self._tasks.items():
                result.append({
                    "name": name,
                    "interval_seconds": task["interval"],
                    "last_run": (
                        task["last_run"].isoformat() if task["last_run"] else None
                    ),
                    "next_run": task["next_run"].isoformat(),
                    "run_count": task["run_count"],
                    "error_count": task["error_count"],
                })
        return result

    def start(self) -> None:
        """Start the scheduler background thread."""
        if self._running:
            return

        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True, name="memall-scheduler")
        self._thread.start()

    def stop(self) -> None:
        """Stop the scheduler thread gracefully."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)

    def _loop(self) -> None:
        """Main scheduler loop — polls every second for due tasks."""
        while self._running:
            now = datetime.now(timezone.utc)

            with self._lock:
                for name, task in list(self._tasks.items()):
                    if task["next_run"] <= now:
                        # Run in separate daemon thread so one slow task
                        # doesn't block the others
                        t = threading.Thread(
                            target=self._run_task,
                            args=(name,),
                            daemon=True,
                        )
                        t.start()

            time.sleep(1)

    def _run_task(self, name: str) -> None:
        """Execute a single task and update its metadata."""
        with self._lock:
            if name not in self._tasks:
                return
            task = self._tasks[name]

        try:
            task["func"]()
            task["run_count"] += 1
        except Exception as e:
            task["error_count"] += 1
            print(f"[Scheduler] Task '{name}' failed: {e}", file=sys.stderr)
        finally:
            task["last_run"] = datetime.now(timezone.utc)
            task["next_run"] = datetime.now(timezone.utc) + timedelta(
                seconds=task["interval"]
            )


# ── Built-in default tasks ─────────────────────────────────────────────

def _daily_forget() -> None:
    """Run low-value memory cleanup (built-in daily task)."""
    try:
        from memall.pipeline.forget import forget_low_value

        result = forget_low_value()
        print(
            f"[Scheduler] Daily forget: {result.get('deleted_memories', 0)} removed"
        )
    except ImportError:
        logger.warning("scheduler.py: silent error", exc_info=True)
    except Exception as e:
        print(f"[Scheduler] Daily forget error: {e}", file=sys.stderr)


def _daily_security_audit() -> None:
    """Run security audit (built-in daily task)."""
    try:
        from memall.pipeline.security import audit_sensitive

        result = audit_sensitive()
        count = result.get("total_findings", 0)
        if count > 0:
            print(
                f"[Scheduler] Daily audit: {count} sensitive findings "
                f"(risk={result.get('risk_level', '?')})"
            )
    except ImportError:
        logger.warning("scheduler.py: silent error", exc_info=True)
    except Exception as e:
        print(f"[Scheduler] Daily audit error: {e}", file=sys.stderr)


def create_default_scheduler() -> TaskScheduler:
    """Create a TaskScheduler pre-loaded with built-in daily tasks.

    Intervals are read from config (``scheduler.forget_interval`` and
    ``scheduler.audit_interval``) with a default of 86400 seconds (24h).

    Returns:
        Configured TaskScheduler (not yet started).
    """
    forget_interval = get_config("scheduler.forget_interval", 86400)
    audit_interval = get_config("scheduler.audit_interval", 86400)
    sched = TaskScheduler()
    sched.add_task("daily_forget", _daily_forget, forget_interval, run_immediately=False)
    sched.add_task("daily_security", _daily_security_audit, audit_interval, run_immediately=False)
    return sched


def on_pipeline(**kwargs) -> None:
    """Log pipeline completion with step-level detail."""
    status = kwargs.get("status", "?")
    elapsed = kwargs.get("elapsed", 0)
    results = kwargs.get("results", {})

    # Count step outcomes
    oks = sum(1 for k, v in results.items()
              if k not in ("metrics", "discipline") and isinstance(v, int) and v > 0)
    fails = sum(1 for k, v in results.items()
                if k not in ("metrics", "discipline") and isinstance(v, dict) and v.get("status") == "failed")

    if oks or fails:
        logger.info(
            "Pipeline %s: %.1fs, %d steps ok, %d failed, %d results",
            status, elapsed, oks, fails, len(results),
        )
    else:
        logger.info("Pipeline %s finished in %.1fs", status, elapsed)


def _check_capture_discussion(memory_id: int) -> dict | None:
    """Check if a newly captured memory relates to a pending discussion.

    Looks at the memory's ``supersedes`` field and metadata for discussion
    references.  If it finds an active discussion where all participants
    have now responded, auto-converge it.

    Returns:
        Dict with convergence result, or None if no action taken.
    """
    try:
        from memall.core.db import get_conn
        from memall.pipeline.convergence import converge_discussion

        conn = get_conn()
        try:
            row = conn.execute(
                "SELECT supersedes, metadata FROM memories WHERE id = ?",
                (memory_id,),
            ).fetchone()
            if not row:
                return None

            # Check supersedes: does this memory reference a discussion?
            supersedes = row["supersedes"]
            if not supersedes:
                return None

            # supersedes could be an int (direct discussion id) or a JSON list
            discussion_ids: list[int] = []
            if isinstance(supersedes, int):
                discussion_ids = [supersedes]
            elif isinstance(supersedes, str) and supersedes.strip():
                try:
                    parsed = json.loads(supersedes)
                    if isinstance(parsed, list):
                        discussion_ids = [int(x) for x in parsed if str(x).isdigit()]
                    else:
                        discussion_ids = [int(parsed)]
                except (json.JSONDecodeError, ValueError):
                    # Try as single int string
                    if supersedes.strip().isdigit():
                        discussion_ids = [int(supersedes)]

            if not discussion_ids:
                return None

            for disc_id in discussion_ids:
                disc = conn.execute(
                    "SELECT * FROM memories WHERE id = ? AND level = 'L5' AND category = 'discussion'",
                    (disc_id,),
                ).fetchone()
                if not disc:
                    continue

                meta = json.loads(disc.get("metadata") or "{}")
                if meta.get("status") != "active":
                    continue

                # Check participants
                participants = []
                raw = meta.get("participants", meta.get("_participants", []))
                if isinstance(raw, dict) and "value" in raw:
                    raw = raw["value"]
                if isinstance(raw, list):
                    participants = raw

                if not participants:
                    # No participants → converge immediately
                    responses = conn.execute(
                        "SELECT * FROM memories WHERE id IN ("
                        "  SELECT target_id FROM edges WHERE source_id = ? AND relation_type = 'cites'"
                        ") AND level = 'P2' ORDER BY created_at ASC LIMIT 1000",
                        (disc_id,),
                    ).fetchall()
                    result = converge_discussion(conn, dict(disc), [dict(r) for r in responses],
                                                  "Capture-triggered convergence (no participants)")
                    conn.commit()
                    logger.info("on_capture: auto-converged discussion #%d (no participants)", disc_id)
                    return result

                # Count responded agents (including this new memory's agent)
                responded = set()
                responses = conn.execute(
                    "SELECT * FROM memories WHERE id IN ("
                    "  SELECT target_id FROM edges WHERE source_id = ? AND relation_type = 'cites'"
                    ") AND level = 'P2' ORDER BY created_at ASC LIMIT 1000",
                    (disc_id,),
                ).fetchall()
                for r in responses:
                    rmeta = json.loads(r.get("metadata") or "{}")
                    agent = rmeta.get("agent_name", r.get("agent_name", ""))
                    if agent:
                        responded.add(agent)

                # Check if this memory itself is an implicit response
                mem_meta = json.loads(row.get("metadata") or "{}")
                mem_agent = mem_meta.get("agent_name", "")
                if mem_agent:
                    responded.add(mem_agent)

                missing = [p for p in participants if p not in responded]
                if not missing:
                    result = converge_discussion(conn, dict(disc), [dict(r) for r in responses],
                                                  "Capture-triggered convergence: all participants responded")
                    conn.commit()
                    logger.info("on_capture: auto-converged discussion #%d (all %d participants responded)",
                                disc_id, len(participants))
                    return result

            return None
        finally:
            conn.close()
    except Exception:
        logger.warning("on_capture discussion check failed", exc_info=True)
        return None


def on_capture(**kwargs) -> None:
    """Triggered after each memory capture.

    Does two things (both non-blocking, in background threads):
      1. Lightweight pipeline: debounce-coalesced, runs fast steps
      2. Discussion check: auto-converge if all participants responded
    """
    memory_id = kwargs.get("memory_id")
    if not memory_id:
        return

    # ── 1. Discussion auto-convergence (inline, fast DB check) ──
    if isinstance(memory_id, int):
        threading.Thread(
            target=_check_capture_discussion,
            args=(memory_id,),
            daemon=True,
        ).start()

    # ── 2. Lightweight pipeline (debounced background thread) ──
    global _last_light_pipeline
    now = time.time()
    with _pipeline_lock:
        if now - _last_light_pipeline < _debounce_seconds:
            _record_plugin_event(
                "on_capture", "Lightweight pipeline skipped (debounce)", memory_id=memory_id,
            )
            return
        _last_light_pipeline = now

    _record_plugin_event(
        "on_capture", "Lightweight pipeline triggered (debounce-coalesced)", memory_id=memory_id,
    )
    threading.Thread(target=_run_capture_pipeline, daemon=True).start()


def on_pre_retrieve(**kwargs) -> None:
    """Triggered before each retrieve() call.

    Checks for pending discussions and tasks for the querying agent (``viewer``)
    and creates P2 reminder memories so they appear in retrieve results.
    This makes the system proactively surface pending work whenever an agent
    reads memories — no separate timer needed.
    """
    viewer = kwargs.get("viewer") or kwargs.get("agent_name", "")
    if not viewer:
        return

    try:
        from memall.pipeline.convergence import check_pending_discussions
        from memall.scheduler.agent_round import notify_pending_tasks

        # Create P2 reminders for pending discussions
        disc_reminders = check_pending_discussions(viewer)
        if disc_reminders:
            logger.info(
                "on_pre_retrieve: %d discussion reminders for agent '%s'",
                len(disc_reminders), viewer,
            )
            _record_plugin_event(
                "on_pre_retrieve",
                f"Created {len(disc_reminders)} discussion reminder(s) for {viewer}",
            )

        # Create P2 reminders for pending tasks
        task_reminders = notify_pending_tasks(viewer)
        if task_reminders:
            logger.info(
                "on_pre_retrieve: %d task reminders for agent '%s'",
                len(task_reminders), viewer,
            )
            _record_plugin_event(
                "on_pre_retrieve",
                f"Created {len(task_reminders)} task reminder(s) for {viewer}",
            )

        if not disc_reminders and not task_reminders:
            _record_plugin_event(
                "on_pre_retrieve",
                f"No pending reminders for {viewer}",
            )
    except Exception:
        logger.debug("on_pre_retrieve check failed (non-fatal)", exc_info=True)


def _run_capture_pipeline() -> None:
    """Run lightweight pipeline in background, log results."""
    t0 = time.time()
    try:
        from memall.pipeline.pipeline import run_lightweight_pipeline

        result = run_lightweight_pipeline()
        status = result.get("status", "?")
        elapsed = result.get("elapsed", 0)
        logger.info("on_capture: lightweight pipeline %s in %.2fs", status, elapsed)

        step_counts = {}
        if status == "ok":
            for step_name, step_result in result.get("results", {}).items():
                if isinstance(step_result, int):
                    step_counts[step_name] = step_result
                elif isinstance(step_result, dict):
                    step_counts[step_name] = step_result.get("processed", 0)
            if step_counts:
                logger.info("on_capture: pipeline step results: %s", step_counts)

        # Record event
        oks = sum(1 for v in step_counts.values() if v > 0)
        total = sum(step_counts.values())
        _record_plugin_event(
            "on_capture",
            f"Lightweight pipeline {status} ({elapsed:.1f}s, {oks} steps, {total} items)",
        )
    except Exception:
        logger.warning("on_capture lightweight pipeline failed", exc_info=True)
        _record_plugin_event("on_capture", "Lightweight pipeline failed", status="failed")


def register():
    """Return plugin metadata."""
    return {
        "name": "scheduler",
        "version": "1.0.0",
        "description": "Periodic task scheduler for automated forget and security audit",
        "author": "MemALL",
    }


# ── Hook activity recording helper ───────────────────────────────────────

def _record_plugin_event(hook_point: str, description: str, status: str = "ok", memory_id: int | None = None) -> None:
    """Record a scheduler plugin event into the hook effects ring buffer."""
    try:
        from memall.mcp.hook_effects import record_event as _re
        _re(hook_point=hook_point, description=description, plugin="scheduler", status=status, memory_id=memory_id)
    except Exception:
        pass