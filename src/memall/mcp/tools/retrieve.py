import logging
import json
from memall.core.thin_waist import retrieve, vector_search, hybrid_search
from memall.core.db import get_conn
from memall.search.intent_router import classify, resolve_mode
logger = logging.getLogger(__name__)

# Enhancement flags shared by handle_retrieve / handle_unified_search.
_ENHANCE_FLAGS = ("expand_query", "suggest", "explain", "cluster", "personalize", "context_boost")


def _split_facet_fields(arguments: dict):
    """Merge ``facet_fields`` (array) and ``facet_fields_str`` (string) inputs."""
    fields = arguments.get("facet_fields")
    if isinstance(fields, str):
        fields = [f.strip() for f in fields.split(",") if f.strip()]
    if not fields:
        raw = arguments.get("facet_fields_str") or ""
        if isinstance(raw, str) and raw.strip():
            fields = [f.strip() for f in raw.split(",") if f.strip()]
    if isinstance(fields, str) and fields.strip().lower() == "auto":
        return "auto"
    return fields or None


def _wants_enhancements(arguments: dict) -> bool:
    if _split_facet_fields(arguments):
        return True
    return any(bool(arguments.get(f)) for f in _ENHANCE_FLAGS)


def handle_retrieve(arguments: dict) -> str:
    from memall.core.thin_waist import retrieve, enhanced_search
    if _wants_enhancements(arguments):
        # Route through the enhancement layer; drop routing-only keys.
        args = dict(arguments)
        args["facet_fields"] = _split_facet_fields(args)
        result = enhanced_search(**args)
        items = [{
            "id": m.id, "content": m.content, "category": m.category,
            "level": m.level, "owner": m.owner, "agent_name": m.agent_name,
            "subject": m.subject, "occurred_at": m.occurred_at,
        } for m in result["results"]]
        payload = {"results": items, "total": result.get("total", len(items))}
        for key in ("facets", "suggestions", "clusters", "explanations", "expanded_query"):
            if key in result:
                payload[key] = result[key]
        return json.dumps(payload, ensure_ascii=False)

    result = retrieve(**arguments)
    if result is None:
        return json.dumps({"status": "not_found"})
    if isinstance(result, list):
        return json.dumps([{
            "id": r.id, "content": r.content, "category": r.category,
            "level": r.level, "owner": r.owner, "agent_name": r.agent_name,
            "subject": r.subject, "occurred_at": r.occurred_at,
        } for r in result], ensure_ascii=False)
    return json.dumps({
        "id": result.id, "content": result.content, "category": result.category,
        "level": result.level, "owner": result.owner, "agent_name": result.agent_name,
        "subject": result.subject, "occurred_at": result.occurred_at,
    }, ensure_ascii=False)


def handle_vector_search(arguments: dict) -> str:
    if not (arguments.get("query") or "").strip():
        return json.dumps({"error": "query is required (non-empty string)"}, ensure_ascii=False)
    result = vector_search(
        query=arguments["query"],
        top_k=arguments.get("top_k", 10),
        # Audit P1-6: semantic search could not be isolated at the MCP
        # surface either — the filters existed in the core but were never
        # passed through.
        category=arguments.get("category"),
        level=arguments.get("level"),
        owner=arguments.get("owner"),
        project=arguments.get("project"),
        agent_name=arguments.get("agent_name"),
        # The adapter re-attaches the session identity; without handing it to
        # the trust filter every path could return other agents' rows.
        viewer=arguments.get("viewer") or arguments.get("agent_name") or "",
    )
    return json.dumps(result, ensure_ascii=False, default=str)


def handle_hybrid_search(arguments: dict) -> str:
    if not (arguments.get("query") or "").strip():
        return json.dumps({"error": "query is required (non-empty string)"}, ensure_ascii=False)
    result = hybrid_search(
        query=arguments["query"],
        top_k=arguments.get("top_k", 10),
        rrf_k=arguments.get("rrf_k", 60),
        category=arguments.get("category"),
        level=arguments.get("level"),
        owner=arguments.get("owner"),
        # Audit P1-5: project/agent_name were missing here too, so the MCP
        # surface could not isolate by project even after the core gained
        # the parameter.
        project=arguments.get("project"),
        agent_name=arguments.get("agent_name"),
        rerank=arguments.get("rerank", False),
        viewer=arguments.get("viewer") or arguments.get("agent_name") or "",
    )
    return json.dumps(result, ensure_ascii=False, default=str)


def handle_unified_search(arguments: dict) -> str:
    """Unified search — auto-routes between FTS5, vector, and hybrid engines."""
    if not (arguments.get("query") or "").strip():
        return json.dumps({"error": "query is required (non-empty string)"}, ensure_ascii=False)
    from memall.core.thin_waist import unified_search
    return unified_search(arguments)


