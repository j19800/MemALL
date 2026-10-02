"""Project inference — single source of truth for ``project`` field inference.

This module centralizes the logic that decides which ``project`` a memory
belongs to when the caller does not provide one explicitly.

Why this exists
---------------
Previously ``infer_project`` lived in ``memall/agent_memory.py`` and was
re-implemented (differently!) in three other places:

  * ``mcp/tools/capture.py``            — imported the agent_memory version
  * ``mcp/tools/memory_write.py``       — imported the agent_memory version
  * ``mcp/server.py`` ``quick`` action  — its OWN hard-coded ``kw_map``

Because the ``smart_store`` / ``store_batch`` / raw ``capture()`` paths never
called it, ~77% of memories ended up with an empty ``project`` field.
``capture()`` is the one write entry that every other path funnels through,
so we move the canonical inference HERE and call it from ``capture()``.
All other callers now import from this module, eliminating the drift.
"""

import re

# ── Agent → project mapping (single-project agents) ──
_AGENT_PROJECT_MAP: dict[str, str] = {
    "workbuddy": "memall",
    "douyin-daily": "douyin-daily",
    "marvis": "memall",
    "opencode": "memall",
    "claude": "memall",
    "memall-desktop": "memall-desktop",
}

# ── Content keyword → project (heuristic fallbacks) ──
_CONTENT_PROJECT_RULES: list[tuple[re.Pattern, str]] = [
    (re.compile(r"抖音|douyin|短视频|带货", re.I), "douyin-daily"),
    # Match dotted/hyphenated AND spaced/joined forms: "agent hub", "agent.hub",
    # "agent-hub", "agenthub" (and the hub-agent variants). Spaced form is the
    # most common natural phrasing, so the rule must not require a separator.
    (re.compile(r"agent[\s.\-]?hub|hub[\s.\-]?agent", re.I), "memall-agent-hub"),
    (re.compile(r"desktop|electron", re.I), "memall-desktop"),
    # Preserve the prior ``quick``-action mapping so no behavior is lost.
    (re.compile(r"股票|交易|分析", re.I), "tradingagents"),
]

_DEFAULT_PROJECT = "memall"


def infer_project(agent_name: str = "", category: str = "",
                  content: str = "") -> str:
    """Infer a project name from available context when none is explicit.

    Priority (highest first):
      1. Known agent → project mapping
      2. Content keyword hints
      3. Default project (``memall``)

    Args:
        agent_name: Agent that authored the memory.
        category:   Memory category (currently unused, kept for API stability).
        content:    Memory content (used for keyword heuristics).

    Returns:
        A non-empty project string. Never returns ``""``.
    """
    # 1) Agent-based inference
    agent_lower = (agent_name or "").lower().strip()
    if agent_lower:
        for key, proj in _AGENT_PROJECT_MAP.items():
            if key in agent_lower or agent_lower in key:
                return proj

    # 2) Content-based inference
    if content:
        for pattern, proj in _CONTENT_PROJECT_RULES:
            if pattern.search(content):
                return proj

    # 3) Default
    return _DEFAULT_PROJECT
