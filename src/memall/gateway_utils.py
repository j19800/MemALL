"""Gateway utility functions — extracted from gateway.py for modularity.

Contains: HTML escaping, CORS headers, auth, response helpers, debt cache.
"""

import hmac
import json
import logging
from pathlib import Path
from typing import Any, Dict, Optional

from aiohttp import web

logger = logging.getLogger("memall.gateway.utils")

# ── CORS constants ──────────────────────────────────────────────────────────
_CORS_HEADERS = {
    "Access-Control-Allow-Methods": "POST, GET, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type, Authorization, X-MemAll-CSRF",
}

_CORS_ALLOWED_ORIGINS = {"http://127.0.0.1:9919", "http://localhost:9919", "http://127.0.0.1:9920", "http://localhost:9920", "http://127.0.0.1:8199"}


def origin_allowed(request: web.Request) -> bool:
    """True if the request Origin (if any) is from a trusted loopback origin.

    Absent Origin (curl/native clients / same-origin GET) is allowed.
    """
    origin = request.headers.get("Origin", "")
    if not origin:
        return True
    return origin in _CORS_ALLOWED_ORIGINS


def is_loopback_request(request: web.Request) -> bool:
    """True if the peer address is a loopback address (127.0.0.0/8, ::1).

    Used to scope the unauthenticated SPA endpoints to the local UI only.
    An unresolvable or missing peer address is treated as non-loopback
    (i.e. it must authenticate) — fail closed.
    """
    import ipaddress
    remote = request.remote or ""
    if not remote:
        return False
    try:
        return ipaddress.ip_address(remote).is_loopback
    except ValueError:
        return False


def esc_html(text: str) -> str:
    """Escape HTML special characters."""
    if not text:
        return ""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def _density_color(count: int, max_count: int) -> str:
    """Return a green-scale hex color based on density ratio."""
    ratio = count / max_count if max_count > 0 else 0
    r = int(0x2e * ratio + 0xe8 * (1 - ratio))
    g = int(0x7d * ratio + 0xf5 * (1 - ratio))
    b = int(0x32 * ratio + 0xe9 * (1 - ratio))
    return f"#{r:02x}{g:02x}{b:02x}"


def _cors_headers(request: web.Request) -> Dict[str, str]:
    """Build CORS headers, echoing Origin only if it's in the allowed list."""
    origin = request.headers.get("Origin", "")
    if origin in _CORS_ALLOWED_ORIGINS:
        return {**_CORS_HEADERS, "Access-Control-Allow-Origin": origin}
    return _CORS_HEADERS


def _extract_token(request: web.Request) -> str:
    """Return the token from the ``Authorization: Bearer`` header or ``?token=``."""
    provided = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
    if not provided:
        provided = request.query.get("token", "")
    return provided


def _auth_ok(request: web.Request, auth_token: str) -> bool:
    """True if the request carries a valid Bearer/query token (constant-time)."""
    provided = _extract_token(request)
    return bool(provided) and hmac.compare_digest(provided, auth_token)


def _require_auth(request: web.Request, auth_token: str) -> Optional[web.Response]:
    """Return a 401 Response if the request does not carry a valid token, else None.

    The token can be provided via the ``Authorization: Bearer <token>``
    header or the ``token`` query parameter.
    """
    if not _auth_ok(request, auth_token):
        return web.json_response(
            {"error": "unauthorized", "message": "valid Bearer token required"},
            status=401,
        )
    return None


def _require_csrf(request: web.Request, csrf_token: str) -> Optional[web.Response]:
    """Return a 403 Response unless the request echoes the per-instance CSRF token.

    State-changing loopback endpoints that the SPA reaches without a Bearer
    token must carry the token issued by ``GET /ui/session`` in the
    ``X-MemAll-CSRF`` header.  A cross-origin page can neither read that token
    (CORS) nor set this custom header without a preflight, which the Origin
    gate already rejects — so this blocks drive-by / DNS-rebinding writes.
    """
    provided = request.headers.get("X-MemAll-CSRF", "")
    if not provided or not hmac.compare_digest(provided, csrf_token):
        return web.json_response(
            {"error": "forbidden", "message": "valid CSRF token required"},
            status=403,
        )
    return None


def _ok(data=None):
    """Standard success response wrapper."""
    return {"success": True, "data": data}


_DEBT_SCAN_CACHE = Path.home() / ".memall" / "debt_scan_cache.json"


def _load_debt_cache():
    """Load debt scan cache from disk, or None."""
    if _DEBT_SCAN_CACHE.exists():
        try:
            return json.loads(_DEBT_SCAN_CACHE.read_text(encoding="utf-8"))
        except Exception:
            return None
    return None


def _save_debt_cache(data: dict):
    """Save debt scan cache to disk with history (last 20 scans)."""
    _DEBT_SCAN_CACHE.parent.mkdir(parents=True, exist_ok=True)
    prev = _load_debt_cache() or {}
    history = prev.get("history", [])
    scan = data.get("scan", {})
    if scan:
        history.append({
            "scan_time": scan.get("scan_time", ""),
            "line_count": scan.get("line_count", 0),
            "total": sum(scan.get("counts", {}).values()),
            "density": scan.get("density", 0),
            "severity_summary": scan.get("severity_summary", ""),
            "counts": scan.get("counts", {}),
        })
        history = history[-20:]
    data["history"] = history
    _DEBT_SCAN_CACHE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


# ── Helpers shared by gateway.py and gateway_html_handlers.py ──────

def _safe_int(val: Any, default: int = 0) -> int:
    """Safely parse an integer from query params, returning *default* on invalid input."""
    try:
        return int(val)
    except (TypeError, ValueError):
        return default


def _epoch_narrative(mems: list) -> str:
    """Generate a one-line narrative summary for an epoch's memories."""
    from collections import Counter
    cats = Counter()
    for m in mems:
        c = (getattr(m, "category", "general") or "general").strip()
        if c and c != "general":
            cats[c] += 1
    if not cats:
        return ""
    top = cats.most_common(3)
    parts = [f"{cat}({cnt})" for cat, cnt in top]
    return "核心：" + " · ".join(parts)
