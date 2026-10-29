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
  * the spawn decision is CLAIMED in the ``wake_claims`` DB table first
    (migration 040).  Every stdio MCP client session spawns its own server
    process with its own fallback timer, so the in-process cooldown cannot
    coordinate them: with N live sessions the same (agent, goal, action) was
    spawnable once per process.  The DB claim is a single fact both
    processes agree on — the second one gets "claimed" and skips.  A DB
    error is fail-closed: it must never become "spawn anyway", because two
    concurrent wakes writing the same store is the exact hazard the claim
    exists to prevent.
  * a hard timeout with kill, and every attempt recorded, so a runaway wake is
    visible rather than mysterious.

The wake is a *nudge*, not a guarantee: it tells the agent "you have work
waiting".  The agent still goes through its normal MCP tools, so every existing
guard (claim must be addressed to you, producers cannot verify) still applies.

A wake command must be THAT AGENT'S OWN runtime
-----------------------------------------------
Configuring one CLI under several agent names and stamping
``MEMALL_AGENT_NAME=<someone else>`` on it does not dispatch work to that agent —
it manufactures an impostor that writes under the agent's name while running in
a fresh throwaway session.  That is the opposite of handing work to a colleague:
the real agent never sees it, its context is not used, and the store records
authorship that never happened.

