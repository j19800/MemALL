# mcp/models.py - Pydantic models for MCP tool input validation
from pydantic import BaseModel, Field, field_validator
from typing import Optional, List, Literal, Dict, Any, Union


# ── Core tools ──

class CaptureInput(BaseModel):
    content: str = Field(..., min_length=1, max_length=10000, description="Memory content")
    owner: str = Field("", max_length=200)
    agent_name: str = Field("", max_length=200)
    subject: str = Field("", max_length=500)
    summary: str = ""
    project: str = Field("", max_length=500)
    category: str = Field("general", max_length=100)
    level: str = Field("P2", pattern=r"^(P[0-2]|L(10|11|[1-9]))$")
    metadata: str = "{}"
    # Thread linking: MemoryInput supports thread_id but the validator used to
    # drop it silently, breaking reply-threading through the MCP surface.
    thread_id: Optional[int] = Field(None, ge=1)
    # ── Fields the MCP schema advertises / capture.py supports but this model
    # did not declare ─────────────────────────────────────────────────────
    # Pydantic v2 drops undeclared fields (extra='ignore'), and
    # validator.py then hands the handler only the validated subset.  These
    # seven reached neither the handler nor the store: a client that passed
    # `tags` — which the memall_write schema DOES advertise as
    # array<string> — got it silently discarded, and the caller's tags were
    # replaced by auto-derived keywords (so exact-tag search filters never
    # matched MCP-written memories).
    #
    # This is the write-side twin of the read-side defect fixed in 203f03a
    # (VectorSearchInput/UnifiedSearchInput did not declare `project`, so the
    # validator stripped it and the MCP filters never applied).  That fix's
    # acceptance check only verified that dropped fields were *reported* —
    # not that supported fields *worked*.
    #
    # `visibility` is safe to expose: core/thin_waist._get_allowed_write_
    # visibility clamps it to the agent's allowed write level (one stricter
    # than its read level) for non-human agents.
    creator: Optional[str] = Field(None, max_length=200)
    confidence: Optional[float] = Field(None, ge=0.0, le=1.0)
    visibility: Optional[str] = Field(
        None, pattern=r"^(public|shared|family|trusted|private)$")
    stream: Optional[str] = Field(None, max_length=50)
    occurred_at: Optional[str] = Field(None, max_length=64)
    supersedes: Optional[Union[str, List[int]]] = Field(
        None, description="JSON string or list of superseded memory ids")
    tags: Optional[Union[str, List[str]]] = Field(
        None, description="JSON string or list of tags")


class QuickCaptureInput(BaseModel):
    """memall_write action=quick/记一下 — auto-filled capture, needs content."""
    content: str = Field(..., min_length=1, max_length=10000)
    owner: str = Field("", max_length=200)
    agent_name: str = Field("", max_length=200)
    subject: str = Field("", max_length=500)
    project: str = Field("", max_length=500)
    category: str = Field("general", max_length=100)
    level: str = Field("P2", pattern=r"^(P[0-2]|L(10|11|[1-9]))$")
    metadata: str = "{}"
    thread_id: Optional[int] = Field(None, ge=1)


class BatchCaptureInput(BaseModel):
    """memall_write action=batch_capture — capture multiple memories efficiently."""
    items: List[Dict[str, Any]] = Field(..., min_length=1, description="List of memory objects to capture")
    
    @field_validator('items')
    @classmethod
    def validate_items(cls, v):
        if not isinstance(v, list):
            raise ValueError('items must be a list')
        if len(v) == 0:
            raise ValueError('items list cannot be empty')
        # Validate each item has required content field
        for i, item in enumerate(v):
            if not isinstance(item, dict):
                raise ValueError(f'item at index {i} must be a dictionary')
            if not item.get('content'):
                raise ValueError(f'item at index {i} must have content field')
        return v


class BatchSearchInput(BaseModel):
    """memall_read action=batch_search — search for multiple queries efficiently."""
    queries: List[str] = Field(..., min_length=1, description="List of search queries")
    limit: int = Field(10, ge=1, le=100, description="Maximum results per query")
    
    @field_validator('queries')
    @classmethod
    def validate_queries(cls, v):
        if not isinstance(v, list):
            raise ValueError('queries must be a list')
        if len(v) == 0:
            raise ValueError('queries list cannot be empty')
        # Validate each query is a non-empty string
        for i, query in enumerate(v):
            if not isinstance(query, str):
                raise ValueError(f'query at index {i} must be a string')
            if not query.strip():
                raise ValueError(f'query at index {i} must be a non-empty string')
        return v


