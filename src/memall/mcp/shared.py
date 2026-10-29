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
import threading as _threading

_NOTE_LOCK = _threading.Lock()
_PENDING_SESSION_NOTES: dict[str, str] = {}
# Unacked notes (audit P2-12): consume_session_note() used to pop the note
# and lose it if the response never reached the client.  A note now stays
# pending until it is explicitly acknowledged, so a timed-out call does not
# permanently drop the session context.
_NOTE_ACKED: set[str] = set()

# Per-agent push timestamps (P3-4).  A note that is never acknowledged used to
# stay queued forever, and so did its dedup keys: an agent that stopped calling
# kept a goal prompt from months ago, which the server would still inject as
# "your next action", and the driver could never re-issue that prompt because
# the key was still marked pending.  Notes now expire; a note with no stamp
# (written directly by a guard or an older code path) is treated as fresh and
# stamped on first read, so nothing is dropped for lack of bookkeeping.
_NOTE_TS: dict[str, str] = {}
_NOTE_TTL_DEFAULT = 6 * 3600

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
                # Via the public push so the note lands in the DB and is readable
                # by the process that actually serves this agent, not just this one.
                push_session_note(agent_name, injection)
    except Exception:
        logger.warning("shared.py: silent error", exc_info=True)


# Dedup keys for notes pushed by the goal driver, so a repeating tick does not
# queue the same prompt over and over while it is still unread.
_NOTE_DEDUP: dict[str, set] = {}


def _note_ttl() -> float:
    try:
        from memall.config import get_config
        return float(get_config("session.note_ttl_seconds", _NOTE_TTL_DEFAULT))
    except Exception:
        return _NOTE_TTL_DEFAULT


def _note_is_stale(agent: str) -> bool:
    stamp = _NOTE_TS.get(agent)
    if not stamp:
        return False          # no bookkeeping → treat as fresh, stamp on read
    try:
        at = datetime.fromisoformat(stamp)
    except (TypeError, ValueError):
        return True
    if at.tzinfo is None:
        at = at.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - at).total_seconds() > _note_ttl()


def _expire_note(agent: str) -> bool:
    """Drop *agent*'s in-memory note if it outlived the TTL.  Caller holds _NOTE_LOCK.

    Only the fallback path uses this; the primary store is the session_notes
    table, whose expiry is a DELETE in :func:`_expire_notes`.
    """
    if agent in _PENDING_SESSION_NOTES and _note_is_stale(agent):
        logger.debug("shared.py: expiring stale in-memory note for %s", agent)
        _PENDING_SESSION_NOTES.pop(agent, None)
        _NOTE_TS.pop(agent, None)
        _NOTE_DEDUP.pop(agent, None)
        return True
    return False


def _notes_conn():
    """The DB connection backing session notes, or None if unavailable.

    Delivery MUST cross processes: each stdio MCP client gets its own server
    process, so a note held in module memory reaches nobody but the process that
    wrote it.  The dicts above are kept only as a same-process fallback for the
    case where the DB is unreachable — never as the primary store.
    """
    try:
        from memall.core.db import get_conn

        return get_conn()
    except Exception:
        return None


def _note_cutoff_iso() -> str:
    from datetime import timedelta

    return (datetime.now(timezone.utc) - timedelta(seconds=_note_ttl())).isoformat()


def _expire_notes(conn, agent: str) -> None:
    try:
        conn.execute("DELETE FROM session_notes WHERE agent = ? AND created_at < ?",
                     (agent, _note_cutoff_iso()))
        conn.commit()
    except Exception:
        logger.debug("shared.py: note expiry failed for %s", agent, exc_info=True)


def _drop_note(agent: str) -> bool:
    """Drop the agent's notes from BOTH stores and free their dedup keys.

    Both, not "whichever is primary": the DB is the cross-process store, but the
    in-memory dict is still written directly by guards and by any path that
    predates this change.  A drop that cleared only the DB left a memory note
    readable forever and reported the ack as a no-op - measured as two failing
    guards.
    """
    removed = False
    conn = _notes_conn()
    if conn is not None:
        try:
            _expire_notes(conn, agent)
            cur = conn.execute("DELETE FROM session_notes WHERE agent = ?", (agent,))
            conn.commit()
            removed = cur.rowcount > 0
        except Exception:
            logger.debug("shared.py: DB note drop failed for %s", agent, exc_info=True)
        finally:
            try:
                conn.close()
            except Exception:
                pass
    with _NOTE_LOCK:
        if _PENDING_SESSION_NOTES.pop(agent, None) is not None:
            removed = True
        _NOTE_TS.pop(agent, None)
        _NOTE_DEDUP.pop(agent, None)
    return removed


