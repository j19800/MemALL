"""REST + WebSocket client for MemALL Agent Hub (127.0.0.1:12431).

Phase 8: MCP 对接 agent-hub — 双向桥接层.

Architecture:
  MemALL (federation_tools) → hub_client → Agent Hub REST API
  Agent Hub WebSocket events → hub_client → MemALL capture/publish

Usage:
    from memall.mcp.hub_client import (
        hub_health, hub_list_agents, hub_list_groups,
        hub_send_message, hub_get_group_messages,
        hub_create_memory, hub_list_memories,
        hub_get_stats,
    )
"""

import json
import logging
import urllib.request
import urllib.error
from typing import Any

HUB_BASE = "http://127.0.0.1:12431"
_HUB_TIMEOUT = 5  # seconds (default)
_HUB_TIMEOUT_HEAVY = 30  # seconds (for slow operations)

logger = logging.getLogger("memall.mcp.hub_client")


# ════════════════════════════════════════════════════════════════
# Low-level HTTP client
# ════════════════════════════════════════════════════════════════

def _hub_request(method: str, path: str, body: dict | None = None) -> dict | list | str:
    """Make an HTTP request to Agent Hub. Returns parsed JSON (or raw string for non-JSON)."""
    url = f"{HUB_BASE}{path}"
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json; charset=utf-8")

    try:
        with urllib.request.urlopen(req, timeout=_HUB_TIMEOUT) as resp:
            raw = resp.read().decode("utf-8")
            if not raw.strip():
                return {}
            try:
                return json.loads(raw)
            except (json.JSONDecodeError, ValueError):
                return raw  # non-JSON response (e.g. healthz: "ok")
    except urllib.error.HTTPError as e:
        body_text = e.read().decode("utf-8", errors="replace")
        logger.warning("Hub HTTP %d: %s %s -> %s", e.code, method, path, body_text[:200])
        return {"error": f"HTTP {e.code}", "detail": body_text[:300]}
    except urllib.error.URLError as e:
        logger.warning("Hub unreachable: %s %s -> %s", method, path, e.reason)
        return {"error": f"Hub unreachable: {e.reason}"}
    except Exception as e:
        logger.warning("Hub request failed: %s %s -> %s", method, path, e)
        return {"error": str(e)}


# ════════════════════════════════════════════════════════════════
# Health & connectivity
# ════════════════════════════════════════════════════════════════

def hub_health() -> dict:
    """Ping Hub health endpoint."""
    return _hub_request("GET", "/healthz")


# ════════════════════════════════════════════════════════════════
# Agents
# ════════════════════════════════════════════════════════════════

def hub_list_agents() -> list[dict]:
    """List all agents registered in Hub."""
    result = _hub_request("GET", "/api/agents")
    if isinstance(result, list):
        return result
    if isinstance(result, dict):
        agents = result.get("agents") or result.get("data") or []
        return agents if isinstance(agents, list) else []
    return []


# ════════════════════════════════════════════════════════════════
# Groups
# ════════════════════════════════════════════════════════════════

def hub_list_groups() -> list[dict]:
    """List all groups in Hub."""
    result = _hub_request("GET", "/api/groups")
    if isinstance(result, list):
        return result
    if isinstance(result, dict):
        groups = result.get("groups") or result.get("data") or []
        return groups if isinstance(groups, list) else []
    return []


def hub_send_message(group_id: str, sender_id: str, content: str,
                     msg_type: str = "text") -> dict:
    """Send a message to a Hub group via REST. Returns response."""
    body = {
        "sender_id": sender_id,
        "content": content,
        "msg_type": msg_type,
    }
    return _hub_request("POST", f"/api/groups/{group_id}/messages", body)


def hub_get_group_messages(group_id: str, limit: int = 50) -> list[dict]:
    """Get message history from a Hub group."""
    result = _hub_request("GET", f"/api/groups/{group_id}/messages?limit={limit}")
    if isinstance(result, list):
        return result
    if isinstance(result, dict):
        msgs = result.get("messages") or result.get("data") or []
        return msgs if isinstance(msgs, list) else []
    return []


# ════════════════════════════════════════════════════════════════
# Memories (Agent Hub 侧的记忆)
# ════════════════════════════════════════════════════════════════

def hub_create_memory(title: str, content: str,
                      agent_id: str = "", category: str = "fact",
                      tags: list | None = None) -> dict:
    """Create a memory entry in Agent Hub."""
    body = {
        "title": title,
        "content": content,
        "agent_id": agent_id,
        "category": category,
        "tags": tags or [],
    }
    return _hub_request("POST", "/api/memories", body)


def hub_list_memories(category: str = "", q: str = "",
                      limit: int = 20) -> list[dict]:
    """Query memories from Agent Hub (returns entries under 'entries' key)."""
    params = f"?limit={limit}"
    if category:
        params += f"&category={category}"
    if q:
        params += f"&q={urllib.request.quote(q)}"
    result = _hub_request("GET", f"/api/memories{params}")
    if isinstance(result, list):
        return result
    if isinstance(result, dict):
        # Hub returns {"entries": [...]}
        mems = result.get("entries") or result.get("memories") or result.get("data") or []
        return mems if isinstance(mems, list) else []
    return []


# ════════════════════════════════════════════════════════════════
# Stats / dashboard
# ════════════════════════════════════════════════════════════════

def hub_get_stats() -> dict:
    """Get Hub dashboard stats."""
    return _hub_request("GET", "/api/stats")


# ════════════════════════════════════════════════════════════════
# Convenience: verify connectivity + return summary
# ════════════════════════════════════════════════════════════════

def hub_status() -> dict:
    """Full connectivity check — health + agent count + group count."""
    health = hub_health()
    if isinstance(health, dict) and health.get("error"):
        return {"connected": False, "error": health["error"]}
    if not isinstance(health, dict):
        # healthz returns "ok" as plain text — that means connected
        pass

    agents = hub_list_agents()
    groups = hub_list_groups()
    stats = hub_get_stats()

    return {
        "connected": True,
        "hub_url": HUB_BASE,
        "agent_count": len(agents) if isinstance(agents, list) else 0,
        "group_count": len(groups) if isinstance(groups, list) else 0,
        "agents_preview": [
            {"id": a.get("id", ""), "name": a.get("name", ""), "status": a.get("status", "")}
            for a in (agents if isinstance(agents, list) else [])[:10]
        ],
        "stats": stats if isinstance(stats, dict) else {},
    }