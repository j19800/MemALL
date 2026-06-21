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

# ── Capture ──
registry.register(ToolDef(
    name="capture", description="Store a memory",
    input_schema={"type": "object", "properties": {
        "content": {"type": "string", "description": "Memory content"},
        "owner": {"type": "string", "description": "Owner name"},
        "agent_name": {"type": "string", "description": "Agent name"},
        "subject": {"type": "string", "description": "Subject or topic"},
        "summary": {"type": "string", "description": "Short summary"},
        "project": {"type": "string", "description": "Project name"},
        "category": {"type": "string", "description": "Category"},
        "level": {"type": "string", "enum": ["P0", "P1", "P2", "L1", "L2", "L3", "L4", "L5", "L6", "L7", "L8", "L9", "L10"], "default": "P2"},
        "metadata": {"type": "string", "description": "JSON metadata"},
        "thread_id": {"type": "integer", "description": "Optional parent memory ID for thread context"},
    }, "required": ["content"]},
    handler=capture.handle,
    annotations={"readOnlyHint": False, "idempotentHint": False},
))

# ── Retrieve ──
registry.register(ToolDef(
    name="retrieve", description="Search or get memories by ID",
    input_schema={"type": "object", "properties": {
        "query": {"type": "string", "description": "Search query or ID"},
        "owner": {"type": "string"},
        "agent_name": {"type": "string"},
        "category": {"type": "string"},
        "project": {"type": "string"},
        "limit": {"type": "integer", "default": 20},
    }},
    handler=retrieve.handle_retrieve,
    annotations={"readOnlyHint": True, "idempotentHint": True},
    intercept_category="query",
))

# ── Connect ──
registry.register(ToolDef(
    name="connect", description="Create a relationship between two memories",
    input_schema={"type": "object", "properties": {
        "source_id": {"type": "integer"},
        "target_id": {"type": "integer"},
        "relation_type": {"type": "string", "enum": ["extends", "contradicts", "refines", "cites", "supersedes"]},
        "weight": {"type": "number", "default": 1.0},
    }, "required": ["source_id", "target_id"]},
    handler=graph.handle_connect,
    annotations={"readOnlyHint": False, "idempotentHint": False},
))

# ── Traverse (original) ──
registry.register(ToolDef(
    name="traverse", description="Explore the knowledge graph from a memory",
    input_schema={"type": "object", "properties": {
        "node_id": {"type": "integer"},
        "depth": {"type": "integer", "default": 1, "max": 5},
        "relation_filter": {"type": "string"},
    }, "required": ["node_id"]},
    handler=graph.handle_traverse,
    annotations={"readOnlyHint": True, "idempotentHint": True},
    intercept_category="query",
))

# ── Timeline ──
registry.register(ToolDef(
    name="timeline", description="Get time-ordered memories",
    input_schema={"type": "object", "properties": {
        "query": {"type": "string"},
        "hours": {"type": "integer", "default": 24},
        "category": {"type": "string"},
        "project": {"type": "string"},
        "limit": {"type": "integer", "default": 50},
    }},
    handler=timeline.handle,
    annotations={"readOnlyHint": True, "idempotentHint": True},
    intercept_category="query",
))

# ── Persona ──
registry.register(ToolDef(
    name="memall_persona", description="Get the digital persona/profile of an Agent",
    input_schema={"type": "object", "properties": {
        "agent_name": {"type": "string", "description": "Agent whose persona to retrieve"},
        "evolution": {"type": "boolean", "default": False, "description": "Also return evolution time series"},
        "window_days": {"type": "integer", "default": 30, "description": "Window size (days) for evolution"},
    }, "required": ["agent_name"]},
    handler=persona.handle_persona,
    annotations={"readOnlyHint": True, "idempotentHint": True},
    intercept_category="profile",
))

