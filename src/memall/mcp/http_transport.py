"""MCP Streamable HTTP transport — persistent server, no STDIO dependency.

Implements MCP 2025-03-26 Streamable HTTP spec:
  POST /mcp  — JSON-RPC request/response (non-streaming tools)
  GET  /mcp  — SSE subscription for tool list changes
"""

import asyncio
import concurrent.futures
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

from aiohttp import web

# Thread pool for synchronous tool calls (keeps event loop responsive)
_TOOL_EXECUTOR = concurrent.futures.ThreadPoolExecutor(max_workers=12)
_TOOL_HEAVY = concurrent.futures.ThreadPoolExecutor(max_workers=2)  # slow ops: pipeline, index_rebuild, etc.
_TOOL_TIMEOUT = 120  # max seconds for a single tool call
_HEAVY_TIMEOUT = 600  # max seconds for heavy operations

# ── Global exception middleware ────────────────────────────────────────
@web.middleware
async def _error_middleware(request: web.Request, handler) -> web.Response:
    """Catch all unhandled exceptions and return 500 instead of crashing."""
    try:
        return await handler(request)
    except web.HTTPException:
        raise
    except Exception as e:
        _log.error("Unhandled error in %s %s: %s", request.method, request.path, e, exc_info=True)
        return web.json_response(
            {"jsonrpc": "2.0", "error": {"code": -32603, "message": f"internal error: {e}"}},
            status=500,
        )


# ── Graceful shutdown ──────────────────────────────────────────────────
async def _on_shutdown(app: web.Application):
    _log.info("MCP HTTP server shutting down")
    _TOOL_EXECUTOR.shutdown(wait=False)
    _TOOL_HEAVY.shutdown(wait=False)
    # Give in-flight requests time to finish
    await asyncio.sleep(0.5)


# Harden DB_PATH to current user's home (bypass SYSTEM profile when run as service)
# Use USERPROFILE on Windows (correct even when running as SYSTEM service),
# fall back to Path.home() for other platforms.
_user_home = os.environ.get("USERPROFILE") or str(Path.home())
_fixed_path = os.path.join(_user_home, ".memall", "data.db")
print(f"[http_transport] USER_HOME = {_user_home}, setting MEMALL_DB_PATH = {_fixed_path}")
os.environ.setdefault("MEMALL_DB_PATH", _fixed_path)

from memall.mcp.adapter import TOOL_DEFINITIONS, handle_call, _intercept, consume_session_note
from memall.core.db import init_db

_log = logging.getLogger("memall.mcp.http")

# Bearer token for MCP HTTP transport (required for production).
# Set MEMALL_MCP_TOKEN env var, falls back to MEMALL_AUTH_TOKEN.
_MCP_TOKEN = os.environ.get("MEMALL_MCP_TOKEN") or os.environ.get("MEMALL_AUTH_TOKEN") or ""
if not _MCP_TOKEN:
    _log.warning("MCP HTTP auth disabled — set MEMALL_MCP_TOKEN or MEMALL_AUTH_TOKEN for production use")


async def _check_auth(request: web.Request) -> bool:
    """Return True if request is authorized (token not configured = always OK)."""
    if not _MCP_TOKEN:
        return True
    auth = request.headers.get("Authorization", "")
    return auth == f"Bearer {_MCP_TOKEN}"


