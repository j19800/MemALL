"""
Gateway REST API handlers — extracted from gateway.py for modularity.

Each handler is an async function that receives (request, gateway) and returns
web.Response. The ``gateway`` parameter provides access to _read_json,
_validate, _auth_token, etc.
"""
import json
import logging
from datetime import datetime, timezone

from aiohttp import web

from memall.core.db import pool_conn, get_conn, db_stats, optimize_db, vacuum_db, DB_PATH
from memall.core.thin_waist import (
    capture, retrieve, traverse, timeline, connect, smart_store,
    store_batch, update, vector_search, MemoryInput, hybrid_search,
)
from memall.core.models import MemoryInput as MemoryInputModel
from memall.core.rate_limiter import get_rate_limiter
from memall.pipeline.persona import generate_profile_3layer, generate_persona, get_evolution
from memall.pipeline.session import session_start, session_end, session_summary
from memall.pipeline.ask import ContextAssembler
from memall.pipeline.forget import forget_expired, forget_low_value, forget_review, forget_stats, forget_step
from memall.pipeline.adaptive import adaptive_step, adaptive_report
from memall.pipeline.security import audit_sensitive, set_permission, check_access, list_agents_by_permission, security_score
from memall.pipeline.ops import merge_memories, split_memory, tag_memory, batch_tag, batch_archive, batch_restore, deduplicate
from memall.pipeline.observe import reflection_dashboard
from memall.pipeline.pipeline import run_pipeline
from memall.mcp.federation_tools import fed_query, fed_publish, fed_conflicts, auto_inject, auto_extract
from memall.migrations import get_migration_status, run_migrations
from memall.mcp.models import (
    CaptureInput, RetrieveInput, TraverseInput, TimelineInput,
    PersonaProfileInput, DiscussionCreateInput, DiscussionRespondInput,
)
from memall.gateway_utils import esc_html, _ok, _load_debt_cache, _save_debt_cache

logger = logging.getLogger("memall.gateway.api")


async def handle_capture(request: web.Request, gw) -> web.Response:
    data = await gw._read_json(request)
    if data is None:
        return web.json_response({"error": "invalid JSON body"}, status=400)
    validated, err = gw._validate(data, CaptureInput)
    if err:
        return web.json_response({"error": err}, status=400)
    mid = capture(MemoryInput(**validated))
    return web.json_response({"id": mid, "status": "ok"})


async def handle_retrieve(request: web.Request, gw) -> web.Response:
    data = await gw._read_json(request)
    if data is None:
        return web.json_response({"error": "invalid JSON body"}, status=400)
    validated, err = gw._validate(data, RetrieveInput)
    if err:
        return web.json_response({"error": err}, status=400)
    results = retrieve(**validated)
    if results is None:
        return web.json_response({"results": []})
    if isinstance(results, list):
        return web.json_response({"results": [dict(r) if hasattr(r, 'keys') else r for r in results]})
    return web.json_response({"result": dict(results) if hasattr(results, 'keys') else results})


async def handle_traverse(request: web.Request, gw) -> web.Response:
    data = await gw._read_json(request)
    if data is None:
        return web.json_response({"error": "invalid JSON body"}, status=400)
    validated, err = gw._validate(data, TraverseInput)
    if err:
        return web.json_response({"error": err}, status=400)
    result = traverse(**validated)
    return web.json_response(result)


async def handle_timeline(request: web.Request, gw) -> web.Response:
    data = await gw._read_json(request)
    if data is None:
        return web.json_response({"error": "invalid JSON body"}, status=400)
    validated, err = gw._validate(data, TimelineInput)
    if err:
        return web.json_response({"error": err}, status=400)
    result = timeline(**validated)
    return web.json_response({"data": result})


async def handle_profile(request: web.Request, gw) -> web.Response:
    data = await gw._read_json(request)
    if data is None:
        return web.json_response({"error": "invalid JSON body"}, status=400)
    validated, err = gw._validate(data, PersonaProfileInput)
    if err:
        return web.json_response({"error": err}, status=400)
    profile = generate_profile_3layer(**validated)
    return web.json_response(profile)


