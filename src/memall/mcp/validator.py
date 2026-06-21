# mcp/validator.py - Input validation using Pydantic models
import json
from typing import Any, Dict, Tuple
from pydantic import ValidationError

from .models import (
    CaptureInput,
    RetrieveInput,
    ConnectInput,
    TraverseInput,
    TimelineInput,
    PersonaInput,
    PersonaProfileInput,
    AskInput,
    SmartStoreInput,
    StoreBatchInput,
    UpdateInput,
    VectorSearchInput,
    SessionStartInput,
    SessionEndInput,
    SessionSummaryInput,
    GraphInput,
    FedQueryInput,
    FedPublishInput,
    FedConflictsInput,
    FedInjectInput,
    FedExtractInput,
    ForgetInput,
    SecurityInput,
    OpsInput,
    AdaptiveInput,
    GatewayInput,
    DBInput,
    DiscussionCreateInput,
    DiscussionRespondInput,
    DiscussionStatusInput,
    TraceInput,
    IdentityInput,
    ReflectInteractInput,
    OnboardingInput,
    RunPipelineInput,
    HubConnectInput,
    HubSyncInput,
)

# Mapping from tool name to Pydantic model class
TOOL_VALIDATORS: Dict[str, Any] = {
    # Core tools
    "capture": CaptureInput,
    "retrieve": RetrieveInput,
    "connect": ConnectInput,
    "traverse": TraverseInput,
    "timeline": TimelineInput,
    # Persona & Ask
    "memall_persona": PersonaInput,
    "memall_persona_profile": PersonaProfileInput,
    "memall_ask": AskInput,
    # Smart / Batch / Update / VectorSearch
    "memall_smart_store": SmartStoreInput,
    "memall_store_batch": StoreBatchInput,
    "memall_update": UpdateInput,
    "memall_vector_search": VectorSearchInput,
    # Session tools
    "memall_session_start": SessionStartInput,
    "memall_session_end": SessionEndInput,
    "memall_session_summary": SessionSummaryInput,
    "memall_graph": GraphInput,
    # Federation tools
    "memall_fed_query": FedQueryInput,
    "memall_fed_publish": FedPublishInput,
    "memall_fed_conflicts": FedConflictsInput,
    "memall_fed_inject": FedInjectInput,
    "memall_fed_extract": FedExtractInput,
    # Phase 11-21 tools
    "memall_forget": ForgetInput,
    "memall_security": SecurityInput,
    "memall_ops": OpsInput,
    "memall_adaptive": AdaptiveInput,
    "memall_gateway": GatewayInput,
    "memall_db": DBInput,
    # Discussion tools
    "memall_discussion_create": DiscussionCreateInput,
    "memall_discussion_respond": DiscussionRespondInput,
    "memall_discussion_status": DiscussionStatusInput,
    # Trace & Identity
    "memall_trace": TraceInput,
    "memall_identity": IdentityInput,
    # Reflection interaction
    "memall_reflect_interact": ReflectInteractInput,
    # Onboarding
    "memall_onboarding": OnboardingInput,
    # Pipeline
    "memall_run_pipeline": RunPipelineInput,
    # Agent Hub bridge
    "memall_hub_connect": HubConnectInput,
    "memall_hub_sync": HubSyncInput,
}


def validate_tool_input(
    tool_name: str, params: Dict[str, Any]
) -> Tuple[bool, Any, str]:
    """Validate tool input using Pydantic model.

    Args:
        tool_name: The MCP tool name (e.g. "capture", "memall_persona").
        params: Raw parameters dict from the MCP request arguments.

    Returns:
        Tuple of (is_valid, validated_data_or_None, error_message).
        - If a validator exists and validation passes: (True, model_dump_dict, "")
        - If a validator exists and validation fails: (False, None, error_string)
        - If no validator is registered for the tool: (True, original_params, "")
    """
    model_class = TOOL_VALIDATORS.get(tool_name)
    if model_class is None:
        # No validator registered — pass through unchanged
        return True, params, ""

    try:
        validated = model_class(**params)
        return True, validated.model_dump(), ""
    except ValidationError as e:
        return False, None, str(e)


def format_validation_error(tool_name: str, error_msg: str) -> str:
    """Return a JSON string with a structured validation error.

    This is a convenience helper for adapter.py to produce consistent
    error responses.
    """
    return json.dumps(
        {
            "error": f"Input validation failed for tool '{tool_name}'",
            "detail": error_msg,
        },
        ensure_ascii=False,
    )