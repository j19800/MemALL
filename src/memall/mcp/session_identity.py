"""Session-scoped agent identity — the primitive that makes routing work.

Root cause F3: every routing decision (auto_inject, the notification bar,
agent isolation) reads ``arguments.get("agent_name")``.  But 28 of the tool
input models never declare ``agent_name``, so ``validate_tool_input`` strips
it before the handler ever runs.  An agent that only used ``memall_system``
or ``vector_search`` therefore could not be addressed at all: its session was
never started, its pending tasks never delivered, its discussions never
surfaced.

Fix: bind the identity ONCE, at MCP session initialisation, and let the
adapter fall back to it.  Then no tool model has to declare ``agent_name``,
and a client that sends it explicitly still overrides (backwards compatible).

Implementation notes:
  * A ContextVar keeps this cheap and thread-safe.  The MCP stdio server is
    single-threaded per request; the HTTP/gateway transports run handlers in
    a thread pool, so a ContextVar is copied per task and can be set per
    request without leaking across concurrent clients.
  * ``bind_session_agent`` is also called from ``initialize`` so the very
    first request already knows who is talking.
"""
from contextvars import ContextVar
from typing import Optional

_session_agent: ContextVar[str] = ContextVar("memall_session_agent", default="")

# Last identity bound by a full MCP handshake.  Used as a fallback when a
# transport cannot propagate the ContextVar (e.g. a handler executed on a
# fresh thread pool worker that Python did not copy the context into).
_handshake_agent: str = ""


def bind_session_agent(agent_name: Optional[str]) -> str:
    """Bind the identity for the current request/session. Returns it."""
    global _handshake_agent
    name = (agent_name or "").strip()
    if not name:
        return get_session_agent()
    _session_agent.set(name)
    _handshake_agent = name
    return name


def bind_handshake_agent(agent_name: Optional[str]) -> str:
    """Record the identity from ``initialize`` (survives context switches)."""
    global _handshake_agent
    name = (agent_name or "").strip()
    if name:
        _handshake_agent = name
        _session_agent.set(name)
    return _handshake_agent


def get_session_agent() -> str:
    """Current session identity, or ``""`` when unknown."""
    return _session_agent.get() or _handshake_agent


def reset() -> None:
    """Clear the bound identity (tests / new connection)."""
    global _handshake_agent
    _session_agent.set("")
    _handshake_agent = ""


def identify(arguments: Optional[dict]) -> str:
    """Identity for a tool call: explicit argument wins, else the session.

    ``arguments`` is the *validated* payload, so an ``agent_name`` here has
    already survived the model (the caller declared it).  When the model
    stripped it — the F3 case — the session identity is used instead.
    """
    if isinstance(arguments, dict):
        explicit = (arguments.get("agent_name") or "").strip()
        if explicit:
            bind_session_agent(explicit)
            return explicit
    return get_session_agent()
