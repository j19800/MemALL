"""Bus DLQ helpers — local-side bookkeeping for undeliverable messages.

This module provides the *client-side* dead-letter queue helpers.  The bus
server itself is a separate process; when it is unavailable (BusUnavailable)
the message is recorded here so it can be replayed later instead of being
silently dropped.
"""
import json
import sqlite3
from datetime import datetime, timezone
from memall.core.db import get_conn


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ensure_dlq_table(conn) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS bus_dlq (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            message_id TEXT NOT NULL,
            to_agent TEXT NOT NULL,
            payload TEXT NOT NULL,
            error TEXT,
            retry_count INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            resolved_at TEXT
        )
    """)
    conn.commit()


def record_failure(message_id: str, to_agent: str, payload, error: str) -> int:
    """Record a failed send into the local DLQ.  Returns the DLQ row id."""
    conn = get_conn()
    try:
        _ensure_dlq_table(conn)
        cur = conn.execute(
            "INSERT INTO bus_dlq (message_id, to_agent, payload, error, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (message_id, to_agent, json.dumps(payload, ensure_ascii=False,
                                            default=str), error, _now()),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def list_pending(limit: int = 50) -> list:
    """List unresolved DLQ entries."""
    conn = get_conn()
    try:
        _ensure_dlq_table(conn)
        rows = conn.execute(
            "SELECT * FROM bus_dlq WHERE resolved_at IS NULL "
            "ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def resolve(dlq_id: int) -> None:
    """Mark a DLQ entry as resolved."""
    conn = get_conn()
    try:
        _ensure_dlq_table(conn)
        conn.execute(
            "UPDATE bus_dlq SET resolved_at = ? WHERE id = ?",
            (_now(), dlq_id),
        )
        conn.commit()
    finally:
        conn.close()


def retry(dlq_id: int) -> dict:
    """Attempt to replay a DLQ entry.  Returns {success, error}."""
    from memall.mcp.bus import BusClient, BusUnavailable
    conn = get_conn()
    try:
        _ensure_dlq_table(conn)
        row = conn.execute(
            "SELECT * FROM bus_dlq WHERE id = ?", (dlq_id,),
        ).fetchone()
        if not row:
            return {"success": False, "error": "dlq entry not found"}
        if row["resolved_at"]:
            return {"success": False, "error": "already resolved"}
        to_agent = row["to_agent"]
        payload = json.loads(row["payload"])
        agent = row.get("to_agent", "")
        # agent_name is stored in message_id prefix if needed; fall back to
        # the caller's identity via arguments.
        try:
            with BusClient() as bus:
                bus.connect(to_agent)
                bus.publish(to_agent, payload, dedup_key=row["message_id"])
        except BusUnavailable as exc:
            conn.execute(
                "UPDATE bus_dlq SET retry_count = retry_count + 1, error = ? "
                "WHERE id = ?",
                (str(exc), dlq_id),
            )
            conn.commit()
            return {"success": False, "error": str(exc)}
        conn.execute(
            "UPDATE bus_dlq SET resolved_at = ? WHERE id = ?",
            (_now(), dlq_id),
        )
        conn.commit()
        return {"success": True}
    finally:
        conn.close()
