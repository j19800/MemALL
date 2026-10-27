"""Wake a session-based agent so a hand-off actually moves.

The gap this closes: the goal driver can compute "whose turn it is" and leave a
session note, but a note only reaches an agent that happens to make a tool call
later.  With no live session there is no progress — which is why the loop was
"半自动": the prompt was reliable, the wake-up was not.

How it wakes, and why this shape
--------------------------------
An MCP server cannot call an agent: MCP is client-driven, and the Sampled
`sampling/createMessage` capability is opt-in per client (this server does not
advertise it, and most hosts do not implement it).  What a server CAN do is
spawn a headless run of the agent's own CLI.  On this machine that is
``hermes -z "<prompt>"``.

That is a powerful and dangerous ability — it starts a process that can edit
code.  So the defaults are deliberately paranoid:

  * ``agents.wake.enabled`` defaults to **False**.  Nothing spawns until an
    operator turns it on.
  * ``agents.wake.dry_run`` defaults to **True**, so the first enablement logs
    the exact argv it *would* run instead of running it.
  * argv only, never ``shell=True``: the prompt is one list element, so text
    inside a prompt can never become a command.  (A prompt is model-authored
    or discussion-authored text; treating it as a shell string would be a
    remote code execution path.)
  * disabled agents (the write deny-list) are never woken.
  * a per-(agent, goal, action) cooldown and an hourly cap, because a timer
    that ticks every five minutes would otherwise spawn an agent every five
    minutes.
  * a hard timeout with kill, and every attempt recorded, so a runaway wake is
    visible rather than mysterious.

The wake is a *nudge*, not a guarantee: it tells the agent "you have work
waiting".  The agent still goes through its normal MCP tools, so every existing
guard (claim must be addressed to you, producers cannot verify) still applies.
"""
from __future__ import annotations

import json
import logging
import os
import shlex
import subprocess
import threading
import time
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger("memall.pipeline.waker")

_lock = threading.Lock()
_last_wake: dict[str, float] = {}
_wake_log: list[dict] = []
_HOURLY: dict[str, list[float]] = {}


def _cfg(key: str, default):
    try:
        from memall.config import get_config

        return get_config(f"agents.wake.{key}", default)
    except Exception:
        return default


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def enabled() -> bool:
    return bool(_cfg("enabled", False))


def dry_run() -> bool:
    return bool(_cfg("dry_run", True))


def _commands() -> dict:
    cmds = _cfg("commands", {}) or {}
    return cmds if isinstance(cmds, dict) else {}


def _cooldown() -> int:
    try:
        return max(30, int(_cfg("cooldown_seconds", 600)))
    except (TypeError, ValueError):
        return 600


def _max_per_hour() -> int:
    try:
        return max(1, int(_cfg("max_per_hour", 3)))
    except (TypeError, ValueError):
        return 3


def _timeout() -> int:
    try:
        return max(30, int(_cfg("timeout_seconds", 600)))
    except (TypeError, ValueError):
        return 600


def _cwd() -> Optional[str]:
    v = _cfg("cwd", "") or ""
    return v or None


def build_invocation(agent_name: str, prompt: str):
    """(argv, env) for *agent_name*, or (None, None) when not configured.

    ``commands.<agent>`` accepts three shapes:
        "hermes -z {prompt}"                       # string, split once
        ["hermes", "-z", "{prompt}"]                # argv list
        {"argv": [...], "env": {"K": "V"}}          # argv + environment
    The dict form exists because a woken process MUST know which agent it is:
    every downstream guard (claim must be addressed to you, a producer cannot
    verify) keys off identity, and identity comes from MEMALL_AGENT_NAME when
    the process is started outside a handshake.

    The prompt is substituted into ONE element and never shell-parsed.
    """
    spec = _commands().get(agent_name)
    if not spec:
        return None, None
    env: dict = {}
    if isinstance(spec, dict):
        env = {str(k): str(v).replace("{agent}", agent_name)
               for k, v in (spec.get("env") or {}).items()}
        spec = spec.get("argv") or spec.get("command") or []
    if isinstance(spec, str):
        # A plain string is split once, and the placeholder is replaced inside
        # the resulting element — still no shell involved.
        parts = shlex.split(spec, posix=False)
    elif isinstance(spec, (list, tuple)):
        parts = [str(p) for p in spec]
    else:
        return None, None
    marker = "{prompt}"
    used = False
    out: list[str] = []
    for p in parts:
        if marker in p:
            out.append(p.replace(marker, prompt))
            used = True
        else:
            out.append(p)
    if not used:
        out.append(prompt)
    return out, env


def build_argv(agent_name: str, prompt: str) -> Optional[list[str]]:
    """Backwards-compatible wrapper returning only the argv."""
    argv, _ = build_invocation(agent_name, prompt)
    return argv


