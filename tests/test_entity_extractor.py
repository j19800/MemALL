"""
Test Suite — Entity Extractor
==============================
Tests extract_entities, extract_triples, resolve_entity.
"""

import os
import sys
import tempfile
import sqlite3

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


def _make_conn():
    db = tempfile.mktemp(suffix=".db")
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE IF NOT EXISTS entities (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            entity_type TEXT NOT NULL DEFAULT 'unknown',
            canonical_name TEXT NOT NULL DEFAULT '',
            description TEXT NOT NULL DEFAULT '',
            metadata TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(name, entity_type)
        )
    """)
    conn.commit()
    return conn, db


def test_extract_technology_entities():
    from memall.core.entity_extractor import extract_entities
    text = "We built the application using Python and FastAPI with PostgreSQL."
    ents = extract_entities(text)
    names = [e["name"] for e in ents]
    assert "Python" in names, f"Expected Python, got {names}"
    assert "FastAPI" in names, f"Expected FastAPI, got {names}"
    assert "PostgreSQL" in names, f"Expected PostgreSQL, got {names}"
    print("  PASS test_extract_technology_entities")


def test_extract_person_entities():
    from memall.core.entity_extractor import extract_entities
    text = "The author @john created this project. The developer @alice contributed."
    ents = extract_entities(text)
    names = [e["name"] for e in ents]
    assert "john" in names, f"Expected john, got {names}"
    assert "alice" in names, f"Expected alice, got {names}"
    print("  PASS test_extract_person_entities")


def test_extract_empty_text():
    from memall.core.entity_extractor import extract_entities
    assert extract_entities("") == []
    assert extract_entities("   ") == []
    print("  PASS test_extract_empty_text")


def test_extract_triples_simple():
    from memall.core.entity_extractor import extract_triples
    text = "Python is a programming language. FastAPI is built on top of Starlette."
    triples = extract_triples(text)
    assert len(triples) >= 1, f"Expected at least 1 triple, got {len(triples)}"
    subjects = [t["subject"] for t in triples]
    assert "Python" in subjects, f"Expected Python subject, got {subjects}"
    print("  PASS test_extract_triples_simple")


def test_extract_triples_chinese():
    from memall.core.entity_extractor import extract_triples
    text = "MemALL 是一个 Agent 记忆系统。这个系统基于 Python 开发。"
    triples = extract_triples(text)
    assert len(triples) >= 1, f"Expected at least 1 triple, got {len(triples)}"
    print("  PASS test_extract_triples_chinese")


def test_resolve_entity_creates_new():
    from memall.core.entity_extractor import resolve_entity
    conn, db = _make_conn()
    try:
        eid = resolve_entity("Python", "language", conn)
        assert eid > 0, f"Expected positive ID, got {eid}"
        row = conn.execute("SELECT * FROM entities WHERE id = ?", (eid,)).fetchone()
        assert row["name"] == "Python"
        assert row["entity_type"] == "language"
        print("  PASS test_resolve_entity_creates_new")
    finally:
        conn.close()
        os.unlink(db)


def test_resolve_entity_dedup():
    from memall.core.entity_extractor import resolve_entity
    conn, db = _make_conn()
    try:
        eid1 = resolve_entity("Python", "language", conn)
        eid2 = resolve_entity("Python", "language", conn)
        assert eid1 == eid2, f"Expected same ID, got {eid1} vs {eid2}"
        print("  PASS test_resolve_entity_dedup")
    finally:
        conn.close()
        os.unlink(db)


def test_extract_deduplication():
    from memall.core.entity_extractor import extract_entities
    text = "Python is great. Python is versatile."
    ents = extract_entities(text)
    names = [e["name"] for e in ents]
    # Python appears twice in the text but should be deduplicated per type.
    # It matches both _TECH_PATTERN and _LANG_PATTERN, giving 2 entities:
    # (Python, technology) and (Python, language)
    python_count = names.count("Python")
    assert python_count >= 1, f"Expected at least 1 Python entity, got {python_count}"
    # Same name + same type should not appear twice
    seen = set()
    for e in ents:
        key = (e["name"], e["entity_type"])
        assert key not in seen, f"Duplicate entity: {key}"
        seen.add(key)
    print("  PASS test_extract_deduplication")


if __name__ == "__main__":
    print("=" * 60)
    print("Entity Extractor Tests")
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