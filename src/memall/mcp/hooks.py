from __future__ import annotations
import re
import logging
from dataclasses import dataclass, field
from typing import Any, Callable

logger = logging.getLogger(__name__)

HOOK_PRE_TOOL_USE = "pre_tool_use"
HOOK_POST_TOOL_USE = "post_tool_use"
HOOK_POST_TOOL_USE_FAILURE = "post_tool_use_failure"
HOOK_PRE_COMPACT = "pre_compact"
HOOK_STOP = "stop"
HOOK_SESSION_START = "session_start"
HOOK_SESSION_END = "session_end"


@dataclass
class HookDef:
    hook_point: str
    matcher: str
    handler: Callable
    description: str = ""
    blocking: bool = False


class HookRegistry:
    _hooks: list[HookDef] = []

    @classmethod
    def register(cls, hook: HookDef):
        cls._hooks.append(hook)

    @classmethod
    def clear(cls):
        cls._hooks.clear()

    @classmethod
    def dispatch(
        cls,
        hook_point: str,
        tool_name: str = "",
        arguments: dict | None = None,
        result: Any = None,
        error: str | None = None,
        extra: dict | None = None,
    ) -> list[Any]:
        results: list[Any] = []
        for hook in cls._hooks:
            if hook.hook_point != hook_point:
                continue
            if not _match_tool(hook.matcher, tool_name):
                continue
            try:
                r = hook.handler(
                    tool_name=tool_name,
                    arguments=arguments or {},
                    result=result,
                    error=error,
                    extra=extra or {},
                )
                results.append(r)
                if hook.blocking and r is False:
                    results.append(False)
                    return results
            except Exception:
                logger.exception(f"Hook failed: {hook.hook_point} / {hook.matcher}")
        return results

    @classmethod
    def list_hooks(cls) -> list[dict]:
        return [
            {
                "hook_point": h.hook_point,
                "matcher": h.matcher,
                "description": h.description,
                "blocking": h.blocking,
            }
            for h in cls._hooks
        ]


def _match_tool(matcher: str, tool_name: str) -> bool:
    if matcher == "*":
        return True
    escaped = re.escape(matcher)
    escaped = escaped.replace(r"\*", ".*").replace(r"\|", "|")
    pattern = "^(" + escaped + ")$"
    return bool(re.match(pattern, tool_name))


def hook(
    hook_point: str,
    matcher: str = "*",
    description: str = "",
    blocking: bool = False,
):
    def decorator(fn):
        HookRegistry.register(HookDef(
            hook_point=hook_point,
            matcher=matcher,
            handler=fn,
            description=description,
            blocking=blocking,
        ))
        return fn
    return decorator