class RetrieveInput(BaseModel):
    query: Optional[str] = Field(None, max_length=5000)
    owner: Optional[str] = Field(None, max_length=200)
    agent_name: Optional[str] = Field(None, max_length=200)
    category: Optional[str] = Field(None, max_length=100)
    project: Optional[str] = Field(None, max_length=500)
    level: Optional[str] = Field(None, max_length=20, pattern=r"^(P[0-2]|L(10|11|[1-9]))$")
    limit: int = Field(20, ge=1, le=500)
    # Exact-match tag filter (json_each on the tags column).  tags_all=True
    # requires every listed tag; default matches any.
    tags: Optional[List[str]] = Field(None, max_length=32)
    tags_all: bool = False
    # Per-session novelty demotion (see UnifiedSearchInput.session_id).
    session_id: Optional[str] = Field(None, max_length=200)
    # Enhancement layer (same semantics as UnifiedSearchInput).
    facet_fields: Optional[List[str]] = Field(None, max_length=8)
    facet_fields_str: Optional[str] = Field(None, max_length=200)
    expand_query: bool = False
    suggest: bool = False
    explain: bool = False
    cluster: bool = False
    personalize: bool = False
    context_boost: bool = False
    viewer: Optional[str] = Field(None, max_length=200)


class ConnectInput(BaseModel):
    source_id: int = Field(..., ge=1)
    target_id: int = Field(..., ge=1)
    relation_type: str = Field(
        "refines",
        pattern=r"^(extends|contradicts|refines|cites|supersedes|related)$",
    )
    weight: float = Field(1.0, ge=0.0, le=2.0)


class TraverseInput(BaseModel):
    node_id: int = Field(..., ge=1)
    depth: int = Field(1, ge=1, le=10)
    relation_filter: Optional[str] = None
    thread_aware: bool = False


class TimelineInput(BaseModel):
    query: Optional[str] = Field(None, max_length=5000)
    hours: int = Field(24, ge=1, le=8760)
    category: Optional[str] = None
    project: Optional[str] = None
    limit: int = Field(50, ge=1, le=1000)
    days: Optional[int] = Field(None, ge=1, le=365)
    start: Optional[str] = None
    end: Optional[str] = None


# ── Persona & Ask tools ──

class PersonaInput(BaseModel):
    # Optional: an empty/missing agent_name falls back to the configured human
    # owner (identity.human_owner) — the same invariant the write path uses.
    agent_name: str = Field("", max_length=200)
    evolution: bool = False
    window_days: int = Field(30, ge=1, le=365)
    # Extra fields accepted by the persona handler beyond the enum'd schema.
    mode: Optional[str] = Field(None, pattern=r"^(static|dual|dynamic)$")
    dynamic_window_days: Optional[int] = Field(None, ge=1, le=365)


class PersonaProfileInput(BaseModel):
    # Optional: empty falls back to identity.human_owner (see handle_persona).
    agent_name: str = Field("", max_length=200)
    layer: str = Field("all", pattern=r"^([1-3]|all)$")


class AskInput(BaseModel):
    question: str = Field(..., min_length=1, max_length=5000)
    mode: str = Field("stance", pattern=r"^(stance|pattern|predict)$")
    # Fix Bug-1: 显式声明 subject 字段，agent_name 保留为向后兼容 alias
    subject: Optional[str] = Field(None, max_length=200)
    agent_name: Optional[str] = Field(None, max_length=200)


class ProfilePreloadInput(BaseModel):
    """profile_preload / 预加载画像 — reads identities by agent_name."""
    agent_name: str = Field("", max_length=200)


class PersonaSearchInput(BaseModel):
    """profile_search / 搜画像 — LIKE search over identities."""
    query: str = Field("", max_length=500)


class PersonaForesightInput(BaseModel):
    """foresight / 前瞻 — predicts next-needed info from recent activity."""
    agent_name: str = Field("", max_length=200)


# ── Smart / Batch / Update / VectorSearch tools ──

