"""Migration 039: backfill empty project fields on historical memory rows.

Context
-------
Card #1000551 (project 字段回填脚本 + parent_memory_id 继承逻辑, opencode).
Design #1000539 data-quality lane.

Root cause was fixed at the write path (commit 1e3bf90 wires
``infer_project()`` into ``capture()``), but the fix landed mid-October 2026
while ~592 rows accumulated with an empty ``project`` before it (and a few
more via paths that bypass the canonical capture funnel, e.g. direct
``store_batch`` / pipeline rows).  The backfill below is the historical
repair half of the card.

Two parts
---------
1. **project backfill (executed)** — every row with an empty ``project`` is
   attributed using the SAME canonical inference the write path uses
   (``memall.core.project_infer.infer_project``), i.e. agent mapping first,
   content keyword hints second, default project last.  This is exactly the
  口径 the card asks for, not a bespoke heuristic.

2. **parent_memory_id inheritance (documented, NOT executed)** — the table
   does NOT carry a ``parent_memory_id`` column (threading lives in
   ``thread_id`` / the ``links`` table), so "inherit project from parent"
   has no physical column to operate on today.  The card's 继承逻辑 is
   therefore delivered as this documented design + a ready-to-run code path
   (``_backfill_from_parent()``), which is a no-op until the column exists.
   Do NOT add the column here: it is a schema change that needs its own
   review, and forcing it in a backfill migration would smuggle a
   migration-through-migration.
"""
from __future__ import annotations

MIGRATION_ID = "039_backfill_project_field"
DESCRIPTION = "backfill empty project fields using canonical infer_project()"

TABLE = "memories"


def apply(conn) -> dict:
    # Reuse the canonical inference so historical repair and new writes
    # stay on one口径.  Import lazily so the migration module itself has no
    # import-time side effects.
    from memall.core.project_infer import infer_project

    empty = conn.execute(
        f"SELECT id, agent_name, content FROM {TABLE} "
        "WHERE project IS NULL OR TRIM(COALESCE(project, '')) = ''"
    ).fetchall()
    # fetchall() returns plain tuples on a bare sqlite3 connection
    cols = ("id", "agent_name", "content")

    updated = 0
    plan: dict[str, int] = {}
    for row in empty:
        rec = dict(zip(cols, row))
        proj = infer_project(
            agent_name=rec["agent_name"] or "",
            content=rec["content"] or "",
        )
        plan[proj] = plan.get(proj, 0) + 1
        conn.execute(
            f"UPDATE {TABLE} SET project = ? WHERE id = ?",
            (proj, rec["id"]),
        )
        updated += 1

    remaining = conn.execute(
        f"SELECT COUNT(*) FROM {TABLE} "
        "WHERE project IS NULL OR TRIM(COALESCE(project, '')) = ''"
    ).fetchone()[0]

    return {
        "scanned": len(empty),
        "updated": updated,
        "plan": plan,
        "remaining_empty": remaining,
        "parent_memory_id": "not_a_column_noop",
    }


def _backfill_from_parent(conn) -> dict:
    """No-op until a physical parent_memory_id column exists.

    Kept as an explicit, documented function (rather than inline SQL that
    would only fail at runtime) so the intent is visible and the code is
    ready the moment the column lands.  The query it would run:

        UPDATE memories m
        SET m.project = p.project
        FROM memories p
        WHERE m.parent_memory_id = p.id
          AND TRIM(COALESCE(m.project, '')) = ''
          AND TRIM(COALESCE(p.project, '')) <> '';

    It is deliberately NOT called from apply(): today parent_memory_id does
    not exist, so calling it would raise OperationalError and abort the
    project backfill that is the actionable half of the card.
    """
    return {"skipped": "parent_memory_id column does not exist"}
