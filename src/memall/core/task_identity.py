"""Single source of truth for "who may act on this task card".

Before this module the same rule lived twice - ``memall.mcp.tasks`` and
``memall.pipeline.task_lifecycle`` - with different parameter names and
different supervisor handling, so fixing one copy silently diverged from the
other.  Both now delegate here.

Semantics are stated per operation because they are deliberately different:

  * The addressee is ``metadata.assignee``, else the card's ``agent_name``
    column.  Comparison trims whitespace and ignores case.
  * CLAIM is strictly the addressee's own act.  ``security.supervisor_agents``
    is NOT honoured: claiming means "I promise to deliver this", and a
    supervisor cannot make that promise for someone else.
  * RESOLVE / BLOCK / ACKNOWLEDGE may be done by the addressee OR a supervisor.
    Supervisors exist to close work abandoned by a dead or decommissioned
    identity - that is a different power from taking the work on.
  * ``caller is None`` is the trusted internal path (scheduler, migrations,
    guards) and bypasses the check entirely.

``claimed_by`` is NOT a second identity track.  It is the audit stamp written
when the addressee accepts the card, and by construction equals the assignee;
``effective_executor`` reads assignee first so rows written before the claim
path recorded assignee keep working.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def normalize_agent(name) -> str:
    """Trim + casefold an agent name for comparison."""
    return str(name or "").strip().lower()


def is_addressee(meta: dict, row_agent_name: str, agent_name: str) -> bool:
    """Whether *agent_name* is the card's addressee (assignee, else agent_name)."""
    meta = meta or {}
    target = str(meta.get("assignee") or row_agent_name or "").strip()
    return bool(target) and normalize_agent(target) == normalize_agent(agent_name)


def is_supervisor(agent: str) -> bool:
    """True if *agent* is listed in ``security.supervisor_agents``."""
    try:
        from memall.config import get_config

        supes = get_config("security.supervisor_agents", []) or []
        if isinstance(supes, str):
            supes = [s for s in supes.split(",") if s.strip()]
        allowed = {normalize_agent(s) for s in supes}
        return normalize_agent(agent) in allowed
    except Exception:
        logger.debug("is_supervisor check failed", exc_info=True)
        return False


def effective_executor(meta: dict, row_agent_name: str) -> str:
    """Who actually executes the card.  assignee is the single track."""
    meta = meta or {}
    return str(meta.get("assignee") or meta.get("claimed_by")
               or row_agent_name or "").strip()


def can_claim(meta: dict, row_agent_name: str, agent_name: str) -> bool:
    """Only the addressee may claim.  No supervisor exemption (see module doc)."""
    return is_addressee(meta, row_agent_name, agent_name)


def can_mutate(meta: dict, row_agent_name: str, caller) -> bool:
    """Whether *caller* may resolve / block / acknowledge the card.

    ``caller is None`` is the trusted-internal path and always allowed.
    """
    if caller is None:
        return True
    return is_addressee(meta, row_agent_name, caller) or is_supervisor(caller)


def forbidden_payload(meta: dict, row_agent_name: str, caller, task_id: int,
                      operation: str = "change") -> dict:
    """Structured refusal for a caller who may not act on the card."""
    meta = meta or {}
    return {
        "status": "forbidden",
        "task_id": task_id,
        "reason": (f"this card is addressed to another agent; only the assignee "
                   f"or a security.supervisor_agents member may {operation} it"),
        "assignee": meta.get("assignee") or row_agent_name,
        "caller": caller,
    }
