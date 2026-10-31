import json
import logging
import time

from memall.mcp.validator import validate_tool_input, format_validation_error
from memall.mcp.shared import (
    ensure_session_started, consume_session_note, ack_session_note, run_intercept,
)
from memall.mcp.registry import registry
from memall.mcp.hooks import HookRegistry, HOOK_PRE_TOOL_USE, HOOK_POST_TOOL_USE, HOOK_POST_TOOL_USE_FAILURE
from memall.core.metrics import get_metrics
from memall.core.tracer import span, reset_trace

logger = logging.getLogger(__name__)


def _intercept(tool_name: str, arguments: dict, result_str: str = "") -> None:
    """Backward compat wrapper — result_str is unused."""
    run_intercept(tool_name, arguments)


TOOL_DEFINITIONS = []
_loaded = False

# ── Eager, but cheap (0.08s each) ────────────────────────────────────────
# The hooks are registered by importing these modules; anything that imports
# the adapter is entitled to see them.  Deferring them too broke
# auto-capture for every caller that touched the hook registry before making
# a tool call (the registry looked empty).  Only the genuinely heavy module
# — memall.mcp.tools, which drags in sentence-transformers — is lazy.
from memall.mcp import hooks_builtin  # noqa: E402,F401  (registers hooks)
from memall import auto_capture  # noqa: E402,F401  (registers the extractor)


def _ensure_loaded():
    """Lazily import the HEAVY tool/plugin modules.

    ``memall.mcp.tools`` registers the 18 tool modules and pulls in
    sentence-transformers (measured 1.58s hot, more when cold), and
    ``load_all_plugins`` loads every plugin on top of that.  Doing it at
    module import blocked the MCP stdio server from answering the
    ``initialize`` handshake, so clients timed out and reported
    "Not connected".  Hooks (0.08s) stay eager above; this defers only the
    expensive part to the first tool call.

    Every reader of TOOL_DEFINITIONS must call this first.
    """
    global _loaded
    if _loaded:
        return
    # Import tools to register them (side-effect)
    from memall.mcp import tools  # noqa: F401
    # Load plugins so lifecycle hooks (on_capture, on_pipeline, etc.) fire
    from memall.plugins.loader import load_all_plugins as _load_all_plugins
    _load_all_plugins()
    # Mutate in place so `from ... import TOOL_DEFINITIONS` bindings in other
    # modules (server.py) see the populated list, not just a rebind.
    TOOL_DEFINITIONS.clear()
    TOOL_DEFINITIONS.extend(registry.list_definitions())
    _loaded = True


