import json
import logging

from memall.mcp.validator import validate_tool_input, format_validation_error
from memall.mcp.shared import ensure_session_started, consume_session_note, run_intercept
from memall.mcp.registry import registry
from memall.mcp.hooks import HookRegistry, HOOK_PRE_TOOL_USE, HOOK_POST_TOOL_USE, HOOK_POST_TOOL_USE_FAILURE

logger = logging.getLogger(__name__)


def _intercept(tool_name: str, arguments: dict, result_str: str = "") -> None:
    """Backward compat wrapper — result_str is unused."""
    run_intercept(tool_name, arguments)

# Import tools to register them (side-effect)
from memall.mcp import tools  # noqa: F401
# Import built-in hooks (side-effect registers them)
from memall.mcp import hooks_builtin  # noqa: F401

TOOL_DEFINITIONS = registry.list_definitions()


def handle_call(tool_name: str, arguments: dict) -> str:
    is_valid, validated_data, err_msg = validate_tool_input(tool_name, arguments)
    if not is_valid:
        return format_validation_error(tool_name, err_msg)
    arguments = validated_data

    if tool_name not in ("memall_session_start", "memall_session_end", "ping", "memall_onboarding"):
        agent_name = arguments.get("agent_name", "")
        if agent_name:
            _READ_ONLY_TOOLS = frozenset({
                "retrieve", "traverse", "timeline", "memall_vector_search",
                "memall_fed_query", "memall_fed_conflicts", "memall_session_summary",
                "memall_persona", "memall_persona_profile", "memall_ask",
                "memall_identity", "memall_trace", "memall_discussion_status",
                "memall_hub_connect", "memall_db",
            })
            ensure_session_started(agent_name, auto_inject=tool_name not in _READ_ONLY_TOOLS)

    # Pre-tool-use hooks — can block the call
    pre_results = HookRegistry.dispatch(HOOK_PRE_TOOL_USE, tool_name, arguments)
    if False in pre_results:
        logger.info("Tool call blocked by hook: %s", tool_name)
        return json.dumps({"status": "blocked", "tool": tool_name, "reason": "blocked by pre_tool_use hook"})

    # Execute
    try:
        result = registry.dispatch(tool_name, arguments)
    except Exception as e:
        logger.exception("Tool call failed: %s", tool_name)
        error_result = json.dumps({"status": "error", "error": str(e), "tool": tool_name})
        HookRegistry.dispatch(HOOK_POST_TOOL_USE_FAILURE, tool_name, arguments, error=str(e))
        return error_result

    run_intercept(tool_name, arguments)

    # Post-tool-use hooks
    HookRegistry.dispatch(HOOK_POST_TOOL_USE, tool_name, arguments, result=result)
    return result