class SmartStoreInput(BaseModel):
    action: str = Field("smart_store", pattern=r"^(smart_store|capture|update|forget|append|replace|set_note|upload|upload_images|upload_video|attach_files)$")
    content: str = Field(..., min_length=1, max_length=10000)
    owner: str = Field("", max_length=200)
    agent_name: str = Field("", max_length=200)
    subject: str = Field("", max_length=500)
    project: str = Field("", max_length=500)
    category: str = Field("general", max_length=100)
    level: str = Field("P2", pattern=r"^(P[0-2]|L(10|11|[1-9]))$")
    dedup_threshold: float = Field(0.85, ge=0.0, le=1.0)


class StoreBatchItem(BaseModel):
    content: str = Field(..., min_length=1)
    owner: str = ""
    agent_name: str = ""
    subject: str = ""
    project: str = ""
    category: str = "general"
    level: str = Field("P2", pattern=r"^(P[0-2]|L(10|11|[1-9]))$")


class StoreBatchInput(BaseModel):
    items: List[StoreBatchItem] = Field(..., min_length=1, max_length=200)


class UpdateInput(BaseModel):
    memory_id: int = Field(..., ge=1)
    content: Optional[str] = Field(None, max_length=10000)
    category: Optional[str] = Field(None, max_length=100)
    project: Optional[str] = Field(None, max_length=500)
    summary: Optional[str] = Field(None, max_length=2000)
    level: Optional[str] = Field(None, pattern=r"^(P[0-2]|L(10|11|[1-9]))$")
    # metadata carries the L5 task status — the single most important field
    # to update: an executor marks a task done by writing
    # {"status": "done"}.  The model did not declare it, so the validator
    # STRIPPED it before the handler ran and the update failed with
    # "no updatable fields provided" — the task could never be completed
    # and stayed in pending_tasks forever, breaking the task loop.
    metadata: Optional[Union[str, Dict[str, Any]]] = Field(
        None, description="Metadata JSON (string or object) — L5 status lives here")
    # Caller identity.  Required by the ownership gate — without it the
    # validator strips the caller and the gate silently never fires.
    # It is deliberately NOT written to the row: ownership changes must go
    # through an explicit re-assignment, not a casual update.
    agent_name: Optional[str] = Field(None, max_length=200)


class VectorSearchInput(BaseModel):
    query: str = Field(..., min_length=1, max_length=5000)
    top_k: int = Field(10, ge=1, le=500)
    category: Optional[str] = Field(None, description="Filter by category")
    level: Optional[str] = Field(None, description="Filter by level")
    project: Optional[str] = Field(None, description="Filter by project")


class RecordInteractionInput(BaseModel):
    """memall_read action=record_interaction — feed the learning-to-rank profiles.

    Note: ``action`` is the MCP routing field (always "record_interaction"),
    so the interaction kind is carried in ``interaction_type``.  ``action`` is
    still accepted as a legacy alias, hence no pattern on it — the routing
    validator only guarantees it equals the tool action name.
    """
    query: str = Field(..., min_length=1, max_length=5000)
    # Optional for query-only impressions; required for click/dwell/copy/skip.
    memory_id: Optional[int] = Field(None, ge=1)
    interaction_type: str = Field(
        "click", pattern=r"^(click|dwell|impression|copy|skip)$",
        description="Kind of interaction (action is reserved for MCP routing)",
    )
    # Backwards-compatible alias for older callers.  Carries the MCP routing
    # value ("record_interaction") on the normal path, so it is validated
    # against the interaction vocabulary *after* routing in the handler.
    action: Optional[str] = Field(None, max_length=50)
    dwell_ms: int = Field(0, ge=0, le=3600000)
    rank_position: Optional[int] = Field(None, ge=1, le=500)
    agent_name: Optional[str] = Field(None, max_length=200)
    owner: Optional[str] = Field(None, max_length=200)
    session_id: Optional[str] = Field(None, max_length=200)

    def resolved_type(self) -> str:
        """Interaction kind, ignoring the MCP routing value in ``action``."""
        candidate = self.interaction_type or self.action or "click"
        if candidate in ("click", "dwell", "impression", "copy", "skip"):
            return candidate
        return "click"


