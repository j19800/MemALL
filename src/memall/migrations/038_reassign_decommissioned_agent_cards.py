"""
Migration 038: reassign the four task cards orphaned by claude's roster exit.

Context
-------
Decision #1000527 (claude leaves the roster; new roster = dsh / opencode /
qoder / workbuddy) requires four live L5 task cards still addressed to
``claude`` be handed to their new owners:

    #1000411  -> dsh          (task-loop convergence arbitration)
    #11183    -> dsh          (thread_id inheritance technical assessment)
    #11174    -> opencode      (next-phase data-quality + productisation)
    #10784    -> workbuddy     (naming-standard review; qoder's measurement
                                half already delivered, see #1000531)

qoder proved (see #1000532) that this has NO executable path on the current
MCP surface: ``mcp/tools/memory_write.py`` strips ``agent_name`` from every
writable field (so no identity can re-attribute via update), and
``security.enforce_agent_ownership`` (on by default) rejects a non-supervisor
writing even ``metadata``. The only supervisor is ``opencode`` — which cannot
self-audit (AGENTS.md §5: verifier ≠ producer). So ownership transfer of a
decommissioned agent's cards is a *data repair*, not an agent-runtime
operation: it belongs in a migration the maintainer applies, not in a tool an
agent calls. That is why this migration exists instead of a ``reassign_to``
update parameter (which would first require deciding who maintains the
supervisor list — deferred to a P1 the human owner must rule on).

Operation
---------
For exactly the four ids above, move ``agent_name`` and ``metadata.assignee``
from claude to the new owner. Deliberately conservative:

  * keyed by an explicit id map, NOT by scanning ``agent_name = 'claude'`` —
    so the two pre-existing dispatch meta-cards (#8194 / #8047, no assignee,
    not in decision #1000527) are left for a human, and no future claude row
    is touched by surprise;
  * only rows still addressed to claude and not archived are changed;
  * ``subject`` prefix is never touched (``[任务]`` is the visibility
    predicate in check_fixes and migration 037 — changing it re-hides the
    card);
  * ``owner`` is left as-is (out of scope; the owner/creator invariant for
    these legacy cards is a separate audit item);
  * prior values are kept in ``metadata.previous_assignee`` plus
    ``reassigned_by`` / ``reassigned_at`` / ``reassigned_reason`` so the
    repair is auditable and reversible;
  * ``category`` and ``level`` are pinned (metadata.pinned_fields) so the
    keyword classifier cannot rewrite these cards again, the way m037 had to
    undo for the earlier orphans.

Idempotent: a second run finds each target row already reassigned (agent_name
is no longer 'claude') and applies nothing.
"""

MIGRATION_ID = "038_reassign_decommissioned_agent_cards"
DESCRIPTION = "Move 4 claude-exit task cards to their new roster owners"

# id -> new owner, per decision #1000527. Anything not listed is untouched.
_REASSIGN = {
    1000411: "dsh",
    11183: "dsh",
    11174: "opencode",
    10784: "workbuddy",
}

_REASSIGNED_BY = "migration-038/decision-1000527"
_REASON = "claude left the roster (decision #1000527)"


def _unwrap(value):
    """metadata values may be enveloped as {"value": …} by the pipeline."""
    if isinstance(value, dict):
        return value.get("value")
    return value


def apply(conn):
    import json
    from datetime import datetime, timezone

    moved = 0
    now = datetime.now(timezone.utc).isoformat()

    for mem_id, new_owner in _REASSIGN.items():
        row = conn.execute(
            "SELECT id, agent_name, metadata FROM memories "
            "WHERE id = ? AND level = 'L5' "
            "AND COALESCE(memory_status, '') != 'archived'",
            (mem_id,),
        ).fetchone()
        if row is None:
            continue
        if (row["agent_name"] or "") != "claude":
            # already reassigned (or never was claude's) — idempotent no-op
            continue

        try:
            raw = row["metadata"]
            meta = json.loads(raw) if isinstance(raw, str) and raw.strip() else (raw or {})
        except (json.JSONDecodeError, TypeError):
            meta = {}
        if not isinstance(meta, dict):
            meta = {}

        prev = _unwrap(meta.get("assignee")) or "claude"
        meta["previous_assignee"] = prev
        meta["assignee"] = new_owner
        meta["reassigned_by"] = _REASSIGNED_BY
        meta["reassigned_at"] = now
        meta["reassigned_reason"] = _REASON

        pinned = meta.get("pinned_fields") or []
        if isinstance(pinned, str):
            pinned = [pinned]
        for f in ("category", "level"):
            if f not in pinned:
                pinned.append(f)
        meta["pinned_fields"] = pinned

        conn.execute(
            "UPDATE memories SET agent_name = ?, metadata = ?, "
            "updated_at = ? WHERE id = ?",
            (new_owner, json.dumps(meta, ensure_ascii=False), now, mem_id),
        )
        moved += 1

    if moved:
        conn.commit()