def handle_recap(arguments: dict) -> str:
    """Compaction recap — restore the essentials an agent loses after a host
    context compaction (P1.10, ported from dsh-meow-memory's re-injection).

    A MemALL MCP server cannot see the host's compaction event, so this is a
    *pull* surface: the agent (or a hook) calls ``recap`` once after a
    compaction and gets back the small set of things that must not be lost —
    the last session summary and the recent decisions / open tasks — in one
    cheap read.  Read-only; creates nothing.
    """
    from memall.core.db import get_conn

    agent = (arguments.get("agent_name") or "").strip()
    limit = max(1, min(int(arguments.get("limit") or 5), 50))
    out: dict = {"agent_name": agent}

    conn = get_conn()
    try:
        # Last ended (or current) session summary for this agent.  The sessions
        # table is created lazily by session_start; on a DB that has never run
        # one it may not exist yet, so this block is best-effort.
        try:
            where = "WHERE 1=1"
            params: list = []
            if agent:
                where += " AND agent_name = ?"
                params.append(agent)
            sess = conn.execute(
                f"SELECT session_id, agent_name, started_at, ended_at, memory_count, "
                f"summary, status FROM sessions {where} "
                f"ORDER BY COALESCE(ended_at, started_at) DESC LIMIT 1",
                params,
            ).fetchone()
            if sess:
                out["session"] = {
                    "session_id": sess["session_id"],
                    "started_at": sess["started_at"],
                    "ended_at": sess["ended_at"],
                    "memory_count": sess["memory_count"],
                    "summary": sess["summary"],
                    "status": sess["status"],
                }
        except Exception:
            logger.debug("recap: sessions lookup skipped", exc_info=True)

        # Recent decisions and open tasks (the working set an agent needs to
        # resume).  Derived/distilled levels excluded so the recap is real
        # observations, not the pipeline's own echoes.
        recent: list = []
        mwhere = "WHERE level IN ('L4','L5') "
        mparams: list = []
        if agent:
            mwhere += "AND agent_name = ? "
            mparams.append(agent)
        rows = conn.execute(
            f"SELECT id, level, category, subject, summary, occurred_at "
            f"FROM memories {mwhere} ORDER BY occurred_at DESC LIMIT ?",
            mparams + [limit],
        ).fetchall()
        for r in rows:
            recent.append({
                "id": r["id"], "level": r["level"], "category": r["category"],
                "subject": r["subject"], "summary": r["summary"],
                "occurred_at": r["occurred_at"],
            })
        out["recent"] = recent
    finally:
        conn.close()

    out["note"] = "post-compaction recap; re-inject these and continue"
    return json.dumps(out, ensure_ascii=False, default=str)


def handle_record_interaction(arguments: dict) -> str:
    """Record a search interaction (click / dwell / impression / copy / skip).

    Feeds the personalisation and context-boost profiles.  Best-effort:
    telemetry failures never break the caller's turn.

    The interaction kind is read from ``interaction_type`` (``action`` is
    reserved for MCP routing); a legacy ``action`` value is accepted as a
    fallback.
    """
    from memall.core.db import get_conn
    from memall.search.enhancements import record_interaction

    query = arguments.get("query") or ""
    if not query:
        return json.dumps({"error": "query is required"}, ensure_ascii=False)
    memory_id = arguments.get("memory_id")
    interaction = (
        arguments.get("interaction_type")
        or arguments.get("action")
        or "click"
    )
    conn = get_conn()
    try:
        ok = record_interaction(
            conn,
            query=query,
            memory_id=int(memory_id) if memory_id is not None else None,
            action=interaction,
            dwell_ms=int(arguments.get("dwell_ms") or 0),
            rank_position=arguments.get("rank_position"),
            agent_name=arguments.get("agent_name", "") or "",
            owner=arguments.get("owner", "") or "",
            session_id=arguments.get("session_id", "") or "",
        )
    finally:
        conn.close()
    if ok:
        return json.dumps({"status": "ok", "recorded": True}, ensure_ascii=False)
    return json.dumps(
        {"status": "ignored", "recorded": False,
         "reason": "invalid action, or memory_id required for non-impression actions"},
        ensure_ascii=False,
    )


