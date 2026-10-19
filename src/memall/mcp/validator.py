# mcp/validator.py - Input validation using Pydantic models
#
# Design notes (v0.1.66 audit):
# - Aggregated tools dispatch on top-level "action"; several actions then
#   re-dispatch on "sub_action" (memall_write forget/ops, memall_system
#   distill/gateway/db/security/adaptive/reflect/onboarding). The handler
#   rewrites args["action"] = sub_action BEFORE dispatch, so the model chosen
#   here must accept the SUB-action vocabulary in its "action" field.
# - Therefore for sub_action-driven actions we validate the pair
#   (tool, action, sub_action): the sub_action decides the model, and the
#   model's action pattern matches that sub-action. Missing sub_action is
#   fine too — the model defaults mirror the handler defaults.
# - Every registered action is covered here; unknown (tool, action) pairs now
#   FAIL with a structured error listing valid actions instead of silently
#   passing through to an opaque handler ValueError.
import json
from typing import Any, Dict, List, Tuple

from pydantic import BaseModel, ValidationError

from memall.mcp.models import (
    AdaptiveInput, AskInput, BatchCaptureInput, BatchSearchInput, CaptureInput, ConnectInput, DBInput,
    DigestInput, DiscussionCreateInput, DiscussionRespondInput,
    DiscussionStatusInput, DistillListInput, DistillSummarizeInput,
    FedConflictsInput, FedDeliverInput, FedExtractInput, FedInjectInput,
    FedPublishInput, FedQueryInput, ForgetInput, GatewayInput,
    HubConnectInput, HubSyncInput, IdentityInput, ImportInput,
    IndexRebuildInput, OnboardingInput, OpsInput, PersonaForesightInput,
    PersonaInput, PersonaProfileInput, PersonaSearchInput,
    ProfilePreloadInput, ReflectInteractMCPInput, RetrieveInput,
    RecordInteractionInput, RunPipelineInput, SecurityInput,
    SessionEndInput, SessionStartInput,
    SessionSummaryInput, SmartStoreInput, StoreBatchInput, TimelineInput,
    TraceInput, TraverseInput, UnifiedSearchInput, UpdateInput,
    VectorSearchInput, HooksRecentInput,
)

