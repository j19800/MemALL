import logging
from datetime import datetime, timezone
logger = logging.getLogger(__name__)

_AUTO_START_CACHE: dict[str, str] = {}
_AUTO_START_COOLDOWN = 1800
_PENDING_SESSION_NOTE: str | None = None

# Actions that are read-only — no session auto-start needed
_READ_ONLY_ACTIONS = frozenset({
    "retrieve", "vector_search", "hybrid_search", "search", "trace",
    "traverse", "timeline", "fed_query", "fed_conflicts",
    "persona", "persona_profile", "identity", "ask",
    "discussion_status", "hub_connect", "db", "index_rebuild",
    "session_summary",
})

# Tools/actions that skip intercept logging entirely
_INTERCEPT_SKIP_TOOLS = frozenset({
    "memall_onboarding",
})
_INTERCEPT_SKIP_ACTIONS = frozenset({
    "capture", "smart_store", "store_batch", "ping",
    "session_start", "session_end",
})

# Actions that log a simple [query] line
_INTERCEPT_QUERY_ACTIONS = frozenset({
    "retrieve", "search", "vector_search", "hybrid_search",
    "traverse", "timeline", "fed_query", "session_summary",
    "db",
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