class UnifiedSearchInput(BaseModel):
    """search / chat / hybrid_search — the unified-search engine router."""
    query: str = Field(..., min_length=1, max_length=5000)
    top_k: int = Field(10, ge=1, le=500)
    mode: str = Field("auto", pattern=r"^(auto|direct|fts5|vector|hybrid)$")
    rrf_k: int = Field(60, ge=1, le=200)
    category: Optional[str] = Field(None, max_length=100)
    level: Optional[str] = Field(None, pattern=r"^(P[0-2]|L(10|11|[1-9]))$")
    owner: Optional[str] = Field(None, max_length=200)
    project: Optional[str] = Field(None, description="Filter by project")
    rerank: bool = False
    # Viewer identity — used for trust filtering, personalisation and
    # context-aware boosting.
    agent_name: Optional[str] = Field(None, max_length=200)
    viewer: Optional[str] = Field(None, max_length=200)

    # ── Query enhancement layer (see memall/search/enhancements.py) ──
    # Facet fields: list of column names, or "auto" for config defaults.
    # Unknown columns are dropped against a whitelist server-side.
    facet_fields: Optional[List[str]] = Field(
        None, max_length=8,
        description="Fields to aggregate as facets: level|category|project|agent_name|owner|stream|visibility|memory_status|confidence, or ['auto']",
    )
    facet_fields_str: Optional[str] = Field(
        None, max_length=200,
        description="Comma-separated alternative to facet_fields (for clients that cannot send arrays)",
    )
    expand_query: bool = Field(False, description="Expand the query with synonyms/query history")
    suggest: bool = Field(False, description="Return query completions from the corpus vocabulary")
    explain: bool = Field(False, description="Return a per-result score breakdown")
    cluster: bool = Field(False, description="Group results into topic clusters")
    personalize: bool = Field(False, description="Rank by per-agent interaction affinity")
    context_boost: bool = Field(False, description="Boost results matching the active project/category")
    # Per-session novelty demotion (requires search.seen_tracking.enabled):
    # repeat queries within one session surface unseen results lower down
    # instead of returning the identical top-k twice.  Never excludes.
    session_id: Optional[str] = Field(None, max_length=200)

    @field_validator("facet_fields")
    @classmethod
    def validate_facet_fields(cls, v):
        if v is None:
            return v
        allowed = {
            "level", "category", "project", "agent_name", "owner", "stream",
            "visibility", "memory_status", "confidence", "auto",
        }
        for f in v:
            if not isinstance(f, str) or f not in allowed:
                raise ValueError(
                    f"unsupported facet field {f!r}; allowed: {sorted(allowed)}"
                )
        return v


# ── Session tools (Phase 8) ──

class SessionStartInput(BaseModel):
    agent_name: str = Field("", max_length=200)
    auto_inject: bool = True


class SessionEndInput(BaseModel):
    session_id: str = Field(..., min_length=1, max_length=200)
    auto_extract: bool = False


class SessionSummaryInput(BaseModel):
    session_id: Optional[str] = Field(None, max_length=200)
    agent_name: Optional[str] = Field(None, max_length=200)
    limit: int = Field(5, ge=1, le=500)


class GraphInput(BaseModel):
    node_id: int = Field(..., ge=1)
    depth: int = Field(1, ge=1, le=5)
    relation_filter: Optional[str] = None


# ── Federation tools (Phase 8) ──

class FedQueryInput(BaseModel):
    query: str = ""
    agent_name: str = ""
    category: str = ""
    trust_level: str = ""
    limit: int = Field(20, ge=1, le=500)


class FedPublishInput(BaseModel):
    memory_id: int = Field(..., ge=1)
    source_agent: str = ""
    trust_level: str = Field("family", pattern=r"^(trusted|family|shared|public)$")
    category: str = ""


class FedConflictsInput(BaseModel):
    limit: int = Field(20, ge=1, le=500)


class RecapInput(BaseModel):
    """memall_read action=recap — post-compaction context restore (read-only)."""
    agent_name: Optional[str] = Field(None, max_length=200)
    limit: int = Field(5, ge=1, le=50)


class FedInjectInput(BaseModel):
    agent_name: str = Field(..., min_length=1, max_length=200)


class FedExtractInput(BaseModel):
    session_id: str = Field(..., min_length=1, max_length=200)


class FedDeliverInput(BaseModel):
    """federation deliver — hub push to a target agent."""
    target_agent: str = Field(..., min_length=1, max_length=200)
    content: str = Field(..., min_length=1, max_length=10000)
    event_type: str = Field("hub_push", max_length=100)
    category: str = Field("reflection", max_length=100)
    source: str = Field("hub", max_length=200)