def handle_call(tool_name: str, arguments: dict) -> str:
    reset_trace()
    _ensure_loaded()
    m = get_metrics()
    m.incr("tool_call_total")
    m.incr(f"tool_call.{tool_name}")
    _t0 = time.time()

    # Unknown tool: name the real ones instead of falling through to the
    # validator's "unknown action ... Valid actions: (empty)" message, which
    # is misleading when the tool itself does not exist.
    if registry.get(tool_name) is None:
        m.incr("tool_call_unknown_tool")
        m.record_latency(f"tool.{tool_name}", (time.time() - _t0) * 1000)
        known = ", ".join(sorted(
            d["name"] for d in registry.list_definitions() if isinstance(d, dict)
        ))
        return json.dumps({
            "status": "error",
            "error": f"unknown tool '{tool_name}'",
            "valid_tools": known,
        }, ensure_ascii=False)

    is_valid, validated_data, err_msg = validate_tool_input(tool_name, arguments)
    if not is_valid:
        m.incr("tool_call_validation_error")
        m.record_latency(f"tool.{tool_name}", (time.time() - _t0) * 1000)
        return format_validation_error(tool_name, err_msg)
    # Keep the ORIGINAL parameters: the validator drops fields the action's
    # model does not declare, so a handler could not tell that the client
    # passed e.g. dedup_threshold to action=capture and that it silently
    # did nothing (audit P0-2).  The original keys are handed to the
    # handler as "__extra_args__" for that reporting.
    #
    # It is added ONLY for the capture action: handlers that splat their
    # arguments into a function signature (graph.connect, retrieve,
    # timeline) would treat it as a real field, and update() would report
    # "updated: false, fields: ['__extra_args__']" while dropping the
    # metadata the caller actually asked to change.
    _original_args = dict(arguments) if isinstance(arguments, dict) else {}
    arguments = validated_data
    if _original_args and arguments.get("action") == "capture":
        extra = {k: v for k, v in _original_args.items()
                 if k not in arguments and k != "action"}
        if extra:
            arguments = dict(arguments)
            arguments["__extra_args__"] = extra

    # ── F3: restore the caller's identity after validation ──────────────
    # validate_tool_input drops fields the action's model does not declare,
    # and 28 models never declared agent_name — so every routing decision
    # below (ensure_session_started, the notification bar, agent isolation)
    # silently saw an empty identity.  The identity is now bound once at MCP
    # handshake time and re-attached here, so a client is addressable no
    # matter which tool it calls.
    #
    # The SESSION identity wins over an explicit argument on every READ path
    # (I5, no impersonation).  This used to be "explicit wins", which let any
    # caller send agent_name=writer once and then read writer's private
    # memories -- and (because identify() also bound the value) act as writer
    # for the rest of the session.  Dispatch actions are the exception: there
    # an explicit agent_name names the agent the work is being created FOR, so
    # it must survive (identify() decides, see session_identity.DISPATCH_ACTIONS).
    # Rewriting here also canonicalises the value ("WorkBuddy" vs the stored
    # "workbuddy"); an exact-match isolation filter would otherwise return
    # nothing.
    try:
        from memall.mcp.session_identity import identify

        _action = arguments.get("action", "") if isinstance(arguments, dict) else ""
        _caller = identify(arguments, _action) or ""
        if _caller:
            arguments = dict(arguments)
            arguments["agent_name"] = _caller
    except Exception:
        logger.debug("session identity resolution failed", exc_info=True)

    # ── Read-path entry enforcement ────────────────────────────────────
    # Every search path now applies the trust filter, so an identity-less
    # read can only see 'public' rows -- and since visibility defaults to
    # 'private', that reads as "no results".  Returning an empty list would
    # be indistinguishable from "nothing matched", so refuse and explain.
    # The local CLI binds the human owner before calling, so it is unaffected.
    try:
        from memall.mcp import session_identity as _si

        _act = arguments.get("action", "") if isinstance(arguments, dict) else ""
        if (_si.requires_identity(_act) and not (arguments.get("agent_name") or "")):
            m.incr("tool_call_missing_identity")
            return json.dumps({
                "status": "error",
                "error": _si.missing_identity_error(_act),
            }, ensure_ascii=False)
    except Exception:
        logger.debug("read-path identity check failed", exc_info=True)

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
        result = _inject_session_context(result, arguments.get("agent_name", ""))

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


def _inject_session_context(result_str: str, agent_name: str = "") -> str:
    """Inject ``_meta.session_context`` for read-only tools.

    The pending note is only dropped when it was ACTUALLY delivered (the
    acknowledgement follows the injection).  Previously the note was popped
    on read, so a response that never reached the client (timeout, crash
    between read and write) lost the session context permanently — the MCP
    protocol has no way to ask for it again (audit P2-12).
    """
    try:
        note = consume_session_note(agent_name)
        if not note:
            return result_str

        parsed = json.loads(result_str)
        if not isinstance(parsed, dict):
            return result_str

        parsed.setdefault("_meta", {})["session_context"] = note
        # Delivered — acknowledge (drop) it now.
        ack_session_note(agent_name)
        return json.dumps(parsed, ensure_ascii=False)
    except Exception:
        logger.debug("session_context injection failed (non-fatal)", exc_info=True)
        return result_str
