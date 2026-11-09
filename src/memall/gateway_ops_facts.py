"""
Gateway REST routes for operational facts (``ops_facts``)
========================================================
Exposes the isolated ops data store over the same LAN port / Bearer token as
MCP and the message bus.  Deliberately NOT an MCP tool: ops data is plain
infrastructure state, not agent memory — keeping it out of ``tools/list``
avoids the pipeline/LLM treating it as retrievable knowledge.
"""
from __future__ import annotations

from aiohttp import web

from memall import ops_facts


class OpsFactsHandlersMixin:
    """``/ops/facts/*`` — CRUD for the isolated operations table."""

    async def _handle_ops_facts_list(self, request: web.Request) -> web.Response:
        category = request.query.get("category", "")
        history = request.query.get("history", "").lower() in ("1", "true", "yes")
        return web.json_response(ops_facts.list_facts(category, history))

    async def _handle_ops_facts_categories(self, request: web.Request) -> web.Response:
        return web.json_response(ops_facts.categories())

    async def _handle_ops_facts_get(self, request: web.Request) -> web.Response:
        category = request.match_info.get("category", "")
        key = request.match_info.get("key", "")
        history = request.query.get("history", "").lower() in ("1", "true", "yes")
        return web.json_response(ops_facts.get(category, key, history))

    async def _handle_ops_facts_put(self, request: web.Request) -> web.Response:
        data = await self._read_json(request)
        if data is None:
            return web.json_response({"error": "invalid JSON body"}, status=400)
        return web.json_response(ops_facts.put(
            (data.get("category") or "").strip(),
            (data.get("key") or "").strip(),
            data.get("value"),
            data.get("detail", ""),
            (data.get("source") or "").strip(),
        ))

    async def _handle_ops_facts_delete(self, request: web.Request) -> web.Response:
        data = await self._read_json(request)
        if data is None:
            return web.json_response({"error": "invalid JSON body"}, status=400)
        return web.json_response(ops_facts.delete(
            (data.get("category") or "").strip(),
            (data.get("key") or "").strip(),
        ))