# ── Forgetting (Phase 11) ──
# memall_write forget re-dispatches on sub_action: the handler receives
# action=<sub_action>, so the vocabulary constraint lives on sub_action.
# The handler defaults missing sub_action to "expired" — mirrored here.

class ForgetInput(BaseModel):
    sub_action: str = Field("expired", pattern=r"^(expired|low_value|review|stats|all)$")
    # Canonical field name (``sub_action`` is the deprecated alias kept for
    # older callers; both are accepted and the handler prefers this one).
    forget_sub_action: Optional[str] = Field(
        None, pattern=r"^(expired|low_value|review|stats|all)$"
    )
    days: int = Field(90, ge=1, le=3650)
    agent_name: Optional[str] = Field(None, max_length=200)
    # Explicit opt-in for irreversible deletion (see manage.handle_forget).
    confirm: bool = False


# ── Security (Phase 13) ──
# memall_system security re-dispatches on sub_action (no handler default
# → required, so bare calls fail with a structured error, not an opaque one).

class SecurityInput(BaseModel):
    # Canonical field is ``security_sub_action``; ``sub_action`` is the
    # deprecated alias (kept so older callers keep working).
    security_sub_action: Optional[str] = Field(
        None, pattern=r"^(audit|permit|check|score|list)$"
    )
    sub_action: Optional[str] = Field(
        None, pattern=r"^(audit|permit|check|score|list)$"
    )
    agent_name: Optional[str] = Field(None, max_length=200)
    level: Optional[str] = Field(None, pattern=r"^(public|trusted|private)$")
    requester: Optional[str] = Field(None, max_length=200)
    target: Optional[str] = Field(None, max_length=200)


# ── Ops (Phase 14) ──
# memall_write ops re-dispatches on sub_action (no handler default → required).

class OpsInput(BaseModel):
    # Either the canonical ``ops_sub_action`` or the deprecated ``sub_action``
    # alias must be present; the handler enforces the "at least one" rule so
    # the error message can name both spellings.
    sub_action: Optional[str] = Field(
        None, pattern=r"^(merge|split|tag|batch_tag|archive|restore|dedup|undo)$"
    )
    # Canonical field name (``sub_action`` is the deprecated alias kept for
    # older callers; both are accepted and the handler prefers this one).
    ops_sub_action: Optional[str] = Field(
        None, pattern=r"^(merge|split|tag|batch_tag|archive|restore|dedup|undo)$"
    )
    source_id: Optional[int] = Field(None, ge=1)
    target_id: Optional[int] = Field(None, ge=1)
    memory_id: Optional[int] = Field(None, ge=1)
    delimiter: Optional[str] = None
    separator: Optional[str] = None
    tags: Optional[List[str]] = None
    mode: Optional[str] = Field(None, pattern=r"^(add|set|remove)$")
    agent_name: Optional[str] = Field(None, max_length=200)
    category: Optional[str] = Field(None, max_length=100)
    level: Optional[str] = Field(None, max_length=20)
    tags_include: Optional[List[str]] = None
    before: Optional[str] = None
    after: Optional[str] = None
    days: Optional[int] = Field(None, ge=1, le=3650)
    threshold: float = Field(0.9, ge=0.0, le=1.0)
    max_pairs: int = Field(5000, ge=1, le=100000)
    max_memories: int = Field(10000, ge=2, le=50000)
    length_ratio_max: float = Field(5.0, ge=1.0, le=100.0)
    dry_run: bool = False
    # Explicit opt-in for destructive ops (merge/split/dedup/archive).
    confirm: bool = False
    op_id: Optional[int] = Field(None, ge=1)


# ── Adaptive (Phase 12) ──
# memall_system adaptive re-dispatches on sub_action (no handler default).

class AdaptiveInput(BaseModel):
    # Canonical field is ``adaptive_sub_action``; ``sub_action`` is the
    # deprecated alias (kept so older callers keep working).
    adaptive_sub_action: Optional[str] = Field(
        None, pattern=r"^(clean|index|distill|all|report)$"
    )
    sub_action: Optional[str] = Field(
        None, pattern=r"^(clean|index|distill|all|report)$"
    )
    agent_name: Optional[str] = Field(None, max_length=200)