async def handle_mcp_post(request: web.Request) -> web.Response:
    """Handle JSON-RPC requests via POST /mcp."""
    if not await _check_auth(request):
        return web.json_response(
            {"jsonrpc": "2.0", "error": {"code": -32001, "message": "unauthorized"}},
            status=401,
        )
    try:
        body = await request.json()
    except json.JSONDecodeError:
        return web.json_response(
            {"jsonrpc": "2.0", "error": {"code": -32700, "message": "Parse error"}},
            status=400,
        )

    req_id = body.get("id")
    method = body.get("method", "")
    params = body.get("params", {})

    if req_id is None:
        # Notification — no response
        return web.json_response({}, status=202)

    # ── Initialize ──
    if method == "initialize":
        from memall.onboarding import _get_status
        import os as _os

        identity_path = _os.path.join(_os.path.expanduser("~"), ".memall", "identity.json")
        user_id = "default"
        actor_id = "unknown"
        try:
            if _os.path.exists(identity_path):
                with open(identity_path, encoding="utf-8") as f:
                    ident = json.load(f)
                user_id = ident.get("user_id", "default")
                actor_id = ident.get("actor_id", "unknown")
        except Exception as e:
            _log.warning("Failed to read identity.json: %s", e)

        try:
            onboarding_status = _get_status(user_id)
            onboarding_completed = bool(onboarding_status.get("completed"))
            onboarding_step = onboarding_status.get("current_step", 1)
        except Exception:
            onboarding_completed = False
            onboarding_step = 1

        _log.info("Initialize: user=%s actor=%s", user_id, actor_id)

        return web.json_response({
            "jsonrpc": "2.0", "id": req_id,
            "result": {
                "serverInfo": {
                    "name": "memall",
                    "version": "0.1.0",
                    "user_id": user_id,
                    "actor_id": actor_id,
                },
                "protocolVersion": "2025-03-26",
                "capabilities": {"tools": {"listChanged": True}},
                "memall": {
                    "onboarding_required": not onboarding_completed,
                    "onboarding_step": onboarding_step,
                    "onboarding_user_id": user_id,
                },
            },
        })

    # ── Ping ──
    if method == "ping":
        return web.json_response({"jsonrpc": "2.0", "id": req_id, "result": {}})

    # ── Tools/List ──
    if method == "tools/list":
        return web.json_response({
            "jsonrpc": "2.0", "id": req_id,
            "result": {"tools": TOOL_DEFINITIONS},
        })

    # ── Tools/Call ──
    if method == "tools/call":
        tool_name = params.get("name", "")
        arguments = params.get("arguments", {})
        _arg_preview = str(arguments.get("action", "") or arguments.get("query", "") or arguments.get("id", ""))
        _log.info("Call tool: %s %s", tool_name, _arg_preview[:60])

        # Route heavy ops to separate pool (pipeline, index_rebuild, etc.) to
        # avoid exhausting the regular tool pool.
        _HEAVY_TOOLS = frozenset({
            "memall_run_pipeline", "memall_index_rebuild", "memall_adaptive",
            "memall_forget", "memall_gateway", "memall_hub_sync",
            "memall_persona_profile",
        })
        if tool_name in _HEAVY_TOOLS:
            _pool = _TOOL_HEAVY
            _timeout = _HEAVY_TIMEOUT
        else:
            _pool = _TOOL_EXECUTOR
            _timeout = _TOOL_TIMEOUT

        # Run synchronous tool execution in thread pool (preserves event loop)
        def _run_tool():
            result_str = handle_call(tool_name, arguments)
            _intercept(tool_name, arguments, result_str)
            result_data = json.loads(result_str)
            content = [{"type": "text", "text": json.dumps(result_data, ensure_ascii=False)}]
            # Agent notifications (DB query, also sync)
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
                    _nconn.close()
                    if task_count > 0:
                        content.append({
                            "type": "text",
                            "text": f"[NOTIFICATION] 你有 {task_count} 个待完成任务。"
                        })
                except Exception:
                    _log.warning("http_transport.py: silent error", exc_info=True)
            session_note = consume_session_note()
            if session_note:
                content.append({"type": "text", "text": session_note})
            return content

        try:
            loop = asyncio.get_event_loop()
            content = await asyncio.wait_for(
                loop.run_in_executor(_pool, _run_tool),
                timeout=_timeout,
            )
            return web.json_response({
                "jsonrpc": "2.0", "id": req_id,
                "result": {"content": content},
            })
        except asyncio.TimeoutError:
            _log.error("Tool %s timed out after %ss", tool_name, _timeout)
            return web.json_response({
                "jsonrpc": "2.0", "id": req_id,
                "error": {"code": -32603, "message": f"tool {tool_name} timed out after {_timeout}s"},
            })
        except Exception as e:
            _log.error("Tool %s failed: %s", tool_name, e, exc_info=True)
            return web.json_response({
                "jsonrpc": "2.0", "id": req_id,
                "error": {"code": -32603, "message": str(e)},
            })

    # ── SetLevel ──
    if method == "setLevel":
        level = params.get("level", "info")
        _log.setLevel(getattr(logging, level.upper(), logging.INFO))
        return web.json_response({"jsonrpc": "2.0", "id": req_id, "result": {}})

    return web.json_response({
        "jsonrpc": "2.0", "id": req_id,
        "error": {"code": -32601, "message": f"unknown method: {method}"},
    })


async def handle_sse(request: web.Request) -> web.Response:
    """SSE endpoint for tool list change notifications."""
    response = web.StreamResponse(
        status=200,
        reason="OK",
        headers={
            "Content-Type": "text/event-stream",
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "Access-Control-Allow-Origin": "*",
        },
    )
    await response.prepare(request)
    # Send initial tool list event
    event = {"type": "tool_list_changed", "tools": [_t["name"] for _t in TOOL_DEFINITIONS]}
    await response.write(f"data: {json.dumps(event, ensure_ascii=False)}\n\n".encode())
    # Keep connection alive with periodic heartbeats
    try:
        while True:
            await asyncio.sleep(30)
            await response.write(b": heartbeat\n\n")
    except (ConnectionResetError, ConnectionAbortedError, ConnectionError):
        _log.info("SSE client disconnected")
    except asyncio.CancelledError:
        _log.info("SSE task cancelled")
    except Exception:
        _log.warning("SSE unexpected error", exc_info=True)
    return response


