"""Route registration for the gateway — extracted from gateway.py.

Kept as a mixin so ``_setup_routes`` can keep referring to ``self._handle_*``
handlers defined across the other handler mixins.
"""

from pathlib import Path

from aiohttp import web


class RoutesMixin:
    """Builds the aiohttp route table for MemAllGateway."""

    def _setup_routes(self, app: web.Application) -> None:
        import memall.gateway_api as api
        app.router.add_get("/health", self._handle_health)
        app.router.add_get("/recent", self._handle_recent_html)
        app.router.add_get("/todos", self._handle_todos_html)
        app.router.add_get("/timeline", self._handle_timeline_html)
        app.router.add_get("/identity/{agent_name}", self._handle_identity_html)
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
        app.router.add_get("/graph", self._handle_graph_html)
        app.router.add_get("/artifact", self._handle_artifact)
        app.router.add_get("/features", self._handle_features)
        app.router.add_get("/api/graph", self._handle_api_graph)
        app.router.add_get("/api/graph/center", self._handle_api_graph_center)
        app.router.add_get("/api/discussions", self._handle_api_discussions)
        app.router.add_get("/api/discussions/{topic_id}", self._handle_api_discussion_detail)
        app.router.add_post("/api/discussions/create", self._handle_api_discussion_create)
        app.router.add_post("/api/discussions/respond", self._handle_api_discussion_respond)
        app.router.add_post("/capture", lambda r: api.handle_capture(r, self))
        app.router.add_post("/retrieve", lambda r: api.handle_retrieve(r, self))
        app.router.add_post("/traverse", lambda r: api.handle_traverse(r, self))
        app.router.add_post("/timeline", lambda r: api.handle_timeline(r, self))
        app.router.add_post("/profile", lambda r: api.handle_profile(r, self))
        # Federation routes
        import memall.gateway_federation as fed
        app.router.add_post("/federation/events", lambda r: fed.handle_federation_event(r, self))
        app.router.add_get("/federation/query", lambda r: fed.handle_fed_query(r, self))
        app.router.add_post("/federation/publish", lambda r: fed.handle_fed_publish(r, self))
        app.router.add_get("/federation/conflicts", lambda r: fed.handle_fed_conflicts(r, self))
        app.router.add_post("/federation/inject/{agent_name}", lambda r: fed.handle_fed_inject(r, self))
        app.router.add_post("/federation/extract/{session_id}", lambda r: fed.handle_fed_extract(r, self))
        # MCP Streamable HTTP routes
        app.router.add_post("/mcp", self._handle_mcp_post)
        app.router.add_get("/mcp", self._handle_mcp_sse)
        app.router.add_get("/metrics", self._handle_metrics)
        # REST API routes (from server.py merge)
        app.router.add_post("/memories", lambda r: api.handle_api_capture(r, self))
        app.router.add_get("/memories", self._handle_root_list_memories)
        app.router.add_get("/memories/search", lambda r: api.handle_api_search(r, self))
        app.router.add_get("/memories/vector-search", lambda r: api.handle_api_vector_search(r, self))
        app.router.add_put("/memories", self._handle_api_update)
        app.router.add_post("/memories/smart-store", self._handle_api_smart_store)
        app.router.add_post("/memories/batch", self._handle_api_batch_store)
        app.router.add_post("/memories/import", self._handle_api_import)
        app.router.add_get("/memories/stats", lambda r: api.handle_api_memories_stats(r, self))
        app.router.add_get("/memories/{memory_id}", lambda r: api.handle_api_get_memory(r, self))
        app.router.add_get("/timeline/api", self._handle_api_timeline)
        app.router.add_post("/edges", self._handle_api_edges)
        app.router.add_get("/graph/{node_id}", self._handle_api_graph_traverse)
        app.router.add_get("/graph/search", self._handle_api_graph_search)
        app.router.add_get("/persona/{agent_name}", self._handle_api_persona)
        app.router.add_get("/persona/{agent_name}/profile", self._handle_api_persona_profile)
        app.router.add_post("/ask", self._handle_api_ask)
        app.router.add_post("/sessions", self._handle_api_session_start)
        app.router.add_post("/sessions/{session_id}/end", self._handle_api_session_end)
        app.router.add_get("/sessions/{session_id}", lambda r: api.handle_api_session_summary(r, self))
        app.router.add_get("/sessions", self._handle_api_sessions_list)
        app.router.add_post("/forget", lambda r: api.handle_api_forget(r, self))
        app.router.add_post("/adaptive", self._handle_api_adaptive)
        app.router.add_get("/adaptive/report", self._handle_api_adaptive_report)
        app.router.add_post("/security", self._handle_api_security)
        app.router.add_post("/ops", self._handle_api_ops)
        app.router.add_get("/ops/dedup", self._handle_api_ops_dedup)
        app.router.add_post("/gateway", self._handle_api_gateway)
        app.router.add_post("/db/optimize", self._handle_api_db_optimize)
        app.router.add_get("/agents", lambda r: api.handle_api_agents(r, self))
        app.router.add_get("/db/stats", lambda r: api.handle_api_db_stats(r, self))
        app.router.add_post("/db/vacuum", self._handle_api_db_vacuum)
        # Intelligence routes
        import memall.gateway_intelligence as ig
        app.router.add_get("/api/intelligence", lambda r: ig.handle_intelligence(r, self))
        app.router.add_get("/api/intelligence/timeline", lambda r: ig.handle_memory_timeline(r, self))
        app.router.add_get("/api/intelligence/agent/{name}", lambda r: ig.handle_agent_profile(r, self))
        app.router.add_get("/debt/stats", self._handle_api_debt_stats)
        app.router.add_post("/debt/scan", self._handle_api_debt_scan)
        app.router.add_get("/reflection/dashboard", self._handle_api_reflection_dashboard)
        app.router.add_post("/reflection/interact", self._handle_api_reflection_interact)
        app.router.add_post("/pipeline/run", lambda r: api.handle_api_run_pipeline(r, self))
        app.router.add_get("/migrations/status", lambda r: api.handle_api_migration_status(r, self))
        app.router.add_post("/migrations/run", lambda r: api.handle_api_run_migrations(r, self))
        # v30 API
        import memall.gateway_v30 as v30
        app.router.add_get("/v30api/memories", lambda r: v30.handle_list_memories(r, self))
        app.router.add_get("/v30api/memories/stats", lambda r: v30.handle_memories_stats(r, self))
        app.router.add_get("/v30api/memories/{memory_id}", lambda r: v30.handle_get_memory(r, self))
        app.router.add_delete("/v30api/memories/{memory_id}", lambda r: v30.handle_delete_memory(r, self))
        app.router.add_post("/v30api/memories", lambda r: v30.handle_create_memory(r, self))
        app.router.add_put("/v30api/memories/{memory_id}", lambda r: v30.handle_update_memory(r, self))
        # Frontend
        app.router.add_get("/", self._handle_serve_frontend)
        app.router.add_get("/api/routes", self._handle_api_routes)
        # Static file mounts
        _frontend_dir = Path(__file__).resolve().parent.parent.parent / "frontend"
        if _frontend_dir.exists() and (_frontend_dir / "index.html").exists():
            app.router.add_static("/static", str(_frontend_dir), name="frontend_static")
        _v30_dir = Path(__file__).resolve().parent.parent.parent / "desktop" / "v30"
        if _v30_dir.exists():
            app.router.add_static("/v30", str(_v30_dir), name="v30_frontend")
        # Catch-all OPTIONS for CORS preflight
        app.router.add_route("OPTIONS", "/{tail:.*}", self._handle_options)
