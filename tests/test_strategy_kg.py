"""
Test Suite — Strategy KG
========================
Tests KGStrategy triple extraction, store, retrieve, and traverse.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


def test_kg_should_extract_threshold():
    """KG should only extract from L6+ memories."""
    from memall.strategy.kg import KGStrategy

    s = KGStrategy("kg_test", {"min_level": "L6"})
    assert s._should_extract("L6") is True
    assert s._should_extract("L7") is True
    assert s._should_extract("L9") is True
    assert s._should_extract("P2") is False, "P2 should not extract triples"
    assert s._should_extract("L4") is False, "L4 should not extract triples"
    assert s._should_extract("") is False, "Empty level should not extract"
    print("  PASS test_kg_should_extract_threshold")


def test_kg_default_config():
    from memall.strategy.kg import KGStrategy
    s = KGStrategy("cfg_test")
    assert s.min_level == "L6"
    assert s.max_triples == 20
    assert s.traverse_depth == 1
    assert s.auto_extract is True
    print("  PASS test_kg_default_config")


def test_kg_custom_config():
    from memall.strategy.kg import KGStrategy
    s = KGStrategy("cfg_test", {"min_level": "L4", "max_triples": 5, "traverse_depth": 2})
    assert s.min_level == "L4"
    assert s.max_triples == 5
    assert s.traverse_depth == 2
    print("  PASS test_kg_custom_config")


def test_kg_extract_and_store_triples():
    """Test triple extraction from a memory with relational content."""
    from tests.test_helpers import init_temp_db, cleanup_temp_db, insert_memory
    from memall.strategy.kg import KGStrategy
    from memall.core.db import pool_conn, get_conn
    from memall.core.models import MemoryInput

    db_path, patcher = init_temp_db()
    try:
        conn = get_conn()
        insert_memory(conn, "经过分析和比较，Python 是一种编程语言，FastAPI 构建在 Starlette 之上。这是我们的技术选型结论。",
                      agent_name="kg_agent", level="L6")
        conn.close()

        s = KGStrategy("kg_agent")
        mem_id = s.store(MemoryInput(
            content="经过分析和比较，Python 是一种编程语言，FastAPI 构建在 Starlette 之上。这是我们的技术选型结论。",
            agent_name="kg_agent",
            level="L6",
        ))
        assert mem_id > 0, f"Expected positive ID, got {mem_id}"
        with pool_conn() as conn:
            count = conn.execute("SELECT COUNT(*) FROM knowledge_triples").fetchone()[0]
            assert count >= 1, f"Expected at least 1 triple, got {count}"
            print("  PASS test_kg_extract_and_store_triples")
    finally:
        cleanup_temp_db(db_path, patcher)


def test_kg_skip_low_level():
    """KG should NOT extract triples from low-level memories."""
    from tests.test_helpers import init_temp_db, cleanup_temp_db, insert_memory
    from memall.strategy.kg import KGStrategy
    from memall.core.db import pool_conn, get_conn
    from memall.core.models import MemoryInput

    db_path, patcher = init_temp_db()
    try:
        conn = get_conn()
        insert_memory(conn, "Python is a programming language.", agent_name="kg_agent", level="P2")
        conn.close()

        s = KGStrategy("kg_agent", {"min_level": "L6"})
        mid = s.store(MemoryInput(content="Python is a programming language.", agent_name="kg_agent", level="P2"))
        with pool_conn() as conn:
            count = conn.execute("SELECT COUNT(*) FROM knowledge_triples").fetchone()[0]
            assert count == 0, f"Expected 0 triples for P2, got {count}"
            print("  PASS test_kg_skip_low_level")
    finally:
        cleanup_temp_db(db_path, patcher)


def test_kg_traverse():
    """Test KG traversal from an entity."""
    from tests.test_helpers import init_temp_db, cleanup_temp_db
    from memall.strategy.kg import KGStrategy
    from memall.core.db import pool_conn, get_conn
    from memall.core.entity_extractor import resolve_entity
    from datetime import datetime, timezone

    db_path, patcher = init_temp_db()
    try:
        # Add entities + knowledge_triples tables first via init_db
        conn = get_conn()
        now = datetime.now(timezone.utc).isoformat()
        python_id = resolve_entity("Python", "language", conn)
        fastapi_id = resolve_entity("FastAPI", "technology", conn)
        # Use a real memory_id from an inserted memory
        conn.execute(
            "INSERT INTO memories (content, content_hash, level, agent_name, occurred_at, created_at, updated_at) "
            "VALUES (?, ?, 'L6', 'traverse_agent', ?, ?, ?)",
            ("Test content about Python and FastAPI.", "hash_traverse_test_001", now, now, now),
        )
        mem_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute(
            "INSERT OR IGNORE INTO knowledge_triples (subject_id, predicate, object_id, source_memory_id, confidence, weight, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (python_id, "is", fastapi_id, mem_id, 0.8, 1.0, now),
        )
        conn.commit()
        conn.close()

        s = KGStrategy("traverse_agent")
        result = s.traverse("Python", depth=1)
        assert isinstance(result, dict), f"Expected dict, got {type(result)}"
        assert "entities" in result, f"Expected entities in result"
        assert "triples" in result, f"Expected triples in result"
        if result["triples"]:
            assert result["triples"][0]["predicate"] == "is"
        print("  PASS test_kg_traverse")
    finally:
        cleanup_temp_db(db_path, patcher)


def test_kg_traverse_nonexistent():
    """Traverse from a nonexistent entity should return empty."""
    from tests.test_helpers import init_temp_db, cleanup_temp_db
    from memall.strategy.kg import KGStrategy

    db_path, patcher = init_temp_db()
    try:
        s = KGStrategy("traverse_empty")
        result = s.traverse("NonexistentEntityXYZ", depth=1)
        assert result["entities"] == []
        assert result["triples"] == []
        print("  PASS test_kg_traverse_nonexistent")
    finally:
        cleanup_temp_db(db_path, patcher)


def test_kg_retrieve_standard():
    """KG retrieve should return a list."""
    from tests.test_helpers import init_temp_db, cleanup_temp_db
    from memall.strategy.kg import KGStrategy
    from memall.core.models import MemoryInput

    db_path, patcher = init_temp_db()
    try:
        s = KGStrategy("kg_retrieve")
        s.store(MemoryInput(
            content="经过分析和比较，Python 是一种用于数据科学的编程语言，这是我们的技术选型结论。",
            agent_name="kg_retrieve",
            level="L6",
        ))
        results = s.retrieve(query="Python", top_k=5)
        assert isinstance(results, list), f"Expected list, got {type(results)}"
        print("  PASS test_kg_retrieve_standard")
    finally:
        cleanup_temp_db(db_path, patcher)


if __name__ == "__main__":
    print("=" * 60)
    print("KGStrategy Tests")
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