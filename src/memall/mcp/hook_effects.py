"""
Hook Effects — Ring buffer for collecting async hook events visible to agents.

Provides a thread-safe ring buffer (maxlen=200) that records hook events as they
happen. The adapter consumes recent events on each tool call and injects them
into the tool response, so agents see async hook effects in their chat window.
"""

from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass, field
from threading import Lock
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class HookEvent:
    """A single hook execution record for agent-visible activity reporting."""

    hook_point: str
    description: str
    elapsed_ms: int = 0
    status: str = "ok"
    plugin: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


# ── Thread-safe ring buffer ──────────────────────────────────────────────

_buffer: deque[HookEvent] = deque(maxlen=200)
_lock = Lock()
_counter: int = 0  # total events ever appended
_watermark: int = 0  # last consumed index


def record_event(
    hook_point: str,
    description: str,
    elapsed_ms: int = 0,
    status: str = "ok",
    plugin: str = "",
    **metadata: Any,
) -> None:
    """Append a hook event to the ring buffer (thread-safe).

    Args:
        hook_point: The lifecycle hook point name (HOOK_* constant).
        description: Human-readable description of what happened.
        elapsed_ms: Execution time in milliseconds.
        status: One of "ok", "running", "failed", "skipped".
        plugin: Plugin name that produced this event (empty = core system).
    """
    global _counter
    event = HookEvent(
        hook_point=hook_point,
        description=description,
        elapsed_ms=elapsed_ms,
        status=status,
        plugin=plugin,
        metadata=metadata,
    )
    with _lock:
        _buffer.append(event)
        _counter += 1


def consume_recent() -> list[HookEvent]:
    """Return all events appended since the last call to consume_recent().

    Uses a monotonically increasing watermark to avoid duplicates even when
    the ring buffer wraps around. Thread-safe.
    """
    global _watermark
    with _lock:
        if _watermark >= _counter:
            return []
        count = _counter - _watermark
        _watermark = _counter
        return list(_buffer)[-count:]


def peek_recent(n: int = 5) -> list[HookEvent]:
    """Return the last *n* events without consuming them (thread-safe)."""
    with _lock:
        if not _buffer:
            return []
        return list(_buffer)[-n:]


def format_activity(events: list[HookEvent]) -> str | None:
    """Format a list of HookEvent into a compact text block.

    Returns ``None`` when *events* is empty.
    """
    if not events:
        return None

    lines: list[str] = []
    for ev in events:
        icon = _status_icon(ev.status)
        tag = f"{ev.plugin}:" if ev.plugin else ""
        elapsed = _fmt_elapsed(ev.elapsed_ms) if ev.elapsed_ms else ""
        ts = f" ({elapsed})" if elapsed else ""
        lines.append(f"  {icon} {tag}{ev.hook_point} → {ev.description}{ts}")

    if not lines:
        return None

    return "\n".join([
        "── Hook Activity ──",
        *lines,
        "── End Hook Activity ──",
    ])


def _status_icon(status: str) -> str:
    return {"ok": "✓", "running": "○", "failed": "✗", "skipped": "–"}.get(status, "·")


def _fmt_elapsed(ms: int) -> str:
    if ms >= 1000:
        return f"{ms / 1000:.1f}s"
    return f"{ms}ms"