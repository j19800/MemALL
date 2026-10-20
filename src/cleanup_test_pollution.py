"""Archive test-pollution memories using the deny-list as the single criterion.

Using the same `security.disabled_agents` patterns for cleanup and for write
gating means the two definitions cannot drift: whatever the deployment bans
from being written is exactly what gets swept.

  python cleanup_test_pollution.py            # dry-run (preview only)
  python cleanup_test_pollution.py --apply    # archive for real
"""
import sys

sys.path.insert(0, r"E:\MemALL\src")

from memall.core.db import get_conn  # noqa: E402
from memall.core.denylist import is_disabled_agent  # noqa: E402
from memall.pipeline.ops import batch_archive  # noqa: E402

APPLY = "--apply" in sys.argv


def main() -> int:
    conn = get_conn()
    rows = conn.execute(
        "SELECT agent_name, COUNT(*) c FROM memories "
        "WHERE COALESCE(memory_status,'') != 'archived' "
        "GROUP BY agent_name ORDER BY c DESC"
    ).fetchall()
    total_active = conn.execute(
        "SELECT COUNT(*) FROM memories WHERE COALESCE(memory_status,'') != 'archived'"
    ).fetchone()[0]
    conn.close()

    targets = [(r["agent_name"], r["c"]) for r in rows
               if is_disabled_agent(r["agent_name"])]
    target_rows = sum(c for _, c in targets)

    print(f"active memories: {total_active}")
    print(f"deny-listed agents: {len(targets)}, rows: {target_rows}")
    print()
    for a, c in targets:
        print(f"  {a:32s} {c:5d}")

    if not targets:
        print("\nnothing to clean.")
        return 0

    if not APPLY:
        print("\n--- DRY RUN (pass --apply to archive) ---")
        for a, _ in targets:
            r = batch_archive(agent_name=a, days=0, dry_run=True)
            print(f"  would archive {r['matched']:4d}  {a}")
        print(f"\ntotal would archive: {target_rows}")
        return 0

    print("\n--- APPLYING ---")
    done = 0
    for a, expect in targets:
        r = batch_archive(agent_name=a, days=0, dry_run=False)
        done += r["archived"]
        flag = "ok" if r["archived"] == expect else "MISMATCH"
        print(f"  archived {r['archived']:4d}/{expect:<4d} {a}  [{flag}]")

    conn = get_conn()
    remaining = conn.execute(
        "SELECT COUNT(*) FROM memories WHERE COALESCE(memory_status,'') != 'archived'"
    ).fetchone()[0]
    archived = conn.execute(
        "SELECT COUNT(*) FROM memories WHERE memory_status = 'archived'"
    ).fetchone()[0]
    conn.close()
    print(f"\narchived now: {archived}")
    print(f"active remaining: {remaining}  (was {total_active}, archived {done})")
    return 0


if __name__ == "__main__":
    sys.exit(main())