# Consolidated tools dispatch by "action" field — map (tool_name, action) → model
_ACTION_MODELS: dict[tuple[str, str], type[BaseModel]] = {
    # memall_write — direct actions
    ("memall_write", "capture"): CaptureInput,
    ("memall_write", "smart_store"): SmartStoreInput,
    ("memall_write", "store_batch"): StoreBatchInput,
    ("memall_write", "update"): UpdateInput,
    ("memall_write", "connect"): ConnectInput,
    ("memall_write", "batch_capture"): BatchCaptureInput,
    # memall_write — sub_action-driven (handler rewrites action=sub_action)
    ("memall_write", "forget"): ForgetInput,
    ("memall_write", "ops"): OpsInput,
    # memall_write — search-interaction telemetry (writes to search_interactions)
    ("memall_write", "record_interaction"): RecordInteractionInput,
    # memall_write — quick capture aliases
    ("memall_write", "quick"): CaptureInput,
    ("memall_write", "记一下"): CaptureInput,
    ("memall_write", "快速记"): CaptureInput,
    ("memall_write", "随手记"): CaptureInput,

    # memall_read
    ("memall_read", "retrieve"): RetrieveInput,
    ("memall_read", "search"): UnifiedSearchInput,
    ("memall_read", "unified_search"): UnifiedSearchInput,
    ("memall_read", "chat"): UnifiedSearchInput,
    ("memall_read", "nl_search"): UnifiedSearchInput,
    ("memall_read", "自然语言搜索"): UnifiedSearchInput,
    ("memall_read", "vector_search"): VectorSearchInput,
    ("memall_read", "hybrid_search"): UnifiedSearchInput,
    ("memall_read", "batch_search"): BatchSearchInput,
    ("memall_read", "record_interaction"): RecordInteractionInput,
    ("memall_read", "trace"): TraceInput,
    ("memall_read", "traverse"): TraverseInput,
    ("memall_read", "timeline"): TimelineInput,
    ("memall_read", "fed_query"): FedQueryInput,
    ("memall_read", "fed_conflicts"): FedConflictsInput,

    # memall_persona
    ("memall_persona", "persona"): PersonaInput,
    ("memall_persona", "persona_profile"): PersonaProfileInput,
    ("memall_persona", "identity"): IdentityInput,
    ("memall_persona", "ask"): AskInput,
    ("memall_persona", "profile_preload"): ProfilePreloadInput,
    ("memall_persona", "预加载画像"): ProfilePreloadInput,
    ("memall_persona", "加载画像"): ProfilePreloadInput,
    ("memall_persona", "画像"): ProfilePreloadInput,
    ("memall_persona", "profile_search"): PersonaSearchInput,
    ("memall_persona", "搜画像"): PersonaSearchInput,
    ("memall_persona", "搜索画像"): PersonaSearchInput,
    ("memall_persona", "查画像"): PersonaSearchInput,
    ("memall_persona", "查找画像"): PersonaSearchInput,
    ("memall_persona", "foresight"): PersonaForesightInput,
    ("memall_persona", "前瞻"): PersonaForesightInput,
    ("memall_persona", "预测"): PersonaForesightInput,
    ("memall_persona", "猜我需要"): PersonaForesightInput,
    ("memall_persona", "下一步"): PersonaForesightInput,

    # memall_discussion
    ("memall_discussion", "create"): DiscussionCreateInput,
    ("memall_discussion", "respond"): DiscussionRespondInput,
    ("memall_discussion", "status"): DiscussionStatusInput,

    # memall_federation
    ("memall_federation", "query"): FedQueryInput,
    ("memall_federation", "publish"): FedPublishInput,
    ("memall_federation", "conflicts"): FedConflictsInput,
    ("memall_federation", "inject"): FedInjectInput,
    ("memall_federation", "extract"): FedExtractInput,
    ("memall_federation", "deliver"): FedDeliverInput,

    # memall_system — direct actions
    ("memall_system", "run_pipeline"): RunPipelineInput,
    ("memall_system", "hub_connect"): HubConnectInput,
    ("memall_system", "hub_sync"): HubSyncInput,
    ("memall_system", "session_start"): SessionStartInput,
    ("memall_system", "session_end"): SessionEndInput,
    ("memall_system", "session_summary"): SessionSummaryInput,
    ("memall_system", "index_rebuild"): IndexRebuildInput,
    ("memall_system", "import"): ImportInput,
    # digest/hot take no parameters — validate empty payloads only
    ("memall_system", "digest"): DigestInput,
    ("memall_system", "每日摘要"): DigestInput,
    ("memall_system", "日报"): DigestInput,
    ("memall_system", "总结"): DigestInput,
    ("memall_system", "hot"): DigestInput,
    ("memall_system", "热门"): DigestInput,
    ("memall_system", "热点"): DigestInput,
    ("memall_system", "热榜"): DigestInput,
    # memall_system — sub_action-driven (handler rewrites action=sub_action)
    ("memall_system", "distill"): DistillListInput,
    ("memall_system", "gateway"): GatewayInput,
    ("memall_system", "db"): DBInput,
    ("memall_system", "security"): SecurityInput,
    ("memall_system", "adaptive"): AdaptiveInput,
    ("memall_system", "onboarding"): OnboardingInput,
    ("memall_system", "reflect"): ReflectInteractMCPInput,
}

# Tool-level fallback (kept for backwards compatibility; empty is safe now
# because _ACTION_MODELS covers every registered action).
TOOL_VALIDATORS: dict[str, type[BaseModel]] = {}

# Tools that carry no action routing at all — validate the whole payload
# with a single tool-level model.
_TOOL_ONLY_MODELS: dict[str, type[BaseModel]] = {
    "memall_hooks_recent": HooksRecentInput,
}


