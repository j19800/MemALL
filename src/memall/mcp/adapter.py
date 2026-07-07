import json
import logging
import time

from memall.mcp.validator import validate_tool_input, format_validation_error
from memall.mcp.shared import ensure_session_started, consume_session_note, run_intercept
from memall.mcp.registry import registry
from memall.mcp.hooks import HookRegistry, HOOK_PRE_TOOL_USE, HOOK_POST_TOOL_USE, HOOK_POST_TOOL_USE_FAILURE
from memall.core.metrics import get_metrics
from memall.core.tracer import span, reset_trace

logger = logging.getLogger(__name__)


def _intercept(tool_name: str, arguments: dict, result_str: str = "") -> None:
    """Backward compat wrapper — result_str is unused."""
    run_intercept(tool_name, arguments)

# Import tools to register them (side-effect)
from memall.mcp import tools  # noqa: F401
# Import built-in hooks (side-effect registers them)
from memall.mcp import hooks_builtin  # noqa: F401
# Load plugins so lifecycle hooks (on_capture, on_pipeline, etc.) fire
from memall.plugins.loader import load_all_plugins as _load_all_plugins
_load_all_plugins()

TOOL_DEFINITIONS = registry.list_definitions()


def handle_call(tool_name: str, arguments: dict) -> str:
    reset_trace()
    m = get_metrics()
    m.incr("tool_call_total")
    m.incr(f"tool_call.{tool_name}")
    _t0 = time.time()

    is_valid, validated_data, err_msg = validate_tool_input(tool_name, arguments)
    if not is_valid:
        m.incr("tool_call_validation_error")
        m.record_latency(f"tool.{tool_name}", (time.time() - _t0) * 1000)
        return format_validation_error(tool_name, err_msg)
    arguments = validated_data

    # Session-based tools (memall_system actions) that should not auto-start sessions
    _SESSION_SKIP_TOOLS = frozenset({"ping"})
    _SESSION_SKIP_ACTIONS = frozenset({"session_start", "session_end", "session_summary", "onboarding"})

    if tool_name not in _SESSION_SKIP_TOOLS:
        action = arguments.get("action", "")
        if action not in _SESSION_SKIP_ACTIONS:
            agent_name = arguments.get("agent_name", "")
            if agent_name:
                ensure_session_started(agent_name, auto_inject=True)

    # Pre-tool-use hooks — can block the call
    pre_results = HookRegistry.dispatch(HOOK_PRE_TOOL_USE, tool_name, arguments)
    if False in pre_results:
        logger.info("Tool call blocked by hook: %s", tool_name)
        m.incr("tool_call_blocked")
        m.record_latency(f"tool.{tool_name}", (time.time() - _t0) * 1000)
        return json.dumps({"status": "blocked", "tool": tool_name, "reason": "blocked by pre_tool_use hook"})

    # Execute (wrapped in tracing span)
    attrs = {"tool_name": tool_name, "arg_keys": list(arguments.keys())[:5]}
    try:
        with span(f"tool.{tool_name}", "tool_call", attrs):
            result = registry.dispatch(tool_name, arguments)
    except Exception as e:
        logger.exception("Tool call failed: %s", tool_name)
        m.incr("tool_call_error")
        m.incr(f"tool_error.{tool_name}")
        m.record_latency(f"tool.{tool_name}", (time.time() - _t0) * 1000)
        error_result = json.dumps({"status": "error", "error": str(e), "tool": tool_name})
        HookRegistry.dispatch(HOOK_POST_TOOL_USE_FAILURE, tool_name, arguments, error=str(e))
        return error_result

    m.record_latency(f"tool.{tool_name}", (time.time() - _t0) * 1000)
    run_intercept(tool_name, arguments)

    # Post-tool-use hooks
    HookRegistry.dispatch(HOOK_POST_TOOL_USE, tool_name, arguments, result=result)

    # Inject hook activity into the tool response so agents see async effects
    result = _inject_hook_activity(result)

    # Inject session context for read-only tools (memall_read, memall_persona)
    if tool_name in {"memall_read", "memall_persona"}:
        result = _inject_session_context(result)

    return result


def _inject_hook_activity(result_str: str) -> str:
    """Consume recent hook events and inject ``_meta.hook_activity`` into the
    JSON response.  Only works for dict-shaped results (arrays skip injection).
    """
    try:
        from memall.mcp.hook_effects import consume_recent, format_activity

        events = consume_recent()
        if not events:
            return result_str

        formatted = format_activity(events)
        if not formatted:
            return result_str

        parsed = json.loads(result_str)
        if not isinstance(parsed, dict):
            return result_str

        parsed.setdefault("_meta", {})["hook_activity"] = formatted
        return json.dumps(parsed, ensure_ascii=False)
    except Exception:
        logger.debug("hook_activity injection failed (non-fatal)", exc_info=True)
        return result_str


def _inject_session_context(result_str: str) -> str:
    """Consume the pending session note and inject ``_meta.session_context``
    into the JSON response for read-only tools.
    """
    try:
        note = consume_session_note()
        if not note:
            return result_str

        parsed = json.loads(result_str)
        if not isinstance(parsed, dict):
            return result_str

        parsed.setdefault("_meta", {})["session_context"] = note
        return json.dumps(parsed, ensure_ascii=False)
    except Exception:
        logger.debug("session_context injection failed (non-fatal)", exc_info=True)
        return result_str
