import logging
from memall.mcp.hooks import HookRegistry, HookDef, HOOK_POST_TOOL_USE, HOOK_POST_TOOL_USE_FAILURE, HOOK_STOP

logger = logging.getLogger(__name__)


def _hook_capture_audit(tool_name: str, arguments: dict, result: str = "", **kwargs) -> None:
    """Log all capture operations to the audit trail.

    Fires for every memall_write call; only the ones that actually stored
    something are logged, so the audit trail shows WRITES (capture,
    smart_store, store_batch, update) and not reads/queries.
    """
    action = (arguments or {}).get("action", "")
    if action not in ("capture", "smart_store", "store_batch", "batch_capture",
                      "update", "quick", "append", "replace"):
        return
    content = (arguments or {}).get("content", "")
    if not content:
        items = (arguments or {}).get("items") or []
        content = f"{len(items)} item(s)"
    logger.info("AUDIT: %s — %s...", action, str(content)[:80])


def _hook_tool_error(tool_name: str, arguments: dict, error: str = "", **kwargs) -> None:
    """Log tool failures for monitoring."""
    logger.warning("TOOL FAILURE: %s — %s", tool_name, error[:200] if error else "unknown")


def _hook_pipeline_stop(arguments: dict, **kwargs) -> None:
    """Log pipeline completion summary after each run."""
    raw = arguments.get("results", {})
    elapsed = arguments.get("elapsed", 0)

    def _count(v):
        if isinstance(v, dict):
            return v.get("changed", 0) or 0
        return v or 0

    ok = _count(raw.get("enrich", 0)) + _count(raw.get("classify", 0)) + _count(raw.get("link", 0))
    logger.info("HOOK|stop: pipeline done in %.1fs, %d items processed", elapsed, ok)


HookRegistry.register(HookDef(
    hook_point=HOOK_POST_TOOL_USE,
    # The matcher is compared against the MCP TOOL name that the adapter
    # dispatches ("memall_write", "memall_read", ...).  It used to be
    # "capture" — the core function name — so the audit hook matched
    # nothing and never fired: the deployment believed it had a capture
    # audit trail and had none (audit P2-9, verified with a spy: NEVER).
    matcher="memall_write",
    handler=_hook_capture_audit,
    description="Audit log all capture operations",
))
HookRegistry.register(HookDef(
    hook_point=HOOK_POST_TOOL_USE_FAILURE,
    matcher="*",
    handler=_hook_tool_error,
    description="Log all tool call failures",
))
HookRegistry.register(HookDef(
    hook_point=HOOK_STOP,
    matcher="*",
    handler=_hook_pipeline_stop,
    description="Log pipeline completion",
))