async def handle_info(request: web.Request) -> web.Response:
    def _check_db():
        try:
            from memall.core.db import get_conn
            conn = get_conn()
            total = conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
            db_ok = conn.execute("PRAGMA quick_check").fetchone()[0]
            conn.close()
            return ("ok", total) if db_ok == "ok" else ("error", total)
        except Exception as e:
            return (f"error: {e}", 0)

    try:
        loop = asyncio.get_event_loop()
        db_status, total = await loop.run_in_executor(_TOOL_EXECUTOR, _check_db)
    except Exception:
        db_status = "error"
        total = 0

    return web.json_response({
        "server": "memall MCP HTTP transport",
        "version": "0.1.0",
        "protocol": "MCP 2025-03-26 (Streamable HTTP)",
        "status": "running",
        "uptime": _uptime(),
        "database": {
            "status": db_status,
            "memories": total,
            "path": str(_correct_db_path()),
        },
        "endpoints": {
            "GET /health": "health check (this response)",
            "POST /mcp": "JSON-RPC request/response",
            "GET /mcp": "SSE subscription",
        },
    })


def _correct_db_path() -> Path:
    """Resolve the actual database path used by this server."""
    _home = os.environ.get("USERPROFILE") or str(Path.home())
    return Path(_home) / ".memall" / "data.db"


def _uptime() -> str:
    """Return uptime string since module import."""
    if _start_time:
        elapsed = int(datetime.now(timezone.utc).timestamp() - _start_time)
        h, r = divmod(elapsed, 3600)
        m, s = divmod(r, 60)
        return f"{h}h {m}m {s}s"
    return "unknown"


_start_time = None  # set in _startup


def create_app() -> web.Application:
    app = web.Application(middlewares=[_error_middleware], client_max_size=10 * 1024 * 1024)
    app.router.add_get("/health", handle_info)
    app.router.add_post("/mcp", handle_mcp_post)
    app.router.add_get("/mcp", handle_sse)
    app.router.add_get("/", handle_info)
    app.on_shutdown.append(_on_shutdown)
    async def _startup(app):
        global _start_time
        _start_time = datetime.now(timezone.utc).timestamp()
        _log.info("MCP HTTP server starting")
        # Force correct database path (USERPROFILE avoids SYSTEM profile on Windows)
        from memall.core import db as _memall_db
        _user_home = os.environ.get("USERPROFILE") or str(Path.home())
        _correct_path = os.path.join(_user_home, ".memall", "data.db")
        _memall_db.DB_PATH = Path(_correct_path)
        _log.info("DB_PATH set to: %s", _correct_path)
        try:
            init_db()
        except Exception as e:
            _log.error("DB init failed: %s", e, exc_info=True)
            raise
    app.on_startup.append(_startup)
    return app


def serve_http_forever(port: int = 9876, max_retries: int = 0):
    """Run MCP HTTP server with auto-restart on crash.

    Args:
        port: TCP port to listen on.
        max_retries: 0 = unlimited restart.
    """
    import time as _time
    retries = 0
    while True:
        try:
            logging.basicConfig(
                level=logging.INFO,
                format="[MCP-HTTP %(asctime)s] %(levelname)s %(message)s",
                datefmt="%H:%M:%S",
            )
            app = create_app()
            _log.info("MCP HTTP server starting on http://127.0.0.1:%d/mcp", port)
            web.run_app(app, host="127.0.0.1", port=port, print=lambda _: None)
            # Normal shutdown — exit loop
            break
        except OSError as e:
            # Port conflict — wait and retry
            _log.error("Port %d conflict: %s — retrying in 3s", port, e)
            _time.sleep(3)
            retries += 1
            if max_retries and retries >= max_retries:
                _log.critical("Max retries (%d) reached, giving up", max_retries)
                raise
        except Exception as e:
            _log.error("Server crashed: %s — restarting in 2s", e, exc_info=True)
            _time.sleep(2)
            retries += 1
            if max_retries and retries >= max_retries:
                _log.critical("Max retries (%d) reached, giving up", max_retries)
                raise


def serve_http(port: int = 9876):
    """Start MCP Streamable HTTP server (auto-restart on crash)."""
    serve_http_forever(port)


if __name__ == "__main__":
    serve_http()