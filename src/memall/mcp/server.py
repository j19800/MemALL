import json
import logging
import sys
from memall.mcp.adapter import TOOL_DEFINITIONS, handle_call, _intercept, consume_session_note

_initialized = False
_client_name = ""
_client_version = ""
_log = logging.getLogger("memall.mcp.server")

# Error codes
_ERR_NOT_INITIALIZED = -32000
_ERR_VERSION_MISMATCH = -32001

# Protocol version support
_SUPPORTED_PROTOCOL_VERSIONS = {"2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05"}
_MAX_LINE_LENGTH = 65536  # 64KB max input line

# ── Stderr logging to diagnose crashes without interfering with MCP stdio ──
_LOGGING_CONFIGURED = False


def _ensure_logging():
    global _LOGGING_CONFIGURED
    if not _LOGGING_CONFIGURED:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(logging.Formatter(
            "[MCP %(asctime)s] %(levelname)s %(message)s",
            datefmt="%H:%M:%S",
        ))
        _log.addHandler(handler)
        _log.setLevel(logging.INFO)
        _LOGGING_CONFIGURED = True


def _read_request() -> dict | None:
    line = sys.stdin.readline(_MAX_LINE_LENGTH)
    if not line:
        return None
    if len(line) >= _MAX_LINE_LENGTH:
        # Drain the rest of this oversized line to keep stream in sync
        sys.stdin.readline()
        return None
    line = line.strip()
    if not line:
        return None
    try:
        return json.loads(line)
    except json.JSONDecodeError:
        return None


