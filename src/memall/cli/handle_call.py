"""CLI → MCP adapter: lets CLI commands call handle_call() instead of thin_waist directly."""

import json
from collections import namedtuple

from memall.mcp.adapter import handle_call as _mcp_handle

McpResult = namedtuple("McpResult", ["ok", "data", "error"])


def _bind_local_identity() -> None:
    """Bind the local human as this CLI's viewer.

    The CLI is the trusted local surface: it runs on the operator's machine,
    as the operator.  It therefore reads as the configured human owner
    (``identity.human_owner``, an ``agent_type='human'`` identity whose read
    level is ``private``).

    This matters because search now runs the trust filter on *every* path.
    Without a viewer an unregistered identity gets only ``public``, and since
    ``memories.visibility`` defaults to ``private`` every normal memory would
    vanish from CLI results.  Binding the human here keeps the CLI usable
    while leaving the identity-less case locked down for external transports.
    """
    try:
        from memall.config import get_config
        from memall.mcp import session_identity as si

        if si.get_session_agent():
            return
        human = (get_config("identity.human_owner", "") or "").strip()
        if human:
            si.bind_handshake_agent(human)
    except Exception:
        # Never let identity setup break a CLI command; an unbound viewer
        # simply yields public-only results.
        pass


def mcp_call(tool_name: str, **kwargs) -> "McpResult":
    """Call an MCP tool and return a structured result.

    Args:
        tool_name: The MCP tool name (e.g. "capture", "retrieve").
        **kwargs: Arguments passed directly to the tool handler.

    Returns:
        McpResult(ok=True, data=..., error=None) on success,
        McpResult(ok=False, data=None, error=...) on failure.
    """
    _bind_local_identity()
    raw = _mcp_handle(tool_name, kwargs)
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return McpResult(False, None, f"invalid JSON response: {raw[:200]}")

    if isinstance(parsed, dict):
        if parsed.get("status") in ("error", "blocked"):
            return McpResult(False, None, parsed.get("error") or parsed.get("reason", "unknown error"))
        if "error" in parsed:
            return McpResult(False, None, str(parsed["error"]))

    return McpResult(True, parsed, None)
