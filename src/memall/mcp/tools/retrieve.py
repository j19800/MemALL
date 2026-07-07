import logging
import json
from memall.core.thin_waist import retrieve, vector_search, hybrid_search
from memall.core.db import get_conn
from memall.search.intent_router import classify, resolve_mode
logger = logging.getLogger(__name__)



def handle_retrieve(arguments: dict) -> str:
    from memall.core.thin_waist import retrieve
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
    result = vector_search(
        query=arguments["query"],
        top_k=arguments.get("top_k", 10),
    )
    return json.dumps(result, ensure_ascii=False, default=str)


def handle_hybrid_search(arguments: dict) -> str:
    result = hybrid_search(
        query=arguments["query"],
        top_k=arguments.get("top_k", 10),
        rrf_k=arguments.get("rrf_k", 60),
        category=arguments.get("category"),
        level=arguments.get("level"),
        owner=arguments.get("owner"),
        rerank=arguments.get("rerank", False),
    )
    return json.dumps(result, ensure_ascii=False, default=str)


def handle_unified_search(arguments: dict) -> str:
    """Unified search — auto-routes between FTS5, vector, and hybrid engines."""
    query = arguments.get("query", "").strip()
    top_k = arguments.get("top_k", 10)
    mode = arguments.get("mode", "auto")

    if not query:
        return json.dumps({"error": "query is required"})

    if mode == "auto":
        intent = classify(query)
        mode = resolve_mode(intent)

    if mode == "direct":
        result = retrieve(query=query)
        if result is None:
            return json.dumps({"query": query, "mode": "direct", "results": [], "total": 0})
        if isinstance(result, list):
            items = [{"memory_id": r.id, "content": r.content[:200], "subject": r.subject,
                       "category": r.category, "level": r.level} for r in result]
        else:
            items = [{"memory_id": result.id, "content": result.content[:200], "subject": result.subject,
                       "category": result.category, "level": result.level}]
        return json.dumps({"query": query, "mode": "direct", "results": items, "total": len(items)},
                          ensure_ascii=False)

    if mode == "fts5":
        result = retrieve(query=query)
        if result is None:
            return json.dumps({"query": query, "mode": "fts5", "results": [], "total": 0})
        items = []
        for r in (result if isinstance(result, list) else [result]):
            items.append({"memory_id": r.id, "content": r.content[:200], "subject": r.subject,
                           "category": r.category, "level": r.level, "owner": r.owner, "agent_name": r.agent_name})
        return json.dumps({"query": query, "mode": "fts5", "results": items, "total": len(items)},
                          ensure_ascii=False)

    if mode == "vector":
        result = vector_search(query=query, top_k=top_k)
        return json.dumps(result, ensure_ascii=False, default=str)

    result = hybrid_search(
        query=query, top_k=top_k,
        rrf_k=arguments.get("rrf_k", 60),
        category=arguments.get("category"),
        level=arguments.get("level"),
        owner=arguments.get("owner"),
        rerank=arguments.get("rerank", False),
    )
    result["mode"] = mode if mode != "auto" else result.get("mode", "hybrid_rrf")
    return json.dumps(result, ensure_ascii=False, default=str)


def handle_trace(arguments: dict) -> str:
    mem_id = arguments["memory_id"]
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

        rels = conn.execute(
            "SELECT m.id, m.subject, m.level, e.relation_type "
            "FROM edges e JOIN memories m ON m.id = CASE WHEN e.source_id = ? THEN e.target_id ELSE e.source_id END "
            "WHERE e.source_id = ? OR e.target_id = ? LIMIT 10",
            (mem_id, mem_id, mem_id),
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
