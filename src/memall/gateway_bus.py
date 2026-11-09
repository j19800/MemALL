"""
Gateway-integrated realtime message bus (mailbox + SSE push)
===========================================================
Historically ``memall_msg`` talked to a *separate* TCP process
(``memall_bus.py``, 127.0.0.1:8770, line-delimited JSON, protocol v2).  On
this deployment that server does not exist, so the tool was permanently
"degraded" while the MCP HTTP gateway (0.0.0.0:9920) — already reachable on
the LAN with Bearer auth — was healthy.

This module **merges** the realtime notification layer into the gateway:

  * one process / one port (9920), same bind address as MCP,
  * same Bearer token as ``/mcp`` (no separate bus_token),
  * mailbox persisted next to the main DB (``bus.db``), messages carry only
    a ``memory_id`` pointer — the L5 task card in ``memories`` stays the
    single source of truth (design unchanged),
  * SSE subscription (``GET /bus/subscribe``) gives the "realtime push" that
    the old TCP server provided to online recipients.

Kept intentionally small and dependency-free (stdlib sqlite3 + asyncio).
All DB access is synchronous but guarded by a lock; it runs inside the
gateway's aiohttp event loop via ``asyncio.to_thread`` where useful, and the
critical sections are short enough that a plain lock is fine.
"""
from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from aiohttp import web

logger = logging.getLogger("memall.gateway.bus")

# Same protocol version the old TCP bus used (kept for compatibility with
# clients that display it in `status`).
BUS_PROTOCOL_VERSION = 2


def bus_db_path() -> Path:
    """Mailbox lives next to the main memory DB (``~/.memall/bus.db``)."""
    try:
        from memall.core.db import get_db_path
        return get_db_path().parent / "bus.db"
    except Exception:
        return Path.home() / ".memall" / "bus.db"


