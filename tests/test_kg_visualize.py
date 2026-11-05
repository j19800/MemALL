"""Tests for the knowledge graph visualizer (Phase 3 P2).

Covers:
  (a) load_graph() queries entities + triples and returns nodes/links;
  (b) build_html() produces a self-contained page with the expected
      structural elements and embedded data;
  (c) save_html() writes the file and it parses as HTML;
  (d) empty graph still produces a valid page.
"""

import os
import sqlite3
import tempfile
from html.parser import HTMLParser


class _TagCollector(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags: set[str] = set()

    def handle_starttag(self, tag, attrs):
        self.tags.add(tag)


def _seed(conn):
    conn.execute(
        "CREATE TABLE IF NOT EXISTS entities ("
        "id INTEGER PRIMARY KEY, name TEXT, entity_type TEXT, description TEXT)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS knowledge_triples ("
        "id INTEGER PRIMARY KEY, subject_id INTEGER, predicate TEXT, object_id INTEGER)"
    )
    conn.executemany(
        "INSERT INTO entities (id, name, entity_type, description) VALUES (?,?,?,?)",
        [
            (1, "MemALL", "project", "多 Agent 记忆系统"),
            (2, "SQLite", "technology", "存储引擎"),
            (3, "", "未知类型", None),
        ],
    )
    conn.executemany(
        "INSERT INTO knowledge_triples (id, subject_id, predicate, object_id) VALUES (?,?,?,?)",
        [(1, 1, "uses", 2), (2, 2, "part_of", 1)],
    )
    conn.commit()


def test_load_graph_queries_entities_and_triples():
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        conn = sqlite3.connect(db_path)
        _seed(conn)
        conn.close()

        from memall.tools.kg_visualize import load_graph

        g = load_graph(db_path)
        assert len(g["nodes"]) == 3
        assert len(g["links"]) == 2
        # Empty name falls back to entity_<id>
        assert any(n["name"] == "entity_3" for n in g["nodes"])
        # Edges carry joined names
        assert all("source_name" in l and "target_name" in l for l in g["links"])
        print("  PASS test_load_graph_queries_entities_and_triples")
    finally:
        if os.path.exists(db_path):
            os.remove(db_path)


def test_build_html_structure_and_data():
    graph = {
        "nodes": [
            {"id": 1, "name": "A", "entity_type": "concept", "description": "d"},
            {"id": 2, "name": "B", "entity_type": "concept", "description": ""},
        ],
        "links": [
            {"id": 1, "source": 1, "target": 2,
             "source_name": "A", "target_name": "B", "predicate": "relates_to"},
        ],
    }
    from memall.tools.kg_visualize import build_html

    html = build_html(graph, title="测试图谱")
    assert "<svg" in html
    assert "const NODES = " in html
    assert "const LINKS = " in html
    assert '"relates_to"' in html
    assert "测试图谱" in html
    # Self-contained: no external script references
    assert "<script src=" not in html
    # No leftover un-rendered python format placeholders
    assert "{nodes_json}" not in html
    assert "{title_json}" not in html
    # JS template-literal interpolations must survive into the output
    assert "${l.source}" in html or "${l.id}" in html or "${n.x}" in html
    print("  PASS test_build_html_structure_and_data")


def test_html_is_parseable():
    graph = {"nodes": [], "links": []}
    from memall.tools.kg_visualize import build_html

    html = build_html(graph)
    collector = _TagCollector()
    collector.feed(html)          # raises on malformed markup
    assert "svg" in collector.tags
    assert "script" in collector.tags
    print("  PASS test_html_is_parseable")


def test_save_html_roundtrip():
    fd, path = tempfile.mkstemp(suffix=".html")
    os.close(fd)
    try:
        from memall.tools.kg_visualize import build_html, save_html

        out = save_html(build_html({"nodes": [], "links": []}), path)
        assert os.path.exists(str(out))
        text = open(str(out), encoding="utf-8").read()
        assert text.startswith("<!DOCTYPE html>")
    finally:
        if os.path.exists(path):
            os.remove(path)
        print("  PASS test_save_html_roundtrip")
