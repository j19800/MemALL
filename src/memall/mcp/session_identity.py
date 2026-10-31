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


def canonical_agent(agent_name: Optional[str]) -> str:
    """Canonical identity form: lowercase.

    Every store of agent identity (memories.agent_name, identities.agent_name,
    bus_agents/*.token) is lowercase, and isolation filters compare with
    ``agent_name = ?`` — an exact match.  A client handshaking as "WorkBuddy"
    therefore matched zero rows and every search came back empty, silently.
    """
    return (agent_name or "").strip().lower()


def bind_session_agent(agent_name: Optional[str]) -> str:
    """Bind the identity for the current request/session. Returns it."""
    global _handshake_agent
    name = canonical_agent(agent_name)
    if not name:
        return get_session_agent()
    _session_agent.set(name)
    _handshake_agent = name
    return name


def bind_handshake_agent(agent_name: Optional[str]) -> str:
    """Record the identity from ``initialize`` (survives context switches)."""
    global _handshake_agent
    name = canonical_agent(agent_name)
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


# Actions that ESTABLISH WORK FOR ANOTHER AGENT.  These legitimately pass
# ``agent_name`` to mean "create/address this item for that agent", so the
# explicit value must survive.  Every other call is a READ or a self-scoped
# write, where an explicit ``agent_name`` would be an impersonation attempt.
#
# The distinction is deliberate: dispatching to another agent is a normal
# collaboration act, while *reading as* another agent is never legitimate.
DISPATCH_ACTIONS = frozenset({
    "capture",          # write a card/memory on behalf of an agent
    "smart_store",
    "store_batch",
    "batch_capture",
    "quick",
    "task_claim",       # addressee claims its own card
    "task_resolve",
    "task_block",
    "discussion_create",  # participants are addressed explicitly
    "record_interaction",
})


def is_dispatch_action(action: str) -> bool:
    """True when an explicit ``agent_name`` names a *target*, not a viewer."""
    return (action or "").strip().lower() in DISPATCH_ACTIONS


def identify(arguments: Optional[dict], action: str = "") -> str:
    """Identity for a tool call: the session wins, explicit argument is a fallback.

    Security model (I5: an identity must not be impersonable).  The session
    identity comes from the ``initialize`` handshake and is authoritative for
    every READ path; an explicit ``agent_name`` in a tool payload is only a
    one-shot fallback for a transport that never handshook.

    It used to be the other way round — an explicit argument *overwrote* the
    session binding permanently (``bind_session_agent``), so any caller could
    send ``agent_name=writer`` once and then read writer's private memories
    AND have every later argument-less call (including writes) execute as
    writer.  The argument must never mutate the bound identity.

    Exception: *dispatch* actions (``DISPATCH_ACTIONS``) say "do this FOR that
    agent", which is a legitimate collaboration act rather than a claim to be
    that agent.  For those the explicit value is returned, because rewriting
    it to the session identity would file the card against the wrong agent.

    ``arguments`` is the *validated* payload, so an ``agent_name`` here has
    already survived the model (the caller declared it).  When the model
    stripped it — the F3 case — the session identity is used instead.
    """
    explicit = ""
    if isinstance(arguments, dict):
        explicit = canonical_agent(arguments.get("agent_name"))

    if explicit and is_dispatch_action(action):
        # Addressed to a target agent: keep it, but NEVER bind it as this
        # session's identity -- that is what made impersonation persistent.
        return explicit

    session = get_session_agent()
    if session:
        return session
    return explicit
