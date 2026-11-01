"""Tests for mcp/adapter.py — the JSON-RPC tool dispatch surface.

Guards the contract the MCP server relies on: exactly seven top-level tools,
JSON-shaped error payloads for unknown tools / invalid input, and a working
happy path for a read tool.
"""

import json

from memall.mcp.adapter import TOOL_DEFINITIONS, handle_call, _intercept

EXPECTED_TOOLS = {
    "memall_write",
    "memall_read",
    "memall_persona",
    "memall_discussion",
    "memall_federation",
    "memall_system",
    "memall_hooks_recent",
    # Registered after this set was written: real-time delivery over the
    # memall-bus (the L5 task card stays the source of truth; the bus carries
    # only a pointer to it).
    "memall_msg",
}


def test_tool_definitions_expose_top_level_tools():
    from memall.mcp.adapter import _ensure_loaded
    # Same as above: the list is populated by the lazy loader, not at import.
    _ensure_loaded()
    names = {d.get("name") for d in TOOL_DEFINITIONS}
    assert names == EXPECTED_TOOLS


def test_every_definition_has_description_and_schema():
    for definition in TOOL_DEFINITIONS:
        assert definition.get("description")
        assert "inputSchema" in definition


def test_handle_call_unknown_tool_returns_error_json():
    out = json.loads(handle_call("tool_that_does_not_exist", {}))
    assert out["status"] == "error"
    assert "unknown tool" in out["error"]


def test_handle_call_validation_error_is_reported():
    out = json.loads(handle_call("memall_write", {"action": "capture"}))
    assert "error" in out
    assert "validation" in out["error"].lower()


def test_handle_call_read_timeline_happy_path():
    from memall.core.thin_waist import capture

    capture(
        "适配器读取路径测试：这条记忆用于验证 memall_read/timeline 的返回结构。",
        agent_name="adapter_test",
        level="P2",
    )
    # A read now requires an identity: the trust filter can otherwise only
    # return "public" rows, which is indistinguishable from an empty result
    # set, so the adapter refuses and explains (read-path enforcement).
    out = json.loads(handle_call("memall_read", {"action": "timeline",
                                                "days": 7,
                                                "agent_name": "adapter_test"}))
    assert isinstance(out, list), out
    assert any("适配器读取路径测试" in item.get("content", "") for item in out)


def test_handle_call_persona_returns_features():
    from memall.core.thin_waist import capture

    capture(
        "适配器画像路径测试：这条记忆用于验证 memall_persona/persona 的返回结构。",
        agent_name="adapter_test",
        level="P2",
    )
    out = json.loads(
        handle_call("memall_persona", {"action": "persona", "agent_name": "adapter_test"})
    )
    assert "features" in out


def test_intercept_wrapper_is_callable():
    # Backward-compat wrapper must not raise on a well-formed call.
    _intercept("memall_read", {"action": "timeline"})