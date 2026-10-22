"""
Migration 034: Split the two status tracks on ``memories``.

``memory_status`` and ``metadata.status`` used to be the same thing, then the
pipeline started writing lifecycle states into ``memory_status`` while
``capture()`` kept mirroring task statuses into it (migration 023 backfilled the
same).  One column carried two state machines and they drifted: soft-deleted task
cards kept ``status=active`` and were re-injected as pending work, and a
``conflict`` flag written by dream/reasoning overwrote a card's workflow state.

The tracks are now separate — ``metadata.status`` is the workflow track,
``memory_status`` is storage-lifecycle only (see :mod:`memall.core.status`).
This migration makes the stored data match, in the order that matters:

1. unwrap the ``{"value": X, "_meta": {...}}`` envelopes left in
   ``metadata.status`` by an older writer.  An enveloped value matches no
   ``json_extract`` comparison, so 3 cards whose real status was ``active`` and
   every archived/done marker were invisible to all notification paths.
2. adopt a mirrored task status from ``memory_status`` into
   ``metadata.status`` for the L5 cards that have no status of their own.
3. give the remaining open L5 task cards ``status=active`` — the documented
   default — so the task chain can see them at all.
4. clear task vocabulary out of ``memory_status``, leaving only lifecycle
   values (``archived``/``dormant``/``superseded``/``conflict``/``epoch``).

Idempotent: each statement is guarded by the condition it repairs.  ``validate``
is exported so the test suite can assert the invariant after a fresh migrate.
"""

import logging

from memall.core.status import LIFECYCLE_STATUSES, TASK_STATUSES

MIGRATION_ID = "034_split_status_tracks"
DESCRIPTION = "metadata.status = workflow track only; memory_status = lifecycle track only"

logger = logging.getLogger(__name__)

_CARD_CATEGORIES = ("task", "discussion")
_LIFECYCLE_PH = ",".join("?" * len(LIFECYCLE_STATUSES))
_TASK_PH = ",".join("?" * len(TASK_STATUSES))


def apply(conn):
    # 1. Flatten versioned envelopes on the workflow track.
    conn.execute(
        """
        UPDATE memories
           SET metadata = json_set(metadata, '$.status',
                                    json_extract(metadata, '$.status.value'))
         WHERE json_valid(metadata) = 1
           AND json_type(metadata, '$.status') = 'object'
           AND json_type(metadata, '$.status.value') = 'text'
        """
    )

    # 2. A mirrored value in the lifecycle column is the card's real status when
    #    the card itself says nothing.  Runs before step 4, which clears it.
    cats = ",".join("?" * len(_CARD_CATEGORIES))
    conn.execute(
        f"""
        UPDATE memories
           SET metadata = json_set(metadata, '$.status', memory_status)
         WHERE level = 'L5'
           AND category IN ({cats})
           AND json_valid(metadata) = 1
           AND json_extract(metadata, '$.status') IS NULL
           AND memory_status IN ({_TASK_PH})
        """,
        (*_CARD_CATEGORIES, *TASK_STATUSES),
    )

    # 3. Open L5 task cards default to active (AGENTS.md: 缺省视为 active).
    conn.execute(
        f"""
        UPDATE memories
           SET metadata = json_set(metadata, '$.status', 'active')
         WHERE level = 'L5'
           AND category = 'task'
           AND json_valid(metadata) = 1
           AND json_extract(metadata, '$.status') IS NULL
           AND COALESCE(memory_status, '') != 'archived'
        """
    )

    # 4. Nothing that is not a lifecycle state may stay in the lifecycle column.
    #    NULL means "ordinary live row", which is what these rows are.
    removed = conn.execute(
        f"""
        UPDATE memories
           SET memory_status = NULL
         WHERE memory_status IS NOT NULL
           AND memory_status NOT IN ({_LIFECYCLE_PH})
        """,
        tuple(LIFECYCLE_STATUSES),
    ).rowcount
    if removed:
        logger.info(
            "migration 034: cleared %d non-lifecycle value(s) from memory_status",
            removed,
        )


def validate(conn) -> dict:
    """The post-migration invariant, for doctor and the test suite."""
    lifecycle_bad = conn.execute(
        f"SELECT COUNT(*) FROM memories WHERE memory_status IS NOT NULL "
        f"AND memory_status NOT IN ({_LIFECYCLE_PH})",
        tuple(LIFECYCLE_STATUSES),
    ).fetchone()[0]
    enveloped = conn.execute(
        "SELECT COUNT(*) FROM memories WHERE json_valid(metadata) = 1 "
        "AND json_type(metadata, '$.status') = 'object'"
    ).fetchone()[0]
    ghost = conn.execute(
        f"""
        SELECT COUNT(*) FROM memories
         WHERE level = 'L5'
           AND category IN ({",".join("?" * len(_CARD_CATEGORIES))})
           AND COALESCE(memory_status, '') = 'archived'
           AND json_extract(metadata, '$.status') = 'active'
        """,
        _CARD_CATEGORIES,
    ).fetchone()[0]
    return {
        "lifecycle_track_violations": lifecycle_bad,
        "enveloped_workflow_statuses": enveloped,
        "archived_but_pending_cards": ghost,
    }
