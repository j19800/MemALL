"""MemALL Agent SDK — high-level memory operations for agents and scripts.

This is the recommended entry point for Python agents and automation scripts
that want to store, search, and manage memories without calling ``capture()``
directly or writing raw SQL INSERTs.

Usage::

    from memall.agent_memory import add, search
    mid = add("今天完成了架构重构", agent="claude", project="MemALL")
    results = search("架构", agent="claude")
"""

import json
import logging
from datetime import datetime, timezone
from typing import Optional

from memall.core.models import MemoryInput
from memall.core.thin_waist import capture as _capture, retrieve, connect, traverse
from memall.config import get_config

logger = logging.getLogger(__name__)

# Default project when none is specified
_DEFAULT_PROJECT = "memall"


def infer_project(agent_name: str = "", category: str = "",
                  content: str = "") -> str:
    """Infer a project name from available context when none is explicitly given.

    Priority:
      1. Known agent → project mappings
      2. Content keyword hints
      3. Default project
    """
    # Agent-based inference
    agent_lower = agent_name.lower()
    _AGENT_MAP = {
        "workbuddy": "memall",
        "douyin-daily": "douyin-daily",
        "marvis": "memall",
        "opencode": "memall",
        "claude": "memall",
    }
    for key, proj in _AGENT_MAP.items():
        if key in agent_lower or agent_lower in key:
            return proj

    # Content-based inference
    if content:
        import re
        if re.search(r'抖音|douyin|短视频|带货', content, re.I):
            return "douyin-daily"
        if re.search(r'agent.hub|hub.agent', content, re.I):
            return "memall-agent-hub"
        if re.search(r'desktop|electron', content, re.I):
            return "memall-desktop"

    return _DEFAULT_PROJECT


def add(content: str, agent: str = "", owner: str = "",
        subject: str = "", category: str = "general",
        level: str = "P2", project: str = "",
        summary: str = "", occurred_at: str = "",
        confidence: float = 0.5, visibility: str = "private",
        metadata: Optional[dict] = None,
        tags: Optional[list] = None) -> int:
    """Store a memory with project-aware defaults.

    This is the SDK's primary ``add()`` — use it instead of writing
    raw INSERTs.  Every parameter maps directly to a column in the
    ``memories`` table, and ``project`` is always populated (either
    from the caller or inferred).

    Args:
        content: Memory text content (required).
        agent: Agent name (required for project inference).
        owner: Display name (defaults to agent).
        subject: Short title (auto-generated if empty).
        category: Memory category (default "general").
        level: Memory level (P0-P4, L1-L10).
        project: **Project name.** If empty, inferred from
                 ``agent`` / ``content`` via ``infer_project()``.
                 This ensures the ``project`` field is never left
                 empty — the root cause of the 78% empty-project
                 data-quality issue.
        summary: One-line summary.
        occurred_at: When the event occurred (ISO 8601).
        confidence: Certainty score (0.0-1.0).
        visibility: Access level (public/shared/family/trusted/private).
        metadata: Arbitrary JSON metadata dict.
        tags: List of tag strings.

    Returns:
        The new memory ID.

    Example::

        mid = add(
            content="完成了搜索性能优化",
            agent="claude",
            project="memall",      # explicit
            category="implementation",
        )

        # project inferred from agent_name:
        mid = add(
            content="抖音账号分析报告",
            agent="douyin-daily",   # → project="douyin-daily"
        )
    """
    # Auto-infer project if not provided
    if not project:
        project = infer_project(agent_name=agent, category=category, content=content)

    # Ensure level is safe
    if level not in ("P0", "P1", "P2", "P3", "P4",
                     "L1", "L2", "L3", "L4", "L5",
                     "L6", "L7", "L8", "L9", "L10"):
        level = "P2"

    inp = MemoryInput(
        content=content,
        level=level,
        owner=owner or agent,
        agent_name=agent,
        subject=subject,
        project=project,
        category=category,
        summary=summary,
        occurred_at=occurred_at,
        confidence=confidence,
        visibility=visibility,
        metadata=json.dumps(metadata, ensure_ascii=False) if metadata else "{}",
        tags=tags or [],
    )

    return _capture(inp)


def search(query: str = "", agent: str = "", category: str = "",
           project: str = "", limit: int = 20) -> list:
    """Search memories with optional project filter."""
    return retrieve(query, agent_name=agent, category=category,
                    project=project, limit=limit)