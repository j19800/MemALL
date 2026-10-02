"""
Migration 029 — full agent_name case backfill + unique identity guard.

Migration 027 only lower-cased ``memories.agent_name`` for three known drifters
(Claude/Marvis/WorkBuddy). The same drift survived everywhere else, and worse, in
``identities`` it produced genuine duplicate rows because the table carries a
case-sensitive UNIQUE on ``agent_name``:

  * ``Kun`` (id 106) vs ``kun`` (id 107)
  * ``Marvis`` (id 111) vs ``marvis`` (id 30)

This migration:

  1. Lower-cases ``agent_name`` on every table that carries the column.
  2. Merges the case-duplicate ``identities`` rows into the canonical lower-case
     survivor (preferring the already-lower-case row), coalescing non-empty
     profile fields, then deletes the loser.
  3. Replaces the non-unique ``idx_identities_agent_lower`` with a UNIQUE index on
     ``lower(agent_name)`` so the drift cannot come back at the storage layer.

Idempotent and safe to re-run.
"""

MIGRATION_ID = "029_normalize_agent_case_and_unique_identity"
DESCRIPTION = "Backfill agent_name casing everywhere; merge duplicate identities; add unique lower() index"

# Tables carrying an agent_name column (identities handled separately).
_AGENT_TABLES = (
    "ai_monetization_cases", "corrections", "distill_history", "epochs", "facts",
    "memories", "narratives", "onboarding_status", "query_log", "sessions",
    "time_slices",
)

# Fields coalesced from the loser into the survivor (first non-empty wins).
_MERGE_FIELDS = (
    "agent_type", "description", "icon", "status", "last_heartbeat",
    "metadata", "trusted_by", "owner_type", "profile_json",
    "persona_updated_at", "permission_level", "identity_profile",
)

_EMPTY = {"", "{}", "[]", "null", "None"}


def _is_empty(v) -> bool:
    return v is None or (isinstance(v, str) and v.strip() in _EMPTY)


def apply(conn) -> None:
    conn.execute("BEGIN")
    try:
        # 1. Lower-case agent_name on the plain tables.
        for table in _AGENT_TABLES:
            try:
                conn.execute(
                    f"UPDATE {table} SET agent_name = lower(agent_name) "
                    f"WHERE agent_name <> lower(agent_name)"
                )
            except Exception:
                pass  # table absent on older DBs

        # 2. Merge case-duplicate identities.
        id_cols = [r[1] for r in conn.execute("PRAGMA table_info(identities)")]
        fields = [f for f in _MERGE_FIELDS if f in id_cols]

        groups = [
            r[0] for r in conn.execute(
                "SELECT lower(agent_name) FROM identities "
                "GROUP BY lower(agent_name) HAVING count(*) > 1"
            )
        ]
        for key in groups:
            rows = [
                dict(r) for r in conn.execute(
                    "SELECT * FROM identities WHERE lower(agent_name) = ? ORDER BY id",
                    (key,),
                )
            ]
            survivor = next((r for r in rows if r["agent_name"] == key), rows[0])
            for r in rows:
                if r["id"] == survivor["id"]:
                    continue
                for f in fields:
                    if _is_empty(survivor.get(f)) and not _is_empty(r.get(f)):
                        conn.execute(
                            f"UPDATE identities SET {f} = ? WHERE id = ?",
                            (r[f], survivor["id"]),
                        )
                        survivor[f] = r[f]
                conn.execute("DELETE FROM identities WHERE id = ?", (r["id"],))

        # 3. Any remaining non-lower-case identity name (now collision-free).
        conn.execute(
            "UPDATE identities SET agent_name = lower(agent_name) "
            "WHERE agent_name <> lower(agent_name)"
        )

        # 4. Enforce one identity per canonical (lower-case) name.
        conn.execute("DROP INDEX IF EXISTS idx_identities_agent_lower")
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_identities_agent_name_lower_unique "
            "ON identities(lower(agent_name))"
        )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise


def rollback(conn) -> None:
    # Merging deleted rows; not meaningfully reversible. Restore the non-unique
    # index so the schema at least matches the pre-migration shape.
    conn.execute("DROP INDEX IF EXISTS idx_identities_agent_name_lower_unique")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_identities_agent_lower ON identities(LOWER(agent_name))"
    )