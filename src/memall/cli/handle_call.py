"""CLI → MCP adapter: lets CLI commands call handle_call() instead of thin_waist directly."""

import json
from collections import namedtuple

from memall.mcp.adapter import handle_call as _mcp_handle

McpResult = namedtuple("McpResult", ["ok", "data", "error"])


def mcp_call(tool_name: str, **kwargs) -> "McpResult":
    """Call an MCP tool and return a structured result.

    Args:
        tool_name: The MCP tool name (e.g. "capture", "retrieve").
        **kwargs: Arguments passed directly to the tool handler.

    Returns:
        McpResult(ok=True, data=..., error=None) on success,
        McpResult(ok=False, data=None, error=...) on failure.
    """
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
