"""
Phase 15: Gateway — Device Interconnection
==========================================
Local HTTP gateway (aiohttp async), sync protocol (export/import), LAN device discovery
and pairing, and federated cross-device queries.
"""

import asyncio
import json
import logging
import os
import re
import secrets
import socket
import threading
import time
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional, Dict, List, Any, Tuple


def _safe_int(val: Any, default: int = 0) -> int:
    """Safely parse an integer from query params, returning *default* on invalid input."""
    try:
        return int(val)
    except (TypeError, ValueError):
        return default


from aiohttp import web, ClientSession, ClientTimeout

from memall.core.db import pool_conn, get_conn
from memall.core.thin_waist import (
    capture,
    retrieve,
    traverse,
    timeline,
    MemoryInput,
    normalize_agent_name,
)
from memall.pipeline.persona import generate_profile_3layer


logger = logging.getLogger("memall.gateway")


# Shared navigation bar for HTML pages
_NAV_HTML = '<div style="margin-bottom:16px">' \
    '<a href="/recent" style="color:#555;text-decoration:none;margin-right:16px">最近</a>' \
    '<a href="/timeline" style="color:#555;text-decoration:none;margin-right:16px">时间线</a>' \
    '<a href="/dashboard" style="color:#555;text-decoration:none;margin-right:16px">仪表盘</a>' \
    '<a href="/todos" style="color:#555;text-decoration:none;margin-right:16px">待办</a>' \
    '<a href="/discussions" style="color:#555;text-decoration:none;margin-right:16px">讨论</a>' \
    '<a href="/graph" style="color:#555;text-decoration:none;margin-right:16px">图谱</a>' \
    '<a href="/artifact" style="color:#555;text-decoration:none;margin-right:16px">工单</a>' \
    '<a href="/features" style="color:#555;text-decoration:none;margin-right:16px">功能</a>' \
    '</div>'


# ══════════════════════════════════════════════════════════════════
# Paths
# ══════════════════════════════════════════════════════════════════

_PROJECT_DIR = Path.home() / ".memall"
PEERS_FILE = _PROJECT_DIR / "peers.json"


_PEERS_LOCK = threading.Lock()


