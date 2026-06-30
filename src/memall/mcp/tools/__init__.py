import json
from memall.mcp.registry import registry, ToolDef

from . import capture
from . import retrieve
from . import graph
from . import timeline
from . import persona
from . import memory_write
from . import session
from . import discussion
from . import pipeline
from . import federation
from . import hub
from . import manage
from . import gateway
from . import reflect
from . import onboarding
from . import index
from . import distill


# ──────────────────────────────────────────────
# 1. memall_write — capture, smart_store, batch, update, forget, ops, connect
# ──────────────────────────────────────────────

def _handle_write(args: dict) -> str:
    action = args.pop("action", "")
    if action == "capture":
        return capture.handle(args)
    elif action == "smart_store":
        return memory_write.handle_smart_store(args)
    elif action == "store_batch":
        return memory_write.handle_store_batch(args)
    elif action == "update":
        return memory_write.handle_update(args)
    elif action == "connect":
        return graph.handle_connect(args)
    elif action == "forget":
        args["action"] = args.pop("sub_action", "expired")
        return manage.handle_forget(args)
    elif action == "ops":
        args["action"] = args.pop("sub_action", "")
        return manage.handle_ops(args)
    raise ValueError(f"memall_write: unknown action '{action}'")


registry.register(ToolDef(
    name="memall_write",
    description="Write/capture/search/update memories, create connections, manage forgetting & ops. Actions: capture | smart_store | store_batch | update | connect | forget | ops",
    input_schema={"type": "object", "properties": {
        "action": {"type": "string", "enum": ["capture", "smart_store", "store_batch", "update", "connect", "forget", "ops"]},
        "content": {"type": "string", "description": "Memory content"},
        "owner": {"type": "string"},
        "agent_name": {"type": "string"},
        "subject": {"type": "string"},
        "summary": {"type": "string"},
        "project": {"type": "string"},
        "category": {"type": "string"},
        "level": {"type": "string", "enum": ["P0", "P1", "P2", "L1", "L2", "L3", "L4", "L5", "L6", "L7", "L8", "L9", "L10"]},
        "metadata": {"type": "string", "description": "JSON metadata string"},
        "thread_id": {"type": "integer", "description": "Parent memory ID for thread context"},
        "memory_id": {"type": "integer", "description": "Memory ID (update/connect/forget)"},
        "source_id": {"type": "integer"},
        "target_id": {"type": "integer"},
        "relation_type": {"type": "string", "enum": ["extends", "contradicts", "refines", "cites", "supersedes"]},
        "weight": {"type": "number"},
        "sub_action": {"type": "string", "enum": ["expired", "low_value", "review", "stats", "all", "merge", "split", "tag", "batch_tag", "archive", "restore", "dedup"], "description": "Sub-action for forget/ops"},
        "days": {"type": "integer"},
        "tags": {"type": "array", "items": {"type": "string"}},
        "dedup_threshold": {"type": "number"},
        "items": {"type": "array", "description": "Items for store_batch"},
        "delimiter": {"type": "string"},
        "mode": {"type": "string", "enum": ["add", "set", "remove"]},
        "threshold": {"type": "number"},
    }, "required": ["action"]},
    handler=_handle_write,
    annotations={"readOnlyHint": False, "idempotentHint": False},
))


# ──────────────────────────────────────────────
# 2. memall_read — retrieve, search, trace, traverse, timeline, fed_query/conflicts
# ──────────────────────────────────────────────

def _handle_read(args: dict) -> str:
    action = args.pop("action", "")
    if action == "retrieve":
        return retrieve.handle_retrieve(args)
    elif action == "vector_search":
        return retrieve.handle_vector_search(args)
    elif action == "hybrid_search":
        return retrieve.handle_hybrid_search(args)
    elif action == "search":
        return retrieve.handle_unified_search(args)
    elif action == "trace":
        return retrieve.handle_trace(args)
    elif action == "traverse":
        return graph.handle_traverse(args)
    elif action == "timeline":
        return timeline.handle(args)
    elif action == "fed_query":
        return federation.handle_query(args)
    elif action == "fed_conflicts":
        return federation.handle_conflicts(args)
    raise ValueError(f"memall_read: unknown action '{action}'")


