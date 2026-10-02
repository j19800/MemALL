"""
Migration 027 — repair invalid `level` values and normalize `agent_name` casing.

Context: the production database accumulated rows whose `level` column holds a
value that is not part of the canonical enum (P0,P1,P2,L1..L11, per ADR-0001):

  1. `level='archived'` (820 rows) — an archival pass wrote the *status* into the
     `level` column. The real level is recoverable from the content prefix used by
     the distill/integrate/session steps: `[L9 蒸馏]` / `[L9 聚合]` → L9,
     `[L10 整合]` → L10, `[L4 会话]` → L4. The archival marker is moved to the
     `memory_status` column where it belongs.
  2. `level='medium'` (4 rows) — written by an external producer (TradingAgents);
     reclassified to L1 (raw fact).
  3. `level='P4'` (5 rows) — test junk outside the enum; deleted together with
     their edges / embeddings / pipeline events.
  4. `agent_name` casing drift — Claude/Marvis/WorkBuddy merged into the
     canonical lower-case forms used by `normalize_agent_name()`.

Idempotent and safe to re-run.
"""

MIGRATION_ID = "027_fix_invalid_levels_and_agent_case"
DESCRIPTION = "Repair invalid level values (archived/medium/P4) and normalize agent_name casing"

# Content-prefix → real level, for rows whose level was clobbered by archival.
_PREFIX_LEVELS = (("[L9 ", "L9"), ("[L10 ", "L10"), ("[L4 ", "L4"))

# agent_name casing drift → canonical form
_AGENT_CANON = (("Claude", "claude"), ("Marvis", "marvis"), ("WorkBuddy", "workbuddy"))


def _ids_with_level(conn, level: str) -> list:
    return [r[0] for r in conn.execute("SELECT id FROM memories WHERE level = ?", (level,))]


def apply(conn) -> None:
    conn.execute("BEGIN")
    try:
        # 1. archived → restore real level from content prefix; marker → memory_status
        for prefix, level in _PREFIX_LEVELS:
            conn.execute(
                "UPDATE memories SET level = ?, memory_status = 'archived' "
                "WHERE level = 'archived' AND content LIKE ?",
                (level, prefix + "%"),
            )
        # any leftover archived row (unknown origin) → L1 raw fact, still archived
        conn.execute(
            "UPDATE memories SET level = 'L1', memory_status = 'archived' "
            "WHERE level = 'archived'"
        )

        # 2. medium → L1 raw fact
        conn.execute("UPDATE memories SET level = 'L1' WHERE level = 'medium'")

        # 3. P4 test junk → remove rows and everything referencing them
        junk_ids = _ids_with_level(conn, "P4")
        if junk_ids:
            marks = ",".join("?" * len(junk_ids))
            conn.execute(
                f"DELETE FROM edges WHERE source_id IN ({marks}) OR target_id IN ({marks})",
                junk_ids * 2,
            )
            for table in ("memory_embeddings", "pipeline_events", "memory_clusters",
                          "memory_entities", "memory_edges", "suggestions"):
                try:
                    conn.execute(
                        f"DELETE FROM {table} WHERE memory_id IN ({marks})", junk_ids
                    )
                except Exception:
                    pass  # table may not exist / use a different key on older DBs
            try:
                conn.execute(
                    f"DELETE FROM mem_vec WHERE rowid IN ({marks})", junk_ids
                )
            except Exception:
                pass  # vec0 unavailable on plain connections
            conn.execute(f"DELETE FROM memories WHERE id IN ({marks})", junk_ids)

        # 4. agent_name casing normalization
        for old, new in _AGENT_CANON:
            conn.execute(
                "UPDATE memories SET agent_name = ? WHERE agent_name = ?", (new, old)
            )

        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise


def rollback(conn) -> None:
    # Data repair is not meaningfully reversible (the original invalid values are
    # the bug). Restore the archival marker only, for manual inspection.
    conn.execute(
        "UPDATE memories SET level = 'archived' "
        "WHERE memory_status = 'archived' AND level IN ('L9', 'L10', 'L4', 'L1')"
    )