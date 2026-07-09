import json
from memall.mcp.hooks import (
    HookRegistry, HookDef,
    HOOK_PRE_TOOL_USE, HOOK_POST_TOOL_USE, HOOK_POST_TOOL_USE_FAILURE,
    HOOK_STOP, hook, _match_tool,
)


def setup_function():
    HookRegistry.clear()


def test_register_and_dispatch():
    captured = []

    def my_hook(tool_name, arguments, result, error, extra):
        captured.append((tool_name, arguments.get("content", ""), result))

    HookRegistry.register(HookDef(
        hook_point=HOOK_POST_TOOL_USE,
        matcher="capture",
        handler=my_hook,
        description="test",
    ))

    HookRegistry.dispatch(HOOK_POST_TOOL_USE, "capture", {"content": "hello"}, result="ok")
    assert len(captured) == 1
    assert captured[0][0] == "capture"
    assert captured[0][1] == "hello"
    assert captured[0][2] == "ok"


def test_matcher_wildcard():
    matches = []
    HookRegistry.register(HookDef(hook_point=HOOK_PRE_TOOL_USE, matcher="*", handler=lambda **kw: matches.append(kw["tool_name"])))
    HookRegistry.dispatch(HOOK_PRE_TOOL_USE, "retrieve")
    assert "retrieve" in matches


def test_matcher_specific():
    matches = []
    HookRegistry.register(HookDef(hook_point=HOOK_PRE_TOOL_USE, matcher="capture", handler=lambda **kw: matches.append(kw["tool_name"])))
    HookRegistry.dispatch(HOOK_PRE_TOOL_USE, "capture")
    HookRegistry.dispatch(HOOK_PRE_TOOL_USE, "retrieve")
    assert matches == ["capture"]


def test_blocking_hook():
    HookRegistry.register(HookDef(
        hook_point=HOOK_PRE_TOOL_USE, matcher="*", handler=lambda **kw: False, blocking=True,
    ))
    results = HookRegistry.dispatch(HOOK_PRE_TOOL_USE, "capture")
    assert False in results


def test_post_tool_use_failure():
    captured = []
    HookRegistry.register(HookDef(
        hook_point=HOOK_POST_TOOL_USE_FAILURE, matcher="*",
        handler=lambda **kw: captured.append(kw.get("error", "")),
    ))
    HookRegistry.dispatch(HOOK_POST_TOOL_USE_FAILURE, "bash", error="permission denied")
    assert "permission denied" in captured


def test_stop_hook():
    captured = []
    HookRegistry.register(HookDef(
        hook_point=HOOK_STOP, matcher="*",
        handler=lambda **kw: captured.append(kw.get("arguments", {})),
    ))
    HookRegistry.dispatch(HOOK_STOP, arguments={"results": {"enrich": 5}, "elapsed": 1.2})
    assert captured[0]["results"]["enrich"] == 5


def test_hook_decorator():
    called = []
    @hook(HOOK_PRE_TOOL_USE, "test_tool", "decorator test")
    def test_fn(tool_name, arguments, result, error, extra):
        called.append(tool_name)
    HookRegistry.dispatch(HOOK_PRE_TOOL_USE, "test_tool")
    assert called == ["test_tool"]


def test_match_tool():
    assert _match_tool("*", "anything") is True
    assert _match_tool("capture", "capture") is True
    assert _match_tool("capture", "retrieve") is False
    assert _match_tool("capture|retrieve", "capture") is True
    assert _match_tool("capture|retrieve", "retrieve") is True
    assert _match_tool("memall_*", "memall_forget") is True
    assert _match_tool("memall_*", "forget") is False


def test_default_hooks_loaded():
    from memall.mcp.adapter import TOOL_DEFINITIONS
    assert len(TOOL_DEFINITIONS) >= 7
    # Re-register built-in hooks in case another test cleared them
    from memall.mcp import hooks_builtin as _hb
    import importlib
    importlib.reload(_hb)
    hooks = HookRegistry.list_hooks()
    hook_points = {h["hook_point"] for h in hooks}
    assert HOOK_POST_TOOL_USE in hook_points
    assert HOOK_POST_TOOL_USE_FAILURE in hook_points
    assert HOOK_STOP in hook_points
