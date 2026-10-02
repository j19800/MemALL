"""
Phase 15: Gateway — Device Interconnection
==========================================
Local HTTP gateway (aiohttp async).  ``MemAllGateway`` composes the feature
mixes: HTML pages (``gateway_html_handlers``), REST/JSON API
(``gateway_rest_handlers``), ``/mcp`` + ``/metrics``
(``gateway_mcp_handlers``) and the route table (``gateway_routes``).  The sync
protocol (export/import) and LAN discovery / pairing / federated queries live
in ``gateway_sync`` and ``gateway_peers`` and are re-exported here.
"""


import asyncio
import logging
import os
import secrets
import threading
import time
from pathlib import Path
from typing import Optional, Dict, Any


from aiohttp import web


from memall.gateway_utils import _require_auth, origin_allowed, _CORS_HEADERS, _CORS_ALLOWED_ORIGINS, is_loopback_request
from memall.gateway_html_handlers import HtmlHandlersMixin
from memall.gateway_rest_handlers import RestHandlersMixin
from memall.gateway_mcp_handlers import McpHandlersMixin, _MCP_TOOL_EXECUTOR, _MCP_TOOL_HEAVY
from memall.gateway_routes import RoutesMixin
from memall.core.db import pool_conn, init_db
from memall.core.rate_limiter import get_rate_limiter


logger = logging.getLogger("memall.gateway")


# ══════════════════════════════════════════════════════════════════
# Public route allow-lists
# ══════════════════════════════════════════════════════════════════


# Endpoints reachable with no token and no Origin restriction: liveness,
# the pairing handshake and pure static assets.
_ALWAYS_PUBLIC_PATHS = frozenset({
    "/", "/health", "/pair", "/favicon.ico", "/static",
})


# Web-UI page/data routes that skip the token check — but only for loopback
# peers (see ``security.open_api_loopback_only``) and only from trusted origins.
_SPA_PAGE_PATHS = frozenset({
    "/dashboard", "/graph", "/artifact", "/features", "/recent", "/todos",
    "/v30", "/timeline", "/timeline/api", "/db/stats", "/agents",
    "/debt/stats", "/reflection/dashboard", "/ask", "/pipeline/run",
    "/migrations/run", "/migrations/status",
})


# ══════════════════════════════════════════════════════════════════
# Local HTTP Gateway (aiohttp async)
# ══════════════════════════════════════════════════════════════════


@web.middleware
async def _cors_middleware(request: web.Request, handler) -> web.Response:
    """Add CORS headers to every response automatically."""
    try:
        response = await handler(request)
    except web.HTTPException as exc:
        response = exc
    origin = request.headers.get("Origin", "")
    if "*" in _CORS_ALLOWED_ORIGINS or origin in _CORS_ALLOWED_ORIGINS:
        response.headers["Access-Control-Allow-Origin"] = origin if origin else "*"
    response.headers["Access-Control-Allow-Methods"] = _CORS_HEADERS["Access-Control-Allow-Methods"]
    response.headers["Access-Control-Allow-Headers"] = _CORS_HEADERS["Access-Control-Allow-Headers"]
    return response


