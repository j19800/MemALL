"""
Gateway federation handlers — extracted from gateway.py for modularity.

Handles federation event receiving, querying, publishing, and peer management.
"""

import json
import logging
from aiohttp import web

from memall.core.db import pool_conn
from memall.mcp.federation_tools import fed_query, fed_publish, fed_conflicts, auto_inject, auto_extract
from memall.gateway_utils import _ok

logger = logging.getLogger("memall.gateway.federation")


async def handle_federation_event(request: web.Request, gw) -> web.Response:
    """POST /federation/events — receive federation event from peer."""
    data = await request.json()
    event_type = data.get("type", "")
    if event_type == "memory_shared":
        logger.info("Federation event received: memory_shared from %s", data.get("source_agent", "unknown"))
    return web.json_response({"status": "ok", "received": event_type})


async def handle_fed_query(request: web.Request, gw) -> web.Response:
    """GET /federation/query — query shared memories."""
    query = request.query.get("query", "")
    agent_name = request.query.get("agent_name", "")
    category = request.query.get("category", "")
    trust_level = request.query.get("trust_level", "")
    limit = int(request.query.get("limit", "20"))
    result = fed_query(query=query, source_agent=agent_name, category=category,
                       trust_level=trust_level, limit=limit)
    return web.json_response(result)


async def handle_fed_publish(request: web.Request, gw) -> web.Response:
    """POST /federation/publish — publish a memory to family."""
    data = await request.json()
    result = fed_publish(**data)
    return web.json_response(result)


async def handle_fed_conflicts(request: web.Request, gw) -> web.Response:
    """GET /federation/conflicts — check for conflicts."""
    limit = int(request.query.get("limit", "20"))
    result = fed_conflicts(limit=limit)
    return web.json_response(result)


async def handle_fed_inject(request: web.Request, gw) -> web.Response:
    """POST /federation/inject/{agent_name} — inject shared context."""
    agent_name = request.match_info.get("agent_name", "")
    result = auto_inject(agent_name=agent_name)
    return web.json_response(result)


async def handle_fed_extract(request: web.Request, gw) -> web.Response:
    """POST /federation/extract/{session_id} — extract session to family."""
    session_id = request.match_info.get("session_id", "")
    result = auto_extract(session_id)
    return web.json_response(result)