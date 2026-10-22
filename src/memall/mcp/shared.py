import json
import logging
import time
from datetime import datetime, timezone
logger = logging.getLogger(__name__)

# Auto-start cooldown state.  Lives in the DB (not process memory): the MCP
# server is long-lived, but a restart used to reset the cooldown and let
# every agent re-open a session immediately, piling duplicate sessions into
# the sessions table (audit P2-13).  The in-memory map stays as a fallback
# for the case where the DB is not reachable.
_AUTO_START_COOLDOWN = 1800
_AUTO_START_CACHE: dict[str, str] = {}

# Per-agent pending session notes.  A single module-level string used to be
# overwritten whenever a second agent started a session in the same process,
# so one agent's injected context could be delivered to another.  Keyed by
# agent name, each entry is consumed exactly once.
_PENDING_SESSION_NOTES: dict[str, str] = {}
# Unacked notes (audit P2-12): consume_session_note() used to pop the note
# and lose it if the response never reached the client.  A note now stays
# pending until it is explicitly acknowledged, so a timed-out call does not
# permanently drop the session context.
_NOTE_ACKED: set[str] = set()

# Tools/actions that skip intercept logging entirely
_INTERCEPT_SKIP_TOOLS = frozenset()
_INTERCEPT_SKIP_ACTIONS = frozenset({
    "capture", "smart_store", "store_batch", "ping",
    "session_start", "session_end", "onboarding",
})

# Actions that log a simple [query] line
_INTERCEPT_QUERY_ACTIONS = frozenset({
    "retrieve", "search", "vector_search", "hybrid_search",
    "traverse", "timeline", "fed_query", "session_summary",
    "db",
})


def _cooldown_table(conn) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS session_cooldowns ("
        "agent_name TEXT PRIMARY KEY, last_start TEXT NOT NULL)"
    )


def _last_start(agent_name: str) -> str | None:
    """Last auto-start timestamp for an agent, from the DB (fallback: memory)."""
    try:
        from memall.core.db import pool_conn
        with pool_conn() as conn:
            _cooldown_table(conn)
            row = conn.execute(
                "SELECT last_start FROM session_cooldowns WHERE agent_name = ?",
                (agent_name,),
            ).fetchone()
            return row["last_start"] if row else None
    except Exception:
        return _AUTO_START_CACHE.get(agent_name)


def _record_start(agent_name: str, ts: str) -> None:
    _AUTO_START_CACHE[agent_name] = ts
    try:
        from memall.core.db import pool_conn
        with pool_conn() as conn:
            _cooldown_table(conn)
            conn.execute(
                "INSERT INTO session_cooldowns (agent_name, last_start) VALUES (?, ?) "
                "ON CONFLICT(agent_name) DO UPDATE SET last_start = excluded.last_start",
                (agent_name, ts),
            )
            conn.commit()
    except Exception:
        logger.debug("shared.py: cooldown persist failed", exc_info=True)


def ensure_session_started(agent_name: str, auto_inject: bool = True) -> None:
    from memall.pipeline.session import session_start
    global _PENDING_SESSION_NOTES
    now = datetime.now(timezone.utc)
    last = _last_start(agent_name)
    if last:
        try:
            last_dt = datetime.fromisoformat(last)
            if (now - last_dt).total_seconds() < _AUTO_START_COOLDOWN:
                return
        except ValueError:
            pass
    try:
        result = session_start(agent_name=agent_name, auto_inject=auto_inject)
        _record_start(agent_name, now.isoformat())
        if auto_inject:
            injection = result.get("injection_formatted")
            if injection:
                _PENDING_SESSION_NOTES[agent_name] = injection
                _NOTE_ACKED.discard(agent_name)
    except Exception:
        logger.warning("shared.py: silent error", exc_info=True)


def consume_session_note(agent_name: str = "") -> str | None:
    """Return this agent's pending session note WITHOUT consuming it.

    The note stays pending until :func:`ack_session_note` is called, so a
    client that times out mid-response can retry and still receive the
    context (audit P2-12: "consume-and-drop without ACK" lost the session
    note permanently on a timeout).

    The note belongs to ONE agent.  This used to fall back to "the oldest
    pending note" when the caller passed no name, so a call that arrived
    without an identity took another agent's note — and the matching
    :func:`ack_session_note` then dropped it, so the rightful owner never
    received it and the wrong agent received it in full.  That is an
    identity violation (AGENTS.md §三.4 "身份锁定"), not a delivery quirk:
    the injected context carries the other agent's persona, pending tasks and
    reflections.

    An unnamed caller now gets nothing.  A caller that knows who it is
    passes the name and gets exactly its own note.
    """
    if not agent_name:
        logger.warning(
            "shared.py: session note requested without an agent name; "
            "refusing to guess whose note to deliver"
        )
        return None
    return _PENDING_SESSION_NOTES.get(agent_name)


