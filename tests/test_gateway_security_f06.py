"""
Test Suite — Gateway F-06 回归护栏（回环写端点鉴权 / CSRF / 配对信任）
=====================================================================
覆盖本轮 F-06 修复：

1. 状态改变类回环端点（``/pipeline/run``、``/migrations/run``）不再免鉴权，
   必须携带 Bearer token 或 ``X-MemAll-CSRF``。
2. ``GET /ui/session`` 仅向 loopback + 可信 Origin 下发 CSRF token。
3. ``/pair`` 改为一次性配对码挑战-响应：缺失/错误一律 403 且不建立信任，
   成功后轮换配对码并返回本机 auth token。
4. 限流提前到 SPA 放行之前（未鉴权端点同样受限流保护）。
"""

import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from aiohttp import web

from memall.gateway import (
    MemAllGateway,
    _ALWAYS_PUBLIC_PATHS,
    _CSRF_REQUIRED_PATHS,
    _SPA_PAGE_PATHS,
)
from memall.gateway_utils import _require_csrf
from memall.core.rate_limiter import get_rate_limiter


# ── Fake request / handler ─────────────────────────────────────────────

class _Req:
    """Minimal stand-in for aiohttp.web.Request (only the fields we read)."""

    def __init__(self, path, method="GET", remote="127.0.0.1",
                 headers=None, query=None, body=None):
        self.path = path
        self.method = method
        self.remote = remote
        self.headers = headers or {}
        self.query = query or {}
        self._body = body

    async def json(self):
        if self._body is None:
            raise ValueError("no JSON body")
        return self._body


async def _ok_handler(request):
    return web.json_response({"reached": True})


def _run(coro):
    return asyncio.run(coro)


def _reset_limiter():
    get_rate_limiter().reset()


# ── 1. 常量契约 ────────────────────────────────────────────────────────

def test_state_changing_paths_are_csrf_gated():
    for p in ("/pipeline/run", "/migrations/run"):
        assert p not in _SPA_PAGE_PATHS, f"{p} must not be token-free SPA route"
        assert p in _CSRF_REQUIRED_PATHS, f"{p} must require CSRF"
    # 只读状态查询仍可免 token（loopback）
    assert "/migrations/status" in _SPA_PAGE_PATHS


def test_public_paths_exclude_state_changers():
    for p in ("/pipeline/run", "/migrations/run", "/memories", "/db/stats"):
        assert p not in _ALWAYS_PUBLIC_PATHS, f"{p} must not be always-public"


# ── 2. _require_csrf ───────────────────────────────────────────────────

def test_require_csrf_rejects_missing_and_wrong():
    token = "csrf-abc"
    assert _require_csrf(_Req("/x", headers={}), token) is not None
    assert _require_csrf(_Req("/x", headers={"X-MemAll-CSRF": "nope"}), token) is not None
    assert _require_csrf(_Req("/x", headers={"X-MemAll-CSRF": token}), token) is None


# ── 3. 中间件：状态改变端点鉴权 ────────────────────────────────────────

def test_pipeline_run_requires_token_or_csrf():
    gw = MemAllGateway(host="127.0.0.1", port=0)
    _reset_limiter()

    # loopback、无任何凭证 → 403（CSRF）
    resp = _run(gw._auth_middleware(
        _Req("/pipeline/run", method="POST", body={}), _ok_handler))
    assert resp.status == 403, f"expected 403, got {resp.status}"

    # 错误的 CSRF → 403
    resp = _run(gw._auth_middleware(
        _Req("/pipeline/run", method="POST",
             headers={"X-MemAll-CSRF": "wrong"}, body={}), _ok_handler))
    assert resp.status == 403

    # 正确 CSRF → 放行
    resp = _run(gw._auth_middleware(
        _Req("/pipeline/run", method="POST",
             headers={"X-MemAll-CSRF": gw._csrf_token}, body={}), _ok_handler))
    assert resp.status == 200, f"expected 200, got {resp.status}"

    # Bearer token 亦可
    resp = _run(gw._auth_middleware(
        _Req("/pipeline/run", method="POST",
             headers={"Authorization": f"Bearer {gw._auth_token}"}, body={}), _ok_handler))
    assert resp.status == 200


def test_migrations_run_requires_token_or_csrf():
    gw = MemAllGateway(host="127.0.0.1", port=0)
    _reset_limiter()
    resp = _run(gw._auth_middleware(
        _Req("/migrations/run", method="POST", body={}), _ok_handler))
    assert resp.status == 403
    resp = _run(gw._auth_middleware(
        _Req("/migrations/run", method="POST",
             headers={"X-MemAll-CSRF": gw._csrf_token}, body={}), _ok_handler))
    assert resp.status == 200