def handle_batch_search(arguments: dict) -> str:
    """Search for multiple queries in a single operation for improved efficiency.

    Audit P1-7: this used to call ``retrieve()`` — the plain FTS5 path —
    while the docstring claimed "unified search".  A batch semantic query
    therefore behaved like a keyword query and never recalled memories
    that share no keywords.  It now goes through the unified search with
    ``mode='hybrid'`` (FTS5 + vector + RRF), which is what the tool
    description promises.
    """
    queries = arguments.get("queries") or []
    if not queries:
        return json.dumps({"error": "queries is required (non-empty array)"}, ensure_ascii=False)

    limit = arguments.get("limit", 10)
    mode = arguments.get("mode", "hybrid")
    results = []
    errors = []

    from memall.core.thin_waist import unified_search

    for i, query in enumerate(queries):
        try:
            if not query or not isinstance(query, str):
                errors.append({"index": i, "error": "query must be a non-empty string"})
                continue

            # Unified search (hybrid by default) — semantic recall included.
            raw = unified_search({
                "query": query,
                "top_k": limit,
                "mode": mode,
                "agent_name": arguments.get("agent_name"),
                "category": arguments.get("category"),
                "level": arguments.get("level"),
                "project": arguments.get("project"),
            })
            payload = json.loads(raw) if isinstance(raw, str) else raw
            items = payload.get("results", []) if isinstance(payload, dict) else []
            formatted = [{
                "id": r.get("memory_id") or r.get("id"),
                "content": (r.get("content") or "")[:500],
                "category": r.get("category", ""),
                "level": r.get("level", ""),
                "owner": r.get("owner", ""),
                "agent_name": r.get("agent_name", ""),
                "subject": r.get("subject", ""),
                "occurred_at": r.get("occurred_at", ""),
                "rrf_score": r.get("rrf_score"),
            } for r in items]
            results.append({
                "index": i,
                "query": query,
                "results": formatted,
                "total": len(formatted),
            })
        except Exception as e:
            errors.append({
                "index": i,
                "query": query,
                "error": str(e)
            })
    
    # Return results
    response = {
        "processed": len(results),
        "errors": len(errors),
        "results": results
    }
    
    if errors:
        response["errors_detail"] = errors
        
    return json.dumps(response, ensure_ascii=False)


def handle_trace(arguments: dict) -> str:
    mem_id = arguments.get("memory_id")
    if not mem_id:
        return json.dumps({"error": "memory_id is required"})
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT id, thread_id, level, category, subject, content, agent_name, owner, metadata, created_at "
            "FROM memories WHERE id = ?", (mem_id,)
        ).fetchone()
        if not row:
            return json.dumps({"error": f"memory #{mem_id} not found"})
        
        meta = {}
        try:
            meta = json.loads(row["metadata"]) if isinstance(row["metadata"], str) else row["metadata"]
        except Exception:
            logger.warning("retrieve.py: silent error", exc_info=True)
        
        from datetime import datetime, timezone
        _now = datetime.now(timezone.utc).isoformat()
        rels = conn.execute(
            "SELECT m.id, m.subject, m.level, e.relation_type "
            "FROM edges e JOIN memories m ON m.id = CASE WHEN e.source_id = ? THEN e.target_id ELSE e.source_id END "
            "WHERE (e.source_id = ? OR e.target_id = ?) "
            "AND (e.valid_from IS NULL OR e.valid_from <= ?) AND (e.invalid_at IS NULL OR e.invalid_at > ?) LIMIT 10",
            (mem_id, mem_id, mem_id, _now, _now),
        ).fetchall()
        
        ctx = conn.execute(
            "SELECT id, level, subject, created_at FROM memories "
            "WHERE subject = ? AND id != ? AND level IN ('L4','L5') ORDER BY created_at DESC LIMIT 5",
            (row["subject"], mem_id),
        ).fetchall() if row["subject"] else []
        
        # Thread chain: walk up thread_id parents
        thread_chain = []
        cur_id = row["thread_id"] if row["thread_id"] else None
        seen = {mem_id}
        while cur_id and cur_id not in seen and len(thread_chain) < 10:
            seen.add(cur_id)
            parent = conn.execute(
                "SELECT id, thread_id, level, subject, agent_name FROM memories WHERE id = ?",
                (cur_id,),
            ).fetchone()
            if parent:
                thread_chain.append({
                    "id": parent["id"], "level": parent["level"],
                    "subject": parent["subject"] or "",
                    "agent_name": parent["agent_name"],
                })
                cur_id = parent["thread_id"] if parent["thread_id"] else None
            else:
                break
        
        # Thread children: memories that have this ID as thread_id
        children = conn.execute(
            "SELECT id, level, subject, agent_name, substr(created_at,1,19) as ca "
            "FROM memories WHERE thread_id = ? ORDER BY created_at LIMIT 10",
            (mem_id,),
        ).fetchall()
        
        result = {
            "id": row["id"],
            "level": row["level"],
            "category": row["category"],
            "subject": row["subject"] or "",
            "agent_name": row["agent_name"],
            "owner": row["owner"] or "",
            "created_at": (row["created_at"] or "")[:19],
            "thread_id": row["thread_id"],
            "thread_parents": thread_chain,
            "thread_replies": [{"id": c["id"], "level": c["level"],
                               "subject": c["subject"][:50], "agent": c["agent_name"],
                               "created_at": c["ca"]} for c in children],
            "session_id": meta.get("session_id", "") if isinstance(meta, dict) else "",
            "participants": meta.get("participants", []) if isinstance(meta, dict) else [],
            "key_decisions": (meta.get("key_decisions") or [])[:3] if isinstance(meta, dict) else [],
            "continuation_note": meta.get("continuation_note", "") if isinstance(meta, dict) else "",
            "status": meta.get("status", "") if isinstance(meta, dict) else "",
            "related_memories": [{"id": r["id"], "subject": r["subject"], "relation": r["relation_type"]} for r in rels],
            "context": [{"id": r["id"], "level": r["level"], "subject": r["subject"]} for r in ctx],
        }
    finally:
        conn.close()
    return json.dumps(result, ensure_ascii=False)