# ── Persona Profile ──
registry.register(ToolDef(
    name="memall_persona_profile",
    description="Generate a complete 3-layer Agent Profile",
    input_schema={"type": "object", "properties": {
        "agent_name": {"type": "string", "description": "Agent whose 3-layer profile to generate"},
        "layer": {"type": "string", "enum": ["1", "2", "3", "all"], "default": "all"},
    }, "required": ["agent_name"]},
    handler=persona.handle_persona_profile,
    annotations={"readOnlyHint": True, "idempotentHint": True},
    intercept_category="profile",
))

# ── Ask ──
registry.register(ToolDef(
    name="memall_ask", description="Query your digital twin",
    input_schema={"type": "object", "properties": {
        "question": {"type": "string", "description": "Question to ask"},
        "mode": {"type": "string", "enum": ["stance", "pattern", "predict"], "default": "stance"},
        "subject": {"type": "string", "description": "Agent context"},
        "agent_name": {"type": "string", "description": "DEPRECATED: use subject"},
    }, "required": ["question"]},
    handler=persona.handle_ask,
    annotations={"readOnlyHint": True, "idempotentHint": False},
    intercept_category="ask",
))

# ── Identity ──
registry.register(ToolDef(
    name="memall_identity", description="Get L1 identity traits and L7 preferences for an agent",
    input_schema={"type": "object", "properties": {
        "agent_name": {"type": "string", "description": "Agent name to query"},
    }, "required": ["agent_name"]},
    handler=persona.handle_identity,
    annotations={"readOnlyHint": True, "idempotentHint": True},
))

# ── Smart Store ──
registry.register(ToolDef(
    name="memall_smart_store",
    description="Store a memory with automatic deduplication",
    input_schema={"type": "object", "properties": {
        "content": {"type": "string", "description": "Memory content"},
        "owner": {"type": "string", "default": ""},
        "agent_name": {"type": "string", "default": ""},
        "subject": {"type": "string", "default": ""},
        "project": {"type": "string", "default": ""},
        "category": {"type": "string", "default": "general"},
        "level": {"type": "string", "enum": ["P0", "P1", "P2", "L1", "L2", "L3", "L4", "L5", "L6", "L7", "L8", "L9", "L10"], "default": "P2"},
        "dedup_threshold": {"type": "number", "default": 0.85},
    }, "required": ["content"]},
    handler=memory_write.handle_smart_store,
    annotations={"readOnlyHint": False, "idempotentHint": False},
))

# ── Store Batch ──
registry.register(ToolDef(
    name="memall_store_batch", description="Batch store multiple memories at once",
    input_schema={"type": "object", "properties": {
        "items": {"type": "array", "description": "List of memory objects"},
    }, "required": ["items"]},
    handler=memory_write.handle_store_batch,
    annotations={"readOnlyHint": False, "idempotentHint": False},
))

# ── Update ──
registry.register(ToolDef(
    name="memall_update", description="Update an existing memory's fields",
    input_schema={"type": "object", "properties": {
        "memory_id": {"type": "integer", "description": "Memory ID to update"},
        "content": {"type": "string"},
        "category": {"type": "string"},
        "project": {"type": "string"},
        "summary": {"type": "string"},
        "level": {"type": "string"},
        "metadata": {"type": "string", "description": "JSON string of metadata fields to merge"},
    }, "required": ["memory_id"]},
    handler=memory_write.handle_update,
    annotations={"readOnlyHint": False, "idempotentHint": False},
))

# ── Vector Search ──
registry.register(ToolDef(
    name="memall_vector_search",
    description="Semantic vector search using TF-IDF+SVD embeddings",
    input_schema={"type": "object", "properties": {
        "query": {"type": "string", "description": "Search query"},
        "top_k": {"type": "integer", "default": 10},
    }, "required": ["query"]},
    handler=retrieve.handle_vector_search,
    annotations={"readOnlyHint": True, "idempotentHint": True},
    intercept_category="query",
))

