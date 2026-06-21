import logging
import sqlite3
from datetime import datetime, timezone, timedelta
logger = logging.getLogger(__name__)


from memall.core.db import DB_PATH

_AUTO_START_CACHE: dict[str, str] = {}
_AUTO_START_COOLDOWN = 1800
_PENDING_SESSION_NOTE: str | None = None

_READ_ONLY_TOOLS = frozenset({
    "retrieve", "traverse", "timeline", "memall_vector_search",
    "memall_fed_query", "memall_fed_conflicts", "memall_session_summary",
    "memall_persona", "memall_persona_profile", "memall_ask",
    "memall_identity", "memall_trace", "memall_discussion_status",
    "memall_hub_connect", "memall_db",
})

_INTERCEPT_SKIP = frozenset({
    "capture", "memall_smart_store", "memall_store_batch",
    "ping", "memall_session_start", "memall_session_end",
    "memall_onboarding",
})

_INTERCEPT_CONTEXT_TOOLS = frozenset({
    "retrieve", "traverse", "timeline", "memall_vector_search",
    "memall_db", "memall_security", "memall_fed_query",
    "memall_session_summary",
})


def ensure_session_started(agent_name: str, auto_inject: bool = True) -> None:
    from memall.pipeline.session import session_start
    global _PENDING_SESSION_NOTE
    now = datetime.now(timezone.utc)
    last = _AUTO_START_CACHE.get(agent_name)
    if last:
        last_dt = datetime.fromisoformat(last)
        if (now - last_dt).total_seconds() < _AUTO_START_COOLDOWN:
            return
    try:
        result = session_start(agent_name=agent_name, auto_inject=auto_inject)
        _AUTO_START_CACHE[agent_name] = now.isoformat()
        if auto_inject:
            injection = result.get("injection_formatted")
            if injection:
                _PENDING_SESSION_NOTE = injection
    except Exception:
        logger.warning("shared.py: silent error", exc_info=True)


def consume_session_note() -> str | None:
    global _PENDING_SESSION_NOTE
    note = _PENDING_SESSION_NOTE
    _PENDING_SESSION_NOTE = None
    return note


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
    if tool_name in _INTERCEPT_SKIP:
        return
    try:
        content = None
        if tool_name in _INTERCEPT_CONTEXT_TOOLS:
            query = arguments.get("query", arguments.get("question", ""))
            if query:
                content = f"[query] {tool_name}: {query[:200]}"
        elif tool_name == "update":
            mid = arguments.get("memory_id")
            fields = [k for k in arguments if k != "memory_id"]
            if mid and fields:
                content = f"[update] updated #{mid}: {', '.join(fields)}"
        elif tool_name == "connect":
            src = arguments.get("source_id")
            tgt = arguments.get("target_id")
            rel = arguments.get("relation_type", "related")
            if src and tgt:
                content = f"[link] #{src} -{rel}-> #{tgt}"
        elif tool_name == "memall_update":
            mid = arguments.get("memory_id")
            fields = [k for k in arguments if k != "memory_id" and k != "metadata"]
            meta = arguments.get("metadata", "")
            if "status" in meta and "done" in meta:
                fields.append("status->done")
            if mid and fields:
                content = f"[update] #{mid}: {', '.join(fields)}"
        elif tool_name in ("memall_forget", "memall_adaptive", "memall_ops"):
            action = arguments.get("action", "")
            if action:
                content = f"[manage] {tool_name}: {action}"
        elif tool_name == "memall_persona" or tool_name == "memall_persona_profile":
            agent = arguments.get("agent_name", "")
            if agent:
                content = f"[profile] queried persona for {agent}"
        elif tool_name == "memall_ask":
            q = arguments.get("question", "")
            subject = arguments.get("subject", arguments.get("agent_name", ""))
            if q:
                content = f"[ask] {subject}: {q[:200]}"
        elif tool_name == "memall_traverse":
            nid = arguments.get("node_id")
            depth = arguments.get("depth", 1)
            if nid:
                content = f"[graph] traverse #{nid} depth={depth}"

        if content and len(content) > 15:
            write_intercept_log(tool_name, content)
    except Exception:
        logger.warning("shared.py: silent error", exc_info=True)
