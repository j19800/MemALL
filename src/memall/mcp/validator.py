# mcp/validator.py - Input validation using Pydantic models
import json
from typing import Any, Dict, Tuple

from pydantic import BaseModel, ValidationError

from memall.mcp.models import (
    AskInput, CaptureInput, ConnectInput, DiscussionCreateInput,
    DiscussionRespondInput, DiscussionStatusInput, FedConflictsInput,
    FedExtractInput, FedInjectInput, FedPublishInput, FedQueryInput,
    ForgetInput, GatewayInput, HubConnectInput, HubSyncInput,
    IdentityInput, OpsInput, OnboardingInput, PersonaInput,
    PersonaProfileInput, RetrieveInput, RunPipelineInput,
    SmartStoreInput, StoreBatchInput, TimelineInput, TraceInput,
    TraverseInput, UpdateInput, VectorSearchInput,
)

# Consolidated tools dispatch by "action" field — map (tool_name, action) → model
_ACTION_MODELS: dict[tuple[str, str], type[BaseModel]] = {
    ("memall_write", "capture"): CaptureInput,
    ("memall_write", "smart_store"): SmartStoreInput,
    ("memall_write", "store_batch"): StoreBatchInput,
    ("memall_write", "update"): UpdateInput,
    ("memall_write", "forget"): ForgetInput,
    ("memall_write", "ops"): OpsInput,
    ("memall_write", "connect"): ConnectInput,
    ("memall_read", "retrieve"): RetrieveInput,
    ("memall_read", "vector_search"): VectorSearchInput,
    ("memall_read", "traverse"): TraverseInput,
    ("memall_read", "timeline"): TimelineInput,
    ("memall_read", "trace"): TraceInput,
    ("memall_persona", "persona"): PersonaInput,
    ("memall_persona", "profile"): PersonaProfileInput,
    ("memall_persona", "ask"): AskInput,
    ("memall_persona", "identity"): IdentityInput,
    ("memall_discussion", "create"): DiscussionCreateInput,
    ("memall_discussion", "respond"): DiscussionRespondInput,
    ("memall_discussion", "status"): DiscussionStatusInput,
    ("memall_federation", "query"): FedQueryInput,
    ("memall_federation", "publish"): FedPublishInput,
    ("memall_federation", "conflicts"): FedConflictsInput,
    ("memall_federation", "inject"): FedInjectInput,
    ("memall_federation", "extract"): FedExtractInput,
    ("memall_system", "gateway"): GatewayInput,
    ("memall_system", "pipeline"): RunPipelineInput,
    ("memall_system", "onboarding"): OnboardingInput,
    ("memall_system", "hub_connect"): HubConnectInput,
    ("memall_system", "hub_sync"): HubSyncInput,
}

# Keep the original TOOL_VALIDATORS for fallback (tool-level models)
TOOL_VALIDATORS: dict[str, type[BaseModel]] = {}


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
    # Try action-specific model first
    action = params.get("action", "")
    model_class = _ACTION_MODELS.get((tool_name, action))

    # Fall back to tool-level model
    if model_class is None:
        model_class = TOOL_VALIDATORS.get(tool_name)

    if model_class is None:
        return True, params, ""

    try:
        validated = model_class(**params)
        return True, validated.model_dump(exclude_unset=True), ""
    except ValidationError as e:
        return False, None, str(e)


def format_validation_error(tool_name: str, error_msg: str) -> str:
    """Return a JSON string with a structured validation error."""
    return json.dumps(
        {
            "error": f"Input validation failed for tool '{tool_name}'",
            "detail": error_msg,
        },
        ensure_ascii=False,
    )