def _pick_model(tool_name: str, params: Dict[str, Any]):
    """Choose the validation model for a (tool, action, sub_action) triple."""
    action = params.get("action", "")
    model_class = _ACTION_MODELS.get((tool_name, action))
    if model_class is None:
        return None

    # Sub-action-driven pairs: refine the model by sub_action where the
    # vocabulary differs per sub-action (distill list vs summarize).
    # Accept both the canonical ``<action>_sub_action`` field and the
    # deprecated ``sub_action`` alias.
    sub = params.get("sub_action")
    if tool_name == "memall_system" and action == "distill":
        canonical = params.get("distill_sub_action")
        effective = canonical if isinstance(canonical, str) and canonical else sub
        if isinstance(effective, str) and effective:
            return DistillSummarizeInput if effective == "summarize" else DistillListInput

    return model_class


def validate_tool_input(
    tool_name: str, params: Dict[str, Any]
) -> Tuple[bool, Any, str]:
    """Validate tool input using Pydantic model.

    Args:
        tool_name: The MCP tool name (e.g. "memall_write").
        params: Raw parameters dict from the MCP request arguments.

    Returns:
        Tuple of (is_valid, validated_data_or_None, error_message).
    """
    model_class = _pick_model(tool_name, params)

    # Action-less tools use a single tool-level model.
    if model_class is None and tool_name in _TOOL_ONLY_MODELS:
        model_class = _TOOL_ONLY_MODELS[tool_name]

    # Unknown action: reject with a structured error instead of silently
    # passing through to the handler's opaque ValueError.
    if model_class is None:
        action = params.get("action", "")
        known = sorted({a for (t, a) in _ACTION_MODELS if t == tool_name})
        return False, None, (
            f"unknown action '{action}' for tool '{tool_name}'. "
            f"Valid actions: {', '.join(known)}"
        )

    try:
        validated = model_class(**params)
        validated_data = validated.model_dump(exclude_unset=True)
        # Preserve routing fields the model doesn't declare (some models
        # don't declare action/sub_action as fields).
        for key in ("action", "sub_action"):
            if key not in validated_data and key in params:
                validated_data[key] = params[key]
        return True, validated_data, ""
    except ValidationError as e:
        return False, None, _humanize_validation_error(e)


def _humanize_validation_error(exc) -> str:
    """Turn a Pydantic ValidationError into one actionable sentence.

    Raw Pydantic output ("1 validation error for OpsInput / sub_action /
    String should match pattern '^(merge|...)$' / type=string_pattern_mismatch,
    input_value='stats'") is unreadable for an LLM caller and never says what
    to do instead.  Rewrite it as ``field: what's wrong (got 'x') [expected: ...]``
    so the caller can fix the call in one round trip.
    """
    lines: List[str] = []
    try:
        for err in exc.errors():
            loc = ".".join(str(p) for p in err.get("loc", ())) or "input"
            msg = str(err.get("msg", "invalid value"))
            got = err.get("input", None)
            # Pydantic's pattern errors carry the regex in ctx.pattern
            ctx = err.get("ctx") or {}
            expected = ctx.get("pattern")
            if expected and msg.startswith("String should match pattern"):
                allowed = expected.strip("^$()").replace("|", ", ")
                msg = f"must be one of: {allowed}"
            elif msg.startswith("Field required"):
                msg = "is required"
            elif msg.startswith("String should have at least"):
                msg = f"must not be empty ({msg.split('at least ')[-1]})"
            elif msg.startswith("String should have at most"):
                msg = f"is too long ({msg.split('at most ')[-1]})"
            elif msg.startswith("Input should be a valid"):
                msg = msg.replace("Input should be a valid ", "must be a valid ")
            suffix = ""
            # For a *missing* required field Pydantic echoes the whole payload
            # as "input", which is noise — only show the value when it was
            # actually supplied (and is short enough to be readable).
            if got not in (None, "") and not isinstance(got, dict):
                shown = repr(got)
                suffix = f" (got {shown[:60]})"
            lines.append(f"{loc}: {msg}{suffix}")
    except Exception:
        return str(exc)[:500]
    return "; ".join(lines)[:500] if lines else str(exc)[:500]


def format_validation_error(tool_name: str, error_msg: str) -> str:
    """Return a JSON string with a structured validation error."""
    return json.dumps(
        {
            "error": f"Input validation failed for tool '{tool_name}'",
            "detail": error_msg,
        },
        ensure_ascii=False,
    )