# ── Hybrid Search (FTS5 + vec0 RRF) ──
registry.register(ToolDef(
    name="memall_hybrid_search",
    description="Dual-recall search: FTS5 keyword + vec0 vector, merged via RRF (Reciprocal Rank Fusion). Supports optional metadata filters.",
    input_schema={"type": "object", "properties": {
        "query": {"type": "string", "description": "Search query (supports CJK)"},
        "top_k": {"type": "integer", "default": 10, "description": "Results to return"},
        "rrf_k": {"type": "integer", "default": 60, "description": "RRF constant — higher = smoother rank fusion"},
        "category": {"type": "string", "description": "Optional: filter by category"},
        "level": {"type": "string", "description": "Optional: filter by memory level (e.g. L4, L6)"},
        "owner": {"type": "string", "description": "Optional: filter by owner"},
        "rerank": {"type": "boolean", "default": False, "description": "Enable cross-encoder reranking (requires pip install memall-db[rerank], downloads ~1.8GB)"},
    }, "required": ["query"]},
    handler=retrieve.handle_hybrid_search,
    annotations={"readOnlyHint": True, "idempotentHint": True},
    intercept_category="query",
))

# ── Session Start ──
registry.register(ToolDef(
    name="memall_session_start",
    description="Start a new conversation session for tracking",
    input_schema={"type": "object", "properties": {
        "agent_name": {"type": "string", "default": "", "description": "Agent starting the session"},
        "auto_inject": {"type": "boolean", "default": True, "description": "Auto-inject Agent Profile + semantic fragments"},
    }},
    handler=session.handle_session_start,
    annotations={"readOnlyHint": False, "idempotentHint": False},
))

# ── Session End ──
registry.register(ToolDef(
    name="memall_session_end", description="End a session and auto-summarize captured memories",
    input_schema={"type": "object", "properties": {
        "session_id": {"type": "string", "description": "Session ID from session_start"},
        "auto_extract": {"type": "boolean", "default": False, "description": "Auto-extract facts to shared_memories"},
    }, "required": ["session_id"]},
    handler=session.handle_session_end,
    annotations={"readOnlyHint": False, "idempotentHint": False},
))

# ── Session Summary ──
registry.register(ToolDef(
    name="memall_session_summary", description="Get summary of one or more sessions",
    input_schema={"type": "object", "properties": {
        "session_id": {"type": "string", "description": "Specific session ID (optional)"},
        "agent_name": {"type": "string", "description": "Filter by agent name"},
        "limit": {"type": "integer", "default": 5},
    }},
    handler=session.handle_session_summary,
    annotations={"readOnlyHint": True, "idempotentHint": True},
))

# ── Run Pipeline ──
registry.register(ToolDef(
    name="memall_run_pipeline",
    description="Run the full memory pipeline: enrich → classify → reflect → distill → integrate → observe",
    input_schema={"type": "object", "properties": {
        "include_reflect": {"type": "boolean", "default": True},
        "include_distill": {"type": "boolean", "default": True},
        "include_integrate": {"type": "boolean", "default": True},
        "include_persona": {"type": "boolean", "default": True},
        "timeout": {"type": "integer", "default": 300},
    }},
    handler=pipeline.handle,
    annotations={"readOnlyHint": False, "idempotentHint": True},
))

# ── Traverse (memall_) ──
registry.register(ToolDef(
    name="memall_traverse",
    description="Traverse knowledge graph from a memory — 1-hop or 2-hop expansion",
    input_schema={"type": "object", "properties": {
        "node_id": {"type": "integer", "description": "Starting memory ID"},
        "depth": {"type": "integer", "default": 1, "maximum": 2, "description": "Expansion depth"},
        "relation_filter": {"type": "string", "description": "Filter by relation type"},
    }, "required": ["node_id"]},
    handler=graph.handle_traverse,
    annotations={"readOnlyHint": True, "idempotentHint": True},
    intercept_category="query",
))

