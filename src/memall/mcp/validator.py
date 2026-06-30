# mcp/validator.py - Input validation using Pydantic models
import json
from typing import Any, Dict, Tuple
from pydantic import ValidationError

# Validation is handled by the individual action handlers within each
# consolidated tool. No centralized tool→model mapping is needed.
TOOL_VALIDATORS: Dict[str, Any] = {}


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
