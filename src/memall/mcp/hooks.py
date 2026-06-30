from __future__ import annotations
import re
import time
import logging
from dataclasses import dataclass
from typing import Any, Callable

logger = logging.getLogger(__name__)

HOOK_PRE_TOOL_USE = "pre_tool_use"
HOOK_POST_TOOL_USE = "post_tool_use"
HOOK_POST_TOOL_USE_FAILURE = "post_tool_use_failure"
HOOK_PRE_COMPACT = "pre_compact"
HOOK_STOP = "stop"
HOOK_SESSION_START = "session_start"
HOOK_SESSION_END = "session_end"

# ── Lifecycle hook constants ──
HOOK_PRE_CAPTURE = "pre_capture"
HOOK_POST_CAPTURE = "post_capture"
HOOK_PRE_STORE = "pre_store"
HOOK_POST_STORE = "post_store"
HOOK_PRE_RETRIEVE = "pre_retrieve"
HOOK_POST_RETRIEVE = "post_retrieve"
HOOK_PRE_SEARCH = "pre_search"
HOOK_POST_SEARCH = "post_search"
HOOK_PRE_PIPELINE = "pre_pipeline"
HOOK_POST_PIPELINE = "post_pipeline"
HOOK_PRE_STEP = "pre_step"
HOOK_STEP_OK = "step_ok"
HOOK_STEP_FAIL = "step_fail"

# Mapping from lifecycle hook points to plugin function names
_HOOK_TO_PLUGIN: dict[str, str] = {
    HOOK_PRE_CAPTURE: "on_pre_capture",
    HOOK_POST_CAPTURE: "on_capture",
    HOOK_PRE_STORE: "on_pre_store",
    HOOK_POST_STORE: "on_store",
    HOOK_PRE_RETRIEVE: "on_pre_retrieve",
    HOOK_POST_RETRIEVE: "on_retrieve",
    HOOK_PRE_SEARCH: "on_pre_search",
    HOOK_POST_SEARCH: "on_search",
    HOOK_PRE_PIPELINE: "on_pre_pipeline",
    HOOK_POST_PIPELINE: "on_pipeline",
    HOOK_PRE_STEP: "on_pre_step",
    HOOK_STEP_OK: "on_step_ok",
    HOOK_STEP_FAIL: "on_step_fail",
}


@dataclass
class HookDef:
    hook_point: str
    matcher: str
    handler: Callable
    description: str = ""
    blocking: bool = False


class HookRegistry:
    _hooks: list[HookDef] = []

    @classmethod
    def register(cls, hook: HookDef):
        cls._hooks.append(hook)

    @classmethod
    def clear(cls):
        cls._hooks.clear()

    @classmethod
    def dispatch(
        cls,
        hook_point: str,
        tool_name: str = "",
        arguments: dict | None = None,
        result: Any = None,
        error: str | None = None,
        extra: dict | None = None,
    ) -> list[Any]:
        results: list[Any] = []
        for hook in cls._hooks:
            if hook.hook_point != hook_point:
                continue
            if not _match_tool(hook.matcher, tool_name):
                continue
            try:
                r = hook.handler(
                    tool_name=tool_name,
                    arguments=arguments or {},
                    result=result,
                    error=error,
                    extra=extra or {},
                )
                results.append(r)
                if hook.blocking and r is False:
                    results.append(False)
                    return results
            except Exception:
                logger.exception(f"Hook failed: {hook.hook_point} / {hook.matcher}")
        return results

    @classmethod
    def list_hooks(cls) -> list[dict]:
        return [
            {
                "hook_point": h.hook_point,
                "matcher": h.matcher,
                "description": h.description,
                "blocking": h.blocking,
            }
            for h in cls._hooks
        ]


def _match_tool(matcher: str, tool_name: str) -> bool:
    if matcher == "*":
        return True
    escaped = re.escape(matcher)
    escaped = escaped.replace(r"\*", ".*").replace(r"\|", "|")
    pattern = "^(" + escaped + ")$"
    return bool(re.match(pattern, tool_name))


def hook(
    hook_point: str,
    matcher: str = "*",
    description: str = "",
    blocking: bool = False,
):
    def decorator(fn):
        HookRegistry.register(HookDef(
            hook_point=hook_point,
            matcher=matcher,
            handler=fn,
            description=description,
            blocking=blocking,
        ))
        return fn
    return decorator


def dispatch_lifecycle(hook_point: str, blocking: bool = False, **kwargs) -> bool:
    """Dispatch a lifecycle event to both HookRegistry hooks and plugin hooks.

    This bridges the HookRegistry (MCP-oriented) and the plugin system.
    Plugin hooks are loaded lazily via ``run_plugin_hook`` to avoid circular
    imports.

    For hook points without a plugin handler, this method automatically records
    a generic activity event so agents get baseline visibility.  Plugin hooks
    that receive the event should call ``record_event()`` themselves with a
    richer description.

    Args:
        hook_point: The lifecycle hook constant (HOOK_*).
        blocking: If True, any handler returning False will abort the operation.
        **kwargs: Contextual data passed to handlers.

    Returns:
        True to proceed, False if a blocking hook returned False.
    """
    _t0 = time.time()

    # 1. HookRegistry dispatch (existing mechanism)
    results = HookRegistry.dispatch(hook_point, extra=kwargs)
    if blocking and any(r is False for r in results):
        return False

    # 2. Plugin hooks (lazy import to avoid circular dependencies)
    plugin_func = _HOOK_TO_PLUGIN.get(hook_point)
    had_plugin = False  # true only if at least one plugin actually handles this
    if plugin_func:
        try:
            from memall.plugins.loader import run_plugin_hook  # noqa: F811
            plugin_results = run_plugin_hook(plugin_func, **kwargs)
            had_plugin = bool(plugin_results)
            if blocking and any(r is False for r in plugin_results):
                return False
        except Exception:
            logger.exception(
                "Plugin hook %s failed for hook_point=%s",
                plugin_func, hook_point,
            )

    # 3. Record activity event (skip pre_* hooks — they're internal noise)
    elapsed_ms = int((time.time() - _t0) * 1000)
    if not had_plugin and not hook_point.startswith("pre_"):
        from memall.mcp.hook_effects import record_event  # noqa: F811
        record_event(
            hook_point=hook_point,
            description=_describe_lifecycle(hook_point, **kwargs),
            plugin="system",
            elapsed_ms=elapsed_ms,
        )

    return True


# ── Description helpers for auto-recorded events ─────────────────────────

_HOOK_DESCRIPTIONS: dict[str, str] = {
    HOOK_POST_STORE: "Memory stored to database",
    HOOK_POST_RETRIEVE: "Memory retrieved",
    HOOK_POST_SEARCH: "Memory search completed",
    HOOK_POST_PIPELINE: "Pipeline run completed",
    HOOK_STEP_OK: "Pipeline step completed",
    HOOK_STEP_FAIL: "Pipeline step failed",
}


def _describe_lifecycle(hook_point: str, **kwargs) -> str:
    """Build a human-readable description for a lifecycle event."""
    base = _HOOK_DESCRIPTIONS.get(hook_point, f"Hook: {hook_point}")
    # Append contextual info where available
    mem_id = kwargs.get("memory_id")
    viewer = kwargs.get("viewer") or kwargs.get("agent_name", "")
    parts = []
    if mem_id:
        parts.append(f"memory #{mem_id}")
    if viewer:
        parts.append(f"agent={viewer}")
    if parts:
        base += f" ({', '.join(parts)})"
    return base
