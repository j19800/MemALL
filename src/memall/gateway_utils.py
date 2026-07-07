"""Gateway utility functions — extracted from gateway.py for modularity.

Contains: HTML escaping, CORS headers, auth, response helpers, debt cache.
"""

import hmac
import json
import logging
from pathlib import Path
from typing import Dict, Optional

from aiohttp import web

logger = logging.getLogger("memall.gateway.utils")

# ── CORS constants ──────────────────────────────────────────────────────────
_CORS_HEADERS = {
    "Access-Control-Allow-Methods": "POST, GET, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type, Authorization",
}

_CORS_ALLOWED_ORIGINS = {"http://127.0.0.1:9919", "http://localhost:9919", "http://127.0.0.1:8199"}


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
    """Build CORS headers, echoing Origin if it's in the allowed list."""
    origin = request.headers.get("Origin", "")
    if origin in _CORS_ALLOWED_ORIGINS:
        return {**_CORS_HEADERS, "Access-Control-Allow-Origin": origin}
    return _CORS_HEADERS


def _require_auth(request: web.Request, auth_token: str) -> Optional[web.Response]:
    """Return a 401 Response if the request does not carry a valid token, else None.

    The token can be provided via the ``Authorization: Bearer <token>``
    header or the ``token`` query parameter.
    """
    provided = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
    if not provided:
        provided = request.query.get("token", "")
    if not hmac.compare_digest(provided, auth_token):
        return web.json_response(
            {"error": "unauthorized", "message": "valid Bearer token required"},
            status=401,
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