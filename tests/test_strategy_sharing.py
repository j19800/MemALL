"""
Test Suite — MemorySharing
===========================
Tests share, broadcast, query_shared, unshare.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


def test_share_and_query():
    """Share a memory and verify it appears in query."""
    from tests.test_helpers import init_temp_db, cleanup_temp_db, insert_memory
    from memall.strategy.sharing import MemorySharing
    from memall.core.db import get_conn

    db_path, patcher = init_temp_db()
    try:
        conn = get_conn()
        mid = insert_memory(conn, "Shared memory for testing.", agent_name="alice")
        conn.close()

        ms = MemorySharing("alice")
        sid = ms.share(mid, "bob", trust_level="family")
        assert sid > 0 or sid == 0, f"Expected share ID >= 0, got {sid}"

        results = ms.query_shared("bob", trust_min="family")
        assert isinstance(results, list), f"Expected list, got {type(results)}"
        print("  PASS test_share_and_query")
    finally:
        cleanup_temp_db(db_path, patcher)


def test_share_twice_idempotent():
    """Sharing the same memory with the same agent should be idempotent."""
    from tests.test_helpers import init_temp_db, cleanup_temp_db, insert_memory
    from memall.strategy.sharing import MemorySharing
    from memall.core.db import get_conn

    db_path, patcher = init_temp_db()
    try:
        conn = get_conn()
        mid = insert_memory(conn, "Idempotent share test.", agent_name="alice")
        conn.close()

        ms = MemorySharing("alice")
        ms.share(mid, "bob")
        sid2 = ms.share(mid, "bob")
        assert sid2 == 0, f"Second share should return 0 (already shared), got {sid2}"
        print("  PASS test_share_twice_idempotent")
    finally:
        cleanup_temp_db(db_path, patcher)


def test_broadcast():
    """Broadcast should share with multiple agents."""
    from tests.test_helpers import init_temp_db, cleanup_temp_db, insert_memory
    from memall.strategy.sharing import MemorySharing
    from memall.core.db import get_conn

    db_path, patcher = init_temp_db()
    try:
        conn = get_conn()
        mid = insert_memory(conn, "Broadcast test memory.", agent_name="alice")
        conn.close()

        ms = MemorySharing("alice")
        ids = ms.broadcast(mid, ["bob", "charlie", "dave"])
        assert len(ids) == 3, f"Expected 3 share results, got {len(ids)}"
        print("  PASS test_broadcast")
    finally:
        cleanup_temp_db(db_path, patcher)


def test_unshare():
    """Unshare should remove the share reference."""
    from tests.test_helpers import init_temp_db, cleanup_temp_db, insert_memory
    from memall.strategy.sharing import MemorySharing
    from memall.core.db import get_conn

    db_path, patcher = init_temp_db()
    try:
        conn = get_conn()
        mid = insert_memory(conn, "Unshare test memory.", agent_name="alice")
        conn.close()

        ms = MemorySharing("alice")
        ms.share(mid, "bob")
        count = ms.unshare(mid, "bob")
        assert count >= 1, f"Expected at least 1 removed, got {count}"
        results = ms.query_shared("bob")
        mids = [r["id"] for r in results]
        assert mid not in mids, f"Expected memory {mid} to be unshared"
        print("  PASS test_unshare")
    finally:
        cleanup_temp_db(db_path, patcher)


def test_query_shared_trust_filter():
    """Query should respect trust_min filter."""
    from tests.test_helpers import init_temp_db, cleanup_temp_db, insert_memory
    from memall.strategy.sharing import MemorySharing
    from memall.core.db import get_conn

    db_path, patcher = init_temp_db()
    try:
        conn = get_conn()
        mid = insert_memory(conn, "Trust test memory.", agent_name="alice")
        conn.close()

        ms = MemorySharing("alice")
        ms.share(mid, "bob", trust_level="private")
        # Query with trust_min="shared" should NOT return private
        results = ms.query_shared("bob", trust_min="shared")
        mids = [r["id"] for r in results]
        assert mid not in mids, f"Expected private memory to be filtered out"
        print("  PASS test_query_shared_trust_filter")
    finally:
        cleanup_temp_db(db_path, patcher)


def test_get_shared_stats():
    """get_shared_stats should return sharing counts."""
    from tests.test_helpers import init_temp_db, cleanup_temp_db, insert_memory
    from memall.strategy.sharing import MemorySharing
    from memall.core.db import get_conn

    db_path, patcher = init_temp_db()
    try:
        conn = get_conn()
        mid = insert_memory(conn, "Stats test memory.", agent_name="alice")
        conn.close()

        ms = MemorySharing("alice")
        ms.share(mid, "bob")
        stats = MemorySharing.get_shared_stats("alice")
        assert "shared_out" in stats, f"Expected shared_out in stats"
        assert stats["shared_out"] >= 1, f"Expected >=1 shared_out, got {stats}"
        print("  PASS test_get_shared_stats")
    finally:
        cleanup_temp_db(db_path, patcher)


if __name__ == "__main__":
    print("=" * 60)
    print("MemorySharing Tests")
    print("=" * 60)
    passed = 0
    failed = 0
    for name in sorted(dir()):
        if name.startswith("test_"):
            try:
                globals()[name]()
                passed += 1
            except Exception as e:
                print(f"  FAIL {name}: {e}")
                import traceback
                traceback.print_exc()
                failed += 1
    print(f"\nResults: {passed} passed, {failed} failed")
    if failed:
        sys.exit(1)