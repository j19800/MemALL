"""Shared optimistic-concurrency helper for task-card metadata writes.

Every task transition currently does read -> mutate the metadata dict in
memory -> write the WHOLE dict back with ``UPDATE ... WHERE id = ?``.  Two
concurrent transitions therefore interleave as:

    T1 read(active)            T2 read(active)
    T1 write(status=claimed)   ...
                               T2 write(status=done)   <- completion lands
                               ...
    T1's write is already gone if it came first, but if T1 writes LAST the
    completion is silently discarded.  Measured 10/40 on real claim/resolve
    interleavings: a resolved card reappeared as ``claimed``.

The fix is a compare-and-set on the status the writer observed:

    UPDATE memories SET metadata = ?, updated_at = ?
     WHERE id = ? AND <status predicate>

If the row moved under us the UPDATE touches 0 rows and the caller retries
(or reports a conflict) instead of overwriting somebody else's work.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Optional

# Statuses a card is finished with.  A write that would move a card OUT of
# one of these (because it was based on an older read) must lose.
TERMINAL_STATUSES = frozenset({"done", "resolved", "archived", "blocked",
                               "closed", "verified", "converged"})


def is_terminal_task_status(status) -> bool:
    """True when *status* means the card is finished and must not be reopened.

    ``claim_task`` originally refused only ("done", "archived") while the
    lifecycle also closes cards as "resolved" / "blocked" / "converged".  A
    claim could therefore reopen a completed card -- and once the CAS was in
    place it did so "correctly", because the predicate matched the state it
    had just read: resolve landed first, claim read "resolved", and wrote
    "claimed" over it.  Terminal means terminal: every closer shares this set.
    """
    return str(status or "").strip().lower() in TERMINAL_STATUSES


def _status_of(raw) -> str:
    if isinstance(raw, dict):
        return str(raw.get("status") or "")
    return ""


def json_status_expr(column: str = "metadata") -> str:
    """SQLite expression yielding the status stored in a metadata JSON blob."""
    return (f"COALESCE(json_extract({column}, '$.status'), '')")


def cas_update_metadata(conn, task_id: int, meta: dict,
                        expected_status: Optional[str],
                        now: Optional[str] = None) -> bool:
    """Write ``meta`` only if the stored status is still ``expected_status``.

    ``expected_status`` is what the caller read *before* deciding.  Passing
    ``None`` means "I did not read a status / do not care" and falls back to
    an unconditional write — every existing caller that has no notion of a
    prior state keeps working, but the transitions that matter (claim,
    resolve, block) pass the status they actually observed and become
    conflict-detecting.

    Returns True when the row was updated, False when it moved underneath us.
    """
    if now is None:
        now = datetime.now(timezone.utc).isoformat()
    payload = json.dumps(meta, ensure_ascii=False)

    if expected_status is None:
        cur = conn.execute(
            "UPDATE memories SET metadata = ?, updated_at = ? WHERE id = ?",
            (payload, now, task_id))
        return cur.rowcount > 0

    cur = conn.execute(
        "UPDATE memories SET metadata = ?, updated_at = ? "
        f"WHERE id = ? AND {json_status_expr()} = ?",
        (payload, now, task_id, expected_status))
    return cur.rowcount > 0
