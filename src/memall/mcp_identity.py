"""
MCP handshake identity enforcement
==================================
Rule: every MCP client MUST declare an agent name at ``initialize``.  The
gateway refuses the handshake with a JSON-RPC error when no valid identity
can be resolved — no more ``actor_id: unknown`` sessions.

Resolution order (explicit client declaration wins over server fallback):

  1. ``params.agent_name``          — native MCP clients that can set it
  2. ``clientInfo.agent_name``      — same, nested form
  3. ``X-MemAll-Agent`` HTTP header — dsh-mcp-client cannot inject custom
     initialize params, but its ``headers`` config is sent on EVERY request,
     including ``initialize``.  This is the DSH transport's channel.

There is deliberately NO server-side env fallback: the declaration must come
from the client itself.  A missing/invalid declaration is refused rather than
silently attributed, so an unconfigured client cannot borrow an identity.

A resolved identity is also *registered*: inserted into ``identities``
(idempotent) so the trust filter / persona / audit can see it, and echoed back
in ``serverInfo.actor_id``.  Registration is recorded in ``ops_facts``.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

from memall.core.agent_names import validate_agent_name, _invalid_reason
from memall.core.db import get_conn

AGENT_HEADER = "X-MemAll-Agent"


def resolve_agent(request, params: Dict[str, Any]) -> Tuple[str, str, str]:
    """Return ``(canonical_name, source, error_reason)``.

    ``canonical_name`` is empty when the declaration is missing/invalid;
    ``error_reason`` then explains why (safe to return to the client).
    """
    ci = params.get("clientInfo") or {}
    raw = (params.get("agent_name")
           or ci.get("agent_name")
           or request.headers.get(AGENT_HEADER)
           or "")
    source = ("params.agent_name" if params.get("agent_name") else
              "clientInfo.agent_name" if ci.get("agent_name") else
              AGENT_HEADER if request.headers.get(AGENT_HEADER) else
              "none")
    raw = str(raw or "").strip()
    if not raw:
        return "", source, "missing"
    name = validate_agent_name(raw)
    if not name:
        return "", source, f"invalid agent name: {_invalid_reason(raw.lower()) or 'rejected'}"
    return name, source, ""


def register_agent(name: str, source: str, transport: str = "streamable-http") -> Dict[str, Any]:
    """Idempotently register the declared identity in ``identities``.

    Never overwrites an existing human/system row.  Records the handshake in
    ``ops_facts`` (isolated ops table) so registrations are auditable.
    """
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT id, agent_type FROM identities WHERE agent_name = ?", (name,)
        ).fetchone()
        action = "existing"
        if not row:
            conn.execute(
                "INSERT OR IGNORE INTO identities "
                "(agent_name, agent_type, status, last_heartbeat) "
                "VALUES (?, 'ai', 'active', ?)",
                (name, datetime.now(timezone.utc).isoformat()),
            )
            conn.commit()
            action = "registered"
        else:
            conn.execute(
                "UPDATE identities SET last_heartbeat = ? WHERE agent_name = ?",
                (datetime.now(timezone.utc).isoformat(), name),
            )
            conn.commit()
        agent_type = row["agent_type"] if row else "ai"
    finally:
        conn.close()

    # Audit trail in the isolated ops table (best-effort, never blocks handshake)
    try:
        from memall import ops_facts
        ops_facts.put(
            "mcp", f"handshake.{name}",
            json.dumps({"source": source, "transport": transport,
                        "action": action}, ensure_ascii=False),
            {"declared_at": datetime.now(timezone.utc).isoformat()},
            "mcp-initialize",
        )
    except Exception:
        pass
    return {"name": name, "action": action, "agent_type": agent_type,
            "source": source}


def missing_identity_error(reason: str) -> Dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "error": {
            "code": -32001,
            "message": (
                "agent name is required at MCP initialize: "
                "set params.agent_name / clientInfo.agent_name, or send the "
                f"{AGENT_HEADER} header. "
                f"Reason: {reason}."
            ),
        },
    }
