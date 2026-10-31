import json
import logging
import sys
from memall.core.status import PENDING_TASKS, pending_tasks
from memall.mcp.adapter import TOOL_DEFINITIONS, handle_call, _intercept, consume_session_note, _ensure_loaded
from memall.mcp.shared import ack_session_note

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
        fmt = logging.Formatter(
            "[MCP %(asctime)s] %(levelname)s %(message)s",
            datefmt="%H:%M:%S",
        )
        # stderr stream handler (existing behaviour, kept for live clients)
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(fmt)
        _log.addHandler(handler)
        # Persistent file handler so a crash survives client-side truncation:
        # the client only keeps the tail of stderr in memory, but the file on
        # disk keeps the full trail incl. the last "Call tool:" line.
        try:
            import os as _os
            log_path = _os.path.join(_os.path.expanduser("~"), ".memall", "mcp_stderr.log")
            _os.makedirs(_os.path.dirname(log_path), exist_ok=True)
            fhandler = logging.FileHandler(log_path, mode="a", encoding="utf-8")
            fhandler.setFormatter(fmt)
            _log.addHandler(fhandler)
        except Exception as _e:  # pragma: no cover - logging must never block startup
            # The stderr handler above is already attached; do not build a
            # second StreamHandler here (it would never be attached, so the
            # message would vanish). Just record that the persistent trail is
            # missing.
            _log.warning("file handler init failed (no persistent log): %s", _e)
        _log.setLevel(logging.INFO)
        _LOGGING_CONFIGURED = True


def _read_request() -> dict | None:
    """Read one JSON-RPC request from stdin.

    Returns None ONLY at EOF — malformed/oversized requests are recorded in
    ``_pending_errors`` and surfaced as JSON-RPC error responses by serve().
    (Feeding None on a bad request made the very first broken input shut the
    server down: req-is-None doubles as the EOF signal in the main loop.)
    """
    line = sys.stdin.readline(_MAX_LINE_LENGTH)
    if not line:
        return None
    while len(line) >= _MAX_LINE_LENGTH:
        # Drain the rest of this oversized line to keep the stream in sync,
        # then defer a structured JSON-RPC error (a silent drop left the
        # awaiting client hanging forever).
        sys.stdin.readline()
        _pending_errors.append({
            "jsonrpc": "2.0", "id": None,
            "error": {
                "code": -32700,
                "message": f"request line exceeds {_MAX_LINE_LENGTH} bytes",
            },
        })
        line = sys.stdin.readline(_MAX_LINE_LENGTH)
        if not line:
            return None
        if line.strip():
            break
    line = line.strip()
    if not line:
        # Blank line: not EOF, just skip it — read the next request.
        return _read_request()
    try:
        return json.loads(line)
    except json.JSONDecodeError:
        # Malformed JSON must get a parse error response per JSON-RPC 2.0
        # (a silent drop leaves the awaiting client hanging forever).
        _pending_errors.append({
            "jsonrpc": "2.0", "id": None,
            "error": {"code": -32700, "message": "parse error: request is not valid JSON"},
        })
        return _read_request()


