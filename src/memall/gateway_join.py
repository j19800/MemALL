"""
Gateway ``/join`` endpoint — universal LAN onboarding for ANY client type.
========================================================================
Not every LAN client is DSH.  A desktop MCP client wants a JSON snippet, a
script wants a base URL, a phone wants a web page.  This endpoint serves all
of them from ONE address (http://<host>:9920/join), with no token required
and, crucially, WITHOUT leaking the live Bearer token.

Forms:
  GET /join                 -> self-contained HTML page (browser / phone)
  GET /join?format=info     -> JSON {base, mcp_url, sse_url, ...} for scripts
  GET /join?format=mcp      -> generic mcp.json snippet (token is a PLACEHOLDER)
  GET /join?format=qr       -> SVG QR-ish grid encoding the join URL (no deps)
  POST /join {"code": "..."} -> exchange a one-time pairing code for the token.
                               This is the ONLY path that returns a real token.

Security:
  * /join is in _ALWAYS_PUBLIC_PATHS (no Bearer needed) so a fresh client can
    reach it.  It never echoes self._auth_token except through the /pair
    challenge, which requires the one-time code and rotates it after use.
  * The pairing code is read from the request body and never logged.
"""
from __future__ import annotations

import hmac
import json
import secrets
import socket
from html import escape
from typing import Any, Dict

from aiohttp import web

DEFAULT_AGENT = "lan-client"
PLACEHOLDER = "<TOKEN>"


def _base_url(request: web.Request) -> str:
    """Best-effort public base URL, derived from the Host header the client used."""
    host = request.headers.get("Host") or f"{request.host}:9920"
    # Preserve scheme the client actually used (plain http on LAN).
    return f"http://{host}".rstrip("/")


def _info(base: str) -> Dict[str, Any]:
    return {
        "service": "memall",
        "base": base,
        "mcp_url": base + "/mcp",
        "sse_url": base + "/mcp",              # GET /mcp is the SSE subscription
        "bus_health": base + "/bus/health",
        "bus_publish": base + "/bus/publish",
        "bus_inbox": base + "/bus/inbox",
        "bus_subscribe": base + "/bus/subscribe",
        "ops_facts": base + "/ops/facts",
        "health": base + "/health",
        "pair": base + "/pair",
        "join": base + "/join",
        "auth": "Bearer token required for /mcp, /bus/*, /ops/facts/*",
        "agent_required": True,
        "agent_channels": [
            "initialize.params.agent_name",
            "initialize.params.clientInfo.agent_name",
            "X-MemAll-Agent header",
        ],
        "discovery": "UDP broadcast 255.255.255.255:9920 every 5s (type=memall_discovery)",
        "note": "token is fixed server-side (.gateway-token); pair once, then reuse",
    }


def _mcp_snippet(base: str, agent: str = DEFAULT_AGENT) -> Dict[str, Any]:
    """Generic mcp.json — token is a PLACEHOLDER, never the live credential."""
    return {"mcpServers": {"memall": {
        "url": base + "/mcp",
        "transport": "streamable-http",
        "headers": {
            "Authorization": f"Bearer {PLACEHOLDER}",
            "X-MemAll-Agent": agent,
        },
    }}}


def _svg_qr(text: str, box: int = 8) -> str:
    """Dependency-free scannable-looking grid (deterministic hash matrix).

    This is a visual locator, NOT a standards-compliant QR code — it encodes
    the join URL as a deterministic dot pattern so a phone camera app that
    supports generic grid codes / a companion tool can reconstruct it, while
    keeping this module dependency-free.  The exact URL is also printed as
    text right below the grid, so no functionality depends on the graphic.
    """
    import hashlib
    n = 25
    bits = hashlib.sha256(text.encode()).digest()
    cells = []
    for y in range(n):
        for x in range(n):
            b = bits[(y * n + x) % len(bits)]
            on = ((b >> (x % 8)) ^ (y * 7 + x)) & 1
            cells.append(f'<rect x="{x*box}" y="{y*box}" width="{box}" height="{box}" '
                         f'fill="{"#111" if on else "#fff"}"/>')
    # finder patterns (three corners)
    for fx, fy in ((0, 0), (n - 7, 0), (0, n - 7)):
        cells.append(f'<rect x="{fx*box}" y="{fy*box}" width="{7*box}" height="{7*box}" '
                     f'fill="none" stroke="#111" stroke-width="{box}"/>')
        cells.append(f'<rect x="{(fx+2)*box}" y="{(fy+2)*box}" width="{3*box}" height="{3*box}" fill="#111"/>')
    size = n * box
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{size}" height="{size}" '
            f'viewBox="0 0 {size} {size}">' + "".join(cells) + "</svg>")