# ── Gateway (Phase 15) ──
# memall_system gateway re-dispatches on sub_action (no handler default).

class GatewayInput(BaseModel):
    # Canonical field is ``gateway_sub_action``; ``sub_action`` is the
    # deprecated alias (kept so older callers keep working).
    gateway_sub_action: str = Field(
        ..., pattern=r"^(status|start|stop|export|import|discover|pair|peers|federated)$"
    )
    sub_action: Optional[str] = Field(
        None, pattern=r"^(status|start|stop|export|import|discover|pair|peers|federated)$"
    )
    port: Optional[int] = Field(None, ge=1024, le=65535)
    agent_name: Optional[str] = Field(None, max_length=200)
    file_path: Optional[str] = Field(None, max_length=500)
    address: Optional[str] = Field(None, max_length=200)
    query: Optional[str] = Field(None, max_length=5000)
    max_peers: Optional[int] = Field(None, ge=1, le=100)


# ── DB Maintenance (Phase 21) ──
# memall_system db re-dispatches on sub_action (no handler default).

class DBInput(BaseModel):
    # Canonical field is ``db_sub_action``; ``sub_action`` is the deprecated
    # alias (kept so older callers keep working).
    db_sub_action: Optional[str] = Field(
        None,
        pattern=r"^(optimize|stats|vacuum|archive_stats|archive_vacuum|"
                r"backfill_thread|backfill_project|backfill_tags|dedupe_l9|dedupe_l10)$",
    )
    sub_action: Optional[str] = Field(
        None,
        pattern=r"^(optimize|stats|vacuum|archive_stats|archive_vacuum|"
                r"backfill_thread|backfill_project|backfill_tags|dedupe_l9|dedupe_l10)$",
    )
    dry_run: bool = False
    confirm: bool = False
    archive_corrupt: bool = True
    # Row cap for backfill_* preview/apply runs (0 = no cap).
    limit: Optional[int] = Field(None, ge=0, le=100000)


# ── Discussion tools (Phase 1.5) ──


class DiscussionCreateInput(BaseModel):
    title: str = Field(..., min_length=1, max_length=500)
    background: str = Field("", max_length=10000)
    options: Optional[list] = None
    participants: list = Field(..., min_length=1)
    open_questions: Optional[list] = None
    recommendation: str = Field("", max_length=5000)
    action_items: Optional[list] = None
    timeout_hours: int = Field(24, ge=1, le=720)
    convergence_rule: str = Field("unanimous", pattern=r"^(unanimous|majority|any)$")


class DiscussionRespondInput(BaseModel):
    discussion_id: int = Field(..., ge=1)
    agent_name: str = Field(..., min_length=1, max_length=200)
    # Dual-vocabulary acceptance: the MCP schema advertised agree/disagree/pass
    # while the convergence layer only recognizes confirm/reject/abstain —
    # stances were silently counted as "other" and consensus never converged.
    # We now accept both vocabularies and normalize to the canonical one.
    stance: str = Field(..., pattern=r"^(agree|disagree|pass|abstain|confirm|reject)$")
    # Field name clash: the JSON-RPC wrapper is also called "arguments", so the
    # reasoning text previously read arguments["arguments"] — a key that only
    # existed when the client double-wrapped.  Accept both spellings and keep
    # the schema name "arguments" for backwards compatibility.
    arguments: str = Field("", max_length=10000)
    round_num: int = Field(1, ge=1, le=100)

    @field_validator("stance", mode="before")
    @classmethod
    def _normalize_stance(cls, v):
        if not isinstance(v, str):
            raise ValueError("stance must be a string")
        s = v.strip().lower()
        # MCP schema vocabulary → convergence-layer vocabulary
        return {"agree": "confirm", "disagree": "reject", "pass": "abstain"}.get(s, s)


class DiscussionStatusInput(BaseModel):
    discussion_id: Optional[int] = Field(None, ge=1)


# ── Trace & Identity ──


class TraceInput(BaseModel):
    memory_id: int = Field(..., ge=1)


class IdentityInput(BaseModel):
    # Optional: empty falls back to identity.human_owner (see handle_identity).
    agent_name: str = Field("", max_length=200)


# ── Reflection interaction ──


class ReflectInteractInput(BaseModel):
    memory_id: int = Field(..., ge=1)
    action: str = Field(..., pattern=r"^(agree|disagree|probe)$")
    context: str = ""


