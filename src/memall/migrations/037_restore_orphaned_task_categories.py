"""
Migration 037: restore the contract category on orphaned task cards.

Context
-------
``AGENTS.md`` §二 defines a task card by its schema: ``level='L5'``,
``category='task'``, ``metadata.status``, and an addressee.  Eleven live cards
carry a ``[任务] …`` subject and a valid ``metadata.assignee``, but their
**category was rewritten by the keyword classifier** to meeting / planning /
documentation / architecture.

They predate ``metadata.pinned_fields`` (the pin that now protects contract
categories), so nothing stopped the classifier — and nothing noticed, because
a card whose category is wrong is simply invisible: both the notification bar
(``mcp/notify.py::pending_tasks_for``) and the task view
(``mcp/tasks.py``) select ``category='task'``.  Work addressed to an agent sat
in the store with nobody able to see it.

Operation
---------
For every non-archived ``L5`` row whose subject starts with ``[任务]``,
category is not already ``task``, and which carries a task-shaped metadata
block, restore ``category='task'`` and pin ``category+level`` so this cannot
happen again.

Deliberately conservative:
  * only rows that still have an active/claimed status and an addressee are
    touched — a ``[任务] …`` subject on a closed or malformed row is left
    alone rather than guessed at;
  * archived rows are untouched;
  * the original category is preserved in ``metadata.category_restored_from``
    so the repair is auditable and reversible.

Idempotent: a second run finds no mismatched rows.
"""

MIGRATION_ID = "037_restore_orphaned_task_categories"
DESCRIPTION = "Restore category='task' on [任务] cards the classifier rewrote"

# Statuses that still represent real, owed work.
_OWED = {"active", "claimed"}


def _unwrap(value):
    """metadata values may be enveloped as {"value": …} by the pipeline."""
    if isinstance(value, dict):
        return value.get("value")
    return value


def apply(conn):
    import json

    rows = conn.execute(
        "SELECT id, category, subject, agent_name, metadata FROM memories "
        "WHERE level = 'L5' "
        "AND subject LIKE '[任务]%' "
        "AND category != 'task' "
        "AND COALESCE(memory_status, '') != 'archived'"
    ).fetchall()

    fixed = 0
    for r in rows:
        try:
            meta = r["metadata"]
            meta = json.loads(meta) if isinstance(meta, str) else (meta or {})
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(meta, dict):
            continue

        status = _unwrap(meta.get("status"))
        status = (status or "active").strip().lower() if isinstance(status, str) else "active"
        if status not in _OWED:
            continue  # closed / malformed — do not guess

        addressee = _unwrap(meta.get("assignee")) or r["agent_name"]
        if not addressee:
            continue  # nobody to address it to — leave it for a human

        meta["category_restored_from"] = r["category"]
        pinned = meta.get("pinned_fields") or []
        if isinstance(pinned, str):
            pinned = [pinned]
        for f in ("category", "level"):
            if f not in pinned:
                pinned.append(f)
        meta["pinned_fields"] = pinned

        conn.execute(
            "UPDATE memories SET category = 'task', metadata = ?, "
            "updated_at = datetime('now') WHERE id = ?",
            (json.dumps(meta, ensure_ascii=False), r["id"]),
        )
        fixed += 1

    if fixed:
        conn.commit()
