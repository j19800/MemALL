"""
Test Suite — MCP Tool Handlers
================================
Tests the consolidated tool handlers (memall_write, memall_read, etc.) directly.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


def test_mcp_write_capture_handler():
    """Test _handle_write with capture action."""
    from tests.test_helpers import init_temp_db, cleanup_temp_db
    from memall.mcp.tools.__init__ import _handle_write
    import json

    db_path, patcher = init_temp_db()
    try:
        result = _handle_write({
            "action": "capture",
            "content": "MCP tool handler test memory with enough content for quality gate.",
            "agent_name": "mcp_test",
        })
        data = json.loads(result)
        assert "id" in data, f"Expected id in result, got {data}"
        assert data.get("status") == "ok"
        print("  PASS test_mcp_write_capture_handler")
    finally:
        cleanup_temp_db(db_path, patcher)


def test_mcp_write_smart_store():
    from tests.test_helpers import init_temp_db, cleanup_temp_db
    from memall.mcp.tools.__init__ import _handle_write
    import json

    db_path, patcher = init_temp_db()
    try:
        result = _handle_write({
            "action": "smart_store",
            "content": "Smart store via MCP handler with sufficient content for quality gate.",
            "agent_name": "mcp_test",
        })
        data = json.loads(result)
        assert "status" in data, f"Expected status in result, got {data}"
        print("  PASS test_mcp_write_smart_store")
    finally:
        cleanup_temp_db(db_path, patcher)


def test_mcp_write_forget_action():
    from tests.test_helpers import init_temp_db, cleanup_temp_db
    from memall.mcp.tools.__init__ import _handle_write
    import json

    db_path, patcher = init_temp_db()
    try:
        result = _handle_write({
            "action": "forget",
            "sub_action": "stats",
            "agent_name": "mcp_test",
        })
        data = json.loads(result)
        assert isinstance(data, dict)
        print("  PASS test_mcp_write_forget_action")
    finally:
        cleanup_temp_db(db_path, patcher)


def test_mcp_write_quick_action():
    from tests.test_helpers import init_temp_db, cleanup_temp_db
    from memall.mcp.tools.__init__ import _handle_write
    import json

    db_path, patcher = init_temp_db()
    try:
        result = _handle_write({
            "action": "quick",
            "content": "Quick记 test memory via MCP handler for testing quick action.",
            "agent_name": "mcp_test",
        })
        data = json.loads(result)
        assert "id" in data, f"Expected id in result, got {data}"
        print("  PASS test_mcp_write_quick_action")
    finally:
        cleanup_temp_db(db_path, patcher)


def test_mcp_read_retrieve():
    from tests.test_helpers import init_temp_db, cleanup_temp_db
    from memall.mcp.tools.__init__ import _handle_read
    from memall.core.thin_waist import capture
    import json

    db_path, patcher = init_temp_db()
    try:
        capture("Retrieve test via MCP handler with enough content for quality.", agent_name="mcp_test")
        result = _handle_read({
            "action": "retrieve",
            "query": "MCP",
        })
        data = json.loads(result)
        assert isinstance(data, (dict, list))
        print("  PASS test_mcp_read_retrieve")
    finally:
        cleanup_temp_db(db_path, patcher)


def test_mcp_persona_identity():
    from tests.test_helpers import init_temp_db, cleanup_temp_db
    from memall.mcp.tools.__init__ import _handle_persona
    import json

    db_path, patcher = init_temp_db()
    try:
        result = _handle_persona({
            "action": "identity",
            "agent_name": "test_agent",
        })
        data = json.loads(result)
        assert isinstance(data, dict)
        print("  PASS test_mcp_persona_identity")
    finally:
        cleanup_temp_db(db_path, patcher)


def test_mcp_discussion_status():
    from tests.test_helpers import init_temp_db, cleanup_temp_db
    from memall.mcp.tools.__init__ import _handle_discussion
    import json

    db_path, patcher = init_temp_db()
    try:
        result = _handle_discussion({
            "action": "status",
        })
        data = json.loads(result)
        assert isinstance(data, dict)
        print("  PASS test_mcp_discussion_status")
    finally:
        cleanup_temp_db(db_path, patcher)


def test_mcp_hooks_recent():
    from memall.mcp.tools.__init__ import _handle_hooks
    import json

    result = _handle_hooks({})
    data = json.loads(result)
    assert isinstance(data, list) or isinstance(data, dict)
    print("  PASS test_mcp_hooks_recent")


def test_mcp_write_connect():
    from tests.test_helpers import init_temp_db, cleanup_temp_db, insert_memory
    from memall.mcp.tools.__init__ import _handle_write
    from memall.core.db import get_conn
    import json

    db_path, patcher = init_temp_db()
    try:
        conn = get_conn()
        m1 = insert_memory(conn, "Source for MCP connect test.", agent_name="mcp_test")
        m2 = insert_memory(conn, "Target for MCP connect test.", agent_name="mcp_test")
        conn.close()

        result = _handle_write({
            "action": "connect",
            "source_id": m1,
            "target_id": m2,
            "relation_type": "refines",
        })
        data = json.loads(result)
        assert "id" in data, f"Expected id, got {data}"
        print("  PASS test_mcp_write_connect")
    finally:
        cleanup_temp_db(db_path, patcher)


def test_mcp_read_timeline():
    from tests.test_helpers import init_temp_db, cleanup_temp_db
    from memall.mcp.tools.__init__ import _handle_read
    import json

    db_path, patcher = init_temp_db()
    try:
        result = _handle_read({"action": "timeline", "hours": 72})
        data = json.loads(result)
        assert isinstance(data, (dict, list))
        print("  PASS test_mcp_read_timeline")
    finally:
        cleanup_temp_db(db_path, patcher)


def test_mcp_system_gateway():
    from tests.test_helpers import init_temp_db, cleanup_temp_db
    from memall.mcp.tools.__init__ import _handle_system
    import json

    db_path, patcher = init_temp_db()
    try:
        result = _handle_system({
            "action": "gateway",
            "sub_action": "peers",
        })
        data = json.loads(result)
        assert isinstance(data, (dict, list))
        print("  PASS test_mcp_system_gateway")
    finally:
        cleanup_temp_db(db_path, patcher)


if __name__ == "__main__":
    print("=" * 60)
    print("MCP Tool Handler Tests")
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