registry.register(ToolDef(
    name="memall_read",
    description="Search/retrieve memories, trace provenance, explore graph, timeline, federated queries. Actions: retrieve | search | vector_search | hybrid_search | trace | traverse | timeline | fed_query | fed_conflicts",
    input_schema={"type": "object", "properties": {
        "action": {"type": "string", "enum": ["retrieve", "search", "vector_search", "hybrid_search", "trace", "traverse", "timeline", "fed_query", "fed_conflicts"]},
        "query": {"type": "string", "description": "Search query"},
        "mode": {"type": "string", "enum": ["auto", "direct", "fts5", "vector", "hybrid"]},
        "top_k": {"type": "integer"},
        "rrf_k": {"type": "integer"},
        "category": {"type": "string"},
        "level": {"type": "string"},
        "owner": {"type": "string"},
        "agent_name": {"type": "string"},
        "project": {"type": "string"},
        "limit": {"type": "integer"},
        "rerank": {"type": "boolean"},
        "node_id": {"type": "integer", "description": "Starting memory ID for traverse"},
        "depth": {"type": "integer", "maximum": 5},
        "relation_filter": {"type": "string"},
        "memory_id": {"type": "integer", "description": "Memory ID for trace"},
        "hours": {"type": "integer"},
        "trust_level": {"type": "string"},
    }, "required": ["action"]},
    handler=_handle_read,
    annotations={"readOnlyHint": True, "idempotentHint": True},
))


# ──────────────────────────────────────────────
# 3. memall_persona — persona, profile, identity, ask
# ──────────────────────────────────────────────

def _handle_persona(args: dict) -> str:
    action = args.pop("action", "")
    if action == "persona":
        return persona.handle_persona(args)
    elif action == "persona_profile":
        return persona.handle_persona_profile(args)
    elif action == "identity":
        return persona.handle_identity(args)
    elif action == "ask":
        return persona.handle_ask(args)
    raise ValueError(f"memall_persona: unknown action '{action}'")


registry.register(ToolDef(
    name="memall_persona",
    description="Query agent persona/profile/identity or ask a digital twin question. Actions: persona | persona_profile | identity | ask",
    input_schema={"type": "object", "properties": {
        "action": {"type": "string", "enum": ["persona", "persona_profile", "identity", "ask"]},
        "agent_name": {"type": "string", "description": "Agent name to query"},
        "question": {"type": "string", "description": "Question for ask action"},
        "mode": {"type": "string", "enum": ["stance", "pattern", "predict"]},
        "subject": {"type": "string", "description": "Subject/agent context"},
        "layer": {"type": "string", "enum": ["1", "2", "3", "all"]},
        "evolution": {"type": "boolean"},
        "window_days": {"type": "integer"},
    }, "required": ["action"]},
    handler=_handle_persona,
    annotations={"readOnlyHint": True, "idempotentHint": True},
))


# ──────────────────────────────────────────────
# 4. memall_discussion — create, respond, status
# ──────────────────────────────────────────────

def _handle_discussion(args: dict) -> str:
    action = args.pop("action", "")
    if action == "create":
        return discussion.handle_create(args)
    elif action == "respond":
        return discussion.handle_respond(args)
    elif action == "status":
        return discussion.handle_status(args)
    raise ValueError(f"memall_discussion: unknown action '{action}'")


