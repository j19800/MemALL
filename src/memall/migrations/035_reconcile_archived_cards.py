"""
Migration 035: Reconcile cards the two status tracks disagree about.

Migration 034 put each track back in its own place but deliberately left one
disagreement standing: rows with ``memory_status='archived'`` (soft-deleted by the
pipeline — L9 distill, ``forget.batch_archive``, dream pruning) whose
``metadata.status`` still said ``active``.  Two of them were live L5 task cards,
and they are exactly the "ghost pending task" the audit reported: every workflow
reader now selects ``pending_tasks()``, which excludes archived rows, so those
cards had already vanished from the todo and notification paths — while the
stored workflow status still claimed they were open, and ``034.validate()`` kept
reporting them.

Here the lifecycle track wins: a soft-deleted card is not pending work, so its
workflow status becomes ``archived``, the value that means the same thing on the
other track.  Nothing is deleted and nothing is hidden — the rows were already
invisible to both tracks' readers; only the misleading marker goes away.

The opposite direction is *not* reconciled on purpose: ``pipeline/forget.py``
archives expired tasks by setting ``metadata.status='archived'`` while leaving the
row live, so those memories stay searchable in history.  Treating that as
disagreement and archiving the column would silently drop them from
``retrieve()``.

Idempotent: the statement only touches rows that still disagree.
"""

import logging

from memall.core.status import TASK_STATUSES

MIGRATION_ID = "035_reconcile_archived_cards"
DESCRIPTION = "metadata.status=archived where memory_status is already archived"

logger = logging.getLogger(__name__)

_OPEN_STATUSES = tuple(s for s in TASK_STATUSES if s != "archived")
_OPEN_PH = ",".join("?" * len(_OPEN_STATUSES))


def apply(conn):
    reconciled = conn.execute(
        f"""
        UPDATE memories
           SET metadata = json_set(metadata, '$.status', 'archived')
         WHERE level = 'L5'
           AND category IN ('task', 'discussion')
           AND json_valid(metadata) = 1
           AND COALESCE(memory_status, '') = 'archived'
           AND json_extract(metadata, '$.status') IN ({_OPEN_PH})
        """,
        _OPEN_STATUSES,
    ).rowcount
    if reconciled:
        logger.info(
            "migration 035: %d card(s) soft-deleted by the pipeline still"
            " claimed an open workflow status",
            reconciled,
        )
