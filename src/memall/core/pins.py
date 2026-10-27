"""Pinned classification fields — the contract between writers and classifiers.

A caller that explicitly chose a ``category`` or ``level`` wrote a contract
with downstream consumers, not a suggestion.  ``notify_pending_tasks()``
selects ``category='task'``; a project-scoped L10 report is selected by
``level='L10'``.  When the keyword-rule classifiers rewrote those chosen
values, the chains died silently (audit P1-16: every task written through
capture() was flipped to fix/testing by a content rule within 60s, and the
dsh reminder query returned zero).

capture() records the caller's explicit fields in
``metadata.pinned_fields = ["category", "level"]``; both classifier paths
(event_processor._inline_classify per new memory, classify_step on batch
runs) consult :func:`is_field_pinned` and never overwrite what was pinned.

Category contract values are pinned *even when provenance is unreadable* —
anything in :data:`CONTRACT_CATEGORIES` was by definition not a default.
"""

# Categories whose value is a consumer contract; they are never keyword-rule
# material. "task" rows feed notify_pending_tasks, "discussion*" rows feed the
# convergence/trace chain, and so on.
CONTRACT_CATEGORIES = frozenset({
    # A goal card carries a phase machine; the classifier rewriting its
    # category/level would make the driver lose track of it entirely.
    "goal",
    "task",
    "discussion",
    "discussion_response",
    "decision",
    "identity",
    "rule",
})


def pinned_fields_of(metadata) -> frozenset:
    """Set of pinned field names recorded in a metadata value (str or dict)."""
    import json

    if isinstance(metadata, dict):
        meta = metadata
    elif isinstance(metadata, str) and metadata:
        try:
            meta = json.loads(metadata)
        except (json.JSONDecodeError, TypeError, ValueError):
            return frozenset()
        if not isinstance(meta, dict):
            return frozenset()
    else:
        return frozenset()
    fields = meta.get("pinned_fields")
    if not isinstance(fields, list):
        return frozenset()
    return frozenset(str(f) for f in fields)


def is_field_pinned(metadata, field: str, value: str | None = None) -> bool:
    """True when ``field`` must not be reclassified.

    Explicit pin wins; for ``category`` additionally any contract value is
    pinned by definition (belt and braces for rows written before the pin
    existed, or through paths that could not record provenance).
    """
    if field in pinned_fields_of(metadata):
        return True
    if field == "category" and value and value in CONTRACT_CATEGORIES:
        return True
    return False