registry.register(ToolDef(
    name="memall_discussion",
    description="Create multi-agent discussions, record stances, check status. Actions: create | respond | status",
    input_schema={"type": "object", "properties": {
        "action": {"type": "string", "enum": ["create", "respond", "status"]},
        "title": {"type": "string", "description": "Discussion title (create)"},
        "background": {"type": "string", "description": "Problem description"},
        "options": {"type": "array", "items": {"type": "string"}, "description": "Solution options"},
        "participants": {"type": "array", "items": {"type": "string"}},
        "open_questions": {"type": "array", "items": {"type": "string"}},
        "recommendation": {"type": "string"},
        "convergence_rule": {"type": "string", "enum": ["unanimous", "majority", "any"]},
        "timeout_hours": {"type": "integer"},
        "action_items": {"type": "array", "items": {"type": "object", "properties": {
            "assigned_to": {"type": "string"},
            "description": {"type": "string"},
        }, "required": ["assigned_to", "description"]}},
        "discussion_id": {"type": "integer", "description": "Discussion memory ID (respond/status)"},
        "agent_name": {"type": "string", "description": "Agent responding"},
        "stance": {"type": "string", "enum": ["agree", "disagree", "abstain"]},
        "arguments": {"type": "string", "description": "Reasoning text"},
        "round_num": {"type": "integer"},
    }, "required": ["action"]},
    handler=_handle_discussion,
    annotations={"readOnlyHint": False, "idempotentHint": False},
))


# ──────────────────────────────────────────────
# 5. memall_federation — publish, inject, extract, deliver
# ──────────────────────────────────────────────

def _handle_federation(args: dict) -> str:
    action = args.pop("action", "")
    if action == "query":
        return federation.handle_query(args)
    elif action == "publish":
        return federation.handle_publish(args)
    elif action == "conflicts":
        return federation.handle_conflicts(args)
    elif action == "inject":
        return federation.handle_inject(args)
    elif action == "extract":
        return federation.handle_extract(args)
    elif action == "deliver":
        return federation.handle_deliver(args)
    raise ValueError(f"memall_federation: unknown action '{action}'")


registry.register(ToolDef(
    name="memall_federation",
    description="Cross-agent knowledge federation: query, publish, resolve conflicts, inject/extract, push deliver. Actions: query | publish | conflicts | inject | extract | deliver",
    input_schema={"type": "object", "properties": {
        "action": {"type": "string", "enum": ["query", "publish", "conflicts", "inject", "extract", "deliver"]},
        "query": {"type": "string", "description": "Search keyword"},
        "agent_name": {"type": "string"},
        "category": {"type": "string"},
        "trust_level": {"type": "string", "enum": ["trusted", "family", "shared", "public"]},
        "project": {"type": "string"},
        "limit": {"type": "integer"},
        "memory_id": {"type": "integer"},
        "source_agent": {"type": "string"},
        "session_id": {"type": "string"},
        "target_agent": {"type": "string"},
        "content": {"type": "string"},
        "event_type": {"type": "string"},
        "source": {"type": "string"},
    }, "required": ["action"]},
    handler=_handle_federation,
    annotations={"readOnlyHint": False, "idempotentHint": False},
))


# ──────────────────────────────────────────────
# 6. memall_system — pipeline, distill, gateway, hub, session, db,
#                    security, adaptive, onboarding, reflect, index_rebuild
# ──────────────────────────────────────────────

def _handle_system(args: dict) -> str:
    action = args.pop("action", "")
    if action == "run_pipeline":
        return pipeline.handle(args)
    elif action == "distill":
        args["action"] = args.pop("sub_action", "list")
        return distill.handle(args)
    elif action == "gateway":
        args["action"] = args.pop("sub_action", "")
        return gateway.handle(args)
    elif action == "hub_connect":
        return hub.handle_connect(args)
    elif action == "hub_sync":
        return hub.handle_sync(args)
    elif action == "session_start":
        return session.handle_session_start(args)
    elif action == "session_end":
        return session.handle_session_end(args)
    elif action == "session_summary":
        return session.handle_session_summary(args)
    elif action == "db":
        args["action"] = args.pop("sub_action", "")
        return manage.handle_db(args)
    elif action == "security":
        args["action"] = args.pop("sub_action", "")
        return manage.handle_security(args)
    elif action == "adaptive":
        args["action"] = args.pop("sub_action", "")
        return manage.handle_adaptive(args)
    elif action == "onboarding":
        args["action"] = args.pop("sub_action", "")
        return onboarding.handle(args)
    elif action == "reflect":
        args["action"] = args.pop("sub_action", "")
        return reflect.handle(args)
    elif action == "index_rebuild":
        return index.handle(args)
    raise ValueError(f"memall_system: unknown action '{action}'")