# Error responses deferred to the next serve-loop iteration (reading stdin
# blocks the loop, so errors detected in _read_request are flushed before
# the next request is processed).
_pending_errors: list[dict] = []


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
    # Force UTF-8 on BOTH pipes.  This used to fix only stdout, so on a
    # Windows GBK console the JSON-RPC frames we READ were decoded with the
    # locale codec: any Chinese in a request (a task subject, a discussion
    # body, a search query) arrived mojibake or raised, which is why agents
    # resorted to passing Chinese through temp files instead of stdio.
    for _stream_name in ("stdout", "stdin"):
        try:
            getattr(sys, _stream_name).reconfigure(
                encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            pass  # Python < 3.7, or a stream that cannot be reconfigured
    _ensure_logging()
    _start_polling_consumers()
    # Audit P0-1: the capture-triggered lightweight pipeline skips distill
    # and integrate, and the only periodic full run lived in the daemon —
    # so a serve-only deployment (the normal MCP client setup) never
    # produced L9/L10 memories and the "self-evolving" model stopped at L7.
    # This watchdog runs distill+integrate when there is work, and skips
    # itself when the daemon is already covering it.
    try:
        from memall.pipeline.fallback import start_fallback_timer

        start_fallback_timer(interval=300)
    except Exception:
        _log.warning("fallback timer failed to start (non-fatal)", exc_info=True)
    global _initialized, _client_name, _client_version

    _log.info("MCP server starting (protocol 2025-03-26)")

    try:
        while True:
            # Flush parse/oversize errors deferred by _read_request first —
            # they belong to requests already consumed from the stream.
            while _pending_errors:
                _respond(_pending_errors.pop(0))

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

                # ── Bind the agent identity ONCE for the whole session ──
                # Routing (task delivery, notifications, isolation) needs to
                # know who is talking, but 28 tool models never declared
                # agent_name, so the per-call argument could not be trusted.
                # Accept, in order of preference: an explicit
                # params.agent_name, clientInfo.agent_name, clientInfo.name.
                try:
                    from memall.mcp.session_identity import bind_handshake_agent

                    # Do NOT fall back to clientInfo.name: that is the
                    # CLIENT SOFTWARE ("dsh-mcp-client", "claude-ai"), not
                    # the agent.  Using it would attribute every agent to one
                    # identity — worse than no identity, because isolation
                    # and task routing would then all point at the same
                    # wrong bucket.  Explicit handshake identity first, then
                    # an env var the launcher can set per agent.
                    import os as _os

                    _agent = (params.get("agent_name")
                              or client_info.get("agent_name")
                              or _os.environ.get("MEMALL_AGENT_NAME")
                              or "")
                    if _agent and _agent != "unknown":
                        bind_handshake_agent(_agent)
                        _log.info("Session identity bound: %s", _agent)
                except Exception:
                    _log.warning("session identity binding failed", exc_info=True)

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
                    _ensure_loaded()
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
                        #
                        # Identity: explicit agent_name, else the session identity
                        # bound at handshake.  28 tool models never declare
                        # agent_name, so a call through one of them arrives
                        # with the field stripped and the bar would find
                        # nobody.
                        from memall.mcp.session_identity import identify
                        agent_name = identify(arguments)
                        if agent_name:
                            # Shared, stateless notification bar (see
                            # memall/mcp/notify.py).  Carries task IDs, titles
                            # and states instead of a bare count, and is
                            # queried fresh on every response so it can never
                            # serve a stale queue.
                            try:
                                from memall.mcp.notify import build_notice

                                _notice = build_notice(agent_name)
                                if _notice:
                                    content.append({"type": "text", "text": _notice})
                            except Exception:
                                _log.warning("notify bar failed", exc_info=True)

                        # The note belongs to the calling agent — pass its
                        # identity so another agent's note can never be
                        # delivered (or acked away) by mistake.
                        session_note = consume_session_note(agent_name)
                        if session_note:
                            content.append({"type": "text", "text": session_note})
                            # delivered -> acknowledge (audit P2-12: a note
                            # dropped on read is lost on timeout)
                            ack_session_note(agent_name)
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
        _log.critical("exit code 255 will be returned to client; inspect ~/.memall/mcp_stderr.log")
        raise
    finally:
        _log.info("MCP server stopped")
        try:
            for h in list(_log.handlers):
                try:
                    h.flush()
                except Exception:
                    pass
        except Exception:
            pass


def _reset_state():
    """Reset module-level session state so serve() can run again in-process
    (tests / gateway import). Without this, a restart inherits the previous
    client's initialized flag and identity."""
    global _initialized, _client_name, _client_version, _LOGGING_CONFIGURED, _pending_errors
    _initialized = False
    _client_name = ""
    _client_version = ""
    _pending_errors.clear()


if __name__ == "__main__":
    serve()
