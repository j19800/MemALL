"""MCP Streamable HTTP transport — persistent server, no STDIO dependency.

Implements MCP 2025-03-26 Streamable HTTP spec:
  POST /mcp  — JSON-RPC request/response (non-streaming tools)
  GET  /mcp  — SSE subscription for tool list changes
"""

import asyncio
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from aiohttp import web

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


async def handle_mcp_post(request: web.Request) -> web.Response:
    """Handle JSON-RPC requests via POST /mcp."""
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
        try:
            result_str = handle_call(tool_name, arguments)
            _intercept(tool_name, arguments, result_str)
            result_data = json.loads(result_str)
            content = [{"type": "text", "text": json.dumps(result_data, ensure_ascii=False)}]

            # ── Agent notifications: piggyback on every tool response ──
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
                    logger.warning("http_transport.py: silent error", exc_info=True)

            session_note = consume_session_note()
            if session_note:
                content.append({"type": "text", "text": session_note})
            return web.json_response({
                "jsonrpc": "2.0", "id": req_id,
                "result": {"content": content},
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
    except (ConnectionResetError, ConnectionAbortedError):
        logger.warning("http_transport.py: silent error", exc_info=True)
    return response


async def handle_info(request: web.Request) -> web.Response:
    try:
        from memall.core.db import get_conn
        conn = get_conn()
        total = conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
        db_ok = conn.execute("PRAGMA quick_check").fetchone()[0]
        conn.close()
        db_status = "ok" if db_ok == "ok" else "error"
    except Exception as e:
        total = 0
        db_status = f"error: {e}"

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
    app = web.Application()
    app.router.add_get("/health", handle_info)
    app.router.add_post("/mcp", handle_mcp_post)
    app.router.add_get("/mcp", handle_sse)
    app.router.add_get("/", handle_info)
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
        init_db()
    app.on_startup.append(_startup)
    return app


def serve_http(port: int = 9876):
    """Start MCP Streamable HTTP server on given port."""
    logging.basicConfig(level=logging.INFO, format="[MCP-HTTP %(asctime)s] %(levelname)s %(message)s", datefmt="%H:%M:%S")
    app = create_app()
    _log.info("MCP HTTP server listening on http://127.0.0.1:%d/mcp", port)
    web.run_app(app, host="127.0.0.1", port=port, print=lambda _: None)


if __name__ == "__main__":
    serve_http()