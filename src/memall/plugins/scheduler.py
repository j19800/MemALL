import logging
"""
Scheduler Plugin — Periodic task scheduler for automated forget, audit, etc.
"""
logger = logging.getLogger(__name__)


import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional

from memall.config import get_config


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
    """Log pipeline completion for scheduler visibility."""
    status = kwargs.get("status", "?")
    elapsed = kwargs.get("elapsed", 0)
    logger.info("Pipeline %s finished in %.1fs", status, elapsed)


def register():
    """Return plugin metadata."""
    return {
        "name": "scheduler",
        "version": "1.0.0",
        "description": "Periodic task scheduler for automated forget and security audit",
        "author": "MemALL",
    }