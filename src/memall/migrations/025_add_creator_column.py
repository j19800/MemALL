"""
Migration 025: Add ``creator`` column to enforce ownership invariant #1.

Design memory #823 defines three invariants. Invariant #1: ``owner != creator`` and
``owner`` is ALWAYS a human; ``creator`` is the agent that triggered the write.
The legacy capture path silently set ``owner = agent_name`` whenever no owner was
supplied (and even rewrote a caller-supplied human owner down to the writing agent),
so 1263/4253 memories ended up with ``owner == agent_name`` and there was no
``creator`` column at all to record who actually wrote them.

This migration:
  1. Adds ``creator TEXT NOT NULL DEFAULT ''`` + index (idempotent via PRAGMA check).
  2. Backfills existing rows:
       - rows where ``owner == agent_name``  -> creator = agent_name, owner = human_owner
       - other rows                          -> creator = agent_name (owner preserved)
  3. Ensures the configured human owner exists in ``identities`` as agent_type='human'
     so the invariant has a concrete anchor.

Risk: low. All operations are additive or idempotent UPDATEs; the column add uses a
transactional ALTER. The migration runner auto-backs-up the DB before applying.
"""

MIGRATION_ID = "025_add_creator_column"
DESCRIPTION = "Add creator column; backfill owner/creator per invariant #1"


def _has_column(conn, table: str, column: str) -> bool:
    cur = conn.execute(f"PRAGMA table_info({table})")
    return any(r["name"] == column for r in cur.fetchall())


def apply(conn):
    from memall.config import get_config

    human_owner = get_config("identity.human_owner", "老陈") or "老陈"

    fk_was_on = conn.execute("PRAGMA foreign_keys").fetchone()[0]
    conn.execute("PRAGMA foreign_keys=OFF")
    try:
        conn.execute("BEGIN TRANSACTION")

        # 1. Add creator column if missing (additive, transactional).
        if not _has_column(conn, "memories", "creator"):
            conn.execute(
                "ALTER TABLE memories ADD COLUMN creator TEXT NOT NULL DEFAULT ''"
            )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_memories_creator ON memories(creator)"
        )

        # 2. Backfill existing rows.
        #    Rows where the owner was silently collapsed onto the writing agent:
        #    record the agent as creator, and restore the human as owner.
        updated_owned = conn.execute(
            "UPDATE memories SET creator = agent_name, owner = ? "
            "WHERE owner = agent_name",
            (human_owner,),
        ).rowcount

        #    Remaining rows (owner != agent_name): keep owner, just stamp creator.
        updated_rest = conn.execute(
            "UPDATE memories SET creator = agent_name "
            "WHERE (creator IS NULL OR creator = '') AND agent_name <> ''"
        ).rowcount

        #    Any memory still owned by an AI agent (owner is an ai identity but
        #    owner != agent_name — e.g. an agent portrait distilled by one agent
        #    but attributed to another) also violates invariant #1.  Reassign
        #    those owners to the human owner.  Human-owned rows are untouched
        #    because 'human' identities are not in the ai subquery.
        updated_ai_owned = conn.execute(
            "UPDATE memories SET owner = ? "
            "WHERE owner IN (SELECT agent_name FROM identities WHERE agent_type='ai') "
            "AND owner <> ?",
            (human_owner, human_owner),
        ).rowcount

        # 3. Ensure the human owner identity exists.
        conn.execute(
            "INSERT OR IGNORE INTO identities (agent_name, agent_type) VALUES (?, 'human')",
            (human_owner,),
        )

        conn.execute("COMMIT")
        return {
            "creator_added": True,
            "owner_collapsed_rows_fixed": updated_owned,
            "creator_stamped_rows": updated_rest,
            "ai_owned_rows_fixed": updated_ai_owned,
            "human_owner": human_owner,
        }
    except Exception:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.execute(f"PRAGMA foreign_keys={'ON' if fk_was_on else 'OFF'}")
