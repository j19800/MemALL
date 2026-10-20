"""
Migration 031: purge test identities from the ``identities`` table.

Context
-------
Audit P2-14: the ``identities`` table accumulated 20+ test identities
(``autocapXXXX``, ``probe2/3/4/6/8``, ``ac_dbg``, ``ac_dbg2``, ``ac_min``,
``acverify*``, ``final_test``, ``smoke``, ``mcp_audit``, ``test_agent``,
``t_agent``, ``e2e_agent``, ``claude_test``, ``test_health``,
``perf_test_direct``, ``gateway_test``, ``research_agent`` …).  They pollute
every identity-facing surface: agent listings, the trust model, persona
profiles, the agent round and the injection context all treated them as
real collaborators.

Operation
---------
Delete the identity rows whose name matches the write deny-list
(``security.disabled_agents`` — the same patterns that refuse a write) or
one of the known test-only exact names.  A name matching neither is left
alone: a real identity must never be removed because of a naming
coincidence.

Their memories are NOT deleted here — the store cleanup archives those
separately (``cleanup_test_pollution.py``), so history stays inspectable.

Idempotent: re-running finds nothing to delete.
"""

MIGRATION_ID = "031_purge_test_identities"
DESCRIPTION = "Delete test identities (autocap*/probe*/ac_*/…) from identities"

# Exact names that are test identities but are not glob-covered by the
# deployment's deny-list.
EXTRA_EXACT = {
    "smoke", "final_test", "mcp_audit", "test_agent", "t_agent",
    "e2e_agent", "claude_test", "test_health", "perf_test_direct",
    "gateway_test", "research_agent", "workbuddy-e2e",
}


def apply(conn):
    from memall.config import get_config
    from memall.core.denylist import matches_any

    patterns = get_config("security.disabled_agents", []) or []
    rows = conn.execute("SELECT id, agent_name FROM identities").fetchall()
    hits = []
    for r in rows:
        name = (r["agent_name"] or "").strip()
        if not name:
            continue
        if matches_any(name, patterns) or name.lower() in EXTRA_EXACT:
            hits.append(r["id"])
    if not hits:
        return
    ph = ",".join("?" * len(hits))
    conn.execute(f"DELETE FROM identities WHERE id IN ({ph})", hits)
    conn.commit()
