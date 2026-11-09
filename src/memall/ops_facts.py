"""
Operational facts store — isolated from the memory pipeline
==========================================================
CRUD for ``ops_facts`` (migration 043).  This is plain SQL on the main DB;
nothing here touches ``memories``, FTS, hooks, levels or trust filters.

Semantics
---------
* A fact is identified by ``(category, key)`` and is *active* while
  ``superseded_at IS NULL``.  A partial UNIQUE index enforces one active row
  per key.
* ``put`` is an upsert with history: when a key already has an active row and
  the value/detail actually changed, the old row is marked superseded and a
  new one inserted.  Identical writes are a no-op (returns ``unchanged``).
* ``get`` / ``list`` return active rows by default; ``history=True`` includes
  superseded ones so the change log is auditable.

Used by the gateway REST routes ``/ops/facts/*`` (and optionally an MCP tool).
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from memall.core.db import get_conn


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row(r: sqlite3.Row) -> Dict[str, Any]:
    d = dict(r)
    # value/detail are stored as TEXT; try to surface JSON when it parses,
    # otherwise return the raw string.  Callers can always read ``raw``.
    for f in ("value", "detail"):
        v = d.get(f, "")
        if isinstance(v, str) and v[:1] in ("{", "["):
            try:
                d[f + "_json"] = json.loads(v)
            except (ValueError, TypeError):
                pass
    return d


def _dump(v: Any) -> str:
    if isinstance(v, str):
        return v
    return json.dumps(v, ensure_ascii=False, default=str)


def put(category: str, key: str, value: Any, detail: Any = "",
        source: str = "") -> Dict[str, Any]:
    """Insert or supersede the active fact for ``(category, key)``."""
    if not category or not key:
        return {"status": "error", "error": "category and key are required"}
    val = _dump(value)
    det = _dump(detail) if detail not in (None, "") else ""
    conn = get_conn()
    try:
        now = _now()
        cur = conn.execute(
            "SELECT id, value, detail FROM ops_facts "
            "WHERE category=? AND key=? AND superseded_at IS NULL",
            (category, key),
        ).fetchone()
        if cur and cur["value"] == val and (cur["detail"] or "") == det:
            return {"status": "ok", "action": "unchanged", "id": cur["id"],
                    "category": category, "key": key}
        if cur:
            conn.execute(
                "UPDATE ops_facts SET superseded_at=? WHERE id=?", (now, cur["id"])
            )
        c = conn.execute(
            "INSERT INTO ops_facts(category, key, value, detail, source, "
            "valid_from, created_at) VALUES(?,?,?,?,?,?,?)",
            (category, key, val, det, source, now, now),
        )
        conn.commit()
        return {"status": "ok", "action": "superseded" if cur else "inserted",
                "id": c.lastrowid, "category": category, "key": key,
                "superseded_id": cur["id"] if cur else None}
    finally:
        conn.close()


def get(category: str, key: str, history: bool = False) -> Dict[str, Any]:
    conn = get_conn()
    try:
        q = ("SELECT * FROM ops_facts WHERE category=? AND key=? "
             + ("" if history else "AND superseded_at IS NULL ")
             + "ORDER BY id DESC")
        rows = conn.execute(q, (category, key)).fetchall()
        return {"status": "ok", "category": category, "key": key,
                "count": len(rows), "rows": [_row(r) for r in rows]}
    finally:
        conn.close()


def list_facts(category: str = "", history: bool = False) -> Dict[str, Any]:
    conn = get_conn()
    try:
        where, params = [], []
        if not history:
            where.append("superseded_at IS NULL")
        if category:
            where.append("category=?")
            params.append(category)
        sql = "SELECT * FROM ops_facts"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY category, key, id DESC"
        rows = conn.execute(sql, params).fetchall()
        return {"status": "ok", "count": len(rows),
                "rows": [_row(r) for r in rows]}
    finally:
        conn.close()


def delete(category: str, key: str) -> Dict[str, Any]:
    """Mark the active fact as superseded (soft delete, keeps history)."""
    conn = get_conn()
    try:
        now = _now()
        cur = conn.execute(
            "UPDATE ops_facts SET superseded_at=? "
            "WHERE category=? AND key=? AND superseded_at IS NULL",
            (now, category, key),
        )
        conn.commit()
        return {"status": "ok", "deleted": cur.rowcount,
                "category": category, "key": key}
    finally:
        conn.close()


def categories() -> Dict[str, Any]:
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT category, COUNT(*) AS n FROM ops_facts "
            "WHERE superseded_at IS NULL GROUP BY category ORDER BY category"
        ).fetchall()
        return {"status": "ok", "categories": [dict(r) for r in rows]}
    finally:
        conn.close()