So a command block must declare ``runtime_agent`` and it must equal the agent
being woken.  A mismatch is refused, not "fixed up".  Agents without their own
entrypoint (a GUI app, a human-driven CLI) are never woken at all: their work is
delivered as a task card plus a directed session note, which their own session
surfaces when it runs.
"""
from __future__ import annotations

import json
import logging
import os
import shlex
import socket
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
        return None, None, False, None
    env: dict = {}
    stdin_prompt = False
    runtime_agent = None
    if isinstance(spec, dict):
        runtime_agent = spec.get("runtime_agent")
        env = {str(k): str(v).replace("{agent}", agent_name)
               for k, v in (spec.get("env") or {}).items()}
        stdin_prompt = bool(spec.get("stdin_prompt"))
        spec = spec.get("argv") or spec.get("command") or []
    if isinstance(spec, str):
        # A plain string is split once, and the placeholder is replaced inside
        # the resulting element — still no shell involved.
        parts = shlex.split(spec, posix=False)
    elif isinstance(spec, (list, tuple)):
        parts = [str(p) for p in spec]
    else:
        return None, None, False
    marker = "{prompt}"
    used = False
    out: list[str] = []
    for p in parts:
        if marker in p:
            out.append(p.replace(marker, prompt))
            used = True
        else:
            out.append(p)
    if not used and not stdin_prompt:
        out.append(prompt)
    return out, env, stdin_prompt, runtime_agent


def build_argv(agent_name: str, prompt: str) -> Optional[list[str]]:
    """Backwards-compatible wrapper returning only the argv."""
    argv, _, _, _ = build_invocation(agent_name, prompt)
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


def _host() -> str:
    try:
        return socket.gethostname()
    except Exception:
        return ""


def _db_claim(agent: str, goal_id: int, action: str,
              cooldown: int) -> tuple[bool, str]:
    """Atomically claim the right to spawn for (agent, goal_id, action).

    Why a DB row (P1-3, memory #1000942): every stdio MCP client session
    spawns its own server process, each with its own FallbackTimer ->
    goal_tick -> wake_agent.  ``_last_wake`` cannot see across processes, so
    the same decision was spawnable once per live session.  The claim is one
    INSERT ... ON CONFLICT DO UPDATE ... WHERE expired: the row is created if
    absent, refreshed if the previous claim is older than the cooldown, and
    left untouched (nothing changed) when another process still holds it
    inside the cooldown window.  One statement, so two racing processes
    cannot both win — SQLite serialises writes and only the winner sees its
    ``total_changes`` advance.

    Returns (claimed, reason); ``claimed=False`` means DO NOT SPAWN.  A
    database error also returns False — fail-closed on purpose: "couldn't
    verify exclusivity" must never degrade into "spawn anyway".
    """
    now = _now_iso()
    conn = None
    try:
        from memall.core.db import get_conn

        conn = get_conn()
        before = conn.total_changes
        conn.execute(
            """
            INSERT INTO wake_claims (agent, goal_id, action, claimed_at, pid, host)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(agent, goal_id, action) DO UPDATE
            SET claimed_at = excluded.claimed_at, pid = excluded.pid,
                host = excluded.host
            WHERE strftime('%s', wake_claims.claimed_at)
                  <= strftime('%s', 'now') - ?
            """,
            (agent, int(goal_id), action, now, os.getpid(), _host(),
             int(cooldown)),
        )
        claimed = conn.total_changes > before
        # The claim must be durable BEFORE the spawn starts: an uncommitted
        # claim is invisible to the other process it exists to stop.
        conn.commit()
        if claimed:
            # Housekeeping: claims older than a day are dead weight.  Every
            # (agent, goal, action) tuple is re-claimed well inside that.
            try:
                conn.execute(
                    "DELETE FROM wake_claims "
                    "WHERE strftime('%s', claimed_at) < strftime('%s', 'now') - 86400"
                )
                conn.commit()
            except Exception:
                logger.debug("agent_waker: claim sweep skipped", exc_info=True)
            return True, ""
        holder = conn.execute(
            "SELECT pid, host FROM wake_claims "
            "WHERE agent=? AND goal_id=? AND action=?",
            (agent, int(goal_id), action),
        ).fetchone()
        who = f"pid {holder[0]}@{holder[1]}" if holder else "another process"
        return False, f"claimed in db by {who} within cooldown"
    except Exception as e:
        return False, f"db claim unavailable, fail-closed ({e})"
    finally:
        try:
            if conn is not None:
                conn.close()
        except Exception:
            pass


def _db_release(agent: str, goal_id: int, action: str) -> bool:
    """Give the claim back when the spawn itself failed.

    The claim protects a spawn decision; if no process was started, holding
    the claim would block every other process (and every later tick of this
    one) for a full cooldown for nothing.  Best effort: on DB error the claim
    simply expires with the cooldown.
    """
    conn = None
    try:
        from memall.core.db import get_conn

        conn = get_conn()
        before = conn.total_changes
        conn.execute(
            "DELETE FROM wake_claims WHERE agent=? AND goal_id=? AND action=?",
            (agent, int(goal_id), action),
        )
        released = conn.total_changes > before
        conn.commit()
        return released
    except Exception:
        return False
    finally:
        try:
            if conn is not None:
                conn.close()
        except Exception:
            pass


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

    argv, extra_env, stdin_prompt, runtime_agent = build_invocation(agent, body)
    if not argv:
        return {"woke": False,
                "reason": (f"no wake command configured for {agent} (work for this "
                           f"agent is delivered as a task card + session note)")}

    # Refuse to manufacture an impostor: a command may only speak for the agent
    # whose runtime it actually is.  Borrowing another agent's CLI and stamping
    # this agent's name on it produces a fresh throwaway session writing under
    # someone else's identity, while the real agent never learns it has work.
    if runtime_agent is not None and str(runtime_agent) != agent:
        return {"woke": False,
                "reason": (f"refusing to wake '{agent}' with a command that "
                           f"declares runtime_agent='{runtime_agent}': waking must "
                           f"use the agent's own entrypoint, not another agent's "
                           f"runtime wearing its name")}
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

    # Cross-process exclusivity: the claim is taken AFTER dry_run (a dry run
    # spawns nothing, so it must not consume the cooldown) and BEFORE Popen
    # (the point of no return).  Fail-closed: claim failure means no spawn.
    claimed, why = _db_claim(agent, goal_id, action, _cooldown())
    if not claimed:
        _record({"at": _now_iso(), "agent": agent, "woke": False,
                 "reason": why})
        return {"woke": False, "reason": why}

    started = time.time()
    try:
        proc = subprocess.Popen(
            argv,
            cwd=_cwd(),
            # stdin_prompt mode feeds the prompt on stdin (hermes
            # --query-file -): nothing touches a shell or even argv, so the
            # prompt text has no way to be reinterpreted.
            stdin=subprocess.PIPE if stdin_prompt else subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            shell=False,
            env=spawn_env,
        )
    except Exception as e:
        # No process started — hand the claim back so another process (or a
        # later tick) may retry instead of waiting out the cooldown for
        # nothing.
        _db_release(agent, goal_id, action)
        _record({"at": _now_iso(), "agent": agent, "woke": False,
                 "reason": f"spawn failed: {e}"})
        logger.warning("agent_waker: spawn failed for %s: %s", agent, e)
        return {"woke": False, "reason": f"spawn failed: {e}"}

    if stdin_prompt:
        try:
            proc.stdin.write(body)
            proc.stdin.close()
        except Exception:
            logger.debug("agent_waker: stdin write failed", exc_info=True)

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
        instr = (act.get("instruction") or "").strip()
        prompt = (f"{prompt}\n\n"
                  f"你现在就以 agent 身份工作，必须完成下面这一件事，完成后停止：\n"
                  f"{instr or '先 memall_read action=goal_status 查看目标当前阶段，再调用对应动作。'}\n\n"
                  f"注意：MemALL 的写动作都挂在 memall_write 上，用 action 字段区分；"
                  f"只改 content 不等于推进阶段。若工具返回 error，按其提示修正重试，"
                  f"不要把未生效的调用当成成功。")
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