class GatewayBus:
    """In-process mailbox with per-agent SSE subscribers.

    Thread-safe: MCP tool handlers run in a ThreadPoolExecutor, SSE handlers
    run in the event loop — both may touch the mailbox concurrently.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._db: Optional[sqlite3.Connection] = None
        # agent -> list[asyncio.Queue]; queues belong to the gateway loop
        self._subscribers: Dict[str, List[asyncio.Queue]] = {}
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    # ── lifecycle ────────────────────────────────────────────────────
    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Remember the event loop so DB-thread pushes can reach SSE queues."""
        self._loop = loop

    def init_db(self) -> None:
        with self._lock:
            if self._db is not None:
                return
            path = bus_db_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(str(path), check_same_thread=False,
                                   timeout=10.0)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=5000")
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS bus_messages (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    sender      TEXT NOT NULL,
                    recipient   TEXT NOT NULL,
                    type        TEXT NOT NULL DEFAULT 'msg',
                    payload     TEXT NOT NULL DEFAULT '{}',
                    dedup_key   TEXT,
                    ts          REAL NOT NULL,
                    read        INTEGER NOT NULL DEFAULT 0
                );
                CREATE UNIQUE INDEX IF NOT EXISTS ux_bus_dedup
                    ON bus_messages(dedup_key) WHERE dedup_key IS NOT NULL AND dedup_key <> '';
                CREATE INDEX IF NOT EXISTS ix_bus_inbox
                    ON bus_messages(recipient, read, id);
                """
            )
            conn.commit()
            self._db = conn
            logger.info("Gateway bus mailbox ready at %s", path)

    # ── ops (called from MCP tool handlers) ───────────────────────────
    def publish(self, sender: str, recipient: str, payload: Any,
                mtype: str = "msg", dedup_key: str = "") -> dict:
        with self._lock:
            self.init_db()
            now = time.time()
            payload_json = json.dumps(payload if payload is not None else {},
                                      ensure_ascii=False, default=str)
            if dedup_key:
                row = self._db.execute(
                    "SELECT id FROM bus_messages WHERE dedup_key=?",
                    (dedup_key,),
                ).fetchone()
                if row:
                    return {"op": "pub_ok", "id": row["id"],
                            "deduplicated": True, "pushed": 0}
            try:
                cur = self._db.execute(
                    "INSERT INTO bus_messages(sender, recipient, type, payload, "
                    "dedup_key, ts, read) VALUES(?,?,?,?,?,?,0)",
                    (sender, recipient, mtype, payload_json,
                     dedup_key or None, now),
                )
                self._db.commit()
            except sqlite3.IntegrityError:
                # Concurrent duplicate on the same dedup_key.
                row = self._db.execute(
                    "SELECT id FROM bus_messages WHERE dedup_key=?",
                    (dedup_key,),
                ).fetchone()
                return {"op": "pub_ok", "id": row["id"] if row else None,
                        "deduplicated": True, "pushed": 0}
            mid = cur.lastrowid
            msg = {"id": mid, "from": sender, "to": recipient,
                   "type": mtype, "payload": payload, "ts": now}
        pushed = self._push(recipient, msg)
        return {"op": "pub_ok", "id": mid, "deduplicated": False,
                "pushed": pushed}

    def inbox(self, agent: str) -> List[dict]:
        with self._lock:
            self.init_db()
            rows = self._db.execute(
                "SELECT id, sender, type, payload, ts FROM bus_messages "
                "WHERE recipient=? AND read=0 ORDER BY id ASC",
                (agent,),
            ).fetchall()
        out = []
        for r in rows:
            try:
                payload = json.loads(r["payload"])
            except Exception:
                payload = {}
            out.append({"id": r["id"], "from": r["sender"],
                        "type": r["type"], "payload": payload, "ts": r["ts"]})
        return out

    def ack(self, agent: str, ids: List[int]) -> dict:
        if not ids:
            return {"op": "ack_ok", "marked": 0, "ids": []}
        placeholders = ",".join("?" for _ in ids)
        with self._lock:
            self.init_db()
            cur = self._db.execute(
                f"UPDATE bus_messages SET read=1 WHERE recipient=? AND read=0 "
                f"AND id IN ({placeholders})",
                [agent, *ids],
            )
            self._db.commit()
            marked = cur.rowcount
        return {"op": "ack_ok", "marked": marked, "ids": ids}

    def stats(self) -> dict:
        with self._lock:
            self.init_db()
            total = self._db.execute(
                "SELECT COUNT(*) AS c FROM bus_messages").fetchone()["c"]
            unread = self._db.execute(
                "SELECT COUNT(*) AS c FROM bus_messages WHERE read=0"
            ).fetchone()["c"]
        return {"total": total, "unread": unread}

    # ── realtime push ────────────────────────────────────────────────
    def subscribe(self, agent: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=256)
        with self._lock:
            self._subscribers.setdefault(agent, []).append(q)
        return q

    def unsubscribe(self, agent: str, q: asyncio.Queue) -> None:
        with self._lock:
            lst = self._subscribers.get(agent, [])
            if q in lst:
                lst.remove(q)
            if not lst:
                self._subscribers.pop(agent, None)

    def _push(self, recipient: str, msg: dict) -> int:
        """Deliver to online subscribers (thread-safe crossing into loop)."""
        with self._lock:
            queues = list(self._subscribers.get(recipient, []))
        if not queues:
            return 0
        loop = self._loop
        n = 0
        for q in queues:
            if loop is not None and loop.is_running():
                loop.call_soon_threadsafe(self._safe_put, q, msg)
                n += 1
            else:
                try:
                    q.put_nowait(msg)
                    n += 1
                except asyncio.QueueFull:
                    pass
        return n

    @staticmethod
    def _safe_put(q: asyncio.Queue, msg: dict) -> None:
        try:
            q.put_nowait(msg)
        except asyncio.QueueFull:
            logger.warning("bus SSE queue full, dropping push to a subscriber")


# Process-wide singleton — the gateway and the MCP tool handlers both use it.
BUS = GatewayBus()


class BusHandlersMixin:
    """``/bus/*`` routes — realtime messaging merged into the HTTP gateway."""

    def _bus_agent(self, request: web.Request) -> str:
        # Identity comes from the caller's own declaration in the JSON/query,
        # exactly like the old TCP bus `hello` op.  Auth is already enforced
        # by the gateway middleware (Bearer token), so a caller cannot
        # impersonate another *gateway client*, only another agent name —
        # same trust model as before.
        return (request.query.get("agent") or "").strip()

    async def _handle_bus_publish(self, request: web.Request) -> web.Response:
        data = await self._read_json(request)
        if data is None:
            return web.json_response({"error": "invalid JSON body"}, status=400)
        sender = (data.get("from") or "").strip()
        recipient = (data.get("to") or "").strip()
        if not sender or not recipient:
            return web.json_response(
                {"error": "from and to are required"}, status=400)
        payload = data.get("payload")
        if payload is None:
            payload = {}
        mtype = (data.get("type") or "msg").strip()
        dedup_key = (data.get("dedup_key") or "").strip()
        result = await asyncio.to_thread(
            BUS.publish, sender, recipient, payload, mtype, dedup_key)
        return web.json_response(result)

    async def _handle_bus_inbox(self, request: web.Request) -> web.Response:
        agent = self._bus_agent(request)
        if not agent:
            return web.json_response({"error": "agent is required"}, status=400)
        msgs = await asyncio.to_thread(BUS.inbox, agent)
        return web.json_response({"op": "inbox", "messages": msgs})

    async def _handle_bus_ack(self, request: web.Request) -> web.Response:
        data = await self._read_json(request)
        if data is None:
            return web.json_response({"error": "invalid JSON body"}, status=400)
        agent = (data.get("agent") or "").strip()
        ids = data.get("ids") or []
        if not agent or not isinstance(ids, list):
            return web.json_response(
                {"error": "agent and ids[] are required"}, status=400)
        try:
            ids = [int(i) for i in ids]
        except (TypeError, ValueError):
            return web.json_response({"error": "ids must be integers"}, status=400)
        result = await asyncio.to_thread(BUS.ack, agent, ids)
        return web.json_response(result)

    async def _handle_bus_subscribe(self, request: web.Request) -> web.StreamResponse:
        """SSE stream of realtime pushes for one agent."""
        agent = self._bus_agent(request)
        if not agent:
            return web.json_response({"error": "agent is required"}, status=400)
        q = BUS.subscribe(agent)
        BUS.bind_loop(asyncio.get_running_loop())
        response = web.StreamResponse(
            status=200, reason="OK",
            headers={
                "Content-Type": "text/event-stream",
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )
        await response.prepare(request)
        await response.write(
            f"data: {json.dumps({'op': 'welcome', 'agent': agent, 'proto': BUS_PROTOCOL_VERSION}, ensure_ascii=False)}\n\n".encode()
        )
        try:
            while True:
                try:
                    msg = await asyncio.wait_for(q.get(), timeout=25.0)
                except asyncio.TimeoutError:
                    await response.write(b": heartbeat\n\n")
                    continue
                await response.write(
                    f"data: {json.dumps({'op': 'msg', **msg}, ensure_ascii=False)}\n\n".encode()
                )
        except (ConnectionResetError, ConnectionAbortedError,
                ConnectionError, asyncio.CancelledError):
            pass
        finally:
            BUS.unsubscribe(agent, q)
        return response

    async def _handle_bus_health(self, request: web.Request) -> web.Response:
        stats = await asyncio.to_thread(BUS.stats)
        return web.json_response({
            "available": True,
            "transport": "gateway-http",
            "proto": BUS_PROTOCOL_VERSION,
            "host": request.host,
            "db": str(bus_db_path()),
            **stats,
        })

    async def _handle_bus_ping(self, request: web.Request) -> web.Response:
        return web.json_response({"op": "pong", "proto": BUS_PROTOCOL_VERSION})