def push_session_note(agent_name: str, text: str, dedup_key: str = "") -> bool:
    """Queue a note for *agent_name* to receive on its next tool call.

    Returns False when the same ``dedup_key`` is already pending for that agent
    (the driver ticks on a timer; without this an unread prompt would be
    re-queued every five minutes).

    Cross-process by construction: the note lands in the DB, so a note pushed for
    another agent is visible to whichever process serves that agent.  An
    in-memory note would only ever reach the process that wrote it — which for
    dispatch means it reaches nobody.
    """
    agent = (agent_name or "").strip()
    body = (text or "").strip()
    if not agent or not body:
        return False
    conn = _notes_conn()
    if conn is not None:
        try:
            _expire_notes(conn, agent)
            conn.execute(
                "INSERT INTO session_notes (agent, dedup_key, body, created_at) "
                "VALUES (?, ?, ?, ?)",
                (agent, dedup_key or "", body, datetime.now(timezone.utc).isoformat()),
            )
            conn.commit()
            return True
        except Exception as exc:
            try:
                conn.rollback()
            except Exception:
                pass
            # IntegrityError here is the dedup contract: that key is pending.
            if "UNIQUE" in str(exc).upper() or "constraint" in str(exc).lower():
                return False
            logger.debug("shared.py: DB note push failed for %s", agent, exc_info=True)
        finally:
            try:
                conn.close()
            except Exception:
                pass
    with _NOTE_LOCK:
        _expire_note(agent)
        if dedup_key:
            keys = _NOTE_DEDUP.setdefault(agent, set())
            if dedup_key in keys:
                return False
            keys.add(dedup_key)
        existing = _PENDING_SESSION_NOTES.get(agent, "")
        _PENDING_SESSION_NOTES[agent] = (existing + "\n" + body) if existing else body
        _NOTE_TS[agent] = datetime.now(timezone.utc).isoformat()
        _NOTE_ACKED.discard(agent)
    return True


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
        logger.debug(
            "shared.py: session note requested without an agent name; "
            "refusing to guess whose note to deliver"
        )
        return None
    conn = _notes_conn()
    if conn is not None:
        try:
            _expire_notes(conn, agent_name)
            rows = conn.execute(
                "SELECT body FROM session_notes "
                "WHERE agent = ? AND acked_at IS NULL ORDER BY id",
                (agent_name,),
            ).fetchall()
            if rows:
                return "\n".join(r[0] for r in rows if r and r[0])
        except Exception:
            logger.debug("shared.py: DB note read failed for %s", agent_name,
                         exc_info=True)
        finally:
            try:
                conn.close()
            except Exception:
                pass
    with _NOTE_LOCK:
        _expire_note(agent_name)
        note = _PENDING_SESSION_NOTES.get(agent_name)
        if note is not None and agent_name not in _NOTE_TS:
            _NOTE_TS[agent_name] = datetime.now(timezone.utc).isoformat()
        return note


def ack_session_note(agent_name: str = "") -> bool:
    """Acknowledge delivery of a session note (drops it).

    Like :func:`consume_session_note`, this only ever touches the named
    agent's note.  The unnamed "oldest" fallback could drop a note that
    belonged to a different agent than the one being acknowledged.
    """
    if not agent_name:
        logger.debug(
            "shared.py: session note ack without an agent name; "
            "refusing to guess whose note to drop"
        )
        return False
    # Dropping the row also frees its dedup key: "delivered" is not "done".  If
    # the agent read the prompt but did not act on it, the driver must be able
    # to re-issue it on a later tick — otherwise a single ack silently kills
    # the hand-off.
    popped = _drop_note(agent_name)
    with _NOTE_LOCK:
        _NOTE_ACKED.add(agent_name)
    return popped


def pending_session_notes() -> dict:
    """Diagnostics: per-agent count of notes waiting for acknowledgement."""
    out: dict[str, int] = {}
    conn = _notes_conn()
    if conn is not None:
        try:
            conn.execute("DELETE FROM session_notes WHERE created_at < ?",
                         (_note_cutoff_iso(),))
            conn.commit()
            for agent, n in conn.execute(
                    "SELECT agent, COUNT(*) FROM session_notes "
                    "WHERE acked_at IS NULL GROUP BY agent").fetchall():
                out[agent] = int(n)
        except Exception:
            logger.debug("shared.py: note diagnostics failed", exc_info=True)
        finally:
            try:
                conn.close()
            except Exception:
                pass
    with _NOTE_LOCK:
        for agent, text in _PENDING_SESSION_NOTES.items():
            out.setdefault(agent, len(text))
    return out


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
