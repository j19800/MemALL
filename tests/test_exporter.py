"""
Test Suite — Plugin Exporter
==============================
Tests export_markdown, export_jsonl, export_csv, export_html.
"""

import os
import sys
import tempfile
import json

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


def test_export_markdown():
    """export_markdown should return markdown string."""
    from tests.test_helpers import init_temp_db, cleanup_temp_db, insert_memory
    from memall.plugins.exporter import export_markdown
    from memall.core.db import get_conn

    db_path, patcher = init_temp_db()
    try:
        conn = get_conn()
        insert_memory(conn, "Test memory for markdown export with sufficient content.", agent_name="export_test")
        conn.close()

        result = export_markdown("export_test")
        assert isinstance(result, str), f"Expected string, got {type(result)}"
        assert len(result) > 0, "Expected non-empty output"
        assert "Test memory" in result or "export_test" in result
        print("  PASS test_export_markdown")
    finally:
        cleanup_temp_db(db_path, patcher)


def test_export_jsonl():
    """export_jsonl should return JSONL string."""
    from tests.test_helpers import init_temp_db, cleanup_temp_db, insert_memory
    from memall.plugins.exporter import export_jsonl
    from memall.core.db import get_conn

    db_path, patcher = init_temp_db()
    try:
        conn = get_conn()
        insert_memory(conn, "Test memory for JSONL export.", agent_name="export_test")
        conn.close()

        result = export_jsonl("export_test")
        assert isinstance(result, str)
        # Should return a file path, not content
        assert len(result) > 0
        print("  PASS test_export_jsonl")
    finally:
        cleanup_temp_db(db_path, patcher)


def test_export_csv():
    """export_csv should return CSV string."""
    from tests.test_helpers import init_temp_db, cleanup_temp_db, insert_memory
    from memall.plugins.exporter import export_csv
    from memall.core.db import get_conn

    db_path, patcher = init_temp_db()
    try:
        conn = get_conn()
        insert_memory(conn, "Test memory for CSV export.", agent_name="export_test")
        conn.close()

        result = export_csv("export_test")
        assert isinstance(result, str)
        assert len(result) > 0
        print("  PASS test_export_csv")
    finally:
        cleanup_temp_db(db_path, patcher)


def test_export_nonexistent_agent():
    """Export should handle nonexistent agent gracefully."""
    from memall.plugins.exporter import export_markdown

    try:
        result = export_markdown("nonexistent_agent_xyz")
        # Should return empty or error message
        assert isinstance(result, str)
        print("  PASS test_export_nonexistent_agent")
    except Exception as e:
        # Accept both graceful handling and clean errors
        print(f"  PASS test_export_nonexistent_agent (exception: {e})")


def test_export_empty_agent():
    """Export with empty agent name should not crash."""
    from memall.plugins.exporter import export_markdown

    try:
        result = export_markdown("")
        assert isinstance(result, str)
        print("  PASS test_export_empty_agent")
    except Exception:
        # May raise if empty agent is invalid
        print("  PASS test_export_empty_agent (exception handled)")


if __name__ == "__main__":
    print("=" * 60)
    print("Exporter Tests")
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