def test_cross_origin_state_change_blocked():
    gw = MemAllGateway(host="127.0.0.1", port=0)
    _reset_limiter()
    resp = _run(gw._auth_middleware(
        _Req("/pipeline/run", method="POST",
             headers={"Origin": "http://evil.example",
                      "X-MemAll-CSRF": gw._csrf_token}, body={}), _ok_handler))
    assert resp.status == 403, "untrusted Origin must be rejected even with CSRF"


def test_non_loopback_requires_bearer():
    gw = MemAllGateway(host="127.0.0.1", port=0)
    _reset_limiter()
    # 非回环、无 token → 401
    resp = _run(gw._auth_middleware(
        _Req("/db/stats", remote="192.168.1.7"), _ok_handler))
    assert resp.status == 401
    # 非回环 + 正确 token → 放行
    resp = _run(gw._auth_middleware(
        _Req("/db/stats", remote="192.168.1.7",
             headers={"Authorization": f"Bearer {gw._auth_token}"}), _ok_handler))
    assert resp.status == 200


# ── 4. /ui/session CSRF 下发 ───────────────────────────────────────────

def test_ui_session_only_loopback():
    gw = MemAllGateway(host="127.0.0.1", port=0)
    _reset_limiter()

    resp = _run(gw._handle_ui_session(_Req("/ui/session")))
    assert resp.status == 200
    payload = json.loads(resp.text)
    assert payload.get("csrf_token") == gw._csrf_token

    # 非回环 → 403
    resp = _run(gw._handle_ui_session(_Req("/ui/session", remote="10.0.0.9")))
    assert resp.status == 403

    # 不可信 Origin → 403
    resp = _run(gw._handle_ui_session(
        _Req("/ui/session", headers={"Origin": "http://evil.example"})))
    assert resp.status == 403


# ── 5. /pair 一次性配对码 ──────────────────────────────────────────────

def test_pair_rejects_missing_or_wrong_code():
    gw = MemAllGateway(host="127.0.0.1", port=0)
    _reset_limiter()

    resp = _run(gw._handle_pair(_Req("/pair", method="POST",
                                     body={"device_name": "peer"})))
    assert resp.status == 403
    assert json.loads(resp.text).get("paired") is False

    resp = _run(gw._handle_pair(_Req("/pair", method="POST",
                                     body={"device_name": "peer", "code": "00000000"})))
    assert resp.status == 403
    assert gw._pairing_code, "failed pairing must not consume the code"


def test_pair_success_rotates_code_and_returns_token():
    gw = MemAllGateway(host="127.0.0.1", port=0)
    _reset_limiter()
    original = gw._pairing_code

    resp = _run(gw._handle_pair(_Req("/pair", method="POST",
                                     body={"device_name": "peer", "code": original})))
    assert resp.status == 200
    payload = json.loads(resp.text)
    assert payload.get("paired") is True
    assert payload.get("token") == gw._auth_token
    assert gw._pairing_code != original, "one-time code must rotate after use"

    # 旧码重放 → 拒绝
    resp = _run(gw._handle_pair(_Req("/pair", method="POST",
                                     body={"device_name": "peer", "code": original})))
    assert resp.status == 403


def test_pair_with_peer_accepts_code():
    import inspect
    from memall.gateway_peers import pair_with_peer
    sig = inspect.signature(pair_with_peer)
    assert "code" in sig.parameters, "pair_with_peer must accept a pairing code"


# ── 6. 限流前置 ────────────────────────────────────────────────────────

def test_rate_limit_applies_to_spa_routes():
    gw = MemAllGateway(host="127.0.0.1", port=0)
    _reset_limiter()
    gw._rate_limit_get = 3

    for i in range(3):
        resp = _run(gw._auth_middleware(_Req("/dashboard"), _ok_handler))
        assert resp.status == 200, f"request {i} unexpectedly limited"

    resp = _run(gw._auth_middleware(_Req("/dashboard"), _ok_handler))
    assert resp.status == 429, "SPA route must be rate-limited before the allow-list"


def test_health_is_not_rate_limited():
    gw = MemAllGateway(host="127.0.0.1", port=0)
    _reset_limiter()
    gw._rate_limit_get = 1
    for _ in range(5):
        resp = _run(gw._auth_middleware(_Req("/health"), _ok_handler))
        assert resp.status == 200, "/health must stay exempt from rate limiting"