def _rate_limited(agent_name: str, key: str) -> Optional[str]:
    now = time.time()
    with _lock:
        last = _last_wake.get(key)
        if last and now - last < _cooldown():
            return f"cooldown ({int(_cooldown() - (now - last))}s left)"
        bucket = [t for t in _HOURLY.get(agent_name, []) if now - t < 3600]
        _HOURLY[agent_name] = bucket
        if len(bucket) >= _max_per_hour():
            return f"hourly cap reached ({_max_per_hour()}/h)"
    return None


def _record(entry: dict) -> None:
    with _lock:
        _wake_log.append(entry)
        del _wake_log[:-50]  # keep the last 50 for diagnostics


def wake_agent(agent_name: str, prompt: str, *, goal_id: int = 0,
               action: str = "") -> dict:
    """Try to start a headless run of *agent_name* with *prompt*.

    Returns a dict describing what happened; never raises.
    """
    agent = (agent_name or "").strip()
    body = (prompt or "").strip()
    if not agent or not body:
        return {"woke": False, "reason": "missing agent or prompt"}

    if not enabled():
        return {"woke": False, "reason": "agents.wake.enabled is false"}

    # Never wake an agent the write deny-list has switched off.
    try:
        from memall.core.denylist import is_disabled_agent

        if is_disabled_agent(agent):
            _record({"at": _now_iso(), "agent": agent, "woke": False,
                     "reason": "agent is disabled"})
            return {"woke": False, "reason": "agent is disabled"}
    except Exception:
        pass

    argv, extra_env = build_invocation(agent, body)
    if not argv:
        return {"woke": False, "reason": f"no wake command configured for {agent}"}
    spawn_env = dict(os.environ)
    if extra_env:
        spawn_env.update(extra_env)

    key = f"{agent}|{goal_id}|{action}"
    limited = _rate_limited(agent, key)
    if limited:
        return {"woke": False, "reason": limited}

    if dry_run():
        _record({"at": _now_iso(), "agent": agent, "woke": False,
                 "reason": "dry_run", "argv_len": len(argv)})
        return {"woke": False, "reason": "dry_run", "would_run": argv[:4],
                "env_keys": sorted(extra_env) if extra_env else []}

    started = time.time()
    try:
        proc = subprocess.Popen(
            argv,
            cwd=_cwd(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            shell=False,
            env=spawn_env,
        )
    except Exception as e:
        _record({"at": _now_iso(), "agent": agent, "woke": False,
                 "reason": f"spawn failed: {e}"})
        logger.warning("agent_waker: spawn failed for %s: %s", agent, e)
        return {"woke": False, "reason": f"spawn failed: {e}"}

    with _lock:
        _last_wake[key] = started
        _HOURLY.setdefault(agent, []).append(started)

    def _reap():
        try:
            out, err = proc.communicate(timeout=_timeout())
        except subprocess.TimeoutExpired:
            proc.kill()
            try:
                out, err = proc.communicate(timeout=10)
            except Exception:
                out, err = "", ""
            _record({"at": _now_iso(), "agent": agent, "woke": True,
                     "timeout": True, "pid": proc.pid})
            logger.warning("agent_waker: %s timed out after %ss, killed",
                           agent, _timeout())
            return
        _record({"at": _now_iso(), "agent": agent, "woke": True,
                 "pid": proc.pid, "rc": proc.returncode,
                 "stdout_tail": (out or "")[-400:],
                 "stderr_tail": (err or "")[-400:]})
        logger.info("agent_waker: %s finished rc=%s", agent, proc.returncode)

    threading.Thread(target=_reap, name=f"memall-wake-{agent}",
                     daemon=True).start()
    return {"woke": True, "pid": proc.pid, "agent": agent}


def wake_for_actions(actions: list[dict]) -> dict:
    """Wake the agents named by a goal_tick result."""
    woke, skipped = [], []
    for act in actions or []:
        agent = (act.get("agent") or "").strip()
        if not agent:
            continue
        prompt = (act.get("message") or "").strip()
        if not prompt:
            continue
        # Tell the agent where to look, not just that something happened.
        prompt = (f"{prompt}\n\n"
                  f"请用 MemALL 的 MCP 工具处理：先 memall_read action=goal_status "
                  f"查看目标 #{act.get('goal_id')} 的当前阶段与你的下一步，"
                  f"再按阶段调用对应动作。")
        res = wake_agent(agent, prompt, goal_id=int(act.get("goal_id") or 0),
                         action=str(act.get("action") or ""))
        (woke if res.get("woke") else skipped).append({"agent": agent, **res})
    return {"woke": woke, "skipped": skipped}


def diagnostics() -> dict:
    with _lock:
        return {
            "enabled": enabled(),
            "dry_run": dry_run(),
            "configured_agents": sorted(_commands().keys()),
            "recent": list(_wake_log[-10:]),
        }