class MemAllGateway(HtmlHandlersMixin, RestHandlersMixin,
                    McpHandlersMixin, RoutesMixin):
    """Local HTTP gateway exposing MemALL operations over REST.

    Launches an ``aiohttp`` web server on a background thread.
    Listens only on localhost for security.

    Attributes:
        host (str): Bind address, always ``127.0.0.1``.
        port (int): TCP port.  Default 9919.
    """

    def __init__(self, host: str = "127.0.0.1", port: int = 9920,
                 secret_key: str = "") -> None:
        self.host = host
        self.port = port
        self._app: Optional[web.Application] = None
        self._runner: Optional[web.AppRunner] = None
        self._start_time: float = 0.0
        self._lock = threading.Lock()
        self._loop_thread: Optional[threading.Thread] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        # Auth token: use provided key or auto-generate one
        self._auth_token: str = secret_key or secrets.token_hex(32)
        logger.info("Gateway auth token: %s ...%s",
                     self._auth_token[:8], self._auth_token[-4:])

    @staticmethod
    def _open_api_loopback_only() -> bool:
        """Whether the token-free SPA endpoints are restricted to loopback peers.

        Defaults to True (``security.open_api_loopback_only``); failing to read
        the config falls back to the safe value.
        """
        try:
            from memall.config import get_config as _get_config
            return bool(_get_config("security.open_api_loopback_only", True))
        except Exception:
            return True

    # ── Public API ──

    def start(self) -> None:
        """启动后台事件循环线程（非阻塞）"""
        with self._lock:
            if self._runner is not None:
                return
            self._start_time = time.time()
            self._loop = asyncio.new_event_loop()
            self._loop_thread = threading.Thread(
                target=self._run_async, daemon=True
            )
            self._loop_thread.start()

    def stop(self) -> None:
        """优雅关闭 gateway"""
        with self._lock:
            if self._runner is not None:
                _runner = self._runner
                self._runner = None
                if self._loop and not self._loop.is_closed():
                    self._loop.call_soon_threadsafe(
                        lambda: asyncio.ensure_future(
                            self._cleanup(_runner), loop=self._loop
                        )
                    )

    # ── Internal async runner ──

    def _run_async(self) -> None:
        """在新线程中运行异步事件循环"""
        asyncio.set_event_loop(self._loop)
        self._app = web.Application(middlewares=[_cors_middleware, self._auth_middleware], client_max_size=10 * 1024 * 1024)
        # ── MCP startup: force correct DB_PATH and ensure DB exists ──
        from memall.core import db as _memall_db
        _user_home = os.environ.get("USERPROFILE") or str(Path.home())
        _correct_path = os.path.join(_user_home, ".memall", "data.db")
        _memall_db.DB_PATH = Path(_correct_path)
        init_db()
        self._setup_routes(self._app)
        self._runner = web.AppRunner(self._app)
        self._loop.run_until_complete(self._runner.setup())
        site = web.TCPSite(self._runner, self.host, self.port)
        self._loop.run_until_complete(site.start())
        self._loop.run_forever()

    async def _cleanup(self, runner: web.AppRunner) -> None:
        """清理 aiohttp runner 并停止事件循环"""
        await runner.cleanup()
        # Shutdown MCP thread pools
        _MCP_TOOL_EXECUTOR.shutdown(wait=False)
        _MCP_TOOL_HEAVY.shutdown(wait=False)
        self._loop.stop()

    async def _read_json(self, request: web.Request) -> Optional[Dict]:
        """Read and parse JSON body, returning None on invalid input."""
        try:
            return await request.json()
        except Exception:
            return None

    # ── Auth middleware ──

    @web.middleware
    async def _auth_middleware(self, request: web.Request,
                               handler: Any) -> web.Response:
        """Require a valid Bearer token, with a loopback-only carve-out for the SPA.

        Three layers:
          1. Always public: ``/``, ``/health``, ``/pair``, ``/favicon.ico``, OPTIONS.
          2. Any request carrying an ``Origin`` that is not an explicitly trusted
             loopback origin is rejected — this covers GETs too (previously only
             state-changing methods were checked), which closes the
             DNS-rebinding / drive-by read hole against a browser on the host.
          3. The remaining SPA endpoints are unauthenticated **only** for loopback
             peers (the local web UI).  Any other peer must present a valid
             token.  Controlled by ``security.open_api_loopback_only``.
        """
        path = request.path
        if request.method == "OPTIONS" or path in _ALWAYS_PUBLIC_PATHS:
            return await handler(request)
        # CSRF / DNS-rebinding defense: untrusted Origin is rejected outright.
        if not origin_allowed(request):
            return web.json_response(
                {"error": "forbidden", "message": "cross-origin request blocked"}, status=403
            )
        # SPA carve-out applies to loopback peers only (unless disabled).
        if self._open_api_loopback_only() and not is_loopback_request(request):
            err = _require_auth(request, self._auth_token)
            if err is not None:
                return err
            return await handler(request)
        if path in _SPA_PAGE_PATHS:
            return await handler(request)
        # SPA read-only endpoints: GET /memories and GET /api/* and GET /persona/* and GET /graph/*
        if request.method == "GET" and (path.startswith("/memories") or path.startswith("/api/") or path.startswith("/persona/") or path.startswith("/sessions/") or path.startswith("/federation/") or path.startswith("/graph/") or path.startswith("/api/intelligence")):
            return await handler(request)
        # SPA write endpoints: POST/PUT to known paths
        if request.method in ("POST", "PUT") and (path in ("/memories", "/memories/smart-store", "/memories/import", "/db/optimize", "/db/vacuum", "/debt/scan")):
            return await handler(request)
        if request.method == "PUT" and path.startswith("/memories/"):
            return await handler(request)
        err = _require_auth(request, self._auth_token)
        if err is not None:
            return err

        # Rate limit: 30/min for POST, 100/min for GET
        client_ip = request.remote or "unknown"
        rl = get_rate_limiter()
        if request.method == "POST":
            # MCP JSON-RPC endpoint gets a higher limit (60/min)
            if request.path == "/mcp":
                limit = getattr(self, "_rate_limit_mcp", 60)
            else:
                limit = getattr(self, "_rate_limit_post", 30)
            if not rl.allow(client_ip, limit=limit):
                return web.json_response(
                    {"error": "rate limit exceeded"}, status=429,
                    headers={"Retry-After": "60"},
                )
        else:
            limit = getattr(self, "_rate_limit_get", 100)
            if not rl.allow(client_ip, limit=limit):
                return web.json_response(
                    {"error": "rate limit exceeded"}, status=429,
                    headers={"Retry-After": "60"},
                )

        return await handler(request)

    async def _handle_health(self, request: web.Request) -> web.Response:
        uptime_s = time.time() - self._start_time
        with pool_conn() as conn:
            mc = conn.execute(
                "SELECT COUNT(*) AS c FROM memories"
            ).fetchone()["c"]
        return web.json_response(
            {
                "status": "ok",
                "uptime": round(uptime_s, 1),
                "memory_count": mc,
            },
                    )

    @staticmethod
    def _validate(data: dict, model) -> tuple[dict | None, str | None]:
        """Validate data against a Pydantic model. Returns (validated_dict, None) or (None, error_msg)."""
        try:
            m = model(**data)
            return m.model_dump(), None
        except Exception as e:
            return None, str(e)

    async def _handle_pair(self, request: web.Request) -> web.Response:
        data = await self._read_json(request)
        if data is None:
            return web.json_response({"error": "invalid JSON body"}, status=400,)
        try:
            device_name = data.get("device_name", "unknown")
            remote_addr = request.remote
            return web.json_response(
                {
                    "paired": True,
                    "peer_name": device_name,
                    "remote_address": remote_addr,
                },
                            )
        except Exception as exc:
            return web.json_response(
                {"error": str(exc)}, status=500,
            )

    async def _handle_serve_frontend(self, request: web.Request) -> web.Response:
        """GET / — serve frontend index.html if available."""
        _frontend_dir = Path(__file__).resolve().parent.parent.parent / "frontend"
        if _frontend_dir.exists() and (_frontend_dir / "index.html").exists():
            index = _frontend_dir / "index.html"
            return web.Response(text=index.read_text(encoding="utf-8"), content_type="text/html",
                                )
        return web.json_response({"status": "ok", "frontend": "not found"},)


# ── Re-exports (moved to dedicated modules for modularity) ──
from memall.gateway_sync import (  # noqa: E402,F401
    export_bundle, import_bundle, _import_identity, _import_memories,
    _import_edges, _path_within_any,
)
from memall.gateway_peers import (  # noqa: E402,F401
    PEERS_FILE, _load_peers, _save_peers,
    start_discovery, stop_discovery, discover_peers, pair_with_peer,
    list_peers, federated_retrieve, federated_retrieve_async,
)