def _load_peers() -> List[Dict[str, Any]]:
    """Load paired peers from peers.json.  Returns empty list if missing."""
    with _PEERS_LOCK:
        if PEERS_FILE.exists():
            try:
                return json.loads(PEERS_FILE.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                return []
        return []


def _save_peers(peers: List[Dict[str, Any]]) -> None:
    """Persist peer list to peers.json (thread-safe, atomic write)."""
    with _PEERS_LOCK:
        _PROJECT_DIR.mkdir(parents=True, exist_ok=True)
        tmp = PEERS_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(peers, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(PEERS_FILE)


# ══════════════════════════════════════════════════════════════════
# 1. Local HTTP Gateway (aiohttp async)
# ══════════════════════════════════════════════════════════════════

_CORS_HEADERS = {
    "Access-Control-Allow-Methods": "POST, GET, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type, Authorization",
}

# Allowed CORS origins (local clients only)
_CORS_ALLOWED_ORIGINS = {"http://127.0.0.1:9919", "http://localhost:9919"}


def esc_html(text: str) -> str:
    """Escape HTML special characters."""
    if not text:
        return ""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def _epoch_narrative(mems: list) -> str:
    """Generate a one-line narrative summary for an epoch's memories."""
    from collections import Counter
    cats = Counter()
    for m in mems:
        c = (m.get("category") or "general").strip()
        if c and c != "general":
            cats[c] += 1
    if not cats:
        return ""
    top = cats.most_common(3)
    parts = [f"{cat}({cnt})" for cat, cnt in top]
    return "核心：" + " · ".join(parts)


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
    return _CORS_HEADERS  # No Access-Control-Allow-Origin = block by default


def _require_auth(request: web.Request, auth_token: str) -> Optional[web.Response]:
    """Return a 401 Response if the request does not carry a valid token, else None.

    The token can be provided via the ``Authorization: Bearer <token>``
    header or the ``token`` query parameter.
    """
    provided = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
    if not provided:
        provided = request.query.get("token", "")
    if provided != auth_token:
        return web.json_response(
            {"error": "unauthorized", "message": "valid Bearer token required"},
            status=401,
        )
    return None


class MemAllGateway:
    """Local HTTP gateway exposing MemALL operations over REST.

    Launches an ``aiohttp`` web server on a background thread.
    Listens only on localhost for security.

    Attributes:
        host (str): Bind address, always ``127.0.0.1``.
        port (int): TCP port.  Default 9919.
    """

    def __init__(self, host: str = "127.0.0.1", port: int = 9919,
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
                if self._loop and not self._loop.is_closed():
                    self._loop.call_soon_threadsafe(
                        lambda: asyncio.ensure_future(
                            self._cleanup(), loop=self._loop
                        )
                    )
                self._runner = None

    # ── Internal async runner ──

    def _run_async(self) -> None:
        """在新线程中运行异步事件循环"""
        asyncio.set_event_loop(self._loop)
        self._app = web.Application(middlewares=[self._auth_middleware])
        self._setup_routes(self._app)
        self._runner = web.AppRunner(self._app)
        self._loop.run_until_complete(self._runner.setup())
        site = web.TCPSite(self._runner, self.host, self.port)
        self._loop.run_until_complete(site.start())
        self._loop.run_forever()

    async def _cleanup(self) -> None:
        """清理 aiohttp runner 并停止事件循环"""
        await self._runner.cleanup()
        self._loop.stop()

    # ── Route registration ──

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
        """Require a valid Bearer token on all endpoints except /health, /pair and OPTIONS."""
        if request.method == "OPTIONS" or request.path in ("/health", "/pair", "/dashboard", "/graph", "/artifact", "/features"):
            return await handler(request)
        err = _require_auth(request, self._auth_token)
        if err is not None:
            return err
        return await handler(request)

    def _setup_routes(self, app: web.Application) -> None:
        app.router.add_get("/health", self._handle_health)
        app.router.add_get("/recent", self._handle_recent)
        app.router.add_get("/todos", self._handle_todos)
        app.router.add_get("/timeline", self._handle_timeline_html)
        app.router.add_get("/identity/{agent_name}", self._handle_identity)
        app.router.add_get("/dashboard", self._handle_dashboard)
        app.router.add_get("/api/slices", self._handle_api_slices)
        app.router.add_get("/api/epochs", self._handle_api_epochs)
        app.router.add_get("/api/epochs/{agent_name}", self._handle_api_epochs_agent)
        app.router.add_get("/api/arcs", self._handle_api_arcs)
        app.router.add_get("/api/arcs/{decision_id}", self._handle_api_arcs_detail)
        app.router.add_get("/api/epochs/{epoch_id}/arcs", self._handle_api_epoch_arcs)
        app.router.add_get("/api/timeline/density", self._handle_api_timeline_density)
        app.router.add_get("/api/timeline/epochs", self._handle_api_timeline_epochs)
        app.router.add_get("/discussions", self._handle_discussions)
        app.router.add_get("/graph", self._handle_graph)
        app.router.add_get("/artifact", self._handle_artifact)
        app.router.add_get("/features", self._handle_features)
        app.router.add_get("/api/graph", self._handle_api_graph)
        app.router.add_get("/api/discussions", self._handle_api_discussions)
        app.router.add_get("/api/discussions/{topic_id}", self._handle_api_discussion_detail)
        app.router.add_post("/api/discussions/create", self._handle_api_discussion_create)
        app.router.add_post("/api/discussions/respond", self._handle_api_discussion_respond)
        app.router.add_post("/capture", self._handle_capture)
        app.router.add_post("/retrieve", self._handle_retrieve)
        app.router.add_post("/traverse", self._handle_traverse)
        app.router.add_post("/timeline", self._handle_timeline)
        app.router.add_post("/profile", self._handle_profile)
        app.router.add_post("/pair", self._handle_pair)
        # Catch-all OPTIONS for CORS preflight
        app.router.add_route("OPTIONS", "/{tail:.*}", self._handle_options)

    # ── CORS preflight ──

    async def _handle_options(self, request: web.Request) -> web.Response:
        return web.Response(status=204, headers=_cors_headers(request))

    # ── Handlers ──

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
            headers=_cors_headers(request),
        )

    # ── HTML pages (user-facing, no MCP dependency) ──

    _HTML_STYLE = """
    <style>
      body { font-family: system-ui, sans-serif; max-width: 800px; margin: 0 auto; padding: 20px; background: #f5f5f5; }
      h1 { color: #333; border-bottom: 2px solid #ddd; padding-bottom: 8px; }
      .card { background: #fff; border-radius: 8px; padding: 16px; margin: 12px 0; box-shadow: 0 1px 3px rgba(0,0,0,.1); }
      .card h3 { margin: 0 0 6px 0; color: #555; }
      .card .meta { font-size: 12px; color: #999; margin-bottom: 8px; }
      .card .content { font-size: 14px; line-height: 1.5; color: #333; white-space: pre-wrap; }
      .tag { display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 11px; margin-right: 4px; background: #e0e0e0; }
      .tag.l5-active { background: #c8e6c9; }
      .tag.l5-done { background: #e0e0e0; }
      .tag.l4 { background: #bbdefb; }
      .tag.l3 { background: #fff9c4; }
      .trait-card { background: #fff; border-radius: 8px; padding: 16px; margin: 12px 0; box-shadow: 0 1px 3px rgba(0,0,0,.1); }
      .trait-card h3 { margin: 0 0 12px 0; color: #555; font-size: 15px; border-left: 3px solid #888; padding-left: 10px; }
      .trait-item { display: inline-block; background: #f0f4ff; border-radius: 16px; padding: 4px 12px; margin: 4px 6px 4px 0; font-size: 13px; color: #333; }
      .trait-item .type-tag { font-size: 10px; color: #888; margin-right: 4px; }
      .persona-header { text-align: center; padding: 20px; margin-bottom: 16px; }
      .persona-header .prototype { font-size: 22px; font-weight: bold; color: #333; }
      .persona-header .subtitle { font-size: 14px; color: #888; margin-top: 4px; }
      .color-bar { display: flex; height: 8px; border-radius: 4px; overflow: hidden; margin: 12px 0; }
      .color-bar .seg { height: 100%; }
      .color-bar .seg.white { background: #e0e0e0; }
      .color-bar .seg.blue { background: #64b5f6; }
      .color-bar .seg.black { background: #424242; }
      .color-bar .seg.red { background: #ef5350; }
      .color-bar .seg.green { background: #81c784; }
      .empty-state { text-align: center; color: #999; padding: 40px; }
    </style>"""

    async def _handle_recent(self, request: web.Request) -> web.Response:
        with pool_conn() as conn:
            rows = conn.execute(
                "SELECT id, level, category, subject, content, agent_name, owner, created_at "
                "FROM memories ORDER BY created_at DESC LIMIT 30"
            ).fetchall()
        items = "\n".join(
            '<div class="card">'
            '<div class="meta">#{} <span class="tag">{}</span> <span class="tag">{}</span> {} · {}</div>'
            '<h3>{}</h3>'
            '<div class="content">{}</div>'
            '</div>'.format(
                r["id"], r["level"], r["category"], esc_html(r["agent_name"]), (r["created_at"] or "")[:19],
                esc_html(r["subject"] or "(无主题)"), esc_html((r["content"] or "")[:300]),
            )
            for r in rows
        )
        html = "<!DOCTYPE html>\n<html><head><meta charset='utf-8'><title>MemALL · 最近记忆</title>{}</head><body>{}<h1>🧠 最近记忆 <span style='font-size:14px;color:#999;font-weight:normal'>最新 30 条</span></h1>{}</body></html>".format(
            self._HTML_STYLE, _NAV_HTML, items or '<p style="color:#999">暂无记忆</p>'
        )
        return web.Response(text=html, content_type="text/html")

    async def _handle_identity(self, request: web.Request) -> web.Response:
        agent_name = request.match_info.get("agent_name", "").strip().lower()
        if not agent_name:
            return web.Response(text="<h1>Missing agent_name</h1>", status=400, content_type="text/html")

        with pool_conn() as conn:
            row = conn.execute(
                "SELECT identity_profile, profile_json, persona_updated_at, agent_type, description "
                "FROM identities WHERE LOWER(agent_name) = LOWER(?)",
                (agent_name,),
            ).fetchone()

        if not row:
            html = f"<!DOCTYPE html>\n<html><head><meta charset='utf-8'><title>MemALL · 画像</title>{self._HTML_STYLE}</head><body><div class='empty-state'><h2>Agent '{agent_name}' 未找到</h2><p>可能还未运行 identity pipeline 或该 agent 不存在</p></div></body></html>"
            return web.Response(text=html, content_type="text/html", status=404)

        id_profile = json.loads(row["identity_profile"]) if isinstance(row["identity_profile"], str) and row["identity_profile"] else {}
        pj = json.loads(row["profile_json"]) if isinstance(row["profile_json"], str) and row["profile_json"] else {}

        l1_list = id_profile.get("l1_identity", []) if isinstance(id_profile, dict) else []
        l7_list = id_profile.get("l7_preferences", []) if isinstance(id_profile, dict) else []
        proto = pj.get("prototype", {}) if isinstance(pj, dict) else {}
        feats = pj.get("features", {}) if isinstance(pj, dict) else {}
        colors = pj.get("color_ratios", {}) if isinstance(pj, dict) else {}
        updated = (row["persona_updated_at"] or "")[:19]

        # L1 cards grouped by type
        l1_html = ""
        if l1_list:
            groups = {}
            for t in l1_list:
                tp = t.get("type", "other")
                groups.setdefault(tp, []).append(t["snippet"])
            for tp, snippets in groups.items():
                tags = "".join(f'<span class="trait-item"><span class="type-tag">{tp}</span> {s}</span>' for s in snippets)
                l1_html += f'<div class="trait-card"><h3>L1 · {tp}</h3>{tags}</div>'
        else:
            l1_html = '<div class="trait-card" style="color:#999;text-align:center">暂无 L1 身份数据</div>'

        # L7 cards grouped by type
        l7_html = ""
        if l7_list:
            groups = {}
            for t in l7_list:
                tp = t.get("type", "other")
                groups.setdefault(tp, []).append(t["snippet"])
            for tp, snippets in groups.items():
                tags = "".join(f'<span class="trait-item"><span class="type-tag">{tp}</span> {s}</span>' for s in snippets)
                l7_html += f'<div class="trait-card"><h3>L7 · {tp}</h3>{tags}</div>'
        else:
            l7_html = '<div class="trait-card" style="color:#999;text-align:center">暂无 L7 偏好数据</div>'

        # Color bar
        color_bar = ""
        if colors:
            segs = "".join(f'<div class="seg {c}" style="flex:{v*100:.0f}"></div>' for c, v in colors.items() if v > 0.01)
            if segs:
                color_bar = f'<div class="color-bar">{segs}</div>'

        # Persona header
        proto_cn = proto.get("cn", "")
        proto_en = proto.get("en", "")
        persona_top = ""
        if proto_cn:
            persona_top = f'<div class="persona-header"><div class="prototype">{proto_cn}</div><div class="subtitle">{proto_en} · 更新于 {updated}</div>{color_bar}</div>'

        # Stats row
        stats = ""
        if feats:
            items = []
            for k, v in [("自信指数", "certainty_score"), ("决策密度", "decision_ratio"),
                          ("提问倾向", "question_ratio"), ("知识广度", "domain_breadth")]:
                val = feats.get(v, 0)
                if val:
                    fmt = f"{val*100:.0f}%" if v != "domain_breadth" else str(val)
                    items.append(f'<span class="trait-item">{k}: {fmt}</span>')
            if items:
                stats = f'<div style="margin:12px 0">{"".join(items)}</div>'

        html = f"""<!DOCTYPE html>
<html><head><meta charset='utf-8'><title>MemALL · {agent_name} 画像</title>{self._HTML_STYLE}</head>
<body>
  <h1>🧬 {agent_name} 画像</h1>
  {persona_top}
  <h3>L1 身份信息</h3>{l1_html}
  <h3>L7 偏好信息</h3>{l7_html}
  {stats}
</body></html>"""
        return web.Response(text=html, content_type="text/html")

    async def _handle_graph(self, request: web.Request) -> web.Response:
        node_id = request.query.get("node_id", "").strip()
        with pool_conn() as conn:
            mem_count = conn.execute("SELECT COUNT(*) AS c FROM memories").fetchone()["c"]
            edge_count = conn.execute("SELECT COUNT(*) AS c FROM edges").fetchone()["c"]
            density = round(edge_count / max(mem_count, 1), 2)

            # Type distribution
            type_rows = conn.execute(
                "SELECT relation_type, COUNT(*) AS cnt FROM edges GROUP BY relation_type ORDER BY cnt DESC"
            ).fetchall()

            # Hub nodes top 20
            hub_rows = conn.execute(
                "SELECT node_id, COUNT(*) AS edge_count FROM ("
                "SELECT source_id AS node_id FROM edges UNION ALL SELECT target_id AS node_id FROM edges"
                ") GROUP BY node_id ORDER BY edge_count DESC LIMIT 20"
            ).fetchall()
            hub_ids = [r["node_id"] for r in hub_rows]
            hub_map = {}
            if hub_ids:
                ph = ",".join("?" for _ in hub_ids)
                for r in conn.execute(f"SELECT id, subject FROM memories WHERE id IN ({ph})", hub_ids).fetchall():
                    hub_map[r["id"]] = r["subject"] or f"#{r['id']}"

        # Stats card
        stats_card = (
            f'<div class="card">'
            f'<h3>整体统计</h3>'
            f'<div class="meta">记忆 {mem_count} · 关系 {edge_count} · 密度 {density}</div>'
            f'</div>'
        )

        # Type distribution card
        total = edge_count or 1
        type_rows_html = "".join(
            f'<tr><td>{esc_html(r["relation_type"])}</td>'
            f'<td>{r["cnt"]}</td>'
            f'<td>{r["cnt"]/total*100:.1f}%</td></tr>'
            for r in type_rows
        )
        types_card = (
            f'<div class="card">'
            f'<h3>关系类型分布</h3>'
            f'<table style="width:100%;border-collapse:collapse;font-size:14px">'
            f'<tr style="background:#f0f0f0"><th style="text-align:left;padding:6px">类型</th><th style="text-align:right;padding:6px">数量</th><th style="text-align:right;padding:6px">占比</th></tr>'
            f'{type_rows_html}'
            f'</table></div>'
        )

        # Hub nodes card
        hub_rows_html = []
        for h in hub_rows:
            hid = h["node_id"]
            subj = hub_map.get(hid, f"#{hid}")
            hub_rows_html.append(
                f'<tr><td>#{hid}</td>'
                f'<td><a href="/graph?node_id={hid}" style="color:#1976d2;text-decoration:none">{esc_html(subj)}</a></td>'
                f'<td style="text-align:right">{h["edge_count"]}</td></tr>'
            )
        hub_rows_html = "".join(hub_rows_html)
        hubs_card = (
            f'<div class="card">'
            f'<h3>活跃节点 TOP 20</h3>'
            f'<table style="width:100%;border-collapse:collapse;font-size:14px">'
            f'<tr style="background:#f0f0f0"><th style="text-align:left;padding:6px">ID</th><th style="text-align:left;padding:6px">主题</th><th style="text-align:right;padding:6px">边数</th></tr>'
            f'{hub_rows_html}'
            f'</table></div>'
        )

        extra = ""
        if node_id.isdigit():
            nid = int(node_id)
            with pool_conn() as conn:
                edge_rows = conn.execute(
                    "SELECT e.source_id, e.target_id, e.relation_type, e.weight, e.created_at, "
                    "ms.subject AS source_subject, mt.subject AS target_subject "
                    "FROM edges e "
                    "LEFT JOIN memories ms ON ms.id = e.source_id "
                    "LEFT JOIN memories mt ON mt.id = e.target_id "
                    "WHERE e.source_id = ? OR e.target_id = ? "
                    "ORDER BY e.id DESC LIMIT 50",
                    (nid, nid),
                ).fetchall()
                node_subj = conn.execute("SELECT subject FROM memories WHERE id=?", (nid,)).fetchone()
            subj = esc_html(node_subj["subject"] if node_subj else f"#{nid}")
            edge_rows_html = []
            for r in edge_rows:
                src = r["source_subject"] or f"#{r['source_id']}"
                tgt = r["target_subject"] or f"#{r['target_id']}"
                edge_rows_html.append(
                    f'<tr>'
                    f'<td>{esc_html(src)}</td>'
                    f'<td style="text-align:center">→ ({esc_html(r["relation_type"])})</td>'
                    f'<td>{esc_html(tgt)}</td>'
                    f'</tr>'
                )
            edge_rows_html = "".join(edge_rows_html)
            extra = (
                f'<div class="card">'
                f'<h3>节点详情: {subj} <span style="font-weight:normal;font-size:13px;color:#999">#{nid}</span></h3>'
                f'<div class="meta">{len(edge_rows)} 条边</div>'
                f'<table style="width:100%;border-collapse:collapse;font-size:14px">'
                f'<tr style="background:#f0f0f0"><th style="text-align:left;padding:6px">来源</th><th style="text-align:center;padding:6px">关系</th><th style="text-align:left;padding:6px">目标</th></tr>'
                f'{edge_rows_html}'
                f'</table></div>'
            )
        elif node_id:
            extra = f'<div class="card"><div class="empty-state">无效节点 ID: {esc_html(node_id)}</div></div>'

        html = (
            f'<!DOCTYPE html>\n<html><head><meta charset="utf-8"><title>MemALL · 图谱</title>{self._HTML_STYLE}'
            f'</head><body>{_NAV_HTML}<h1>知识图谱</h1>{stats_card}{types_card}{hubs_card}{extra}</body></html>'
        )
        return web.Response(text=html, content_type="text/html")

    async def _handle_artifact(self, request: web.Request) -> web.Response:
        """展示本 session 任务成果总览页面。"""
        style = self._HTML_STYLE + """
        .commit { background:#e8f5e9; border-left:3px solid #4caf50; padding:12px; margin:8px 0; border-radius:0 8px 8px 0; }
        .commit .hash { font-family:monospace; color:#2e7d32; font-size:13px; }
        .disc-item { background:#fff3e0; border-left:3px solid #ff9800; padding:12px; margin:8px 0; border-radius:0 8px 8px 0; }
        .agent-tag { display:inline-block; padding:1px 8px; border-radius:10px; font-size:11px; margin:2px; }
        .agent-tag.ok { background:#c8e6c9; }
        .agent-tag.pending { background:#fff9c4; }
        .section { margin:20px 0; padding:16px; background:#fff; border-radius:8px; box-shadow:0 1px 3px rgba(0,0,0,.1); }
        .section h2 { margin:0 0 12px 0; font-size:17px; color:#333; border-bottom:1px solid #eee; padding-bottom:8px; }
        ul { margin:4px 0; padding-left:20px; }
        li { margin:4px 0; line-height:1.5; }
        .stat-row { display:flex; gap:12px; flex-wrap:wrap; margin:12px 0; }
        .stat-card { flex:1; min-width:100px; background:#f5f5f5; border-radius:8px; padding:12px; text-align:center; }
        .stat-card .num { font-size:24px; font-weight:bold; color:#333; }
        .stat-card .label { font-size:12px; color:#888; }"""

        html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>MemALL · 工单</title>{style}
</head><body>
{_NAV_HTML}

<h1>本 Session 成果清单</h1>
<p style="color:#888;font-size:14px">更新至 2026-06-25 · 2 个推送 · 1 个讨论收敛</p>

<div class="stat-row">
  <div class="stat-card"><div class="num">2</div><div class="label">推送</div></div>
  <div class="stat-card"><div class="num">1</div><div class="label">讨论收敛</div></div>
  <div class="stat-card"><div class="num">4</div><div class="label">参与 Agent</div></div>
  <div class="stat-card"><div class="num">40</div><div class="label">数据清理</div></div>
</div>

<div class="section">
<h2> Commits</h2>

<div class="commit">
<div class="hash">4eb0d0e</div>
<strong>agent_name 规范化管理</strong>
<span class="tag">#8476</span>
<ul>
<li><code>normalize_agent_name()</code> 从 capture() 提取为独立函数</li>
<li>convergence.py 4 处 INSERT 路径加校验</li>
<li>gateway.py _import_identity / _import_memories 加校验</li>
<li>数据清理：40 条空 agent_name → "system"</li>
<li>修改文件：thin_waist.py, convergence.py, gateway.py</li>
</ul>
</div>

<div class="commit">
<div class="hash">c14c980</div>
<strong>Phase 1: 层级命名规范统一</strong>
<span class="tag">#10780</span>
<ul>
<li>新增 <code>_LEVEL_SUBJECT_PREFIX</code> 映射表 (level → [Lx 标签])</li>
<li>_make_subject() 签名增加 level 参数，优先使用 level prefix</li>
<li>L9 蒸馏词条追加 [L9 蒸馏] 前缀</li>
<li>L10 整合从 "L10:agent跨领域洞察()" → "[L10 整合] agent 跨领域洞察()"</li>
<li>旧数据不动，85 tests pass（无新增失败）</li>
</ul>
</div>

</div>

<div class="section">
<h2> 讨论 #10780 — 层级命名规范统一</h2>

<div class="disc-item">
<strong>方案决策：方案 C（渐进统一）</strong> — 4 位 Agent 全数通过
</div>

<table style="width:100%;border-collapse:collapse;margin:12px 0">
<tr style="background:#f5f5f5"><th style="padding:6px;text-align:left;border-bottom:1px solid #ddd">Agent</th><th style="padding:6px;text-align:left;border-bottom:1px solid #ddd">评估项</th><th style="padding:6px;text-align:left;border-bottom:1px solid #ddd">结果</th></tr>
<tr><td style="padding:6px"><span class="agent-tag ok">opencode</span></td><td style="padding:6px">全层级 DB 抽样 + distill/integrate 兼容性</td><td style="padding:6px;color:#2e7d32">方案 C，无阻塞</td></tr>
<tr><td style="padding:6px"><span class="agent-tag ok">claude</span></td><td style="padding:6px">capture._make_subject() 改动量评估</td><td style="padding:6px;color:#2e7d32">方案 C，20 行</td></tr>
<tr><td style="padding:6px"><span class="agent-tag ok">codex</span></td><td style="padding:6px">gateway/session_start subject 依赖</td><td style="padding:6px;color:#2e7d32">方案 C，无解析依赖</td></tr>
<tr><td style="padding:6px"><span class="agent-tag ok">workbuddy</span></td><td style="padding:6px">内容前缀与 classify 正则兼容性</td><td style="padding:6px;color:#2e7d32">方案 C，_LAYER_PREFIX_RE 无冲突</td></tr>
</table>

<p><strong>结论：</strong>Phase 1 已实施（capture + distill + integrate），Phase 2（遗留清理）可选延期。Q1（level vs category 标识）已解决：优先 level prefix，fallback category prefix。Q2 无冲突。Q3：L8 不受影响。</p>

</div>

</body></html>"""
        return web.Response(text=html, content_type="text/html")

    async def _handle_features(self, request: web.Request) -> web.Response:
        style = self._HTML_STYLE + """
        .section { margin:20px 0; padding:16px; background:#fff; border-radius:8px; box-shadow:0 1px 3px rgba(0,0,0,.1); }
        .section h2 { margin:0 0 12px 0; font-size:17px; color:#333; border-bottom:2px solid #eee; padding-bottom:8px; }
        .section h3 { margin:12px 0 6px 0; font-size:14px; color:#555; }
        .section h4 { margin:8px 0 4px 0; font-size:13px; color:#666; }
        table { width:100%; border-collapse:collapse; margin:8px 0; font-size:13px; }
        th { background:#f5f5f5; padding:6px; text-align:left; border-bottom:1px solid #ddd; font-weight:600; }
        td { padding:5px 6px; border-bottom:1px solid #eee; }
        .tag { display:inline-block; padding:1px 6px; border-radius:3px; font-size:10px; margin:1px; background:#e8e8e8; }
        .tag.c { background:#e3f2fd; }
        .tag.mcp { background:#fce4ec; }
        .tag.api { background:#e8f5e9; }
        .tag.pipe { background:#fff3e0; }
        .tag.arch { background:#f3e5f5; }
        .code { font-family:monospace; font-size:12px; background:#f5f5f5; padding:1px 4px; border-radius:2px; }
        .layer-row { display:flex; gap:4px; flex-wrap:wrap; margin:8px 0; }
        .layer-item { padding:2px 10px; border-radius:12px; font-size:12px; background:#f5f5f5; border:1px solid #ddd; }
        .layer-item.l0 { background:#e8e8e8; }
        .layer-item.l1 { background:#e3f2fd; border-color:#90caf9; }
        .layer-item.l2 { background:#fff3e0; border-color:#ffcc80; }
        .layer-item.l3 { background:#fce4ec; border-color:#f48fb1; }
        .layer-item.l4 { background:#f3e5f5; border-color:#ce93d8; }
        .layer-item.t { background:#c8e6c9; border-color:#81c784; }
        .two-col { display:flex; gap:16px; flex-wrap:wrap; }
        .col { flex:1; min-width:280px; }"""

        html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>MemALL · 功能清单</title>{style}
</head><body>
{_NAV_HTML}

<h1>MemALL 功能报告</h1>
<p style="color:#888;font-size:14px">v0.1.2 · Python + SQLite + aiohttp · 端口 9919(Gateway) / 9876(MCP)</p>

<div class="section">
<h2>一、记忆层级体系</h2>
<div class="layer-row">
<span class="layer-item l0">P0 原始</span>
<span class="layer-item l0">P1 原始</span>
<span class="layer-item l0">P2 原始</span>
<span class="layer-item l0">P3 原始</span>
<span class="layer-item l0">P4 原始</span>
<span class="layer-item l1">L1 身份</span>
<span class="layer-item l1">L2 时间</span>
<span class="layer-item l1">L3 流程</span>
<span class="layer-item l2">L4 会话</span>
<span class="layer-item l2">L5 计划</span>
<span class="layer-item l3">L6 反思</span>
<span class="layer-item l3">L7 教训</span>
<span class="layer-item l3">L8 关系</span>
<span class="layer-item l4">L9 蒸馏</span>
<span class="layer-item l4">L10 整合</span>
<span class="layer-item l4">L11 商业</span>
<span class="layer-item t">终端不可变</span>
</div>
<p>每个层级有独立的关键词规则、置信度评分、升级策略（只升级不降级）。L6/L8/L9/L10/L11 为终端层，一旦到达不可变更。</p>
</div>

<div class="section">
<h2>二、核心 API（thin_waist.py）</h2>
<table>
<tr><th>函数</th><th>功能</th><th>特点</th></tr>
<tr><td class="code">capture()</td><td>存入记忆</td><td>8维质量评分 + 内容去重 + 可见性校验 + agent身份校验</td></tr>
<tr><td class="code">retrieve()</td><td>搜索/查询</td><td>按 ID/keyword/agent/level/category/project 过滤</td></tr>
<tr><td class="code">update()</td><td>更新字段</td><td>白名单控制 + agent_name 规范化</td></tr>
<tr><td class="code">smart_store()</td><td>自动去重</td><td>语义相似度阈值 0.85</td></tr>
<tr><td class="code">store_batch()</td><td>批量存储</td><td>事务内完成</td></tr>
<tr><td class="code">connect()</td><td>创建关系</td><td>8+ 关系类型</td></tr>
<tr><td class="code">traverse()</td><td>图谱遍历</td><td>最多 5 跳 BFS</td></tr>
<tr><td class="code">vector_search()</td><td>向量搜索</td><td>TF-IDF+SVD 嵌入</td></tr>
<tr><td class="code">hybrid_search()</td><td>混合搜索</td><td>FTS5 + vec0 双通道 + RRF</td></tr>
<tr><td class="code">timeline()</td><td>时间线查询</td><td>小时/日/周聚合</td></tr>
<tr><td class="code">normalize_agent_name()</td><td>名称规范化</td><td>strip+lower+regex+黑名单</td></tr>
</table>
</div>

<div class="section">
<h2>三、HTTP 网关（:9919）</h2>
<div class="two-col">
<div class="col">
<h3>HTML 页面（10 个）</h3>
<table>
<tr><td class="code">/health</td><td>服务器健康</td></tr>
<tr><td class="code">/recent</td><td>最近记忆</td></tr>
<tr><td class="code">/todos</td><td>任务管理</td></tr>
<tr><td class="code">/timeline</td><td>Epoch 时间线</td></tr>
<tr><td class="code">/identity/{{name}}</td><td>Agent 档案</td></tr>
<tr><td class="code">/dashboard</td><td>综合仪表盘</td></tr>
<tr><td class="code">/discussions</td><td>讨论列表</td></tr>
<tr><td class="code">/graph</td><td>图谱可视化</td></tr>
<tr><td class="code">/artifact</td><td>成果工单</td></tr>
<tr><td class="code">/features</td><td>功能报告</td></tr>
</table>
</div>
<div class="col">
<h3>REST API（10+ 个）</h3>
<table>
<tr><td class="code">/api/slices</td><td>时间切片</td></tr>
<tr><td class="code">/api/epochs</td><td>周期数据</td></tr>
<tr><td class="code">/api/arcs</td><td>决策弧</td></tr>
<tr><td class="code">/api/graph</td><td>图谱 JSON</td></tr>
<tr><td class="code">/api/discussions</td><td>讨论数据</td></tr>
<tr><td class="code">/capture</td><td>POST 存记忆</td></tr>
<tr><td class="code">/retrieve</td><td>POST 搜索</td></tr>
<tr><td class="code">/traverse</td><td>POST 遍历</td></tr>
<tr><td class="code">/profile</td><td>POST 画像</td></tr>
<tr><td class="code">/pair</td><td>POST 配对</td></tr>
</table>
</div>
</div>
</div>

<div class="section">
<h2>四、MCP 工具（44+ 个，:9876）</h2>
<div class="two-col">
<div class="col">
<h3>CRUD <span class="tag c">6</span></h3>
<p>capture, retrieve, connect, traverse, timeline, update</p>
<h3>智能存储 <span class="tag c">2</span></h3>
<p>smart_store, store_batch</p>
<h3>搜索 <span class="tag c">2</span></h3>
<p>vector_search, hybrid_search</p>
<h3>画像/问答 <span class="tag c">4</span></h3>
<p>persona, persona_profile, memall_ask, identity</p>
<h3>会话 <span class="tag c">3</span></h3>
<p>session_start, session_end, session_summary</p>
<h3>知识图谱 <span class="tag c">2</span></h3>
<p>memall_traverse, memall_graph</p>
</div>
<div class="col">
<h3>联邦 <span class="tag api">5</span></h3>
<p>fed_query, fed_publish, fed_conflicts, fed_inject, fed_extract</p>
<h3>生命周期 <span class="tag pipe">5</span></h3>
<p>forget, adaptive, security, ops, db</p>
<h3>讨论 <span class="tag arch">3</span></h3>
<p>discussion_create, discussion_respond, discussion_status</p>
<h3>反思/溯源 <span class="tag arch">2</span></h3>
<p>reflect_interact, memall_trace</p>
<h3>管道/蒸馏 <span class="tag pipe">3</span></h3>
<p>run_pipeline, distill_pending, index_rebuild</p>
<h3>其他 <span class="tag">3</span></h3>
<p>gateway, hub_connect, hub_sync, onboarding</p>
</div>
</div>
</div>

<div class="section">
<h2>五、数据处理管道</h2>
<div class="two-col">
<div class="col">
<h3>核心 20 步（顺序执行）</h3>
<table>
<tr><td>1. enrich</td><td>语义增强</td></tr>
<tr><td>2. cleanup</td><td>数据清理</td></tr>
<tr><td>3. classify</td><td>自动分类</td></tr>
<tr><td>4. time_slice</td><td>预聚合</td></tr>
<tr><td>5. arc_status</td><td>决策弧</td></tr>
<tr><td>6. echo</td><td>价值评分</td></tr>
<tr><td>7. epoch</td><td>周期检测</td></tr>
<tr><td>8. convergence</td><td>讨论收敛</td></tr>
<tr><td>9. link</td><td>关联</td></tr>
<tr><td>10. decay</td><td>衰减</td></tr>
<tr><td>11. backup</td><td>备份</td></tr>
<tr><td>12. session</td><td>会话采集</td></tr>
<tr><td>13. embed_index</td><td>索引</td></tr>
<tr><td>14. reflect</td><td>反思</td></tr>
<tr><td>15. distill_l7</td><td>L7 提取</td></tr>
<tr><td>16. distill</td><td>L9 蒸馏</td></tr>
<tr><td>17. integrate</td><td>L10 整合</td></tr>
<tr><td>18. improve</td><td>自我改进</td></tr>
<tr><td>19. observation</td><td>观察</td></tr>
<tr><td>20. identity</td><td>身份更新</td></tr>
</table>
</div>
<div class="col">
<h3>可选 5 步</h3>
<table>
<tr><td>persona</td><td>Agent 认知画像</td></tr>
<tr><td>narrative</td><td>叙事生成</td></tr>
<tr><td>cluster</td><td>K-means 聚类</td></tr>
<tr><td>suggest</td><td>建议提取</td></tr>
<tr><td>bridge</td><td>桥接分析</td></tr>
</table>

<h3 style="margin-top:16px">后台调度器</h3>
<table>
<tr><td>pipeline</td><td>每 6 小时</td></tr>
<tr><td>doctor</td><td>每 1 小时</td></tr>
<tr><td>forget</td><td>每 24 小时</td></tr>
<tr><td>security</td><td>每 24 小时</td></tr>
<tr><td>heartbeat</td><td>每 5 分钟</td></tr>
</table>
</div>
</div>
</div>

<div class="section">
<h2>六、搜索能力</h2>
<table>
<tr><th>方式</th><th>引擎</th><th>维度</th></tr>
<tr><td>FTS5 全文搜索</td><td>SQLite FTS5</td><td>CJK 分词 + 命中高亮</td></tr>
<tr><td>向量搜索</td><td>vec0 (sqlite-vec)</td><td>BGE 512 维 + TF-IDF+SVD</td></tr>
<tr><td>混合搜索</td><td>RRF 融合</td><td>FTS5 + vec0 双通道</td></tr>
<tr><td>交叉编码重排</td><td>BGE-reranker-v2-m3</td><td>选择性加载（约 1.8GB）</td></tr>
<tr><td>FAISS 插件</td><td>可选 Provider</td><td>大规模部署</td></tr>
</table>
</div>

<div class="section">
<h2>七、联邦与安全</h2>
<div class="two-col">
<div class="col">
<h3>联邦能力</h3>
<table>
<tr><td>跨 Agent 共享</td><td>shared_memories 表</td></tr>
<tr><td>家庭圈</td><td>管理员/成员，邀请制</td></tr>
<tr><td>冲突检测</td><td>关键词 + 语义双重</td></tr>
<tr><td>自动解决</td><td>投票制（置信度+时间+权重）</td></tr>
<tr><td>Agent Hub</td><td>:12431 双向同步</td></tr>
<tr><td>LAN 发现</td><td>UDP 广播配对</td></tr>
</table>
</div>
<div class="col">
<h3>安全能力</h3>
<table>
<tr><td>敏感扫描</td><td>API Key/邮箱/IP/手机/身份证</td></tr>
<tr><td>三级权限</td><td>public / trusted / private</td></tr>
<tr><td>写入校验</td><td>agent 必须在 identities 表中注册</td></tr>
<tr><td>综合评分</td><td>扫描 + 权限 + 联邦 打分</td></tr>
</table>
</div>
</div>
</div>

<div class="section">
<h2>八、集成 & 其他</h2>
<table>
<tr><td>Lark/飞书 IM</td><td>多 bot 消息收发，讨论通知卡片</td></tr>
<tr><td>文件桥接</td><td>inbox/outbox 目录监听，文件 ↔ 记忆双向同步</td></tr>
<tr><td>导出</td><td>Markdown / JSONL / CSV / HTML</td></tr>
<tr><td>新手引导</td><td>5 步交互式向导</td></tr>
<tr><td>测试</td><td>35 个活跃测试文件，85+ 用例</td></tr>
<tr><td>DB 迁移</td><td>20 个正式迁移（001-019）</td></tr>
<tr><td>插件</td><td>白名单加载，热重载</td></tr>
</table>
</div>

</body></html>"""
        return web.Response(text=html, content_type="text/html")

    async def _handle_api_graph(self, request: web.Request) -> web.Response:
        with pool_conn() as conn:
            mem_count = conn.execute("SELECT COUNT(*) AS c FROM memories").fetchone()["c"]
            edge_count = conn.execute("SELECT COUNT(*) AS c FROM edges").fetchone()["c"]
            type_rows = conn.execute(
                "SELECT relation_type, COUNT(*) AS cnt FROM edges GROUP BY relation_type ORDER BY cnt DESC"
            ).fetchall()
            hub_rows = conn.execute(
                "SELECT node_id, COUNT(*) AS edge_count FROM ("
                "SELECT source_id AS node_id FROM edges UNION ALL SELECT target_id AS node_id FROM edges"
                ") GROUP BY node_id ORDER BY edge_count DESC LIMIT 20"
            ).fetchall()
            hub_ids = [r["node_id"] for r in hub_rows]
            hub_map = {}
            if hub_ids:
                ph = ",".join("?" for _ in hub_ids)
                for r in conn.execute(f"SELECT id, subject FROM memories WHERE id IN ({ph})", hub_ids).fetchall():
                    hub_map[r["id"]] = r["subject"] or f"#{r['id']}"
        total = edge_count or 1
        return web.json_response(
            {
                "totals": {"memories": mem_count, "edges": edge_count, "density": round(edge_count / max(mem_count, 1), 2)},
                "types": [{"type": r["relation_type"], "count": r["cnt"], "pct": round(r["cnt"] / total * 100, 1)} for r in type_rows],
                "hubs": [{"id": r["node_id"], "subject": hub_map.get(r["node_id"], f"#{r['node_id']}"), "edge_count": r["edge_count"]} for r in hub_rows],
            },
            headers=_cors_headers(request),
        )

    async def _handle_todos(self, request: web.Request) -> web.Response:
        from memall.pipeline.task_lifecycle import list_active_tasks, list_blocked_tasks
        agent_filter = request.query.get("agent", "").strip()

        active_tasks = list_active_tasks(agent_filter)
        blocked_tasks = list_blocked_tasks(agent_filter)

        # Resolved tasks (recent)
        with pool_conn() as conn:
            resolved_rows = conn.execute(
                "SELECT id, subject, agent_name, metadata, created_at "
                "FROM memories WHERE level='L5' AND category='task' "
                "AND json_extract(metadata, '$.status') = 'resolved' "
                "ORDER BY created_at DESC LIMIT 20"
            ).fetchall()

        def _task_card(tid, subject, agent, status, extra=""):
            tag_class = "l5-active" if status == "active" else "l5-done"
            return (
                '<div class="card">'
                '<div class="meta">#{} <span class="tag {}">{}</span> {} {}</div>'
                '<h3>{}</h3>'
                '</div>'
            ).format(tid, tag_class, status, esc_html(agent), extra, esc_html(subject or "(no subject)"))

        items = ""
        if active_tasks:
            items += "<h2>Active ({})</h2>".format(len(active_tasks))
            for t in active_tasks:
                ack_mark = "ack" if t.get("acknowledged_at") else "unack"
                age = (t.get("created_at") or "")[:10]
                items += _task_card(t["task_id"], t["subject"], t["agent_name"], "active", ack_mark + " (" + age + ")")

        if blocked_tasks:
            items += "<h2>Blocked ({})</h2>".format(len(blocked_tasks))
            for b in blocked_tasks:
                reason = (b.get("blocked_reason") or "")[:60]
                items += _task_card(b["task_id"], b["subject"], b["agent_name"], "blocked", reason)

        if resolved_rows:
            items += "<h2>Resolved (recent {})</h2>".format(len(resolved_rows))
            for r in resolved_rows:
                items += _task_card(r["id"], r["subject"], r["agent_name"], "resolved", (r["created_at"] or "")[:10])

        filter_info = " | agent=" + agent_filter if agent_filter else ""
        html = "<!DOCTYPE html>\n<html><head><meta charset='utf-8'><title>MemALL Task Board</title>{}</head><body>{}<h1>Task Board <span style='font-size:14px;color:#999;font-weight:normal'>{} active, {} blocked{}</span></h1>{}</body></html>".format(
            self._HTML_STYLE, _NAV_HTML,
            len(active_tasks), len(blocked_tasks), filter_info,
            items or '<p style="color:#999">No tasks</p>',
        )
        return web.Response(text=html, content_type="text/html")

    async def _handle_dashboard(self, request: web.Request) -> web.Response:
        agent_name = request.query.get("agent_name", "").strip() or None
        days = _safe_int(request.query.get("days", 30))

        with pool_conn() as conn:
            # Daily slices
            if agent_name:
                slice_rows = conn.execute(
                    "SELECT * FROM time_slices WHERE agent_name = ? AND granularity = 'day' "
                    "ORDER BY window_start DESC LIMIT ?",
                    (agent_name, days),
                ).fetchall()
            else:
                slice_rows = conn.execute(
                    "SELECT * FROM time_slices WHERE agent_name = '*' AND granularity = 'day' "
                    "ORDER BY window_start DESC LIMIT ?",
                    (days,),
                ).fetchall()
                if not slice_rows:
                    # Fallback: show all agent-specific slices grouped
                    slice_rows = conn.execute(
                        "SELECT * FROM time_slices WHERE granularity = 'day' "
                        "ORDER BY window_start DESC LIMIT ?",
                        (days * 5,),
                    ).fetchall()

            # Active epochs (ended_at IS NULL)
            if agent_name:
                epoch_rows = conn.execute(
                    "SELECT * FROM epochs WHERE agent_name = ? AND ended_at IS NULL "
                    "ORDER BY started_at DESC",
                    (agent_name,),
                ).fetchall()
            else:
                epoch_rows = conn.execute(
                    "SELECT * FROM epochs WHERE ended_at IS NULL "
                    "ORDER BY started_at DESC LIMIT 20"
                ).fetchall()

            # Recently ended epochs
            recent_epochs = conn.execute(
                "SELECT * FROM epochs WHERE ended_at IS NOT NULL "
                "ORDER BY ended_at DESC LIMIT 15"
            ).fetchall()

            # Decision Arc status
            arc_stats = conn.execute(
                "SELECT arc_status, COUNT(*) as cnt FROM memories WHERE level = 'L4' "
                "AND arc_status IS NOT NULL GROUP BY arc_status"
            ).fetchall()
            arc_counts = {r["arc_status"]: r["cnt"] for r in arc_stats}
            stale_cutoff = (date.today() - timedelta(days=21)).isoformat()
            stale_count = conn.execute(
                "SELECT COUNT(*) as cnt FROM memories WHERE level = 'L4' AND arc_status = 'open' "
                "AND created_at < ? AND id NOT IN ("
                "  SELECT DISTINCT source_id FROM edges WHERE relation_type != 'deleted' "
                "  AND target_id IN (SELECT id FROM memories WHERE level = 'L5')"
                "  UNION "
                "  SELECT DISTINCT target_id FROM edges WHERE relation_type != 'deleted' "
                "  AND source_id IN (SELECT id FROM memories WHERE level = 'L5')"
                ")",
                (stale_cutoff,),
            ).fetchone()
            stale_total = stale_count["cnt"] if stale_count else 0

            # All epochs summary
            all_epochs = conn.execute(
                "SELECT COUNT(*) as total, COUNT(DISTINCT agent_name) as agents FROM epochs"
            ).fetchone()
            total_epochs = all_epochs["total"] if all_epochs else 0
            epoch_agents = all_epochs["agents"] if all_epochs else 0

        # Build heatmap data: bar chart per day
        heatmap_bars = ""
        max_count = 1
        counts = []
        for r in reversed(slice_rows):
            counts.append(r["memory_count"])
            if r["memory_count"] > max_count:
                max_count = r["memory_count"]
        max_count = max(max_count, 1)

        for i, r in enumerate(reversed(slice_rows)):
            pct = (r["memory_count"] / max_count) * 100
            intensity = min(255, 180 + int(75 * (1 - r["memory_count"] / max_count)))
            color = f"rgba(100, 181, 246, {max(0.2, r['memory_count'] / max_count)})"
            date_label = r["slice_key"]
            heatmap_bars += (
                f'<div style="display:flex;align-items:center;margin:2px 0;font-size:12px">'
                f'<span style="width:80px;color:#999">{date_label[-5:]}</span>'
                f'<div style="flex:1;height:16px;background:#eee;border-radius:3px;overflow:hidden">'
                f'<div style="height:100%;width:{pct:.1f}%;background:{color};border-radius:3px"></div></div>'
                f'<span style="width:40px;text-align:right;color:#555;margin-left:6px">{r["memory_count"]}</span>'
                f'</div>'
            )

        if not heatmap_bars:
            heatmap_bars = '<p style="color:#999">暂无时间片数据（需先运行 pipeline）</p>'

        # Arc status cards
        arc_html = ""
        open_c = arc_counts.get("open", 0)
        ip_c = arc_counts.get("in_progress", 0)
        closed_c = arc_counts.get("closed", 0)
        total_c = open_c + ip_c + closed_c
        if total_c > 0:
            closure = round(closed_c / total_c * 100)
            arc_html += (
                f'<div class="card" style="display:inline-block;min-width:80px;text-align:center;margin:4px">'
                f'<div style="font-size:20px;color:#e53935">{open_c}</div>'
                f'<div style="font-size:11px;color:#999">开放</div></div>'
                f'<div class="card" style="display:inline-block;min-width:80px;text-align:center;margin:4px">'
                f'<div style="font-size:20px;color:#fb8c00">{ip_c}</div>'
                f'<div style="font-size:11px;color:#999">进行中</div></div>'
                f'<div class="card" style="display:inline-block;min-width:80px;text-align:center;margin:4px">'
                f'<div style="font-size:20px;color:#43a047">{closed_c}</div>'
                f'<div style="font-size:11px;color:#999">已闭环</div></div>'
                f'<div style="margin-top:8px;font-size:12px;color:#666">'
                f'闭合率 {closure}%'
            )
            if stale_total > 0:
                arc_html += f' · <span style="color:#e53935">{stale_total} 条搁置(&gt;21d)</span>'
            arc_html += '</div>'
            arc_html += (
                f'<div style="margin-top:8px"><a href="/api/arcs" style="font-size:12px">查看详情 →</a></div>'
            )
        else:
            arc_html = '<p style="color:#999">暂无决策弧数据</p>'

        # Epoch cards
        epoch_cards = ""
        for r in epoch_rows:
            label = r["label"] or "(未命名)"
            meta_info = f'{r["boundary_reason"]} · {r["started_at"][:16]}'
            if r["memory_count"]:
                meta_info += f' · {r["memory_count"]} 条记忆'
            epoch_cards += (
                f'<div class="card">'
                f'<h3>{label[:60]}</h3>'
                f'<div class="meta">{meta_info}</div>'
                f'</div>'
            )
        if not epoch_cards:
            epoch_cards = '<p style="color:#999">暂无活跃时期</p>'

        # Recent epochs list
        recent_epoch_list = ""
        for r in recent_epochs[:10]:
            recent_epoch_list += (
                f'<div style="font-size:12px;color:#666;padding:4px 0;border-bottom:1px solid #eee">'
                f'<span style="color:#999">{r["started_at"][:10]}</span> → '
                f'<span style="color:#999">{r["ended_at"][:10]}</span> '
                f'<strong>{r["agent_name"]}</strong>: {(r["label"] or "(未命名)")[:50]} '
                f'<span class="tag">{r["boundary_reason"]}</span>'
                f'</div>'
            )

        # Stats cards
        stats_html = ""
        if slice_rows:
            total_mem = sum(r["memory_count"] for r in slice_rows)
            stats_html += (
                f'<div class="card" style="display:inline-block;min-width:120px;text-align:center;margin-right:8px">'
                f'<div style="font-size:24px;font-weight:bold;color:#333">{len(slice_rows)}</div>'
                f'<div style="font-size:12px;color:#999">日切片</div></div>'
                f'<div class="card" style="display:inline-block;min-width:120px;text-align:center;margin-right:8px">'
                f'<div style="font-size:24px;font-weight:bold;color:#333">{total_mem}</div>'
                f'<div style="font-size:12px;color:#999">记忆数</div></div>'
            )
        stats_html += (
            f'<div class="card" style="display:inline-block;min-width:120px;text-align:center;margin-right:8px">'
            f'<div style="font-size:24px;font-weight:bold;color:#333">{total_epochs}</div>'
            f'<div style="font-size:12px;color:#999">时期(总)</div></div>'
            f'<div class="card" style="display:inline-block;min-width:120px;text-align:center">'
            f'<div style="font-size:24px;font-weight:bold;color:#333">{epoch_agents}</div>'
            f'<div style="font-size:12px;color:#999">Agent</div></div>'
        )

        # Filter form
        filter_html = (
            '<form class="filter-bar" method="get">'
            '<label>Agent: <input type="text" name="agent_name" value="' + (agent_name or "") + '" placeholder="全部" style="width:120px"></label>'
            '<label>天数: <input type="number" name="days" value="' + str(days) + '" min="1" max="365" style="width:60px"></label>'
            '<button type="submit">刷新</button>'
            '</form>'
        )

        dashboard_style = """
        <style>
          .dashboard-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
          .dashboard-section { background: #fff; border-radius: 8px; padding: 16px; box-shadow: 0 1px 3px rgba(0,0,0,.1); }
          .dashboard-section h2 { font-size: 15px; color: #555; margin: 0 0 12px 0; border-bottom: 1px solid #eee; padding-bottom: 8px; }
          .dashboard-section.full { grid-column: 1 / -1; }
          .filter-bar { background: #fff; border-radius: 8px; padding: 12px 16px; margin-bottom: 20px; box-shadow: 0 1px 3px rgba(0,0,0,.1); display: flex; gap: 12px; align-items: flex-end; flex-wrap: wrap; }
          .filter-bar label { font-size: 13px; color: #555; }
          .filter-bar input { border: 1px solid #ccc; border-radius: 4px; padding: 4px 8px; font-size: 13px; }
          .filter-bar button { background: #64b5f6; color: #fff; border: none; border-radius: 4px; padding: 6px 16px; cursor: pointer; font-size: 13px; }
        </style>
        """

        full_style = self._HTML_STYLE.replace("</style>", dashboard_style + "</style>")

        html = (
            "<!DOCTYPE html>\n<html><head><meta charset='utf-8'><title>MemALL · 仪表盘</title>{style}</head><body>{nav}"
            "<h1>时间线仪表盘</h1>{filter}"
            "<div style='margin-bottom:16px'>{stats}</div>"
            "<div class='dashboard-grid'>"
            "<div class='dashboard-section'><h2>记忆热力</h2>{heatmap}</div>"
            "<div class='dashboard-section'><h2>决策弧</h2>{arc}</div>"
            "<div class='dashboard-section'><h2>活跃时期</h2>{epoch}</div>"
            "<div class='dashboard-section full'><h2>最近结束的时期</h2>{recent}</div>"
            "</div>"
            "</body></html>"
        ).format(
            style=full_style, nav=_NAV_HTML, filter=filter_html, stats=stats_html,
            heatmap=heatmap_bars, arc=arc_html,
            epoch=epoch_cards, recent=recent_epoch_list or '<p style="color:#999">暂无</p>',
        )

        return web.Response(text=html, content_type="text/html")

    # ── API: time_slices JSON ──

    async def _handle_api_slices(self, request: web.Request) -> web.Response:
        agent_name = request.query.get("agent_name", "").strip() or "*"
        granularity = request.query.get("granularity", "day")
        days = _safe_int(request.query.get("days", 30))

        from datetime import timedelta
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

        with pool_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM time_slices WHERE agent_name = ? AND granularity = ? "
                "AND window_start >= ? ORDER BY window_start",
                (agent_name, granularity, cutoff),
            ).fetchall()

        return web.json_response({
            "agent_name": agent_name,
            "granularity": granularity,
            "slices": [dict(r) for r in rows],
        }, headers=_cors_headers(request))

    # ── API: timeline density (daily memory counts for heatmap) ──

    async def _handle_api_timeline_density(self, request: web.Request) -> web.Response:
        days = _safe_int(request.query.get("days", 30))
        agent_name = request.query.get("agent_name", "").strip() or None
        from datetime import timedelta
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        with pool_conn() as conn:
            if agent_name:
                rows = conn.execute(
                    "SELECT slice_key, memory_count, category_distribution FROM time_slices "
                    "WHERE agent_name = ? AND granularity = 'day' AND window_start >= ? "
                    "ORDER BY slice_key",
                    (agent_name, cutoff),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT slice_key, SUM(memory_count) as memory_count FROM time_slices "
                    "WHERE granularity = 'day' AND window_start >= ? "
                    "GROUP BY slice_key ORDER BY slice_key",
                    (cutoff,),
                ).fetchall()
        return web.json_response({
            "days": len(rows),
            "density": [{"date": r["slice_key"], "count": r["memory_count"]} for r in rows],
        }, headers=_cors_headers(request))

    # ── API: epoch-structured timeline ──

    async def _handle_api_timeline_epochs(self, request: web.Request) -> web.Response:
        days = _safe_int(request.query.get("days", 7))
        agent_name = request.query.get("agent_name", "").strip() or None

        # Get memories in time window
        results = timeline(days=days)
        if agent_name:
            results = [r for r in results if r.agent_name and r.agent_name.lower() == agent_name.lower()]

        # Get epochs overlapping the time window
        from datetime import timedelta
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        with pool_conn() as conn:
            if agent_name:
                epoch_rows = conn.execute(
                    "SELECT * FROM epochs WHERE agent_name = ? AND "
                    "(ended_at IS NULL OR ended_at >= ?) AND started_at <= ? "
                    "ORDER BY started_at",
                    (agent_name, cutoff, datetime.now(timezone.utc).isoformat()),
                ).fetchall()
            else:
                epoch_rows = conn.execute(
                    "SELECT * FROM epochs WHERE "
                    "(ended_at IS NULL OR ended_at >= ?) AND started_at <= ? "
                    "ORDER BY agent_name, started_at",
                    (cutoff, datetime.now(timezone.utc).isoformat()),
                ).fetchall()

        # Assign each memory to an epoch
        epoch_map = {e["id"]: dict(e) for e in epoch_rows}
        epoch_map[0] = {"id": 0, "label": "未归属", "started_at": cutoff, "ended_at": None,
                        "boundary_reason": "auto", "memory_count": 0, "agent_name": ""}

        epoch_children: dict[int, list] = {eid: [] for eid in epoch_map}

        for mem in results:
            mem_occurred = (mem.occurred_at or mem.created_at or "")
            assigned = False
            for e in sorted(epoch_map.values(), key=lambda x: x.get("started_at", "")):
                e_start = e.get("started_at", "")
                e_end = e.get("ended_at") or "9999"
                if e_start <= mem_occurred <= e_end:
                    epoch_children.setdefault(e["id"], []).append(mem)
                    assigned = True
                    break
            if not assigned:
                epoch_children.setdefault(0, []).append(mem)

        # Build response with edge counts
        result_epochs = []
        for eid, mems in epoch_children.items():
            if eid == 0 and not mems:
                continue
            e = epoch_map[eid]
            with pool_conn() as conn:
                mem_list = []
                for m in mems:
                    sup_cnt = conn.execute(
                        "SELECT COUNT(*) as c FROM edges WHERE source_id = ? AND relation_type = 'supersedes'",
                        (m.id,),
                    ).fetchone()["c"]
                    ref_cnt = conn.execute(
                        "SELECT COUNT(*) as c FROM edges WHERE source_id = ? AND relation_type = 'refines'",
                        (m.id,),
                    ).fetchone()["c"]
                    mem_list.append({
                        "id": m.id,
                        "content": (m.content or "")[:250],
                        "level": m.level,
                        "category": m.category,
                        "agent_name": m.agent_name,
                        "occurred_at": m.occurred_at,
                        "supersedes_count": sup_cnt,
                        "refines_count": ref_cnt,
                    })

            result_epochs.append({
                "epoch": {
                    "id": e["id"],
                    "label": e.get("label", "")[:60],
                    "narrative": _epoch_narrative(mem_list),
                    "started_at": e.get("started_at", ""),
                    "ended_at": e.get("ended_at"),
                    "boundary_reason": e.get("boundary_reason", "auto"),
                    "memory_count": len(mems),
                    "agent_name": e.get("agent_name", ""),
                },
                "memories": mem_list,
            })

        # Sort epochs by start time (newest first), put unassigned at end
        result_epochs.sort(key=lambda x: x["epoch"]["started_at"], reverse=True)
        unassigned = [x for x in result_epochs if x["epoch"]["id"] == 0]
        assigned = [x for x in result_epochs if x["epoch"]["id"] != 0]
        result_epochs = assigned + unassigned

        return web.json_response({
            "epochs": result_epochs,
            "total_memories": len(results),
        }, headers=_cors_headers(request))

    # ── API: epochs JSON ──

    async def _handle_api_epochs(self, request: web.Request) -> web.Response:
        with pool_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM epochs ORDER BY agent_name, started_at"
            ).fetchall()

        return web.json_response({
            "epochs": [dict(r) for r in rows],
            "count": len(rows),
        }, headers=_cors_headers(request))

    async def _handle_api_epochs_agent(self, request: web.Request) -> web.Response:
        agent_name = request.match_info.get("agent_name", "").strip().lower()
        with pool_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM epochs WHERE agent_name = ? ORDER BY started_at",
                (agent_name,),
            ).fetchall()

        return web.json_response({
            "agent_name": agent_name,
            "epochs": [dict(r) for r in rows],
        }, headers=_cors_headers(request))

    # ── API: decision arcs ──

    async def _handle_api_arcs(self, request: web.Request) -> web.Response:
        agent_name = request.query.get("agent", "").strip().lower() or None
        status_filter = request.query.get("status", "").strip() or None

        where = ["level = 'L4' AND arc_status IS NOT NULL"]
        params = []
        if agent_name:
            where.append("agent_name = ?")
            params.append(agent_name)
        if status_filter in ("open", "in_progress", "closed"):
            where.append("arc_status = ?")
            params.append(status_filter)

        with pool_conn() as conn:
            rows = conn.execute(
                f"SELECT id, level, category, subject, agent_name, created_at, arc_status "
                f"FROM memories WHERE {' AND '.join(where)} ORDER BY created_at DESC",
                params,
            ).fetchall()

            stale_count = 0
            if not status_filter or status_filter == "open":
                stale_cutoff = (date.today() - timedelta(days=21)).isoformat()
                stale_rows = conn.execute(
                    "SELECT id FROM memories WHERE level = 'L4' AND arc_status = 'open' "
                    "AND created_at < ? AND id NOT IN ("
                    "  SELECT DISTINCT source_id FROM edges WHERE relation_type != 'deleted' "
                    "  AND target_id IN (SELECT id FROM memories WHERE level = 'L5')"
                    "  UNION "
                    "  SELECT DISTINCT target_id FROM edges WHERE relation_type != 'deleted' "
                    "  AND source_id IN (SELECT id FROM memories WHERE level = 'L5')"
                    ")",
                    (stale_cutoff,),
                ).fetchall()
                stale_ids = {r["id"] for r in stale_rows}

            stats = conn.execute(
                "SELECT arc_status, COUNT(*) as cnt FROM memories WHERE level = 'L4' "
                "AND arc_status IS NOT NULL GROUP BY arc_status"
            ).fetchall()
            status_counts = {r["arc_status"]: r["cnt"] for r in stats}

        arcs = []
        for r in rows:
            arc = dict(r)
            if not status_filter or status_filter == "open":
                arc["stale"] = r["id"] in stale_ids
                if arc.get("stale"):
                    stale_count += 1
            else:
                arc["stale"] = False
            arcs.append(arc)

        return web.json_response({
            "arcs": arcs,
            "stats": status_counts,
            "stale_count": stale_count,
        }, headers=_cors_headers(request))

    async def _handle_api_arcs_detail(self, request: web.Request) -> web.Response:
        try:
            decision_id = int(request.match_info.get("decision_id", "0"))
        except ValueError:
            return web.json_response({"error": "invalid decision_id"}, status=400)

        with pool_conn() as conn:
            decision = conn.execute(
                "SELECT * FROM memories WHERE id = ? AND level = 'L4'", (decision_id,)
            ).fetchone()
            if not decision:
                return web.json_response({"error": "decision not found"}, status=404)

            tasks = conn.execute(
                "SELECT m.id, m.level, m.subject, m.content, m.created_at "
                "FROM memories m JOIN edges e ON "
                "  (e.source_id = m.id OR e.target_id = m.id) "
                "WHERE m.level = 'L5' AND e.relation_type != 'deleted' "
                "AND (e.source_id = ? OR e.target_id = ?)",
                (decision_id, decision_id),
            ).fetchall()

            reflections = conn.execute(
                "SELECT m.id, m.level, m.subject, m.content, m.created_at "
                "FROM memories m JOIN edges e ON "
                "  (e.source_id = m.id OR e.target_id = m.id) "
                "WHERE m.level = 'L6' AND e.relation_type != 'deleted' "
                "AND (e.source_id = ? OR e.target_id = ?)",
                (decision_id, decision_id),
            ).fetchall()

            # Stale check
            stale = False
            if decision["arc_status"] == "open":
                cutoff = (date.today() - timedelta(days=21)).isoformat()
                if (decision["created_at"] or "")[:10] < cutoff and not tasks:
                    stale = True

        return web.json_response({
            "decision": dict(decision),
            "tasks": [dict(t) for t in tasks],
            "reflections": [dict(r) for r in reflections],
            "arc_status": decision["arc_status"],
            "stale": stale,
        }, headers=_cors_headers(request))

    async def _handle_api_epoch_arcs(self, request: web.Request) -> web.Response:
        try:
            epoch_id = int(request.match_info.get("epoch_id", "0"))
        except ValueError:
            return web.json_response({"error": "invalid epoch_id"}, status=400)

        with pool_conn() as conn:
            epoch = conn.execute(
                "SELECT id, agent_name, label, started_at, ended_at FROM epochs WHERE id = ?",
                (epoch_id,),
            ).fetchone()
            if not epoch:
                return web.json_response({"error": "epoch not found"}, status=404)

            start = epoch["started_at"][:10]
            end = (epoch["ended_at"] or "9999-12-31")[:10]

            arcs = conn.execute(
                "SELECT id, level, category, subject, agent_name, created_at, arc_status "
                "FROM memories WHERE level = 'L4' AND arc_status IS NOT NULL "
                "AND agent_name = ? AND created_at >= ? AND created_at <= ? "
                "ORDER BY created_at",
                (epoch["agent_name"], start, end),
            ).fetchall()

            status_counts = {"open": 0, "in_progress": 0, "closed": 0}
            arc_list = []
            for a in arcs:
                s = a["arc_status"] or "open"
                if s in status_counts:
                    status_counts[s] += 1
                arc_list.append({
                    "id": a["id"],
                    "subject": a["subject"],
                    "arc_status": s,
                })

            total = len(arc_list)
            closure_rate = round(status_counts["closed"] / total, 2) if total > 0 else 0.0

        return web.json_response({
            "epoch_id": epoch_id,
            "epoch_label": epoch["label"],
            "total_arcs": total,
            "open": status_counts["open"],
            "in_progress": status_counts["in_progress"],
            "closed": status_counts["closed"],
            "closure_rate": closure_rate,
            "arcs": arc_list,
        }, headers=_cors_headers(request))

    async def _handle_discussions(self, request: web.Request) -> web.Response:
        """HTML page: list all discussion topics with status badges."""
        from memall.pipeline.convergence import list_all_discussions
        all_rows = list_all_discussions()

        cards = ""
        for topic in all_rows:
            participants = topic.get("participants") or []
            resp_count = topic.get("response_count", 0)
            meta_status = topic.get("status", "active")
            color = "#43a047" if meta_status == "converged" else (
                     "#e53935" if meta_status == "stale" else "#fb8c00")
            status_badge = f'<span style="color:{color};font-weight:bold">{meta_status}</span>'
            summary = topic.get("summary", "") or ""
            cards += (
                '<div class="card">'
                '<div class="meta">{} {} · {} 条回复 · {} 位参与者</div>'
                '<h3>{}</h3>'
                '<div class="content">{}</div>'
                '</div>'
            ).format(
                status_badge,
                (topic.get("created_at") or "")[:19],
                resp_count,
                len(participants),
                topic.get("subject", "(无标题)"),
                summary[:200],
            )

        empty_placeholder = '<p style="color:#999">暂无讨论话题</p>'
        html = (
            '<!DOCTYPE html>\n<html><head><meta charset="utf-8">'
            f'<title>MemALL · 讨论看板</title>{self._HTML_STYLE}</head>'
            f'<body>{_NAV_HTML}<h1>讨论看板</h1>'
            f'{cards or empty_placeholder}'
            '</body></html>'
        )
        return web.Response(text=html, content_type="text/html")

    async def _handle_api_discussions(self, request: web.Request) -> web.Response:
        """JSON: list all active L5 discussions."""
        from memall.pipeline.convergence import list_active_discussions
        topics = list_active_discussions()
        return web.json_response({"topics": topics}, headers=_cors_headers(request))

    async def _handle_api_discussion_detail(self, request: web.Request) -> web.Response:
        """JSON: full detail for a single L5 discussion including all responses."""
        topic_id = request.match_info.get("topic_id", "")
        from memall.pipeline.convergence import get_discussion
        result = get_discussion(int(topic_id))
        return web.json_response(result, headers=_cors_headers(request))

    async def _handle_api_discussion_create(self, request: web.Request) -> web.Response:
        """JSON: create a new L5 discussion and return memory_id."""
        data = await self._read_json(request)
        if not data:
            return web.json_response({"error": "invalid JSON"}, status=400, headers=_cors_headers(request))
        from memall.pipeline.convergence import create_discussion
        result = create_discussion(
            title=data.get("title", ""),
            background=data.get("background", ""),
            options=data.get("options"),
            open_questions=data.get("open_questions"),
            recommendation=data.get("recommendation", ""),
            action_items=data.get("action_items"),
        )
        return web.json_response(result, headers=_cors_headers(request))

    async def _handle_api_discussion_respond(self, request: web.Request) -> web.Response:
        """JSON: record an agent's response via L5 P2 + edge."""
        data = await self._read_json(request)
        if not data:
            return web.json_response({"error": "invalid JSON"}, status=400, headers=_cors_headers(request))
        from memall.pipeline.convergence import confirm_discussion
        topic_id = data.get("topic_id", "")
        result = confirm_discussion(
            discussion_id=int(topic_id) if topic_id else 0,
            agent_name=data.get("agent_name", ""),
            stance=data.get("stance", "pass"),
            note=data.get("arguments", ""),
        )
        return web.json_response(result, headers=_cors_headers(request))

    async def _handle_timeline_html(self, request: web.Request) -> web.Response:
        days = _safe_int(request.query.get("days", 7))
        agent_name = request.query.get("agent_name", "").strip() or None
        category = request.query.get("category", "").strip() or None

        # ── 1. Density data from time_slices ──
        from datetime import timedelta
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        density_data: list[dict] = []
        max_density = 1
        with pool_conn() as conn:
            if agent_name:
                den_rows = conn.execute(
                    "SELECT slice_key, memory_count FROM time_slices "
                    "WHERE agent_name = ? AND granularity = 'day' AND window_start >= ? ORDER BY slice_key",
                    (agent_name, cutoff),
                ).fetchall()
            else:
                den_rows = conn.execute(
                    "SELECT slice_key, SUM(memory_count) as memory_count FROM time_slices "
                    "WHERE granularity = 'day' AND window_start >= ? "
                    "GROUP BY slice_key ORDER BY slice_key",
                    (cutoff,),
                ).fetchall()
            for r in den_rows:
                c = r["memory_count"]
                density_data.append({"date": r["slice_key"], "count": c})
                if c > max_density:
                    max_density = c

        # ── 2. Memories and epoch assignment ──
        results = timeline(days=days, category=category)
        if agent_name:
            results = [r for r in results if r.agent_name and r.agent_name.lower() == agent_name.lower()]

        with pool_conn() as conn:
            if agent_name:
                epoch_rows = conn.execute(
                    "SELECT * FROM epochs WHERE agent_name = ? AND "
                    "(ended_at IS NULL OR ended_at >= ?) AND started_at <= ? ORDER BY started_at",
                    (agent_name, cutoff, datetime.now(timezone.utc).isoformat()),
                ).fetchall()
            else:
                epoch_rows = conn.execute(
                    "SELECT * FROM epochs WHERE "
                    "(ended_at IS NULL OR ended_at >= ?) AND started_at <= ? ORDER BY agent_name, started_at",
                    (cutoff, datetime.now(timezone.utc).isoformat()),
                ).fetchall()

        epoch_map: dict[int, dict] = {0: {"id": 0, "label": "未归属", "started_at": cutoff, "ended_at": None,
                                           "boundary_reason": "auto", "memory_count": 0, "agent_name": "",
                                           "is_active": False}}
        for e in epoch_rows:
            e_dict = dict(e)
            is_active = e_dict.get("ended_at") is None
            epoch_map[e_dict["id"]] = {**e_dict, "is_active": is_active}

        epoch_children: dict[int, list] = {eid: [] for eid in epoch_map}
        for mem in results:
            mem_occurred = (mem.occurred_at or mem.created_at or "")
            assigned = False
            for e in sorted(epoch_map.values(), key=lambda x: x.get("started_at", "")):
                e_start = e.get("started_at", "")
                e_end = e.get("ended_at") or "9999"
                if e_start <= mem_occurred <= e_end:
                    epoch_children.setdefault(e["id"], []).append(mem)
                    assigned = True
                    break
            if not assigned:
                epoch_children.setdefault(0, []).append(mem)

        # ── 3. Build epoch group HTML ──
        ordered_epochs = sorted(
            [e for eid, e in epoch_map.items() if eid != 0 and epoch_children.get(eid)],
            key=lambda x: x.get("started_at", ""), reverse=True,
        )
        unassigned = epoch_children.get(0, [])

        cards_html = ""
        for e in ordered_epochs:
            mems = epoch_children[e["id"]]
            # epoch header
            duration = ""
            if e.get("ended_at"):
                dur_days = (datetime.fromisoformat(e["ended_at"]) - datetime.fromisoformat(e["started_at"])).days
                duration = f"{dur_days}天" if dur_days > 0 else "<1天"
            else:
                dur_days = (datetime.now(timezone.utc) - datetime.fromisoformat(e["started_at"])).days
                duration = f"{dur_days}天（进行中）"
            active_class = " epoch-active" if e.get("is_active") else ""

            boundary_label = {
                "gap": "间隔", "category_shift": "主题切换",
                "l6_viewpoint_change": "观点转变", "manual": "手动",
            }.get(e.get("boundary_reason", ""), e.get("boundary_reason", ""))

            with pool_conn() as conn2:
                group_html = "\n".join(
                    self._render_timeline_card(m, conn2)
                    for m in mems
                )

            cards_html += (
                f'<div class="epoch-group{active_class}">'
                f'<div class="epoch-header">'
                f'<span class="epoch-label">{esc_html(e.get("label", "")[:60])}</span>'
                f'<span class="epoch-meta">'
                f'{e.get("started_at", "")[:10]} → {esc_html(duration)}'
                f' · <span class="epoch-badge">{boundary_label}</span>'
                f' · {len(mems)} 条'
                f'{" · @" + e.get("agent_name", "") if e.get("agent_name") else ""}'
                f'</span>'
                f'</div>'
                f'<div class="epoch-narrative">{_epoch_narrative(mems)}</div>'
                f'<div class="timeline-line">{group_html}</div>'
                f'</div>'
            )

        # Unassigned memories (before any epoch)
        if unassigned:
            with pool_conn() as conn2:
                group_html = "\n".join(self._render_timeline_card(m, conn2) for m in unassigned)
            cards_html += (
                f'<div class="epoch-group">'
                f'<div class="epoch-header" style="opacity:0.6">'
                f'<span class="epoch-label">未归属记忆</span>'
                f'<span class="epoch-meta">{len(unassigned)} 条 · 不在任何 Epoch 范围内</span>'
                f'</div>'
                f'<div class="timeline-line">{group_html}</div>'
                f'</div>'
            )

        if not cards_html:
            cards_html = '<div class="empty-state" style="margin-top:40px"><p>该时间段内暂无记忆</p></div>'

        # ── 4. Density chart HTML ──
        density_html = ""
        if density_data:
            bars = "".join(
                '<div class="density-bar" style="height:{}px;background:{}" '
                'title="{}: {} 条" data-date="{}"></div>'.format(
                    max(4, round(d["count"] / max_density * 60)),
                    _density_color(d["count"], max_density),
                    d["date"], d["count"], d["date"],
                )
                for d in density_data
            )
            density_html = f'<div class="density-chart"><div class="density-label">记忆密度</div><div class="density-bars">{bars}</div></div>'

        # ── 5. Filter form ──
        filter_html = (
            '<form class="filter-bar" method="get">'
            '<label>天数: <input type="number" name="days" value="{}" min="1" max="365" style="width:60px"></label>'
            '<label>Agent: <input type="text" name="agent_name" value="{}" placeholder="全部" style="width:120px"></label>'
            '<label>分类: <input type="text" name="category" value="{}" placeholder="全部" style="width:120px"></label>'
            '<button type="submit">筛选</button>'
            '</form>'
        ).format(days, esc_html(agent_name or ""), esc_html(category or ""))

        # ── 6. Style ──
        timeline_style = """
        <style>
          .filter-bar { background: #fff; border-radius: 8px; padding: 12px 16px; margin-bottom: 20px; box-shadow: 0 1px 3px rgba(0,0,0,.1); display: flex; gap: 12px; align-items: flex-end; flex-wrap: wrap; }
          .filter-bar label { font-size: 13px; color: #555; }
          .filter-bar input { border: 1px solid #ccc; border-radius: 4px; padding: 4px 8px; font-size: 13px; }
          .filter-bar button { background: #64b5f6; color: #fff; border: none; border-radius: 4px; padding: 6px 16px; cursor: pointer; font-size: 13px; }
          .filter-bar button:hover { background: #42a5f5; }
          .density-chart { background: #fff; border-radius: 8px; padding: 12px 16px; margin-bottom: 16px; box-shadow: 0 1px 3px rgba(0,0,0,.1); }
          .density-label { font-size: 12px; color: #999; margin-bottom: 8px; }
          .density-bars { display: flex; align-items: flex-end; gap: 2px; height: 70px; overflow-x: auto; flex-wrap: nowrap; }
          .density-bar { min-width: 8px; border-radius: 2px 2px 0 0; cursor: pointer; flex-shrink: 0; transition: opacity .2s; }
          .density-bar:hover { opacity: .7; }
          .epoch-group { margin-bottom: 28px; position: relative; }
          .epoch-group.epoch-active { border-left: 3px solid #4caf50; padding-left: 12px; margin-left: -3px; }
          .epoch-header { font-size: 15px; font-weight: bold; color: #444; margin-bottom: 10px; padding: 8px 12px; background: #fafafa; border-radius: 6px; border: 1px solid #e0e0e0; display: flex; justify-content: space-between; align-items: baseline; flex-wrap: wrap; gap: 4px; }
          .epoch-label { color: #333; }
          .epoch-meta { font-size: 12px; font-weight: normal; color: #999; }
          .epoch-badge { display: inline-block; padding: 1px 6px; border-radius: 3px; font-size: 11px; background: #e3f2fd; color: #1976d2; }
          .epoch-narrative { font-size: 12px; color: #888; margin: -6px 0 10px 16px; padding-left: 4px; font-style: italic; }
          .timeline-line { border-left: 3px solid #ddd; padding-left: 16px; margin-left: 8px; }
          .timeline-card { background: #fff; border-radius: 6px; padding: 10px 14px; margin: 8px 0; box-shadow: 0 1px 2px rgba(0,0,0,.08); position: relative; }
          .timeline-card::before { content: ''; position: absolute; left: -22px; top: 14px; width: 10px; height: 10px; border-radius: 50%; background: #bbb; border: 2px solid #fff; }
          .timeline-card .meta { font-size: 12px; color: #999; margin-bottom: 4px; }
          .timeline-card .time { font-family: monospace; font-size: 11px; color: #aaa; }
          .timeline-card .content { font-size: 13px; line-height: 1.5; color: #333; white-space: pre-wrap; }
          .rel-badge { display: inline-block; padding: 1px 5px; border-radius: 3px; font-size: 10px; margin-left: 4px; }
          .rel-supersedes { background: #e0e0e0; color: #666; }
          .rel-refines { background: #e3f2fd; color: #1565c0; }
        </style>
        """

        full_style = self._HTML_STYLE.replace("</style>", timeline_style + "</style>")
        title = f"记忆时间线 · 最近 {days} 天"
        if agent_name:
            title += f" · {agent_name}"
        if category:
            title += f" · {category}"

        html = "<!DOCTYPE html>\n<html><head><meta charset='utf-8'><title>MemALL · {}</title>{}</head><body>{}</body></html>".format(
            title, full_style,
            _NAV_HTML
            + '<h1>🧠 记忆时间线 <span style="font-size:14px;color:#999;font-weight:normal">最近 {} 天{}{}</span></h1>'.format(
                days,
                f" · {agent_name}" if agent_name else "",
                f" · {category}" if category else "",
            )
            + density_html
            + filter_html
            + cards_html,
        )
        return web.Response(text=html, content_type="text/html")

    # ── Helper: render a single timeline card with relationship badges ──

    def _render_timeline_card(self, mem, conn) -> str:
        sup_cnt = conn.execute(
            "SELECT COUNT(*) as c FROM edges WHERE source_id = ? AND relation_type = 'supersedes'",
            (mem.id,),
        ).fetchone()["c"]
        ref_cnt = conn.execute(
            "SELECT COUNT(*) as c FROM edges WHERE source_id = ? AND relation_type = 'refines'",
            (mem.id,),
        ).fetchone()["c"]
        rel_badges = ""
        if sup_cnt > 0:
            rel_badges += f'<span class="rel-badge rel-supersedes" title="已取代 {sup_cnt} 条">已取代 {sup_cnt}</span>'
        if ref_cnt > 0:
            rel_badges += f'<span class="rel-badge rel-refines" title="基于 {ref_cnt} 条">基于 {ref_cnt}</span>'
        agent_tag = f' <span class="tag" style="background:#f0e6ff">@{mem.agent_name}</span>' if mem.agent_name else ""
        return (
            '<div class="timeline-card">'
            '<div class="meta">'
            '<span class="time">{}</span> '
            '<span class="tag">{}</span> '
            '<span class="tag l4">{}</span>{}'
            '<span style="float:right">{}</span>'
            '</div>'
            '<div class="content">{}</div>'
            '</div>'
        ).format(
            (mem.occurred_at or "")[11:19],
            esc_html(mem.level or ""),
            esc_html(mem.category or ""),
            agent_tag,
            rel_badges,
            esc_html((mem.content or "")[:250]),
        )

    async def _handle_capture(self, request: web.Request) -> web.Response:
        data = await self._read_json(request)
        if data is None:
            return web.json_response({"error": "invalid JSON body"}, status=400, headers=_cors_headers(request))
        try:
            agent_name = data.get("agent_name", "")
            content = data.get("content", "")
            if not content:
                return web.json_response({"error": "content is required"}, status=400, headers=_cors_headers(request))
            inp = MemoryInput(
                content=content,
                agent_name=agent_name,
                category=data.get("category", "general"),
                level=data.get("level", "P2"),
            )
            mid = capture(inp)
            return web.json_response({"id": mid, "status": "ok"}, headers=_cors_headers(request))
        except Exception as exc:
            return web.json_response({"error": str(exc)}, status=500, headers=_cors_headers(request))

    async def _handle_retrieve(self, request: web.Request) -> web.Response:
        data = await self._read_json(request)
        if data is None:
            return web.json_response({"error": "invalid JSON body"}, status=400, headers=_cors_headers(request))
        try:
            query = data.get("query", "")
            agent = data.get("agent_name", None)
            top_n = data.get("top_n", 20)
            results = retrieve(query=query, agent_name=agent, limit=top_n)
            items = []
            for r in results:
                items.append({
                    "id": r.id,
                    "content": r.content,
                    "agent_name": r.agent_name,
                    "category": r.category,
                    "level": r.level,
                    "confidence": r.confidence,
                })
            return web.json_response({"results": items, "count": len(items)}, headers=_cors_headers(request))
        except Exception as exc:
            return web.json_response({"error": str(exc)}, status=500, headers=_cors_headers(request))

    async def _handle_traverse(self, request: web.Request) -> web.Response:
        data = await self._read_json(request)
        if data is None:
            return web.json_response({"error": "invalid JSON body"}, status=400, headers=_cors_headers(request))
        try:
            mid = data.get("memory_id")
            if mid is None:
                return web.json_response(
                    {"error": "memory_id is required"},
                    status=400,
                    headers=_cors_headers(request),
                )
            depth = data.get("depth", 1)
            result = traverse(int(mid), depth=int(depth))
            return web.json_response(result, headers=_cors_headers(request))
        except Exception as exc:
            return web.json_response(
                {"error": str(exc)}, status=500, headers=_cors_headers(request)
            )

    async def _handle_timeline(self, request: web.Request) -> web.Response:
        data = await self._read_json(request)
        if data is None:
            return web.json_response({"error": "invalid JSON body"}, status=400, headers=_cors_headers(request))
        try:
            agent = data.get("agent_name")
            if not agent:
                return web.json_response(
                    {"error": "agent_name is required"},
                    status=400,
                    headers=_cors_headers(request),
                )
            days = data.get("days", 7)
            results = timeline(agent_name=agent, days=int(days))
            items = [
                {
                    "id": r.id,
                    "content": r.content,
                    "occurred_at": r.occurred_at,
                    "category": r.category,
                }
                for r in results
            ]
            return web.json_response(
                {"results": items, "count": len(items)},
                headers=_cors_headers(request),
            )
        except Exception as exc:
            return web.json_response(
                {"error": str(exc)}, status=500, headers=_cors_headers(request)
            )

    async def _handle_profile(self, request: web.Request) -> web.Response:
        data = await self._read_json(request)
        if data is None:
            return web.json_response({"error": "invalid JSON body"}, status=400, headers=_cors_headers(request))
        try:
            agent = data.get("agent_name")
            if not agent:
                return web.json_response(
                    {"error": "agent_name is required"},
                    status=400,
                    headers=_cors_headers(request),
                )
            profile = generate_profile_3layer(agent)
            layer = data.get("layer")
            if layer and layer in profile:
                return web.json_response(
                    {layer: profile[layer]}, headers=_cors_headers(request)
                )
            return web.json_response(profile, headers=_cors_headers(request))
        except Exception as exc:
            return web.json_response(
                {"error": str(exc)}, status=500, headers=_cors_headers(request)
            )

    async def _handle_pair(self, request: web.Request) -> web.Response:
        data = await self._read_json(request)
        if data is None:
            return web.json_response({"error": "invalid JSON body"}, status=400, headers=_cors_headers(request))
        try:
            device_name = data.get("device_name", "unknown")
            remote_addr = request.remote
            return web.json_response(
                {
                    "paired": True,
                    "peer_name": device_name,
                    "remote_address": remote_addr,
                },
                headers=_cors_headers(request),
            )
        except Exception as exc:
            return web.json_response(
                {"error": str(exc)}, status=500, headers=_cors_headers(request)
            )


# ══════════════════════════════════════════════════════════════════
# 2. Sync Protocol — Export / Import
# ══════════════════════════════════════════════════════════════════

def export_bundle(agent_name: str, fmt: str = "json") -> Dict[str, Any]:
    """Export all data for an agent as a portable bundle.

    The bundle includes memories, edges, identity record, tags, and a
    timestamp.  Also writes the bundle to a temp JSON file under
    ``~/.memall/exports/`` and includes the file path in the return dict.

    Args:
        agent_name: Agent to export.
        fmt: Output format (only ``"json"`` supported currently).

    Returns:
        dict with keys: version, exported_at, agent_name, memories,
        edges, identity, file_path.
    """
    if fmt != "json":
        raise ValueError(f"unsupported format '{fmt}', only 'json' is supported")

    conn = get_conn()
    try:
        # ── Memories ──
        mem_rows = conn.execute(
            "SELECT * FROM memories WHERE agent_name = ? ORDER BY id",
            (agent_name,),
        ).fetchall()
        memories = [dict(r) for r in mem_rows]

        # ── Edges (all edges involving this agent's memories) ──
        mem_ids = [r["id"] for r in mem_rows]
        edges = []
        if mem_ids:
            placeholders = ",".join("?" for _ in mem_ids)
            edge_rows = conn.execute(
                f"SELECT * FROM edges WHERE source_id IN ({placeholders}) OR target_id IN ({placeholders})",
                mem_ids + mem_ids,
            ).fetchall()
            edges = [dict(r) for r in edge_rows]

        # ── Identity ──
        ident = conn.execute(
            "SELECT * FROM identities WHERE agent_name = ?", (agent_name,)
        ).fetchone()
        identity = dict(ident) if ident else {}

        bundle = {
            "version": "1.0",
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "agent_name": agent_name,
            "memories": memories,
            "edges": edges,
            "identity": identity,
        }
    finally:
        conn.close()

    # ── Write to file ──
    # Sanitize agent_name to prevent path traversal
    safe_name = re.sub(r'[^a-zA-Z0-9_\-\.]+', '_', agent_name) if agent_name else "unknown"
    export_dir = _PROJECT_DIR / "exports"
    export_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    file_path = export_dir / f"bundle_{safe_name}_{ts}.json"
    file_path.write_text(json.dumps(bundle, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    bundle["file_path"] = str(file_path)
    return bundle


# ── Identity merge ──

def _import_identity(conn, identity: dict, agent_name: str) -> bool:
    """Import/update an identity record. Returns True if updated/inserted."""
    agent_name = normalize_agent_name(agent_name)
    if not identity or not agent_name:
        return False
    existing = conn.execute(
        "SELECT id FROM identities WHERE agent_name = ?", (agent_name,)
    ).fetchone()
    if existing:
        conn.execute(
            "UPDATE identities SET description = ?, agent_type = ?, "
            "last_heartbeat = ?, metadata = ? WHERE agent_name = ?",
            (
                identity.get("description", ""),
                identity.get("agent_type", "ai"),
                identity.get("last_heartbeat", datetime.now(timezone.utc).isoformat()),
                identity.get("metadata", None),
                agent_name,
            ),
        )
    else:
        conn.execute(
            "INSERT INTO identities (agent_name, agent_type, description, "
            "last_heartbeat, metadata) VALUES (?,?,?,?,?)",
            (
                agent_name,
                identity.get("agent_type", "ai"),
                identity.get("description", ""),
                identity.get("last_heartbeat", datetime.now(timezone.utc).isoformat()),
                identity.get("metadata", None),
            ),
        )
    return True


def _import_memories(conn, memories: list, agent_name: str) -> tuple:
    """Import memories with dedup by content_hash.

    Returns (imported_count, old_id_to_new: dict).
    """
    agent_name = normalize_agent_name(agent_name)
    existing_hashes = {
        r["content_hash"]
        for r in conn.execute(
            "SELECT content_hash FROM memories WHERE content_hash IS NOT NULL AND content_hash != ''"
        ).fetchall()
    }
    imported = 0
    old_id_to_new: Dict[int, int] = {}

    for m in memories:
        h = m.get("content_hash", "")
        if h and h in existing_hashes:
            row = conn.execute(
                "SELECT id FROM memories WHERE content_hash = ?",
                (h,),
            ).fetchone()
            if row:
                old_id_to_new[m["id"]] = row["id"]
            continue

        fields = {
            "content": m.get("content", ""),
            "content_hash": h,
            "level": m.get("level", "P2"),
            "owner": m.get("owner", ""),
            "agent_name": agent_name,
            "subject": m.get("subject", ""),
            "project": m.get("project", ""),
            "category": m.get("category", "general"),
            "summary": m.get("summary", ""),
            "occurred_at": m.get("occurred_at", datetime.now(timezone.utc).isoformat()),
            "created_at": m.get("created_at", datetime.now(timezone.utc).isoformat()),
            "updated_at": m.get("updated_at", datetime.now(timezone.utc).isoformat()),
            "supersedes": m.get("supersedes", None),
            "confidence": m.get("confidence", 1.0),
            "visibility": m.get("visibility", "private"),
            "metadata": json.dumps(m.get("metadata", {})) if isinstance(m.get("metadata"), dict) else (m.get("metadata") or "{}"),
        }

        cur = conn.execute("PRAGMA table_info(memories)")
        cols = {r["name"] for r in cur.fetchall()}
        if "tags" in cols:
            fields["tags"] = m.get("tags", "[]")

        columns = list(fields.keys())
        placeholders = ",".join("?" for _ in columns)
        values = [fields[c] for c in columns]

        cur = conn.execute(
            f"INSERT INTO memories ({','.join(columns)}) VALUES ({placeholders})",
            values,
        )
        old_id_to_new[m["id"]] = cur.lastrowid
        existing_hashes.add(h)
        imported += 1

    return imported, old_id_to_new


def _import_edges(conn, edges_in: list, old_id_to_new: dict) -> int:
    """Import edges with dedup and ID remapping. Returns imported count."""
    existing_edge_keys = {
        (r["source_id"], r["target_id"], r["relation_type"])
        for r in conn.execute("SELECT source_id, target_id, relation_type FROM edges").fetchall()
    }
    imported = 0
    for e in edges_in:
        src = old_id_to_new.get(e.get("source_id"))
        tgt = old_id_to_new.get(e.get("target_id"))
        if src is None or tgt is None or src == tgt:
            continue
        key = (src, tgt, e.get("relation_type", "refines"))
        if key in existing_edge_keys:
            continue
        conn.execute(
            "INSERT INTO edges (source_id, target_id, relation_type, weight, created_at, metadata) "
            "VALUES (?,?,?,?,?,?)",
            (
                src, tgt,
                e.get("relation_type", "refines"),
                e.get("weight", 1.0),
                e.get("created_at", datetime.now(timezone.utc).isoformat()),
                e.get("metadata", "{}"),
            ),
        )
        existing_edge_keys.add(key)
        imported += 1
    return imported


def import_bundle(bundle_or_path) -> Dict[str, Any]:
    """Import a full bundle (memories + edges + identity).

    Deduplicates memories by ``content_hash`` and edges by
    ``(source_content_hash, target_content_hash, relation_type)``.
    Identity records are updated if the agent already exists, otherwise
    inserted.

    Args:
        bundle_or_path: A dict bundle or a path (str/Path) to a bundle JSON file.

    Returns:
        dict: {imported_memories, imported_edges, identity_updated}
    """
    # ── Resolve input ──
    if isinstance(bundle_or_path, dict):
        bundle = bundle_or_path
    else:
        path = Path(bundle_or_path)
        if not path.exists():
            raise FileNotFoundError(f"bundle file not found: {path}")
        # Security: restrict import to the exports directory
        allowed_dir = (_PROJECT_DIR / "exports").resolve()
        resolved = path.resolve()
        if not str(resolved).startswith(str(allowed_dir)):
            raise PermissionError(
                f"Import rejected: {resolved} is outside allowed directory {allowed_dir}"
            )
        bundle = json.loads(path.read_text(encoding="utf-8"))

    memories = bundle.get("memories", [])
    edges_in = bundle.get("edges", [])
    identity = bundle.get("identity", {})
    agent_name = bundle.get("agent_name", identity.get("agent_name", ""))

    conn = get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        identity_updated = _import_identity(conn, identity, agent_name)
        imported_memories, old_id_to_new = _import_memories(conn, memories, agent_name)
        imported_edges = _import_edges(conn, edges_in, old_id_to_new)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    return {
        "imported_memories": imported_memories,
        "imported_edges": imported_edges,
        "identity_updated": identity_updated,
    }


# ══════════════════════════════════════════════════════════════════
# 3. Device Discovery & Pairing
# ══════════════════════════════════════════════════════════════════

_DISCOVERY_THREAD: Optional[threading.Thread] = None
_DISCOVERY_RUNNING = threading.Event()
_DISCOVERY_LOCK = threading.Lock()
_DISCOVERY_PORT = 9920


def start_discovery(port: int = 9920) -> None:
    """Start broadcasting MemALL discovery beacons on the LAN.

    Sends a UDP broadcast every 5 seconds.  Runs on a background
    daemon thread.  Call :func:`stop_discovery` to shut it down.

    Args:
        port: UDP port used for discovery broadcasts.
    """
    global _DISCOVERY_THREAD, _DISCOVERY_PORT
    with _DISCOVERY_LOCK:
        _DISCOVERY_PORT = port
        if _DISCOVERY_THREAD is not None and _DISCOVERY_RUNNING.is_set():
            return  # already running

        _DISCOVERY_RUNNING.set()

    def _broadcast_loop() -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        msg = json.dumps({
            "type": "memall_discovery",
            "device_name": socket.gethostname(),
            "port": port,
            "version": "1.0",
        }).encode("utf-8")
        while _DISCOVERY_RUNNING.is_set():
            try:
                sock.sendto(msg, ("255.255.255.255", port))
            except Exception:
                logger.warning("Discovery broadcast failed: %s", exc_info=True)
            time.sleep(5)
        sock.close()

    _DISCOVERY_THREAD = threading.Thread(target=_broadcast_loop, daemon=True)
    logger.info("Discovery started on port %d", port)
    _DISCOVERY_THREAD.start()


def stop_discovery() -> None:
    """Stop the discovery broadcast thread."""
    global _DISCOVERY_THREAD
    with _DISCOVERY_LOCK:
        _DISCOVERY_RUNNING.clear()
        _DISCOVERY_THREAD = None


def discover_peers(timeout: float = 5.0) -> List[Dict[str, Any]]:
    """Listen for MemALL discovery beacons on the LAN.

    Opens a UDP socket on the discovery port and collects announcements
    for *timeout* seconds.  Returns a deduplicated list of peers.

    Args:
        timeout: How many seconds to listen (default 5).

    Returns:
        list of dicts: ``[{device_name, address, port, version}, ...]``
    """
    seen: set = set()
    peers: List[Dict[str, Any]] = []

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.settimeout(timeout)
    try:
        try:
            sock.bind(("", _DISCOVERY_PORT))
        except OSError:
            # Port in use — try a random port for listening
            try:
                sock.bind(("", 0))
            except OSError:
                raise

        deadline = time.time() + timeout
        while time.time() < deadline:
            remaining = deadline - time.time()
            if remaining <= 0:
                break
            try:
                sock.settimeout(remaining)
                data, addr = sock.recvfrom(4096)
                msg = json.loads(data.decode("utf-8"))
                if msg.get("type") == "memall_discovery":
                    dev = msg.get("device_name", addr[0])
                    if dev not in seen:
                        seen.add(dev)
                        peers.append({
                            "device_name": dev,
                            "address": addr[0],
                            "port": msg.get("port", _DISCOVERY_PORT),
                            "version": msg.get("version", "1.0"),
                        })
            except (socket.timeout, json.JSONDecodeError, OSError):
                continue
    finally:
        sock.close()
    return peers


def pair_with_peer(address: str, local_token: str = "") -> Dict[str, Any]:
    """Send a pairing request to a remote MemALL gateway.

    The remote gateway must have its HTTP server running.  Sends
    ``POST /pair`` with the local device name.  On success, records
    the peer in ``peers.json`` along with its auth token.

    Args:
        address: ``"IP:PORT"`` string, e.g. ``"192.168.1.5:9919"``.
        local_token: This gateway's auth token (used to authenticate
                     the remote peer's return requests).

    Returns:
        dict: {paired: bool, peer_name: str}
    """
    url = f"http://{address}/pair"
    payload = json.dumps({
        "device_name": socket.gethostname(),
        "token": local_token,
    }).encode("utf-8")

    req = urllib.request.Request(
        url, data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            result = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, json.JSONDecodeError) as exc:
        return {"paired": False, "peer_name": address, "error": str(exc)}

    # ── Persist to peers.json ──
    peers = _load_peers()
    host = address.split(":")[0]
    port_str = address.split(":")[1] if ":" in address else "9919"
    peer_entry = {
        "device_name": result.get("peer_name", host),
        "address": host,
        "port": int(port_str),
        "token": result.get("token", ""),
        "paired_at": datetime.now(timezone.utc).isoformat(),
    }

    # Update or append
    found = False
    for p in peers:
        if p.get("address") == host and p.get("port") == int(port_str):
            p.update(peer_entry)
            found = True
            break
    if not found:
        peers.append(peer_entry)

    _save_peers(peers)

    return {"paired": True, "peer_name": peer_entry["device_name"]}


def list_peers() -> List[Dict[str, Any]]:
    """Return all currently paired peers from ``peers.json``."""
    return _load_peers()


# ══════════════════════════════════════════════════════════════════
# 4. Federated Query
# ══════════════════════════════════════════════════════════════════

def _remote_retrieve(peer: Dict[str, Any], query: str, timeout: float = 5.0) -> Tuple[str, List[dict]]:
    """POST /retrieve to a remote peer with Bearer auth.  Returns (peer_name, results)."""
    url = f"http://{peer['address']}:{peer['port']}/retrieve"
    payload = json.dumps({"query": query, "top_n": 10}).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    peer_token = peer.get("token", "")
    if not peer_token:
        logger.warning("Federation: skipping peer %s (no auth token configured)", peer.get("address", "?"))
        return peer.get("name", "?"), []
    headers["Authorization"] = f"Bearer {peer_token}"
    req = urllib.request.Request(
        url, data=payload,
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return (peer.get("device_name", peer["address"]), data.get("results", []))
    except Exception:
        return (peer.get("device_name", peer["address"]), [])


def federated_retrieve(query: str, max_peers: int = 3) -> Dict[str, Any]:
    """Query local database AND all paired peers, merge results.

    .. deprecated::
       Prefer ``federated_retrieve_async(query, max_peers)`` for better
       performance and native async I/O.  This sync wrapper is kept for
       CLI backwards compatibility.

    Local results are retrieved first, then parallel HTTP requests are
    sent to up to *max_peers* paired peers.  All results are interleaved
    (local first, then peer results deduplicated by content prefix).

    Args:
        query: Search query string.
        max_peers: Maximum number of peers to query (default 3).

    Returns:
        dict: {local_results, peer_results, merged_top}
    """
    # ── Local search ──
    local_raw = retrieve(query=query, limit=20)
    local_results = [
        {
            "id": r.id,
            "content": r.content,
            "agent_name": r.agent_name,
            "category": r.category,
            "source": "local",
        }
        for r in local_raw
    ]

    # ── Peer search (parallel threads) ──
    peers = _load_peers()[:max_peers]
    peer_results: Dict[str, list] = {}

    if peers:
        threads: List[threading.Thread] = []
        results_lock = threading.Lock()

        def _worker(p: dict) -> None:
            name, res = _remote_retrieve(p, query)
            with results_lock:
                peer_results[name] = res

        for p in peers:
            t = threading.Thread(target=_worker, args=(p,), daemon=True)
            threads.append(t)
            t.start()

        for t in threads:
            t.join(timeout=6)

    # ── Merge: local → top, then peer results deduped ──
    seen_content_prefix: set = set()
    merged_top: List[dict] = []

    for r in local_results:
        prefix = r["content"][:60]
        if prefix not in seen_content_prefix:
            seen_content_prefix.add(prefix)
            merged_top.append(r)

    for pname, results in peer_results.items():
        for r in results:
            r["source"] = pname
            prefix = r.get("content", "")[:60]
            if prefix not in seen_content_prefix:
                seen_content_prefix.add(prefix)
                merged_top.append(r)

    return {
        "local_results": local_results,
        "peer_results": peer_results,
        "merged_top": merged_top,
    }


# ── Async variants (aiohttp) ──


async def _remote_retrieve_async(
    session: ClientSession, peer: Dict[str, Any], query: str, timeout: float = 5.0
) -> Tuple[str, List[dict]]:
    """Async POST /retrieve to a remote peer via aiohttp with Bearer auth.

    Returns (peer_name, results).
    """
    url = f"http://{peer['address']}:{peer['port']}/retrieve"
    headers = {"Content-Type": "application/json"}
    peer_token = peer.get("token", "")
    if not peer_token:
        logger.warning("Federation: skipping async peer %s (no auth token configured)", peer.get("address", "?"))
        return peer.get("device_name", peer.get("address", "?")), []
    headers["Authorization"] = f"Bearer {peer_token}"
    payload = {"query": query, "top_n": 10}
    try:
        async with session.post(url, json=payload, headers=headers,
                                timeout=ClientTimeout(total=timeout)) as resp:
            data = await resp.json()
            return (peer.get("device_name", peer["address"]), data.get("results", []))
    except Exception:
        return (peer.get("device_name", peer["address"]), [])


async def federated_retrieve_async(query: str, max_peers: int = 3) -> Dict[str, Any]:
    """Async federated query using aiohttp instead of threads.

    Local results are retrieved first, then *max_peers* peers are
    queried concurrently via ``asyncio.gather``.
    """
    # ── Local search ──
    local_raw = retrieve(query=query, limit=20)
    local_results = [
        {
            "id": r.id,
            "content": r.content,
            "agent_name": r.agent_name,
            "category": r.category,
            "source": "local",
        }
        for r in local_raw
    ]

    # ── Peer search (async concurrent) ──
    peers = _load_peers()[:max_peers]
    peer_results: Dict[str, list] = {}

    if peers:
        async with ClientSession() as session:
            tasks = [
                _remote_retrieve_async(session, p, query)
                for p in peers
            ]
            outcomes = await asyncio.gather(*tasks, return_exceptions=True)

        for p, outcome in zip(peers, outcomes):
            if isinstance(outcome, Exception):
                continue
            name, results = outcome
            peer_results[name] = results

    # ── Merge ──
    seen_content_prefix: set = set()
    merged_top: List[dict] = []

    for r in local_results:
        prefix = r["content"][:60]
        if prefix not in seen_content_prefix:
            seen_content_prefix.add(prefix)
            merged_top.append(r)

    for pname, results in peer_results.items():
        for r in results:
            r["source"] = pname
            prefix = r.get("content", "")[:60]
            if prefix not in seen_content_prefix:
                seen_content_prefix.add(prefix)
                merged_top.append(r)

    return {
        "local_results": local_results,
        "peer_results": peer_results,
        "merged_top": merged_top,
    }