async def handle_api_capture(request: web.Request, gw) -> web.Response:
    data = await gw._read_json(request)
    if data is None:
        return web.json_response({"error": "invalid JSON body"}, status=400)
    validated, err = gw._validate(data, CaptureInput)
    if err:
        return web.json_response({"error": err}, status=400)
    mid = capture(MemoryInput(**validated))
    return web.json_response({"id": mid, "status": "ok"})


async def handle_api_search(request: web.Request, gw) -> web.Response:
    query = request.query.get("query", "")
    owner = request.query.get("owner", "")
    agent_name = request.query.get("agent_name", "")
    category = request.query.get("category", "")
    level = request.query.get("level", "")
    limit = int(request.query.get("limit", "20"))
    results = retrieve(query, owner=owner or None, agent_name=agent_name or None,
                       category=category or None, level=level or None)
    if not isinstance(results, list):
        results = []
    results = results[:limit]
    return web.json_response({"data": [dict(r) if hasattr(r, 'keys') else r for r in results]})


async def handle_api_vector_search(request: web.Request, gw) -> web.Response:
    q = request.query.get("query", "")
    top_k = int(request.query.get("top_k", "10"))
    results = vector_search(q, top_k=top_k)
    return web.json_response(results)


async def handle_api_memories_stats(request: web.Request, gw) -> web.Response:
    with pool_conn() as conn:
        rows = conn.execute("SELECT level, COUNT(*) as cnt FROM memories GROUP BY level ORDER BY cnt DESC").fetchall()
    return web.json_response({"data": [dict(r) for r in rows]})


async def handle_api_get_memory(request: web.Request, gw) -> web.Response:
    memory_id = int(request.match_info.get("memory_id", "0"))
    result = retrieve(memory_id)
    if result is None:
        return web.json_response({"error": "not found"}, status=404)
    if hasattr(result, 'keys'):
        return web.json_response({"data": dict(result)})
    return web.json_response({"data": result})


async def handle_api_db_stats(request: web.Request, gw) -> web.Response:
    stats = db_stats()
    return web.json_response(stats)


async def handle_api_agents(request: web.Request, gw) -> web.Response:
    with pool_conn() as conn:
        rows = conn.execute(
            "SELECT agent_name, COUNT(*) as cnt FROM memories GROUP BY agent_name ORDER BY cnt DESC"
        ).fetchall()
    return web.json_response({"agents": [{"name": r["agent_name"], "count": r["cnt"]} for r in rows]})


async def handle_api_session_summary(request: web.Request, gw) -> web.Response:
    session_id = request.match_info.get("session_id", "")
    result = session_summary(session_id)
    return web.json_response(result)


async def handle_api_forget(request: web.Request, gw) -> web.Response:
    data = await request.json()
    action = data.get("action", "expired")
    days = data.get("days", 90)
    agent_name = data.get("agent_name", "")
    kwargs = {"days": days}
    if agent_name:
        kwargs["agent_name"] = agent_name
    if action == "expired":
        result = forget_expired(**kwargs)
    elif action == "low_value":
        result = forget_low_value(**kwargs)
    elif action == "review":
        result = forget_review(**kwargs)
    elif action == "stats":
        result = forget_stats()
    elif action == "all":
        result = forget_step(**kwargs)
    else:
        return web.json_response({"error": f"unknown action: {action}"}, status=400)
    return web.json_response(result)


async def handle_api_run_pipeline(request: web.Request, gw) -> web.Response:
    data = await request.json() if request.can_read_body else {}
    include_reflect = data.get("include_reflect", True)
    include_distill = data.get("include_distill", True)
    include_integrate = data.get("include_integrate", True)
    include_persona = data.get("include_persona", True)
    result = run_pipeline(
        include_reflect=include_reflect,
        include_distill=include_distill,
        include_integrate=include_integrate,
        include_persona=include_persona,
    )
    return web.json_response(result, status=200 if result.get("status") == "ok" else 500)


async def handle_api_run_migrations(request: web.Request, gw) -> web.Response:
    conn = get_conn()
    try:
        result = run_migrations(conn, db_path=str(DB_PATH))
        conn.commit()
        return web.json_response(result)
    finally:
        conn.close()


async def handle_api_migration_status(request: web.Request, gw) -> web.Response:
    conn = get_conn()
    try:
        return web.json_response(get_migration_status(conn))
    finally:
        conn.close()