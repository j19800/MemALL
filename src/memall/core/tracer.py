"""Lightweight in-process tracer — span context manager + SQLite persistence."""

import json
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any
from memall.core.db import pool_conn

_TRACE_ID: str | None = None
_lock = threading.Lock()


def ensure_trace() -> str:
    """Return or create the current thread's trace ID."""
    global _TRACE_ID
    if _TRACE_ID is None:
        with _lock:
            if _TRACE_ID is None:
                _TRACE_ID = uuid.uuid4().hex[:16]
    return _TRACE_ID


def reset_trace():
    """Reset trace ID (call at start of each top-level request)."""
    global _TRACE_ID
    _TRACE_ID = None


def _write_span(trace_id: str, parent_span_id: str | None, span_id: str,
                name: str, span_type: str, start_time: str, duration_ms: float,
                status: str, attributes: dict):
    """Persist a span record to the tracing_spans table."""
    try:
        with pool_conn() as conn:
            conn.execute(
                """INSERT INTO tracing_spans
                   (trace_id, parent_span_id, span_id, name, span_type,
                    start_time, duration_ms, status, attributes, created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (trace_id, parent_span_id, span_id, name, span_type,
                 start_time, duration_ms, status, json.dumps(attributes, ensure_ascii=False),
                 datetime.now(timezone.utc).isoformat()),
            )
            conn.commit()
    except sqlite3.Error:
        pass  # tracing must never block the hot path


@contextmanager
def span(name: str, span_type: str = "", attributes: dict[str, Any] | None = None,
         parent_span_id: str | None = None):
    """Context manager recording a span to the tracing DB.

    Usage::

        with span("tool.call", "tool", {"tool_name": "retrieve"}):
            ...
    """
    trace_id = ensure_trace()
    span_id = uuid.uuid4().hex[:12]
    start_ts = datetime.now(timezone.utc).isoformat()
    t0 = time.time()
    status = "ok"
    try:
        yield span_id
    except Exception as exc:
        status = "error"
        if attributes is None:
            attributes = {}
        attributes["error"] = str(exc)
        raise
    finally:
        duration_ms = (time.time() - t0) * 1000
        _write_span(trace_id, parent_span_id, span_id, name, span_type,
                    start_ts, duration_ms, status, attributes or {})
