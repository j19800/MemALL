"""
Test Suite — Gateway HTML
==========================
Tests HTML rendering functions from gateway_html.py.
"""

import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


def test_html_style():
    """HTML_STYLE should be a non-empty string."""
    from memall.gateway_html import HTML_STYLE
    assert isinstance(HTML_STYLE, str)
    assert len(HTML_STYLE) > 100
    assert "body" in HTML_STYLE
    assert "font-family" in HTML_STYLE
    print("  PASS test_html_style")


def test_nav_html():
    """NAV_HTML should contain navigation links."""
    from memall.gateway_html import NAV_HTML
    assert isinstance(NAV_HTML, str)
    assert "/recent" in NAV_HTML
    assert "/timeline" in NAV_HTML
    assert "/dashboard" in NAV_HTML
    assert "/todos" in NAV_HTML
    print("  PASS test_nav_html")


def test_handle_recent():
    """handle_recent should return HTML string."""
    from tests.test_helpers import init_temp_db, cleanup_temp_db, insert_memory
    from memall.gateway_html import handle_recent
    from memall.core.db import pool_conn, get_conn

    db_path, patcher = init_temp_db()
    try:
        conn = get_conn()
        insert_memory(conn, "Recent test memory for HTML rendering.", agent_name="html_test")
        conn.close()
        with pool_conn() as conn:
            result = handle_recent(conn)
        assert isinstance(result, str)
        assert "<!DOCTYPE html>" in result
        print("  PASS test_handle_recent")
    finally:
        cleanup_temp_db(db_path, patcher)


def test_handle_identity():
    """handle_identity should return HTML for valid agent."""
    from tests.test_helpers import init_temp_db, cleanup_temp_db, insert_memory
    from memall.gateway_html import handle_identity
    from memall.core.db import pool_conn, get_conn

    db_path, patcher = init_temp_db()
    try:
        conn = get_conn()
        insert_memory(conn, "Identity test memory for L1.", agent_name="id_test", level="L1")
        conn.close()
        with pool_conn() as conn:
            result = handle_identity(conn, "id_test")
        assert isinstance(result, str)
        assert "<!DOCTYPE html>" in result
        print("  PASS test_handle_identity")
    finally:
        cleanup_temp_db(db_path, patcher)


def test_handle_graph_stats():
    """handle_graph_stats should return HTML with stats."""
    from tests.test_helpers import init_temp_db, cleanup_temp_db, insert_memory
    from memall.gateway_html import handle_graph_stats
    from memall.core.db import pool_conn, get_conn

    db_path, patcher = init_temp_db()
    try:
        conn = get_conn()
        insert_memory(conn, "Graph test memory.", agent_name="graph_test")
        conn.close()
        with pool_conn() as conn:
            result = handle_graph_stats(conn)
        assert isinstance(result, str)
        assert "<!DOCTYPE html>" in result
        print("  PASS test_handle_graph_stats")
    finally:
        cleanup_temp_db(db_path, patcher)


def test_render_artifact_html():
    """render_artifact_html should return static HTML."""
    from memall.gateway_html import render_artifact_html
    result = render_artifact_html()
    assert isinstance(result, str)
    assert "<!DOCTYPE html>" in result
    print("  PASS test_render_artifact_html")


def test_handle_artifact():
    """handle_artifact should return artifact HTML."""
    from memall.gateway_html import handle_artifact
    result = handle_artifact()
    assert isinstance(result, str)
    assert "<!DOCTYPE html>" in result
    print("  PASS test_handle_artifact")


def test_render_features_html():
    """render_features_html should return features HTML."""
    from memall.gateway_html import render_features_html
    result = render_features_html("1.0.0")
    assert isinstance(result, str)
    assert "<!DOCTYPE html>" in result
    assert "1.0.0" in result
    print("  PASS test_render_features_html")


def test_handle_features():
    """handle_features should return features HTML."""
    from memall.gateway_html import handle_features
    result = handle_features("1.0.0")
    assert isinstance(result, str)
    assert "<!DOCTYPE html>" in result
    print("  PASS test_handle_features")


def test_handle_todos():
    """handle_todos should return HTML task board."""
    from tests.test_helpers import init_temp_db, cleanup_temp_db, insert_memory
    from memall.gateway_html import handle_todos
    from memall.core.db import get_conn
    import json
    from datetime import datetime, timezone

    db_path, patcher = init_temp_db()
    try:
        conn = get_conn()
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "INSERT INTO memories (content, content_hash, level, agent_name, metadata, occurred_at, created_at, updated_at) "
            "VALUES (?, ?, 'L5', 'todo_test', ?, ?, ?, ?)",
            ("Todo test task content.", "hash_todo_001", json.dumps({"status": "active"}), now, now, now),
        )
        conn.commit()
        result = handle_todos(conn)
        assert isinstance(result, str)
        assert "<!DOCTYPE html>" in result
        assert "任务管理" in result or "todo" in result.lower() or "Todo" in result
        print("  PASS test_handle_todos")
    finally:
        cleanup_temp_db(db_path, patcher)


if __name__ == "__main__":
    print("=" * 60)
    print("Gateway HTML Tests")
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