# ── Federation Query ──
registry.register(ToolDef(
    name="memall_fed_query",
    description="Query shared_memories across agents — cross-agent knowledge retrieval",
    input_schema={"type": "object", "properties": {
        "query": {"type": "string", "default": "", "description": "Search keyword in content"},
        "agent_name": {"type": "string", "default": "", "description": "Filter by source agent"},
        "category": {"type": "string", "default": "", "description": "Filter by category"},
        "trust_level": {"type": "string", "default": "", "description": "Filter by trust level"},
        "project": {"type": "string", "default": "", "description": "Filter by project name"},
        "limit": {"type": "integer", "default": 20},
    }},
    handler=federation.handle_query,
    annotations={"readOnlyHint": True, "idempotentHint": True},
    intercept_category="query",
))

# ── Federation Publish ──
registry.register(ToolDef(
    name="memall_fed_publish",
    description="Publish a local memory to shared_memories for cross-agent access",
    input_schema={"type": "object", "properties": {
        "memory_id": {"type": "integer", "description": "Local memory ID to publish"},
        "source_agent": {"type": "string", "default": "", "description": "Source agent name"},
        "trust_level": {"type": "string", "default": "family", "enum": ["trusted", "family", "shared", "public"]},
        "category": {"type": "string", "default": "", "description": "Category override"},
    }, "required": ["memory_id"]},
    handler=federation.handle_publish,
    annotations={"readOnlyHint": False, "idempotentHint": False},
))

# ── Federation Conflicts ──
registry.register(ToolDef(
    name="memall_fed_conflicts",
    description="List unresolved conflicts across shared_memories",
    input_schema={"type": "object", "properties": {
        "limit": {"type": "integer", "default": 20},
    }},
    handler=federation.handle_conflicts,
    annotations={"readOnlyHint": True, "idempotentHint": True},
))

# ── Federation Inject ──
registry.register(ToolDef(
    name="memall_fed_inject",
    description="Auto-inject Agent Profile + semantic fragments for session context",
    input_schema={"type": "object", "properties": {
        "agent_name": {"type": "string", "description": "Agent name to inject context for"},
    }, "required": ["agent_name"]},
    handler=federation.handle_inject,
    annotations={"readOnlyHint": False, "idempotentHint": True},
))

# ── Federation Extract ──
registry.register(ToolDef(
    name="memall_fed_extract",
    description="Auto-extract facts from session memories into shared_memories",
    input_schema={"type": "object", "properties": {
        "session_id": {"type": "string", "description": "Session ID from session_start"},
    }, "required": ["session_id"]},
    handler=federation.handle_extract,
    annotations={"readOnlyHint": False, "idempotentHint": False},
))

# ── Hub Connect ──
registry.register(ToolDef(
    name="memall_hub_connect",
    description="Test connectivity to Agent Hub (127.0.0.1:12431)",
    input_schema={"type": "object", "properties": {}, "required": []},
    handler=hub.handle_connect,
    annotations={"readOnlyHint": True, "idempotentHint": False, "openWorldHint": True},
))

# ── Hub Sync ──
registry.register(ToolDef(
    name="memall_hub_sync",
    description="Bidirectional sync between MemALL and Agent Hub",
    input_schema={"type": "object", "properties": {
        "direction": {"type": "string", "enum": ["bidirectional", "to_hub", "from_hub"], "default": "bidirectional"},
        "limit": {"type": "integer", "default": 20},
    }, "required": []},
    handler=hub.handle_sync,
    annotations={"readOnlyHint": False, "idempotentHint": False, "openWorldHint": True},
))

# ── Forget ──
registry.register(ToolDef(
    name="memall_forget",
    description="Phase 11: Automatic forgetting — TTL expiration + low-value decay",
    input_schema={"type": "object", "properties": {
        "action": {"type": "string", "enum": ["expired", "low_value", "review", "stats", "all"]},
        "days": {"type": "integer", "default": 90},
        "agent_name": {"type": "string", "description": "Optional agent filter"},
    }, "required": ["action"]},
    handler=manage.handle_forget,
    annotations={"destructiveHint": True, "readOnlyHint": False, "idempotentHint": False},
    intercept_category="manage",
))