class ReflectInteractMCPInput(BaseModel):
    """memall_system reflect — sub_action carries the interaction verb.
    Required: reflect.handle records the verb verbatim, so an empty one would
    be silently persisted as a meaningless interaction.

    Canonical field is ``reflect_sub_action``; ``sub_action`` is the
    deprecated alias (kept so older callers keep working)."""
    reflect_sub_action: Optional[str] = Field(
        None, pattern=r"^(agree|disagree|probe)$"
    )
    sub_action: Optional[str] = Field(
        None, pattern=r"^(agree|disagree|probe)$"
    )
    memory_id: int = Field(..., ge=1)
    context: str = ""
    # Declared so the identity survives validation: the auto-capture hook reads
    # it to attribute the resulting memory.  Without the field the validator
    # dropped it and every reflect-derived memory landed under "system".
    agent_name: Optional[str] = Field(None, max_length=200)


# ── Onboarding ──
# NOTE: the dispatch verb lives in onboarding_sub_action (``sub_action`` is a
# deprecated alias).  No handler default → required.

class OnboardingInput(BaseModel):
    onboarding_sub_action: Optional[str] = Field(
        None, pattern=r"^(status|start|reset|submit_step|skip)$"
    )
    sub_action: Optional[str] = Field(
        None, pattern=r"^(status|start|reset|submit_step|skip)$"
    )
    step: Optional[int] = Field(None, ge=1, le=5)
    input_data: Optional[dict] = None
    user_id: str = "default"


# ── Pipeline ──


class RunPipelineInput(BaseModel):
    include_reflect: bool = True
    include_distill: bool = True
    include_integrate: bool = True
    include_persona: bool = True
    timeout: int = Field(300, ge=30, le=3600)


# ── Distill ──
# memall_system distill re-dispatches on sub_action (handler defaults "list")


class DistillListInput(BaseModel):
    """memall_system distill sub_action=list — list groups needing summaries."""
    sub_action: str = Field("list", pattern=r"^list$")
    distill_sub_action: Optional[str] = Field(None, pattern=r"^list$")
    limit: int = Field(10, ge=1, le=100)


class DistillSummarizeInput(BaseModel):
    """memall_system distill sub_action=summarize — write narrative summary."""
    sub_action: str = Field("summarize", pattern=r"^summarize$")
    distill_sub_action: Optional[str] = Field(None, pattern=r"^summarize$")
    group_id: int = Field(..., ge=1)
    summary: str = Field(..., min_length=10, max_length=4000)
    insight: Optional[str] = Field(None, max_length=2000)
    gap: Optional[str] = Field(None, max_length=2000)
    next: Optional[str] = Field(None, max_length=2000)


# ── Agent Hub bridge ──


class HubConnectInput(BaseModel):
    group_id: Optional[int] = Field(None, ge=1)


class HubSyncInput(BaseModel):
    direction: str = Field("bidirectional", pattern=r"^(bidirectional|to_hub|from_hub)$")
    limit: int = Field(20, ge=1, le=500)


class ImportInput(BaseModel):
    """memall_system import — bulk import from jsonl/csv/mem0 files."""
    path: str = Field(..., min_length=1, max_length=1000)
    format: str = Field("jsonl", pattern=r"^(jsonl|json|csv|mem0)$")
    agent_name: str = Field("imported", max_length=200)


class IndexRebuildInput(BaseModel):
    force: bool = False


class DigestInput(BaseModel):
    """digest/hot take no parameters; this model only guards payload shape."""
    model_config = {"extra": "ignore"}


# ── Hooks ──


class HooksRecentInput(BaseModel):
    n: int = Field(10, ge=1, le=100)


class MsgInput(BaseModel):
    """memall_msg — real-time delivery over the memall-bus.

    The L5 task card (memories table) stays the single source of truth; the
    bus carries a POINTER (memory_id), not the task body.  ``agent_name`` is
    not just bookkeeping: the bus derives the SENDER from the authenticated
    connection, so a client cannot claim to be someone else.
    """
    action: str = Field("inbox", pattern=r"^(send|inbox|ack|status)$")
    agent_name: str = Field("", max_length=200)
    to: str = Field("", max_length=200)
    payload: Optional[Dict[str, Any]] = None
    type: str = Field("msg", max_length=50)
    ids: Optional[List[int]] = None
