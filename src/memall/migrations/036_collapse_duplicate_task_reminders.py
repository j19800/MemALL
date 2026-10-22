"""
Migration 036: Collapse duplicate task reminders onto their task id.

The task-reminder round used to dedup on a 6-hour time window while
``memories.content_hash`` carries a PERMANENT UNIQUE constraint, so every
reminder past the window re-used its hash and raised
``UNIQUE constraint failed: memories.content_hash``, aborting the whole round
(audit F6).  The round now dedups on the task id recorded in the reminder's
metadata instead — the identity of a (task, agent) pair — and the body keeps
the task id so two tasks sharing a subject still hash differently.

That change left the reminders written under the OLD body format behind: the
old body had no task-id suffix, so its hash differs from the hash the same
(task, agent) pair now produces.  Hash-keyed dedup therefore re-notified
every one of them — each of those tasks got a SECOND reminder row for the
same task, which is exactly the duplicate-delivery the reminder table is
supposed to avoid.

This migration keeps the OLDEST reminder for each task and drops the
newer duplicates, so the table returns to one row per task.  It deletes
nothing else: a task with only one reminder is untouched, and a task with
several keeps the one that has been there longest (the first notification the
agent received).

Idempotent: after the collapse there is at most one live reminder per task,
so re-running finds nothing to drop.
"""

import logging

MIGRATION_ID = "036_collapse_duplicate_task_reminders"
DESCRIPTION = "keep one task_pending reminder per task_id"

logger = logging.getLogger(__name__)


def apply(conn):
    # Duplicates: a task_pending row whose task_id already has an older
    # live sibling.  The subquery picks the oldest id per (task_id), so
    # every other row for that task is a duplicate.
    dropped = conn.execute(
        """
        DELETE FROM memories
         WHERE category = 'task_pending'
           AND json_valid(metadata) = 1
           AND json_extract(metadata, '$.task_id') IS NOT NULL
           AND id NOT IN (
                SELECT MIN(id) FROM memories
                 WHERE category = 'task_pending'
                   AND json_valid(metadata) = 1
                   AND json_extract(metadata, '$.task_id') IS NOT NULL
                 GROUP BY json_extract(metadata, '$.task_id')
           )
        """
    ).rowcount
    if dropped:
        logger.info(
            "migration 036: dropped %d duplicate task reminder(s) "
            "(one row per task_id is kept)",
            dropped,
        )
