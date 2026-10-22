"""The two status tracks on ``memories``, and the SQL that reads them.

``memories`` carries status in two places and they are not the same thing:

``metadata.status``
    The **workflow track** for L5 task/discussion cards — ``active``, ``done``,
    ``blocked``, ``resolved``, ``converged``, ``archived``.  AGENTS.md contracts
    on this field, so it is the only place a task status may live.  Read it with
    :func:`task_status_eq`.

``memory_status``
    The **storage-lifecycle track** — ``archived`` (soft-deleted), ``dormant``,
    ``superseded``, ``conflict``, ``epoch``; NULL is an ordinary live row.
    Owned by the pipeline (archive, decay, conflict detection).  Read the
    not-deleted predicate with :func:`not_archived`.

Before this module existed, ``capture()`` mirrored a task status into
``memory_status`` and migration 023 backfilled the same, so one column carried
both state machines.  They drifted: soft-deleted tasks kept ``status=active``
and resurfaced in every agent's pending list, while a ``conflict`` flag written
by the pipeline overwrote the card's workflow status.  The mirroring is gone and
migration 034 cleared the legacy values.

``archived`` is intentionally in both vocabularies — in each track it means "out
of the live set", so the overlap is consistent rather than ambiguous.  Because
the two tracks are independent, a workflow query must AND in
:func:`not_archived`: a soft-deleted row is not pending whatever its metadata
says.

This lives in ``core`` rather than ``pipeline/sql_fragments`` because ``core``
must not import ``memall.pipeline`` (its package ``__init__`` pulls in every
pipeline step).  ``pipeline.sql_fragments`` re-exports these names so pipeline
callers have one import path.
"""

LIFECYCLE_STATUSES = frozenset(
    {"archived", "dormant", "superseded", "conflict", "epoch"}
)

TASK_STATUSES = frozenset(
    # "claimed" is the accepted-but-not-delivered state: the assignee has
    # taken the card (memall_write action=task_claim) and is working on it.
    # It must stay in the queue, so readers treat active+claimed alike
    # (design #1000007 state machine: active -> claimed -> done -> archived).
    {"active", "claimed", "done", "blocked", "resolved", "converged", "archived"}
)


def task_status_of(value):
    """Return *value* as a valid workflow status, or ``None``.

    Tolerates the ``{"value": X, "_meta": {...}}`` envelope that older writers
    left in ``metadata`` (see :func:`memall.core.utils.unwrap`) by unwrapping
    before validating.  Anything outside :data:`TASK_STATUSES` — including a
    lifecycle-only value such as ``dormant`` — yields ``None``, so callers
    cannot mistake it for a status.
    """
    from memall.core.utils import unwrap

    scalar = unwrap(value) if isinstance(value, (dict, str)) else value
    if isinstance(scalar, str) and scalar in TASK_STATUSES:
        return scalar
    return None


def not_archived(alias: str = "") -> str:
    """Predicate selecting rows that are not soft-deleted.

    NULL is treated as live.  ``alias`` is a bare table alias (``"m"``), not
    ``"m."``.
    """
    col = f"{alias}.memory_status" if alias else "memory_status"
    return f"COALESCE({col}, '') != 'archived'"


def task_status_eq(status: str, alias: str = "") -> str:
    """Predicate matching the workflow track on a memory row.

    ``status`` is validated against :data:`TASK_STATUSES` because it is
    interpolated into SQL: callers get an error instead of an injection point or
    a silently-empty result set.
    """
    if status not in TASK_STATUSES:
        raise ValueError(
            f"unknown task status {status!r}; valid: "
            f"{', '.join(sorted(TASK_STATUSES))}"
        )
    meta = f"{alias}.metadata" if alias else "metadata"
    return f"json_extract({meta}, '$.status') = '{status}'"


def pending_tasks(alias: str = "") -> str:
    """WHERE body for cards an agent still owes work on (task or discussion)."""
    return f"{task_status_eq('active', alias)} AND {not_archived(alias)}"


NOT_ARCHIVED = not_archived()
PENDING_TASKS = pending_tasks()
