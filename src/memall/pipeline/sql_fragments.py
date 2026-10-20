"""Shared SQL fragments for pipeline queries.

Why this exists
---------------
``memories.memory_status`` carries several meanings:

==============  ==========================================================
``archived``    soft-deleted — the row is on disk but NOT part of the
                live store (``ops.batch_archive`` sets it; ``batch_restore``
                clears it).  Pipeline steps must not treat these as
                observations.
``dormant``     alive but stale — still legitimate input.
``superseded``  alive, replaced by a newer row — still legitimate input.
``conflict``    alive, flagged for review.
``epoch``       alive, marker used by the epoch step.
==============  ==========================================================

Only ``archived`` means "deleted".  Every content-scanning pipeline query
therefore has to exclude it, or an archived memory is distilled, promoted,
summarised and re-archived as if it were live — and the pipeline can then
fail outright when the archived row belongs to a denied identity.

Rather than repeat the predicate in ~250 call sites (and get it subtly
different in each), the canonical fragment lives here and is imported by the
steps that scan for content.
"""

# Rows that are NOT archived.  NULL is treated as live (the column is
# nullable and most rows never set it).
NOT_ARCHIVED = "COALESCE(memory_status, '') != 'archived'"

# Same predicate for queries that alias the table as ``m``.
NOT_ARCHIVED_M = "COALESCE(m.memory_status, '') != 'archived'"


def add_not_archived(where: str, alias: str = "") -> str:
    """Append the not-archived predicate to an existing WHERE body.

    Args:
        where: existing WHERE body (without the keyword); may be empty.
        alias: table alias to qualify the column with ("" for no alias).
    """
    clause = NOT_ARCHIVED_M if alias else NOT_ARCHIVED
    if alias:
        clause = f"COALESCE({alias}.memory_status, '') != 'archived'"
    if not where or not where.strip():
        return clause
    return f"{where} AND {clause}"


__all__ = ["NOT_ARCHIVED", "NOT_ARCHIVED_M", "add_not_archived"]
