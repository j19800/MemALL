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
import hmac
import logging
import os
import secrets
import socket
import threading
import time
from pathlib import Path
from typing import Optional, Dict, Any


from aiohttp import web


from memall.gateway_utils import (
    _require_auth, _require_csrf, _auth_ok, origin_allowed,
    _CORS_HEADERS, _CORS_ALLOWED_ORIGINS, is_loopback_request,
)
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


# Paths that must never be rate-limited: liveness probes, the SPA document
# itself and static assets (a single page load fetches many of them).
_RATE_LIMIT_EXEMPT_PATHS = frozenset({"/", "/health", "/favicon.ico"})
_STATIC_PATH_PREFIXES = ("/static",)


# State-changing endpoints that the SPA reaches from loopback without a Bearer
# token.  They must echo the per-instance CSRF token (``X-MemAll-CSRF``) issued
# by ``GET /ui/session``, otherwise a poisoned local script / drive-by page
# could re-run migrations or the pipeline.
_CSRF_REQUIRED_PATHS = frozenset({"/pipeline/run", "/migrations/run"})


# Web-UI page/data routes that skip the token check — but only for loopback
# peers (see ``security.open_api_loopback_only``) and only from trusted origins.
# NOTE: state-changing routes are intentionally absent here; they are gated by
# ``_CSRF_REQUIRED_PATHS`` above.
_SPA_PAGE_PATHS = frozenset({
    "/dashboard", "/graph", "/artifact", "/features", "/recent", "/todos",
    "/v30", "/timeline", "/timeline/api", "/db/stats", "/agents",
    "/debt/stats", "/reflection/dashboard", "/ask", "/migrations/status",
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
        # Per-instance CSRF token for state-changing SPA endpoints.  Handed to
        # the local UI by ``GET /ui/session`` (loopback + trusted origin only).
        self._csrf_token: str = secrets.token_urlsafe(32)
        # One-time pairing code (out-of-band trust anchor).  Rotated after every
        # successful pair; a fresh one is generated per gateway instance.
        self._pairing_code: str = secrets.token_hex(4)
        logger.info("Gateway auth token: %s ...%s",
                     self._auth_token[:8], self._auth_token[-4:])
        logger.info("Gateway pairing code: %s (one-time, rotate after pairing)",
                     self._pairing_code)

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

    def _check_rate_limit(self, request: web.Request) -> Optional[web.Response]:
        """Return a 429 Response if *request* exceeds its per-IP budget, else None.

        POST gets 30/min (``/mcp`` 60/min), everything else 100/min.  Runs
        before the SPA carve-out so unauthenticated endpoints are covered too.
        """
        client_ip = request.remote or "unknown"
        rl = get_rate_limiter()
        if request.method == "POST":
            limit = (getattr(self, "_rate_limit_mcp", 60)
                     if request.path == "/mcp"
                     else getattr(self, "_rate_limit_post", 30))
        else:
            limit = getattr(self, "_rate_limit_get", 100)
        if not rl.allow(client_ip, limit=limit):
            return web.json_response(
                {"error": "rate limit exceeded"}, status=429,
                headers={"Retry-After": "60"},
            )
        return None

    @web.middleware
    async def _auth_middleware(self, request: web.Request,
                               handler: Any) -> web.Response:
        """Require a valid Bearer token, with a loopback-only carve-out for the SPA.

        Layers, in order:
          0. OPTIONS short-circuits (CORS preflight).
          1. Rate limit **first** — covers the unauthenticated public and SPA
             endpoints that previously slipped past it.
          2. Always public: ``/``, ``/health``, ``/pair``, ``/favicon.ico``,
             ``/static``.
          3. Any request carrying an ``Origin`` that is not an explicitly trusted
             loopback origin is rejected — this covers GETs too, closing the
             DNS-rebinding / drive-by read hole against a browser on the host.
          4. State-changing SPA endpoints (``_CSRF_REQUIRED_PATHS``) require a
             full Bearer token **or** the per-instance CSRF token.
          5. Non-loopback peers must present a valid token for everything else.
          6. The remaining SPA endpoints are unauthenticated **only** for
             loopback peers.  Controlled by ``security.open_api_loopback_only``.
        """
        path = request.path
        if request.method == "OPTIONS":
            return await handler(request)
        # 1. Rate limit before any allow-list short-circuit.
        if path not in _RATE_LIMIT_EXEMPT_PATHS and not path.startswith(_STATIC_PATH_PREFIXES):
            rl_err = self._check_rate_limit(request)
            if rl_err is not None:
                return rl_err
        # 2. Always-public endpoints.
        if path in _ALWAYS_PUBLIC_PATHS:
            return await handler(request)
        # 3. CSRF / DNS-rebinding defense: untrusted Origin is rejected outright.
        if not origin_allowed(request):
            return web.json_response(
                {"error": "forbidden", "message": "cross-origin request blocked"}, status=403
            )
        # 4. State-changing SPA endpoints need a token (Bearer) or CSRF proof.
        if path in _CSRF_REQUIRED_PATHS and request.method in ("POST", "PUT", "DELETE"):
            if _auth_ok(request, self._auth_token):
                return await handler(request)
            csrf_err = _require_csrf(request, self._csrf_token)
            if csrf_err is not None:
                return csrf_err
            return await handler(request)
        # 5. Non-loopback peers must authenticate.
        if self._open_api_loopback_only() and not is_loopback_request(request):
            err = _require_auth(request, self._auth_token)
            if err is not None:
                return err
            return await handler(request)
        # 6. Loopback SPA carve-out (read-only + benign writes only).
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
        return await handler(request)

    async def _handle_ui_session(self, request: web.Request) -> web.Response:
        """GET /ui/session — hand the CSRF token to the local web UI.

        Restricted to loopback peers with a trusted (or absent) Origin: a remote
        host or a cross-origin page must not learn the token.  Fails closed.
        """
        if not origin_allowed(request) or not is_loopback_request(request):
            return web.json_response(
                {"error": "forbidden", "message": "loopback origin required"},
                status=403,
            )
        return web.json_response({"csrf_token": self._csrf_token})

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
        """POST /pair — one-time-code challenge/response pairing handshake.

        The caller must present the gateway's one-time *pairing code* (printed
        in the gateway log / shown in the local UI).  The code is the
        out-of-band trust anchor: without it, a LAN host that merely reaches the
        port cannot "pair".  On success the code is rotated (single use) and the
        gateway returns its auth token so the peer can authenticate subsequent
        federated queries.
        """
        data = await self._read_json(request)
        if data is None:
            return web.json_response({"error": "invalid JSON body"}, status=400,)
        device_name = str(data.get("device_name", "unknown"))[:128]
        provided_code = str(data.get("code", ""))
        expected = self._pairing_code or ""
        # Constant-time compare; an empty/absent code can never match.
        if not expected or not provided_code or not hmac.compare_digest(provided_code, expected):
            logger.warning("Pairing rejected for device=%r from %s (bad pairing code)",
                           device_name, request.remote)
            return web.json_response(
                {"paired": False, "error": "invalid pairing code"},
                status=403,
            )
        # One-time code: rotate immediately so a replay cannot pair again.
        self._pairing_code = secrets.token_hex(4)
        logger.info("Paired with device=%r from %s; new pairing code: %s",
                    device_name, request.remote, self._pairing_code)
        return web.json_response(
            {
                "paired": True,
                "peer_name": socket.gethostname(),
                "device_name": device_name,
                "remote_address": request.remote,
                "token": self._auth_token,
            },
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