def ack_session_note(agent_name: str = "") -> bool:
    """Acknowledge delivery of a session note (drops it).

    Like :func:`consume_session_note`, this only ever touches the named
    agent's note.  The unnamed "oldest" fallback could drop a note that
    belonged to a different agent than the one being acknowledged.
    """
    if not agent_name:
        logger.warning(
            "shared.py: session note ack without an agent name; "
            "refusing to guess whose note to drop"
        )
        return False
    popped = _PENDING_SESSION_NOTES.pop(agent_name, None) is not None
    _NOTE_ACKED.add(agent_name)
    return popped


def pending_session_notes() -> dict:
    """Diagnostics: notes waiting for acknowledgement."""
    return {k: len(v) for k, v in _PENDING_SESSION_NOTES.items()}


def write_intercept_log(tool_name: str, summary: str):
    try:
        from memall.core.db import pool_conn
        with pool_conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS intercept_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tool_name TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
            """)
            conn.execute(
                "INSERT INTO intercept_logs (tool_name, summary, created_at) VALUES (?, ?, ?)",
                (tool_name, summary, datetime.now(timezone.utc).isoformat()),
            )
    except Exception:
        logger.warning("shared.py: silent error", exc_info=True)


def run_intercept(tool_name: str, arguments: dict):
    # Map consolidated tool names + action → old-style tool name for logging
    action = arguments.get("action", "")
    if action in _INTERCEPT_SKIP_ACTIONS:
        return
    if tool_name in _INTERCEPT_SKIP_TOOLS:
        return

    try:
        content = None
        if tool_name == "memall_read":
            if action in _INTERCEPT_QUERY_ACTIONS:
                query = arguments.get("query", "")
                if query:
                    content = f"[query] {action}: {query[:200]}"
            elif action == "trace":
                mid = arguments.get("memory_id")
                if mid:
                    content = f"[query] trace #{mid}"
            elif action == "traverse":
                nid = arguments.get("node_id")
                depth = arguments.get("depth", 1)
                if nid:
                    content = f"[graph] traverse #{nid} depth={depth}"

        elif tool_name == "memall_write":
            if action == "update":
                mid = arguments.get("memory_id")
                fields = [k for k in arguments if k not in ("memory_id", "metadata", "action")]
                meta = arguments.get("metadata", "")
                if "status" in meta and "done" in meta:
                    fields.append("status->done")
                if mid and fields:
                    content = f"[update] #{mid}: {', '.join(fields)}"
            elif action == "connect":
                src = arguments.get("source_id")
                tgt = arguments.get("target_id")
                rel = arguments.get("relation_type", "related")
                if src and tgt:
                    content = f"[link] #{src} -{rel}-> #{tgt}"
            elif action in ("forget", "ops"):
                sub = arguments.get("sub_action", "")
                if sub:
                    content = f"[manage] {action}: {sub}"

        elif tool_name == "memall_persona":
            if action in ("persona", "persona_profile"):
                agent = arguments.get("agent_name", "")
                if agent:
                    content = f"[profile] queried {action} for {agent}"
            elif action == "ask":
                q = arguments.get("question", "")
                subject = arguments.get("subject", arguments.get("agent_name", ""))
                if q:
                    content = f"[ask] {subject}: {q[:200]}"

        elif tool_name == "memall_system":
            if action in ("security", "adaptive"):
                sub = arguments.get("sub_action", "")
                if sub:
                    content = f"[manage] {action}: {sub}"
            elif action == "reflect":
                mid = arguments.get("memory_id")
                sub = arguments.get("sub_action", "")
                if mid and sub:
                    content = f"[reflect] #{mid} => {sub}"

        if content and len(content) > 15:
            write_intercept_log(tool_name, content)
    except Exception:
        logger.warning("shared.py: silent error", exc_info=True)
