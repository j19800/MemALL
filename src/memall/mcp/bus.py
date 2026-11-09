"""
MemALL realtime message bus client — HTTP transport via the gateway
==================================================================
Historical note
---------------
This module used to be a *synchronous TCP client* for a standalone process
(``memall_bus.py``, 127.0.0.1:8770, line-delimited JSON, protocol v2).  That
process was never shipped on this deployment, so ``memall_msg`` was stuck in
"degraded" while the MCP HTTP gateway (0.0.0.0:9920, Bearer auth, LAN
reachable) was perfectly healthy.

The realtime notification layer has now been **merged into the gateway**
(``memall/gateway_bus.py``, ``/bus/*`` routes).  This client keeps the exact
same public surface — ``BusClient.connect/publish/inbox/ack/ping`` — but talks
HTTP to the gateway instead of opening a TCP socket.  Consequences:

  * one process / one port instead of two,
  * same bind address as MCP (``0.0.0.0`` → reachable on the LAN),
  * same Bearer token as ``/mcp`` (``MEMALL_GATEWAY_TOKEN`` / ``.gateway-token``),
  * mailbox still persists in ``~/.memall/bus.db``; messages still carry only
    a ``memory_id`` pointer — the L5 task card in ``memories`` is the single
    source of truth.

``BusUnavailable`` semantics are preserved so ``mcp/tools/msg.py`` needs no
changes; ``ensure_bus_running`` is now a no-op that reports the gateway as
the transport (there is no second process to start).
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

# Kept for compatibility with clients that display it in `status`.
PROTOCOL_VERSION = 2

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 9920
DEFAULT_TIMEOUT = 8.0


class BusUnavailable(RuntimeError):
    """总线不可用（网关没起、连不上、鉴权失败、协议不符）。"""


def bus_home() -> str:
    return os.environ.get("MEMALL_BUS_HOME") or os.path.join(
        os.path.expanduser("~"), ".memall")


# ── Gateway connection settings ──────────────────────────────────────
def gateway_base_url() -> str:
    """Base URL of the merged gateway bus.

    Priority: explicit env, then ``MEMALL_BUS_HOST/PORT``, then loopback
    ``127.0.0.1:9920``.  DSH's MCP client uses the LAN IP
    (``http://192.168.1.140:9920``); both work because the gateway binds
    ``0.0.0.0``.
    """
    env = os.environ.get("MEMALL_BUS_URL") or os.environ.get("MEMALL_GATEWAY_URL")
    if env:
        return env.rstrip("/")
    host = os.environ.get("MEMALL_BUS_HOST", DEFAULT_HOST)
    port = int(os.environ.get("MEMALL_BUS_PORT", DEFAULT_PORT))
    return f"http://{host}:{port}"


def gateway_token() -> str:
    """Bearer token — same credential the MCP client uses for ``/mcp``.

    Resolution order: env var, the deployment's ``.gateway-token`` file next
    to the MemALL repo, then ``~/.memall/gateway_token``.
    """
    env = (os.environ.get("MEMALL_GATEWAY_TOKEN")
           or os.environ.get("MEMALL_BUS_TOKEN")
           or os.environ.get("MEMALL_AUTH_TOKEN")
           or os.environ.get("MEMALL_MCP_TOKEN"))
    if env:
        return env.strip()
    candidates = [
        Path(__file__).resolve().parents[3] / ".gateway-token",  # MemALL/.gateway-token
        Path(bus_home()) / "gateway_token",
        Path(bus_home()) / "bus_token",  # legacy
    ]
    for p in candidates:
        try:
            if p.exists():
                tok = p.read_text(encoding="utf-8").strip()
                if tok:
                    return tok
        except OSError:
            pass
    return ""


# ── Legacy token helpers (kept so old imports don't break) ────────────
def install_token_path() -> str:
    return os.path.join(bus_home(), "bus_token")


def agent_token_dir() -> str:
    return os.path.join(bus_home(), "bus_agents")


def read_install_token():
    return gateway_token() or None


def read_agent_token(agent: str):
    return None  # gateway auth is per-client, not per-agent


def write_agent_token(agent: str, tok: str) -> bool:
    return True  # no-op under the merged gateway


class BusClient:
    """Synchronous HTTP client for the gateway-integrated mailbox.

    Mirrors the old TCP ``BusClient`` API so ``memall_msg`` is unchanged.
    """

    def __init__(self, host: str = None, port: int = None,
                 timeout: float = DEFAULT_TIMEOUT):
        # host/port are accepted for backward compatibility; under the merged
        # transport the gateway URL is authoritative.
        self.host = host or os.environ.get("MEMALL_BUS_HOST", DEFAULT_HOST)
        self.port = int(port or os.environ.get("MEMALL_BUS_PORT", DEFAULT_PORT))
        self.timeout = timeout
        self.agent = ""
        self.base_url = gateway_base_url()
        self.token = gateway_token()
        self.server_proto = None

    # ── low-level HTTP ───────────────────────────────────────────────
    def _url(self, path: str) -> str:
        return f"{self.base_url}{path}"

    def _request(self, path: str, payload: dict = None,
                 method: str = "GET") -> dict:
        url = self._url(path)
        data = None
        headers = {"Accept": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        if payload is not None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
            method = "POST"
        req = urllib.request.Request(url, data=data, headers=headers,
                                     method=method)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403):
                raise BusUnavailable(
                    f"网关总线鉴权失败（HTTP {exc.code}）：请检查 "
                    f"MEMALL_GATEWAY_TOKEN / .gateway-token 与 /mcp 是否一致"
                ) from exc
            raise BusUnavailable(f"网关总线请求失败: HTTP {exc.code}") from exc
        except (urllib.error.URLError, OSError) as exc:
            raise BusUnavailable(
                f"连不上网关总线 {self.base_url}（{exc}）。"
                f"总线已合并进 MCP 网关（{self.base_url}/mcp）；"
                f"请确认网关进程存活，且本机/局域网都能访问该端口。"
            ) from exc
        try:
            return json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            raise BusUnavailable(f"网关总线返回了非 JSON 响应: {exc}") from exc

    # ── connection / identity ────────────────────────────────────────
    def connect(self, agent: str) -> dict:
        """Verify the gateway bus is reachable and register the agent name.

        Returns a ``welcome``-shaped dict so existing callers that inspect
        ``proto`` keep working.
        """
        self.agent = agent
        r = self._request("/bus/ping")
        self.server_proto = r.get("proto", PROTOCOL_VERSION)
        if r.get("op") != "pong":
            raise BusUnavailable("网关总线 ping 响应异常")
        return {"op": "welcome", "agent": agent,
                "proto": self.server_proto, "transport": "gateway-http"}

    def close(self):
        return None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # ── public operations ────────────────────────────────────────────
    def publish(self, to: str, payload, mtype: str = "msg",
                dedup_key: str = "") -> dict:
        r = self._request("/bus/publish", {
            "from": self.agent, "to": to, "type": mtype,
            "payload": payload, "dedup_key": dedup_key,
        })
        if r.get("op") == "error":
            raise BusUnavailable(f"publish 失败: {r.get('msg') or r.get('error')}")
        return r

    def inbox(self) -> list:
        if not self.agent:
            raise BusUnavailable("inbox 需要先 connect(agent)")
        r = self._request(f"/bus/inbox?agent={urllib.parse.quote(self.agent)}")
        return r.get("messages", [])

    def ack(self, ids: list) -> dict:
        r = self._request("/bus/ack", {
            "agent": self.agent, "ids": list(ids or []),
        })
        if r.get("op") == "error":
            raise BusUnavailable(f"ack 失败: {r.get('msg') or r.get('error')}")
        return r

    def ping(self) -> bool:
        try:
            return self._request("/bus/ping").get("op") == "pong"
        except BusUnavailable:
            return False


def bus_health(host: str = None, port: int = None) -> dict:
    """Probe the merged gateway bus (no identity registration)."""
    client = BusClient(host=host, port=port, timeout=2.0)
    out = {"available": False, "host": client.base_url,
           "port": client.port, "transport": "gateway-http"}
    try:
        r = client._request("/bus/health")
        out["available"] = bool(r.get("available"))
        out["proto"] = r.get("proto")
        out["total"] = r.get("total")
        out["unread"] = r.get("unread")
        out["db"] = r.get("db")
    except Exception as exc:
        out["error"] = str(exc)
    return out


def ensure_bus_running(timeout_s: float = 10.0) -> dict:
    """Compatibility shim — there is no longer a separate process to start.

    The bus is served by the gateway itself.  If it is unreachable, the only
    fix is to (re)start the gateway; we report that clearly instead of trying
    to spawn a non-existent ``memall_bus.py``.
    """
    h = bus_health()
    return {
        "available": h.get("available", False),
        "started": False,
        "pid": None,
        "error": None if h.get("available") else (
            f"总线已合并进 MCP 网关（{gateway_base_url()}/bus/*），"
            f"无需也无法单独启动；请确认网关进程存活。探测错误: {h.get('error')}"
        ),
    }