def _respond(msg: dict):
    sys.stdout.write(json.dumps(msg, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _start_polling_consumers():
    """Start background bot poller threads for all configured agents."""
    try:
        from memall.lark.credentials import load_all
        from memall.lark.consumer_helpers import ensure_profile
        from memall.lark.consumer import BotPoller
        all_creds = load_all()
        started = 0
        for agent, creds in all_creds.items():
            app_id = creds.get("app_id", "")
            app_secret = creds.get("app_secret", "")
            if not app_id or not app_secret:
                continue
            ensure_profile(agent, app_id, app_secret)
            bot = BotPoller(agent, creds)
            bot.start()
            started += 1
            _log.info("polling consumer started: %s", agent)
        if started:
            _log.info("total polling consumers: %d", started)
    except Exception as e:
        _log.warning("polling consumers startup skipped: %s", e)


def serve():
    """MCP STDIO Server — reads JSON-RPC from stdin, writes to stdout.

    Implements required lifecycle per MCP 2025-03-26 spec:
      initialize → [initialized notification] → tools/list → tools/call
    """
    # Force UTF-8 on stdout to prevent UnicodeEncodeError on Windows GBK consoles
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except AttributeError:
        pass  # Python < 3.7 or non-TTY streams that don't support reconfigure
    _ensure_logging()
    _start_polling_consumers()
    global _initialized, _client_name, _client_version

    _log.info("MCP server starting (protocol 2025-03-26)")

    try:
        while True:
            req = _read_request()
            if req is None:
                _log.info("stdin closed — shutting down")
                break

            req_id = req.get("id")
            method = req.get("method", "")
            params = req.get("params", {})

            # ── Notifications (no id → no response) ──────────────────
            if req_id is None:
                if method == "notifications/initialized":
                    _initialized = True
                    _log.info("Client initialized (%s %s)", _client_name, _client_version)
                continue

            _log.debug("Request: method=%s id=%s", method, req_id)

            # ── Initialize ───────────────────────────────────────────
            if method == "initialize":
                # Extract and log client info
                client_info = params.get("clientInfo", {})
                _client_name = client_info.get("name", "unknown")
                _client_version = client_info.get("version", "")
                _log.info("Initialize from %s %s", _client_name, _client_version)

                # Validate protocol version
                client_protocol = params.get("protocolVersion", "")
                if client_protocol and client_protocol not in _SUPPORTED_PROTOCOL_VERSIONS:
                    _log.warning("Unsupported protocol version: %s", client_protocol)
                    _respond({
                        "jsonrpc": "2.0", "id": req_id,
                        "error": {
                            "code": _ERR_VERSION_MISMATCH,
                            "message": f"unsupported protocol version: {client_protocol}",
                            "data": {"supported": sorted(_SUPPORTED_PROTOCOL_VERSIONS)},
                        },
                    })
                    continue

                # Read global user identity from identity.json (client-agnostic)
                from memall.onboarding import _get_status
                import json as _json
                import os as _os

                identity_path = _os.path.join(_os.path.expanduser("~"), ".memall", "identity.json")
                user_id = "default"
                actor_id = "unknown"
                try:
                    if _os.path.exists(identity_path):
                        with open(identity_path, encoding="utf-8") as f:
                            ident = _json.load(f)
                        user_id = ident.get("user_id", "default")
                        actor_id = ident.get("actor_id", "unknown")
                except Exception as e:
                    _log.warning("Failed to read identity.json: %s", e)

                _log.info("Identity: user=%s actor=%s", user_id, actor_id)

                # Check onboarding status for this user
                try:
                    onboarding_status = _get_status(user_id)
                    onboarding_completed = bool(onboarding_status.get("completed"))
                    onboarding_step = onboarding_status.get("current_step", 1)
                except Exception as e:
                    _log.warning("Onboarding check failed: %s", e)
                    onboarding_completed = False
                    onboarding_step = 1

                # Auto-detect fresh user (0 memories + no onboarding completion)
                welcome_memory_id = None
                if not onboarding_completed:
                    try:
                        from memall.core.db import get_conn as _get_db
                        _c = _get_db()
                        count = _c.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
                        _c.close()
                        if count == 0:
                            from memall.onboarding import _store_welcome_memory as _store_welcome
                            try:
                                welcome_memory_id = _store_welcome(user_id, actor_id)
                                _log.info("Welcome memory stored: id=%s", welcome_memory_id)
                            except Exception as e:
                                _log.warning("Welcome memory failed: %s", e)
                    except Exception as e:
                        _log.warning("Fresh-user check failed: %s", e)

                _respond({
                    "jsonrpc": "2.0", "id": req_id,
                    "result": {
                        "serverInfo": {
                            "name": "memall",
                            "version": "0.1.0",
                            "user_id": user_id,
                            "actor_id": actor_id,
                        },
                        "protocolVersion": "2025-03-26",
                        "capabilities": {
                            "tools": {},
                        },
                        "memall": {
                            "onboarding_required": not onboarding_completed,
                            "onboarding_step": onboarding_step,
                            "onboarding_user_id": user_id,
                            "onboarding_action": "skip" if onboarding_completed else "start",
                            "onboarding_tool": "memall_system",
                            "welcome_memory_id": welcome_memory_id,
                            "onboarding_message": (
                                "MemALL 新手引导已完成。直接使用所有工具。"
                                if onboarding_completed
                                else f"MemALL 新手引导未完成（{user_id} 在步骤 {onboarding_step}/5）。建议调 memall_system action=onboarding sub_action=start 走 5 步引导。"
                            ),
                        }
                    },
                })

            # ── Methods that require initialization ──────────────────
            elif method == "ping":
                if not _initialized:
                    _respond({"jsonrpc": "2.0", "id": req_id,
                              "error": {"code": _ERR_NOT_INITIALIZED, "message": "server not initialized"}})
                else:
                    _respond({"jsonrpc": "2.0", "id": req_id, "result": {}})

            elif method == "tools/list":
                if not _initialized:
                    _respond({"jsonrpc": "2.0", "id": req_id,
                              "error": {"code": _ERR_NOT_INITIALIZED, "message": "server not initialized"}})
                else:
                    _respond({
                        "jsonrpc": "2.0", "id": req_id,
                        "result": {"tools": TOOL_DEFINITIONS},
                    })

            elif method == "tools/call":
                if not _initialized:
                    _respond({"jsonrpc": "2.0", "id": req_id,
                              "error": {"code": _ERR_NOT_INITIALIZED, "message": "server not initialized"}})
                else:
                    tool_name = params.get("name", "")
                    arguments = params.get("arguments", {})
                    _log.info("Call tool: %s", tool_name)
                    try:
                        result_str = handle_call(tool_name, arguments)
                        _intercept(tool_name, arguments, result_str)
                        result_data = json.loads(result_str)
                        content = [{"type": "text", "text": json.dumps(result_data, ensure_ascii=False)}]

                        # ── Agent notifications: piggyback on every tool response ──
                        # If the caller identified itself via agent_name, check for
                        # pending discussions or tasks and attach as a second content
                        # block.  This is the "notification bar" — agents don't need to
                        # explicitly query for pending items.
                        agent_name = arguments.get("agent_name", "")
                        if agent_name:
                            try:
                                from memall.core.db import get_conn as _get_conn
                                _nconn = _get_conn()
                                task_count = _nconn.execute(
                                    "SELECT COUNT(*) as c FROM memories WHERE level='L5' AND category='task' "
                                    "AND agent_name=? AND json_extract(metadata, '$.status')='active'",
                                    (agent_name,),
                                ).fetchone()["c"]
                                disc_count = _nconn.execute(
                                    "SELECT COUNT(*) as c FROM memories m WHERE level='L5' AND category='discussion' "
                                    "AND json_extract(metadata, '$.status')='active' "
                                    "AND json_extract(metadata, '$.participants') LIKE ? "
                                    "AND m.id NOT IN (SELECT e.source_id FROM edges e JOIN memories r ON e.target_id=r.id "
                                    "  WHERE e.relation_type='cites' AND json_extract(r.metadata, '$.agent_name')=?)",
                                    (f'%"{agent_name}"%', agent_name),
                                ).fetchone()["c"]
                                _nconn.close()
                                notes = []
                                if task_count > 0:
                                    notes.append(f"{task_count} 个待完成任务")
                                if disc_count > 0:
                                    notes.append(f"{disc_count} 个待回应讨论")
                                if notes:
                                    content.append({
                                        "type": "text",
                                        "text": f"[NOTIFICATION] {'，'.join(notes)}。"
                                    })
                            except Exception:
                                _log.warning("server.py: silent error", exc_info=True)

                        session_note = consume_session_note()
                        if session_note:
                            content.append({"type": "text", "text": session_note})
                        _respond({
                            "jsonrpc": "2.0", "id": req_id,
                            "result": {"content": content},
                        })
                    except Exception as e:
                        _log.error("Tool %s failed: %s", tool_name, e)
                        _respond({
                            "jsonrpc": "2.0", "id": req_id,
                            "error": {"code": -32603, "message": str(e)},
                        })

            else:
                _respond({
                    "jsonrpc": "2.0", "id": req_id,
                    "error": {"code": -32601, "message": f"unknown method: {method}"},
                })

    except Exception as e:
        _log.critical("Unhandled exception in main loop: %s", e, exc_info=True)
        raise
    finally:
        _log.info("MCP server stopped")


if __name__ == "__main__":
    serve()