# ── Adaptive ──
registry.register(ToolDef(
    name="memall_adaptive",
    description="Phase 12: AI adaptive subsystem — dynamic clean/index/distill",
    input_schema={"type": "object", "properties": {
        "action": {"type": "string", "enum": ["clean", "index", "distill", "all", "report"]},
        "agent_name": {"type": "string", "description": "Optional agent name filter"},
    }, "required": ["action"]},
    handler=manage.handle_adaptive,
    annotations={"readOnlyHint": False, "idempotentHint": False},
    intercept_category="manage",
))

# ── Security ──
registry.register(ToolDef(
    name="memall_security",
    description="Phase 13: Security governance — audit, permit, check, score",
    input_schema={"type": "object", "properties": {
        "action": {"type": "string", "enum": ["audit", "permit", "check", "score", "list"]},
        "agent_name": {"type": "string"},
        "level": {"type": "string", "enum": ["public", "trusted", "private"]},
        "requester": {"type": "string"},
        "target": {"type": "string"},
    }, "required": ["action"]},
    handler=manage.handle_security,
    annotations={"readOnlyHint": False, "idempotentHint": False},
    intercept_category="manage",
))

# ── Ops ──
registry.register(ToolDef(
    name="memall_ops",
    description="Phase 14: Memory operations — merge, split, tag, archive, dedup",
    input_schema={"type": "object", "properties": {
        "action": {"type": "string", "enum": ["merge", "split", "tag", "batch_tag", "archive", "restore", "dedup"]},
        "source_id": {"type": "integer"},
        "target_id": {"type": "integer"},
        "memory_id": {"type": "integer"},
        "delimiter": {"type": "string"},
        "tags": {"type": "array", "items": {"type": "string"}},
        "mode": {"type": "string", "enum": ["add", "set", "remove"]},
        "agent_name": {"type": "string"},
        "category": {"type": "string"},
        "days": {"type": "integer"},
        "threshold": {"type": "number", "default": 0.9},
    }, "required": ["action"]},
    handler=manage.handle_ops,
    annotations={"destructiveHint": True, "readOnlyHint": False, "idempotentHint": False},
    intercept_category="manage",
))

# ── Gateway ──
registry.register(ToolDef(
    name="memall_gateway",
    description="Phase 15: Gateway — start/stop HTTP gateway, export/import, LAN discovery, federated queries",
    input_schema={"type": "object", "properties": {
        "action": {"type": "string", "enum": ["start", "stop", "export", "import", "discover", "pair", "peers", "federated"]},
        "port": {"type": "integer", "default": 9920},
        "agent_name": {"type": "string"},
        "file_path": {"type": "string"},
        "address": {"type": "string"},
        "query": {"type": "string"},
        "max_peers": {"type": "integer", "default": 3},
    }, "required": ["action"]},
    handler=gateway.handle,
    annotations={"openWorldHint": True, "readOnlyHint": False, "idempotentHint": False},
))

# ── Reflect Interact ──
registry.register(ToolDef(
    name="memall_reflect_interact",
    description="对 L6 反思进行交互：agree / disagree / probe",
    input_schema={"type": "object", "properties": {
        "memory_id": {"type": "integer", "description": "L6 反思记忆 ID"},
        "action": {"type": "string", "enum": ["agree", "disagree", "probe"]},
        "context": {"type": "string", "description": "互动说明（可选）"},
    }, "required": ["memory_id", "action"]},
    handler=reflect.handle,
    annotations={"readOnlyHint": False, "idempotentHint": False},
))

# ── DB ──
registry.register(ToolDef(
    name="memall_db",
    description="Phase 21: Database maintenance — optimize, stats, vacuum",
    input_schema={"type": "object", "properties": {
        "action": {"type": "string", "enum": ["optimize", "stats", "vacuum"]},
    }, "required": ["action"]},
    handler=manage.handle_db,
    annotations={"readOnlyHint": False, "idempotentHint": True},
    intercept_category="query",
))

