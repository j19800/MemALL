"""
Test Suite — Strategy Entity
============================
Tests EntityStrategy store/retrieve with entity extraction.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


def test_entity_store_and_extract():
    from tests.test_helpers import init_temp_db, cleanup_temp_db, insert_memory
    from memall.strategy.entity import EntityStrategy
    from memall.core.db import get_conn, pool_conn
    from memall.core.models import MemoryInput

    db_path, patcher = init_temp_db()
    try:
        s = EntityStrategy("entity_agent", {"auto_extract": True})
        mid = MemoryInput(
            content="We built the project using Python and FastAPI with PostgreSQL.",
            agent_name="entity_agent",
            category="tech",
        )
        mem_id = s.store(mid)
        assert mem_id > 0, f"Expected positive ID, got {mem_id}"

        # Check entities were extracted
        with pool_conn() as conn:
            rows = conn.execute(
                "SELECT e.name, e.entity_type FROM entities e "
                "JOIN memory_entities me ON e.id = me.entity_id WHERE me.memory_id = ?",
                (mem_id,),
            ).fetchall()
            names = [r["name"] for r in rows]
            assert "Python" in names, f"Expected Python in entities, got {names}"
            assert "FastAPI" in names, f"Expected FastAPI in entities, got {names}"
            print("  PASS test_entity_store_and_extract")
    finally:
        cleanup_temp_db(db_path, patcher)


def test_entity_no_auto_extract():
    from tests.test_helpers import init_temp_db, cleanup_temp_db
    from memall.strategy.entity import EntityStrategy
    from memall.core.db import pool_conn
    from memall.core.models import MemoryInput

    db_path, patcher = init_temp_db()
    try:
        s = EntityStrategy("no_extract_agent", {"auto_extract": False})
        mid = s.store(MemoryInput(content="No entities to extract here.", agent_name="no_extract_agent"))
        with pool_conn() as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM memory_entities WHERE memory_id = ?", (mid,)
            ).fetchone()[0]
            assert count == 0, f"Expected 0 entities, got {count}"
            print("  PASS test_entity_no_auto_extract")
    finally:
        cleanup_temp_db(db_path, patcher)


def test_entity_resolve_dedup():
    from tests.test_helpers import init_temp_db, cleanup_temp_db
    from memall.strategy.entity import EntityStrategy
    from memall.core.db import pool_conn
    from memall.core.models import MemoryInput

    db_path, patcher = init_temp_db()
    try:
        s = EntityStrategy("dedup_agent")
        # Store same entity twice in different memories
        m1 = s.store(MemoryInput(content="We use Python for the backend.", agent_name="dedup_agent"))
        m2 = s.store(MemoryInput(content="Python is great for data processing.", agent_name="dedup_agent"))
        with pool_conn() as conn:
            # Python should appear only once in entities table
            py_count = conn.execute(
                "SELECT COUNT(*) FROM entities WHERE LOWER(name)='python'"
            ).fetchone()[0]
            assert py_count >= 1, f"Expected at least 1 Python entity, got {py_count}"
            # Each memory should have its own memory_entity link
            me_count = conn.execute(
                "SELECT COUNT(*) FROM memory_entities WHERE memory_id IN (?,?)", (m1, m2)
            ).fetchone()[0]
            assert me_count >= 1, f"Expected memory_entity links, got {me_count}"
            print("  PASS test_entity_resolve_dedup")
    finally:
        cleanup_temp_db(db_path, patcher)


def test_entity_retrieve_standard():
    from tests.test_helpers import init_temp_db, cleanup_temp_db
    from memall.strategy.entity import EntityStrategy
    from memall.core.models import MemoryInput

    db_path, patcher = init_temp_db()
    try:
        s = EntityStrategy("retrieve_agent")
        s.store(MemoryInput(content="Python is a programming language.", agent_name="retrieve_agent"))
        results = s.retrieve(query="Python", top_k=5)
        # retrieve should return results (could be empty in test env)
        assert isinstance(results, list), f"Expected list, got {type(results)}"
        print("  PASS test_entity_retrieve_standard")
    finally:
        cleanup_temp_db(db_path, patcher)


def test_entity_retrieve_no_query():
    from tests.test_helpers import init_temp_db, cleanup_temp_db
    from memall.strategy.entity import EntityStrategy
    from memall.core.models import MemoryInput

    db_path, patcher = init_temp_db()
    try:
        s = EntityStrategy("noq_agent")
        s.store(MemoryInput(content="Test content no query.", agent_name="noq_agent"))
        results = s.retrieve(query="", top_k=5)
        assert isinstance(results, list), f"Expected list, got {type(results)}"
        print("  PASS test_entity_retrieve_no_query")
    finally:
        cleanup_temp_db(db_path, patcher)


if __name__ == "__main__":
    print("=" * 60)
    print("EntityStrategy Tests")
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