"""
Gateway v30 API handlers — extracted from gateway.py for modularity.

Provides backward-compatible v30 REST API endpoints for the desktop client.
"""

import json
import logging
from datetime import datetime, timezone

from aiohttp import web

from memall.core.db import pool_conn, get_conn
from memall.core.thin_waist import capture, update, MemoryInput
from memall.gateway_utils import esc_html, _ok

logger = logging.getLogger("memall.gateway.v30")


def _safe_int(val, default=0, min_val=None, max_val=None):
    """Parse integer safely with bounds checking."""
    try:
        v = int(val)
        if min_val is not None:
            v = max(v, min_val)
        if max_val is not None:
            v = min(v, max_val)
        return v
    except (ValueError, TypeError):
        return default


async def handle_list_memories(request: web.Request, gw) -> web.Response:
    """GET /v30api/memories — list memories with pagination + filters."""
    sort_by = request.query.get("sort_by", "created_at")
    sort_order = request.query.get("sort_order", "desc")
    page = _safe_int(request.query.get("page", "1"), 1)
    per_page = _safe_int(request.query.get("per_page", "50"), 1, 500)
    agent_name = request.query.get("agent_name", "")
    level = request.query.get("level", "")
    category = request.query.get("category", "")
    query = request.query.get("query", "")

    allowed_sort = {"id", "created_at", "updated_at", "level", "agent_name", "category", "project", "access_count"}
    col = sort_by if sort_by in allowed_sort else "created_at"
    dir_ = "DESC" if sort_order.lower() == "desc" else "ASC"

    where = []
    params = []
    if agent_name:
        where.append("agent_name = ?")
        params.append(agent_name)
    if level:
        where.append("level = ?")
        params.append(level)
    if category:
        where.append("category = ?")
        params.append(category)
    if query:
        where.append("(content LIKE ? OR subject LIKE ?)")
        params.extend([f"%{query}%", f"%{query}%"])
    where_sql = " AND ".join(where) if where else "1=1"

    with pool_conn() as conn:
        count = conn.execute(
            f"SELECT COUNT(*) FROM memories WHERE {where_sql}", params
        ).fetchone()[0]
        offset = (page - 1) * per_page
        rows = conn.execute(
            f"SELECT * FROM memories WHERE {where_sql} ORDER BY {col} {dir_} LIMIT ? OFFSET ?",
            params + [per_page, offset],
        ).fetchall()

    items = [dict(r) for r in rows]
    return web.json_response({
        "success": True,
        "data": {"items": items, "total": count, "page": page, "per_page": per_page}
    })


async def handle_memories_stats(request: web.Request, gw) -> web.Response:
    """GET /v30api/memories/stats — memory stats by level."""
    with pool_conn() as conn:
        rows = conn.execute(
            "SELECT level, COUNT(*) as cnt FROM memories GROUP BY level ORDER BY cnt DESC"
        ).fetchall()
    return web.json_response({"success": True, "data": [dict(r) for r in rows]})


async def handle_get_memory(request: web.Request, gw) -> web.Response:
    """GET /v30api/memories/{memory_id} — get a single memory."""
    memory_id = _safe_int(request.match_info.get("memory_id", "0"), 0)
    with pool_conn() as conn:
        row = conn.execute("SELECT * FROM memories WHERE id = ?", (memory_id,)).fetchone()
    if not row:
        return web.json_response({"error": "not found"}, status=404)
    return web.json_response({"success": True, "data": dict(row)})


async def handle_delete_memory(request: web.Request, gw) -> web.Response:
    """DELETE /v30api/memories/{memory_id} — delete a memory."""
    memory_id = _safe_int(request.match_info.get("memory_id", "0"), 0)
    with pool_conn() as conn:
        conn.execute("DELETE FROM edges WHERE source_id = ? OR target_id = ?", (memory_id, memory_id))
        conn.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
        conn.commit()
    return web.json_response(_ok({"deleted": memory_id}))


async def handle_create_memory(request: web.Request, gw) -> web.Response:
    """POST /v30api/memories — create a memory."""
    data = await request.json()
    mid = capture(MemoryInput(**data))
    return web.json_response({"success": True, "data": {"id": mid}})


async def handle_update_memory(request: web.Request, gw) -> web.Response:
    """PUT /v30api/memories/{memory_id} — update a memory."""
    memory_id = _safe_int(request.match_info.get("memory_id", "0"), 0)
    body = await request.json()
    with pool_conn() as conn:
        for field in ("content", "level", "category", "project", "summary"):
            if field in body:
                conn.execute(
                    "UPDATE memories SET %s = ?, updated_at = datetime('now') WHERE id = ?" % field,
                    (body[field], memory_id),
                )
        conn.commit()
    return web.json_response(_ok({"id": memory_id}))