# ── Onboarding ──
registry.register(ToolDef(
    name="memall_onboarding",
    description="MemALL 新手引导（5 步：建 Agent → 首存记忆 → 试用搜索 → 看状态 → 完成）",
    input_schema={"type": "object", "properties": {
        "action": {"type": "string", "enum": ["status", "start", "reset", "submit_step", "skip"]},
        "user_id": {"type": "string", "default": "default"},
        "step": {"type": "integer"},
        "input_data": {"type": "object", "additionalProperties": True},
    }, "required": ["action"]},
    handler=onboarding.handle,
    annotations={"readOnlyHint": False, "idempotentHint": False},
))

# ── Trace ──
registry.register(ToolDef(
    name="memall_trace",
    description="Trace a memory back to its source — shows origin session, creator, and related decisions",
    input_schema={"type": "object", "properties": {
        "memory_id": {"type": "integer", "description": "Memory ID to trace"},
    }, "required": ["memory_id"]},
    handler=retrieve.handle_trace,
    annotations={"readOnlyHint": True, "idempotentHint": True},
))

# ── Index Rebuild ──
registry.register(ToolDef(
    name="memall_index_rebuild",
    description="Rebuild memory embeddings (TF-IDF+SVD) for vector search",
    input_schema={"type": "object", "properties": {
        "force": {"type": "boolean", "default": False, "description": "Force full rebuild of all embeddings"},
    }},
    handler=index.handle,
    annotations={"readOnlyHint": True, "idempotentHint": True},
))

# ── Discussion Create ──
registry.register(ToolDef(
    name="memall_discussion_create",
    description="Create a new discussion as an L5 memory. Returns memory_id.",
    input_schema={"type": "object", "properties": {
        "title": {"type": "string", "description": "Short title for the discussion"},
        "background": {"type": "string", "description": "问题描述：以事实和数据为依据描述问题和背景"},
        "options": {"type": "array", "items": {"type": "string"}, "description": "解决方案：列出各方案的描述和预估工作量"},
        "participants": {"type": "array", "items": {"type": "string"}},
        "open_questions": {"type": "array", "items": {"type": "string"}},
        "recommendation": {"type": "string", "description": "建议：明确推荐哪个方案及理由"},
        "convergence_rule": {"type": "string", "enum": ["unanimous", "majority", "any"], "default": "unanimous"},
        "timeout_hours": {"type": "integer", "default": 24},
        "action_items": {"type": "array", "items": {"type": "object", "properties": {
            "assigned_to": {"type": "string"},
            "description": {"type": "string"},
        }, "required": ["assigned_to", "description"]}},
    }, "required": ["title", "participants"]},
    handler=discussion.handle_create,
    annotations={"readOnlyHint": False, "idempotentHint": False},
))

# ── Discussion Respond ──
registry.register(ToolDef(
    name="memall_discussion_respond",
    description="Record an agent's stance. Captures P2 + edge, auto-checks convergence.",
    input_schema={"type": "object", "properties": {
        "discussion_id": {"type": "integer", "description": "L5 memory_id of the discussion"},
        "agent_name": {"type": "string", "description": "Which agent is responding"},
        "stance": {"type": "string", "enum": ["agree", "disagree", "abstain"]},
        "arguments": {"type": "string", "description": "Free-text reasoning"},
        "round_num": {"type": "integer", "default": 1},
    }, "required": ["discussion_id", "agent_name", "stance"]},
    handler=discussion.handle_respond,
    annotations={"readOnlyHint": False, "idempotentHint": False},
))

# ── Discussion Status ──
registry.register(ToolDef(
    name="memall_discussion_status",
    description="Get discussion L5 + P2 responses. Omit discussion_id to list all active.",
    input_schema={"type": "object", "properties": {
        "discussion_id": {"type": "integer", "description": "Optional: L5 memory_id. Omit to list all active."},
    }},
    handler=discussion.handle_status,
    annotations={"readOnlyHint": True, "idempotentHint": True},
))