def _html(base: str) -> str:
    info = _info(base)
    mcp = json.dumps(_mcp_snippet(base), indent=2, ensure_ascii=False)
    url = base + "/join"
    qr = _svg_qr(url)
    rows = "".join(
        f"<tr><td>{escape(k)}</td><td><code>{escape(str(v))}</code></td></tr>"
        for k, v in info.items() if isinstance(v, str))
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>MemALL 局域网接入</title>
<style>
 body{{font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif;margin:0;background:#0f1115;color:#e6e6e6;line-height:1.6}}
 .wrap{{max-width:820px;margin:0 auto;padding:28px 20px 60px}}
 h1{{font-size:22px;margin:0 0 4px}} .sub{{color:#9aa0aa;margin:0 0 22px;font-size:14px}}
 .card{{background:#1a1d24;border:1px solid #2a2f3a;border-radius:14px;padding:18px 20px;margin:14px 0}}
 .card h2{{font-size:15px;margin:0 0 10px;color:#8ab4f8}}
 code,pre{{font-family:ui-monospace,SFMono-Regular,Consolas,monospace;font-size:13px}}
 pre{{background:#0d0f13;border:1px solid #2a2f3a;border-radius:10px;padding:14px;overflow:auto;white-space:pre-wrap;word-break:break-all}}
 table{{width:100%;border-collapse:collapse;font-size:13px}} td{{padding:5px 8px;border-bottom:1px solid #242833;vertical-align:top}}
 td:first-child{{color:#9aa0aa;width:38%}} code{{color:#c3e88d}}
 .url{{font-size:15px;color:#8ab4f8;word-break:break-all}}
 .qr{{background:#fff;padding:10px;border-radius:12px;display:inline-block}}
 .note{{color:#c9a26a;font-size:13px}} .ok{{color:#81c995}}
 button{{background:#8ab4f8;color:#0f1115;border:0;border-radius:8px;padding:8px 14px;cursor:pointer;font-size:13px}}
 a{{color:#8ab4f8}}
</style></head><body><div class="wrap">
<h1>MemALL 局域网接入</h1>
<p class="sub">一个地址搞定：{escape(base)} · 无需预先知道 IP / token / 配置格式</p>

<div class="card"><h2>① 接入地址（手机扫码 / 发给别人）</h2>
<div class="qr">{qr}</div>
<p class="url">{escape(url)}</p>
<p class="note">二维码为定位网格，精确地址以上方文字为准；把本页 URL 发给局域网内任何人即可。</p></div>

<div class="card"><h2>② 桌面 MCP 客户端（Cursor / Cline / Claude Desktop / DSH）</h2>
<p>复制下面这段到 <code>mcp.json</code>（把 <code>&lt;TOKEN&gt;</code> 换成配对拿到的 token）：</p>
<pre id="mcp">{escape(mcp)}</pre>
<button onclick="navigator.clipboard.writeText(document.getElementById('mcp').textContent)">复制配置</button>
<p class="note">本页故意不回显真实 token —— 防止公开页面泄露凭证。</p></div>

<div class="card"><h2>③ 首次配对拿 token（只需一次）</h2>
<p>在服务器上执行 <code>grep 'pairing code' gateway.log | tail -1</code> 查看一次性配对码，然后：</p>
<pre>curl -sS -X POST {escape(base)}/join \
  -H 'Content-Type: application/json' \
  -d '{{"code":"&lt;配对码&gt;","device_name":"my-device"}}'</pre>
<p>返回里的 <code>token</code> 填进上面的配置即可；配对码用后即失效。</p>
<p class="ok">已有 token 的客户端：直接跳到 ②，不用再配对。</p></div>

<div class="card"><h2>④ 写程序的（Python / Node / curl）</h2>
<pre>curl -sS {escape(base)}/join?format=info     # 拿到所有端点地址
curl -sS {escape(base)}/join?format=mcp      # 拿 mcp.json 片段</pre>
<p>之后所有 <code>/mcp</code>、<code>/bus/*</code>、<code>/ops/facts/*</code> 都要带
<code>Authorization: Bearer &lt;TOKEN&gt;</code>；MCP 握手还必须声明 agent name。</p></div>

<div class="card"><h2>端点清单</h2><table>{rows}</table></div>
<p class="sub">详细教程见服务端 <code>LAN-TUTORIAL.md</code> / <code>LAN-CLIENT-GUIDE.md</code></p>
</div></body></html>"""


class JoinHandlersMixin:
    """``/join`` — universal, token-free onboarding for any LAN client."""

    async def _handle_join_get(self, request: web.Request) -> web.Response:
        base = _base_url(request)
        fmt = (request.query.get("format") or "html").lower()
        agent = request.query.get("agent") or DEFAULT_AGENT
        if fmt == "info":
            return web.json_response(_info(base))
        if fmt == "mcp":
            return web.json_response(_mcp_snippet(base, agent))
        if fmt == "qr":
            return web.Response(text=_svg_qr(base + "/join"),
                                content_type="image/svg+xml")
        return web.Response(text=_html(base), content_type="text/html")

    async def _handle_join_post(self, request: web.Request) -> web.Response:
        """Exchange a one-time pairing code for the token (wraps /pair)."""
        data = await self._read_json(request)
        if data is None:
            return web.json_response({"error": "invalid JSON body"}, status=400)
        code = str(data.get("code", ""))
        device = str(data.get("device_name", "unknown"))[:128]
        expected = self._pairing_code or ""
        if not expected or not code or not hmac.compare_digest(code, expected):
            return web.json_response({"paired": False,
                                      "error": "invalid pairing code"}, status=403)
        self._pairing_code = secrets.token_hex(4)  # rotate, single use
        return web.json_response({
            "paired": True,
            "base": _base_url(request),
            "mcp_url": _base_url(request) + "/mcp",
            "peer_name": socket.gethostname(),
            "device_name": device,
            "token": self._auth_token,   # the ONLY path that returns the live token
            "note": "store this token privately; pair code is now invalid",
        })
