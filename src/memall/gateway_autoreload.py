"""Gateway auto-reload (hot-reload style) — file-watcher + in-process exec restart.

Design (2026-11-12): in-process module reloading (importlib.reload) is unsafe
for MemALL because of module-level state (connection pools, HookRegistry,
locks, lazily-cached failed imports). The reliable equivalent is a supervisor
pattern: watch ``src/memall/**/*.py`` and, when a change settles, replace the
process image in place with ``os.execv`` — same PID, fresh interpreter, no
stale half-loaded modules (the exact failure mode that took /mcp down earlier).

Safety rails:
  * OFF by default. Enable with env ``MEMALL_AUTORELOAD=1`` or config
    ``gateway.autoreload.enabled: true`` (dev deployments only: an in-flight
    request can be cut when a restart lands).
  * Debounce: the changed-file set must be STABLE for ``stable_seconds``
    before restarting (editors write files in multiple touches).
  * Syntax guard: changed files must all py_compile before we restart, so a
    half-written file cannot crash-loop the gateway. Files that fail are
    logged once and we wait for the next save.
"""
from __future__ import annotations

import logging
import os
import sys
import threading
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional

log = logging.getLogger(__name__)

_SKIP_PARTS = {"__pycache__", ".venv", "node_modules", ".git"}


def autoreload_enabled() -> bool:
    """Env gate first (startup-time, no config dependency), config as fallback."""
    v = os.environ.get("MEMALL_AUTORELOAD", "").strip().lower()
    if v in ("1", "true", "yes", "on"):
        return True
    if v in ("0", "false", "no", "off"):
        return False
    try:
        from memall.config import get_config
        return bool(get_config("gateway.autoreload.enabled", False))
    except Exception:
        return False


def snapshot_py(root: Path) -> Dict[str, int]:
    """Map of every .py file path -> mtime_ns under root, skipping junk dirs."""
    out: Dict[str, int] = {}
    try:
        for p in Path(root).rglob("*.py"):
            if _SKIP_PARTS.intersection(p.parts):
                continue
            try:
                out[str(p)] = p.stat().st_mtime_ns
            except OSError:
                pass
    except OSError:
        pass
    return out


def diff_changes(before: Dict[str, int], after: Dict[str, int]) -> Dict[str, Optional[int]]:
    """Changed/new/deleted paths. Value = new mtime_ns, or None if deleted."""
    out: Dict[str, Optional[int]] = {}
    for k in set(before) | set(after):
        b, a = before.get(k), after.get(k)
        if b != a:
            out[k] = a
    return out


def compile_ok(path: str) -> bool:
    """True when the file parses; False on syntax errors or read races.

    Uses the builtin compile() on the source text: no .pyc side effects and no
    temp-file writes next to cfile (which py_compile would attempt).
    """
    try:
        with open(path, "rb") as fh:
            src = fh.read()
        compile(src, path, "exec")
        return True
    except (OSError, SyntaxError, ValueError):
        return False


def _default_restart(changed: List[str]) -> None:
    """Replace the process image in place (keeps PID; supervisor sees no exit).

    Requires a real entry script. When python was started with ``-c`` (inline
    code) there is no script path to re-exec — log and skip instead of
    exec'ing garbage. Run the gateway from a file entrypoint (e.g.
    ``gateway_start.py``) to use auto-reload.
    """
    main = sys.modules.get("__main__")
    script = getattr(main, "__file__", None) if main else None
    if not script or os.path.basename(sys.argv[0] if sys.argv else "") == "-c":
        raise RuntimeError(
            "autoreload: cannot execv-restart a `python -c` inline process; "
            "start the gateway from a script file (gateway_start.py)")
    script = os.path.abspath(script)
    log.info("autoreload: restarting with new code (%d files changed)", len(changed))
    os.execv(sys.executable, [sys.executable, "-u", script, *sys.argv[1:]])


def watch_and_restart(
    root: Path,
    *,
    poll_seconds: float = 2.0,
    stable_seconds: float = 3.0,
    restart_fn: Optional[Callable[[List[str]], None]] = None,
    stop_event: Optional[threading.Event] = None,
) -> threading.Thread:
    """Start the daemon watcher thread. Never raises at start; watcher errors
    are logged and the thread exits (gateway must not die because of dev tooling).
    """
    restart_fn = restart_fn or _default_restart
    stop_event = stop_event or threading.Event()
    # Initial snapshot is taken SYNCHRONOUSLY here (not inside the thread):
    # otherwise a file written by the caller right after this call can land
    # before the thread starts, get absorbed into the first snapshot, and be
    # missed forever (test-visible startup race).
    initial = snapshot_py(root)

    def _loop() -> None:
        try:
            prev = initial
            pending: Dict[str, Optional[int]] = {}
            pending_since = 0.0
            warned: set = set()
            while not stop_event.wait(poll_seconds):
                cur = snapshot_py(root)
                changed = diff_changes(prev, cur)
                prev = cur
                if changed:
                    if changed != pending:
                        pending = changed
                        pending_since = time.monotonic()
                        warned.clear()
                    continue
                if not pending:
                    continue
                if (time.monotonic() - pending_since) < stable_seconds:
                    continue
                to_check = [p for p, mt in pending.items() if mt is not None]
                bad = [p for p in to_check if not compile_ok(p)]
                if bad:
                    for p in bad:
                        if p not in warned:
                            log.warning("autoreload: %s does not compile yet, waiting", p)
                            warned.add(p)
                    pending = {}  # wait for the next save to settle
                    continue
                paths = sorted(pending.keys())
                pending = {}
                try:
                    restart_fn(paths)
                except SystemExit:
                    raise
                except Exception:
                    log.exception("autoreload: restart failed")
                return
        except Exception:
            log.exception("autoreload: watcher thread crashed; auto-reload disabled for this run")

    t = threading.Thread(target=_loop, daemon=True, name="gateway-autoreload")
    t.start()
    return t
