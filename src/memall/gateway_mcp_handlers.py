"""MCP Streamable HTTP handlers — extracted from gateway.py for modularity.

Houses the JSON-RPC ``/mcp`` endpoint (POST), its SSE subscription (GET) and
the ``/metrics`` endpoint, together with the thread pools that run synchronous
MCP tool calls off the event loop.
"""

import asyncio
import concurrent.futures
import json
import logging
import os

from aiohttp import web

logger = logging.getLogger("memall.gateway.mcp")

# Thread pool for synchronous MCP tool calls (keeps event loop responsive)
_MCP_TOOL_EXECUTOR = concurrent.futures.ThreadPoolExecutor(max_workers=12)
_MCP_TOOL_HEAVY = concurrent.futures.ThreadPoolExecutor(max_workers=2)
_MCP_TOOL_TIMEOUT = 120  # max seconds for a single tool call
_MCP_HEAVY_TIMEOUT = 600  # max seconds for heavy operations


class McpHandlersMixin:
    """POST/GET /mcp plus /metrics."""

    async def _handle_mcp_post(self, request: web.Request) -> web.Response:
        """POST /mcp — JSON-RPC request/response (MCP Streamable HTTP)."""
        from memall.mcp.adapter import TOOL_DEFINITIONS, handle_call, _intercept, consume_session_note
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
            return web.json_response({}, status=202)

        # ── Initialize ──
        if method == "initialize":
            from memall.onboarding import _get_status
            identity_path = os.path.join(os.path.expanduser("~"), ".memall", "identity.json")
            user_id = "default"
            actor_id = "unknown"
            try:
                if os.path.exists(identity_path):
                    with open(identity_path, encoding="utf-8") as f:
                        ident = json.load(f)
                    user_id = ident.get("user_id", "default")
                    actor_id = ident.get("actor_id", "unknown")
            except Exception as e:
                logger.warning("Failed to read identity.json: %s", e)
            try:
                onboarding_status = _get_status(user_id)
                onboarding_completed = bool(onboarding_status.get("completed"))
                onboarding_step = onboarding_status.get("current_step", 1)
            except Exception:
                onboarding_completed = False
                onboarding_step = 1
            logger.info("Initialize: user=%s actor=%s", user_id, actor_id)
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
            logger.info("Call tool: %s %s", tool_name, _arg_preview[:60])

            _HEAVY_ACTIONS = frozenset({
                "run_pipeline", "index_rebuild", "adaptive",
                "gateway", "hub_sync", "persona_profile",
            })
            _HEAVY_TOOLS = frozenset({"memall_system", "memall_write"})
            action = arguments.get("action", "")
            is_heavy = (
                tool_name in _HEAVY_TOOLS and action in _HEAVY_ACTIONS
            ) or (
                tool_name == "memall_write" and action == "forget"
            )
            if is_heavy:
                _pool = _MCP_TOOL_HEAVY
                _timeout = _MCP_HEAVY_TIMEOUT
            else:
                _pool = _MCP_TOOL_EXECUTOR
                _timeout = _MCP_TOOL_TIMEOUT

            def _run_tool():
                result_str = handle_call(tool_name, arguments)
                _intercept(tool_name, arguments, result_str)
                result_data = json.loads(result_str)
                content = [{"type": "text", "text": json.dumps(result_data, ensure_ascii=False)}]
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
                        logger.warning("MCP tool notification error", exc_info=True)
                session_note = consume_session_note()
                if session_note:
                    content.append({"type": "text", "text": session_note})
                return content

            try:
                loop = asyncio.get_running_loop()
                content = await asyncio.wait_for(
                    loop.run_in_executor(_pool, _run_tool),
                    timeout=_timeout,
                )
                return web.json_response({
                    "jsonrpc": "2.0", "id": req_id,
                    "result": {"content": content},
                })
            except asyncio.TimeoutError:
                logger.error("Tool %s timed out after %ss", tool_name, _timeout)
                return web.json_response({
                    "jsonrpc": "2.0", "id": req_id,
                    "error": {"code": -32603, "message": f"tool {tool_name} timed out after {_timeout}s"},
                })
            except Exception as e:
                logger.error("Tool %s failed: %s", tool_name, e, exc_info=True)
                return web.json_response({
                    "jsonrpc": "2.0", "id": req_id,
                    "error": {"code": -32603, "message": str(e)},
                })

        # ── SetLevel ──
        if method == "setLevel":
            level = params.get("level", "info")
            logger.setLevel(getattr(logging, level.upper(), logging.INFO))
            return web.json_response({"jsonrpc": "2.0", "id": req_id, "result": {}})

        return web.json_response({
            "jsonrpc": "2.0", "id": req_id,
            "error": {"code": -32601, "message": f"unknown method: {method}"},
        })


    async def _handle_mcp_sse(self, request: web.Request) -> web.Response:
        """GET /mcp — SSE subscription for tool list change notifications."""
        from memall.mcp.adapter import TOOL_DEFINITIONS
        response = web.StreamResponse(
            status=200, reason="OK",
            headers={
                "Content-Type": "text/event-stream",
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "Access-Control-Allow-Origin": "http://127.0.0.1:9919",
            },
        )
        await response.prepare(request)
        event = {"type": "tool_list_changed", "tools": [_t["name"] for _t in TOOL_DEFINITIONS]}
        await response.write(f"data: {json.dumps(event, ensure_ascii=False)}\n\n".encode())
        try:
            while True:
                await asyncio.sleep(30)
                await response.write(b": heartbeat\n\n")
        except (ConnectionResetError, ConnectionAbortedError, ConnectionError):
            logger.info("SSE client disconnected")
        except asyncio.CancelledError:
            logger.info("SSE task cancelled")
        except Exception:
            logger.warning("SSE unexpected error", exc_info=True)
        return response


    async def _handle_metrics(self, request: web.Request) -> web.Response:
        """GET /metrics — return process metrics as JSON."""
        from memall.core.metrics import get_metrics
        snapshot = await asyncio.to_thread(lambda: get_metrics().snapshot())
        return web.json_response(snapshot,)