registry.register(ToolDef(
    name="memall_system",
    description="Pipeline, sessions, gateway, hub sync, DB maintenance, security, adaptive, onboarding, reflection, index rebuild. Actions: run_pipeline | distill | gateway | hub_connect | hub_sync | session_start | session_end | session_summary | db | security | adaptive | onboarding | reflect | index_rebuild",
    input_schema={"type": "object", "properties": {
        "action": {"type": "string", "enum": ["run_pipeline", "distill", "gateway", "hub_connect", "hub_sync", "session_start", "session_end", "session_summary", "db", "security", "adaptive", "onboarding", "reflect", "index_rebuild"]},
        "sub_action": {"type": "string", "enum": ["list", "summarize", "start", "stop", "export", "import", "discover", "pair", "peers", "federated", "status", "reset", "submit_step", "skip", "audit", "permit", "check", "score", "clean", "index", "distill", "all", "report", "optimize", "stats", "vacuum", "agree", "disagree", "probe", "expired", "archive_stats", "archive_vacuum"], "description": "Sub-action for distill/gateway/security/adaptive/db/onboarding/reflect"},
        "session_id": {"type": "string", "description": "Session ID"},
        "agent_name": {"type": "string"},
        "auto_inject": {"type": "boolean"},
        "auto_extract": {"type": "boolean"},
        "include_reflect": {"type": "boolean"},
        "include_distill": {"type": "boolean"},
        "include_integrate": {"type": "boolean"},
        "include_persona": {"type": "boolean"},
        "timeout": {"type": "integer"},
        "limit": {"type": "integer"},
        "group_id": {"type": "integer"},
        "summary": {"type": "string"},
        "insight": {"type": "string"},
        "gap": {"type": "string"},
        "next": {"type": "string"},
        "port": {"type": "integer"},
        "file_path": {"type": "string"},
        "address": {"type": "string"},
        "max_peers": {"type": "integer"},
        "direction": {"type": "string", "enum": ["bidirectional", "to_hub", "from_hub"]},
        "force": {"type": "boolean", "description": "Force index rebuild"},
        "user_id": {"type": "string"},
        "step": {"type": "integer"},
        "input_data": {"type": "object", "additionalProperties": True},
        "memory_id": {"type": "integer"},
        "context": {"type": "string"},
        "level": {"type": "string", "enum": ["public", "trusted", "private"]},
        "requester": {"type": "string"},
        "target": {"type": "string"},
    }, "required": ["action"]},
    handler=_handle_system,
    annotations={"readOnlyHint": False, "idempotentHint": False},
))


# ──────────────────────────────────────────────
# 7. memall_hooks_recent — peek into recent hook activity
# ──────────────────────────────────────────────

def _handle_hooks(args: dict) -> str:
    from memall.mcp.hook_effects import peek_recent, format_activity
    n = args.get("n", 10)
    events = peek_recent(n)
    activity = format_activity(events) if events else None
    if activity:
        return json.dumps({"activity": activity})
    return json.dumps({"activity": "No recent hook activity to show."})


registry.register(ToolDef(
    name="memall_hooks_recent",
    description="Show recent async hook activity (pipeline runs, notifications, reminder checks, etc.)",
    input_schema={"type": "object", "properties": {
        "n": {"type": "integer", "description": "Number of recent events (default 10)"},
    }},
    handler=_handle_hooks,
    annotations={"readOnlyHint": True, "idempotentHint": True},
))
