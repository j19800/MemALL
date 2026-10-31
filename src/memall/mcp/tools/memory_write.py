import json
from memall.config import get_config
from memall.core.db import get_conn
from memall.core.thin_waist import smart_store, store_batch, update, normalize_agent_name, capture
from memall.core.models import MemoryInput


def handle_smart_store(arguments: dict) -> str:
    if not (arguments.get("content") or "").strip():
        return json.dumps({"error": "content is required (non-empty string)"}, ensure_ascii=False)
    # Project inference is centralized in capture(); smart_store → capture() will
    # populate it automatically. Pass the caller's project through unchanged.
    project = arguments.get("project", "")
    result = smart_store(
        content=arguments["content"],
        owner=arguments.get("owner", ""),
        agent_name=arguments.get("agent_name", ""),
        subject=arguments.get("subject", ""),
        project=project,
        category=arguments.get("category", "general"),
        level=arguments.get("level", "P2"),
        dedup_threshold=arguments.get("dedup_threshold", 0.85),
    )
    return json.dumps(result, ensure_ascii=False)


def handle_store_batch(arguments: dict) -> str:
    items = arguments.get("items") or []
    if not items:
        return json.dumps({"error": "items is required (non-empty array)"}, ensure_ascii=False)
    result = store_batch(items)
    return json.dumps(result, ensure_ascii=False)


def handle_batch_capture(arguments: dict) -> str:
    """Capture multiple memories in a single operation for improved efficiency."""
    items = arguments.get("items") or []
    if not items:
        return json.dumps({"error": "items is required (non-empty array)"}, ensure_ascii=False)
    # Process each item
    results = []
    errors = []
    
    for i, item in enumerate(items):
        try:
            # Validate required fields
            if not item.get("content"):
                errors.append({"index": i, "error": "content is required"})
                continue
                
            # Create memory input
            memory_input = MemoryInput(
                content=item["content"],
                owner=item.get("owner", ""),
                agent_name=item.get("agent_name", ""),
                subject=item.get("subject", ""),
                project=item.get("project", ""),
                category=item.get("category", "general"),
                level=item.get("level", "P2"),
                tags=json.dumps(item.get("tags", [])),
                confidence=item.get("confidence", 0.5),
                visibility=item.get("visibility", "private"),
                stream=item.get("stream", "ledger"),
                thread_id=item.get("thread_id")
            )
            
            # Capture the memory
            memory_id = capture(memory_input)
            results.append({
                "index": i,
                "memory_id": memory_id,
                "status": "ok"
            })
        except Exception as e:
            errors.append({
                "index": i,
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


def _is_supervisor(agent: str) -> bool:
    """True if ``agent`` is listed in ``security.supervisor_agents``.

    Delegates to ``memall.core.task_identity`` — this was a third copy of the
    same list lookup (P2-4); a policy change here would otherwise have applied
    to the task gate and not to the ownership gate.
    """
    from memall.core.task_identity import is_supervisor
    return is_supervisor(agent)


def handle_update(arguments: dict) -> str:
    mem_id = arguments.get("memory_id")
    if not mem_id:
        return json.dumps({"error": "memory_id is required"}, ensure_ascii=False)
    fields = {k: v for k, v in arguments.items()
              if k not in ("action", "memory_id", "agent_name", "confirm", "dry_run", "sub_action")
              and v is not None}
    if not fields:
        return json.dumps({"error": "no updatable fields provided"}, ensure_ascii=False)

    # ── Cross-agent ownership enforcement (on by default) ───────────────
    # Without this gate any agent can overwrite another agent's memory *and*
    # reassign ``agent_name`` to itself — i.e. silently take ownership of it.
    # That is a privilege-boundary violation, so the gate is on by default;
    # supervisor/orchestrator agents that legitimately curate others' memories
    # are exempted explicitly via ``security.supervisor_agents``.
    if get_config("security.enforce_agent_ownership", True):
        caller = arguments.get("agent_name")
        if caller is None:
            # No identity supplied: unauthenticated, NOT trusted internal.
            # Reject rather than silently bypass the ownership gate (R3-5).
            return json.dumps({
                "error": "agent_name is required: an empty identity is rejected",
                "memory_id": mem_id,
            }, ensure_ascii=False)
        elif not str(caller).strip():
            # An explicitly empty identity cannot be attributed to any agent,
            # so the ownership gate has nothing to compare against — refuse
            # rather than silently skip it (the old ``if caller and ...``
            # short-circuited on "" and let an anonymous caller overwrite
            # another agent's memory).
            return json.dumps({
                "error": "agent_name is required: an empty identity is rejected",
                "memory_id": mem_id,
            }, ensure_ascii=False)
        else:
            caller = str(caller)
        if caller and not _is_supervisor(caller):
            conn = get_conn()
            try:
                row = conn.execute(
                    "SELECT agent_name FROM memories WHERE id = ?", (mem_id,)
                ).fetchone()
            finally:
                conn.close()
            if row and row["agent_name"]:
                owner = row["agent_name"]
                if owner != normalize_agent_name(caller):
                    return json.dumps({
                        "error": "ownership violation",
                        "message": f"memory {mem_id} belongs to agent '{owner}', "
                                   f"caller is '{caller}'. Add the caller to "
                                   f"security.supervisor_agents if it is a "
                                   f"supervisor, or set "
                                   f"security.enforce_agent_ownership=false.",
                        "memory_id": mem_id,
                    }, ensure_ascii=False)

    # Ownership is not a casual field: ``agent_name`` identifies the *caller*
    # (used by the gate above) and must never be written through this path,
    # otherwise any agent could re-assign a memory to itself.  ``confirm``/
    # ``dry_run`` are control flags, not row columns.
    fields = {k: v for k, v in arguments.items()
              if k not in ("memory_id", "agent_name", "confirm", "dry_run")
              and v is not None}
    ok = update(mem_id, **fields)
    if ok and "metadata" in fields:
        # A task card that just changed state must not stay in the agent's
        # cached view (auto_inject caches per agent for 300s).  The cache-hit
        # path already refreshes the task list, but dropping the whole entry
        # also rebuilds the counts shown in the injected context.
        try:
            from memall.mcp.federation_tools import invalidate_inject_cache
            conn = get_conn()
            try:
                row = conn.execute(
                    "SELECT agent_name FROM memories WHERE id = ?", (mem_id,)).fetchone()
            finally:
                conn.close()
            if row and row["agent_name"]:
                invalidate_inject_cache(row["agent_name"])
        except Exception:
            pass
    return json.dumps({"memory_id": mem_id, "updated": ok, "fields": list(fields.keys())})
