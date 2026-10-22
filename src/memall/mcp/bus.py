"""
MemALL ↔ memall-bus 总线客户端（同步 socket 实现）
==================================================
memall-bus（``D:\\Agent项目\\Claw\\memall-bus``）是一个独立进程：asyncio TCP 服务
+ sqlite3 mailbox。它给 agent 之间提供**实时**投递，而 MemALL 主库里的 L5 任务卡
是**唯一事实源**。两者的分工是：

    L5 任务卡（memories, category='task'）  =  事实源
    总线 mailbox（bus.db）                   =  通知层：告诉对方「有这张卡，去按 id 取」

所以总线消息**只带 memory_id 指针**，不带任务正文。

为什么是同步 socket 而不是 asyncio
----------------------------------
MCP 的 handler 是同步的（``mcp/server.py`` 的 ``_read_request`` 读 stdin 的循环），
而 memall-bus 的客户端库是 asyncio 的。若在这里用 asyncio.run()，每次工具调用
都要新建事件循环 + 新建连接 + 走一遍握手，慢且容易和宿主的事件循环打架。
协议本身只是「行分隔的 JSON」，用 socket 同步实现更合适。

协议版本：与 memall-bus 的协议 v2 一致（2026-10-21）。
本文件是 memall-bus/bus_client.py 的最小同步实现，两边必须同步演进 ——
参考实现见 ``D:\\Agent项目\\Claw\\memall-bus\\技术报告-多Agent即时通讯总线-供Agent调研学习.md``。
"""
import json
import os
import socket
import time

# 与 memall-bus 的 PROTOCOL_VERSION 一致
PROTOCOL_VERSION = 2

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8770
# 与 memall-bus 一样：单行 JSON 上限 16MB
MAX_LINE = 16 * 1024 * 1024
# 一次工具调用的默认超时（秒）—— 必须有上限，否则总线挂住会把 MCP 调用一起挂死
DEFAULT_TIMEOUT = 5.0


class BusUnavailable(RuntimeError):
    """总线不可用（没起、连不上、鉴权失败、协议不符）。"""


def bus_home() -> str:
    return os.environ.get("MEMALL_BUS_HOME") or os.path.join(
        os.path.expanduser("~"), ".memall")


def install_token_path() -> str:
    return os.path.join(bus_home(), "bus_token")


def agent_token_dir() -> str:
    return os.path.join(bus_home(), "bus_agents")


def read_install_token():
    env = os.environ.get("MEMALL_BUS_TOKEN")
    if env:
        return env.strip() or None
    p = install_token_path()
    try:
        if os.path.exists(p):
            with open(p, encoding="utf-8") as f:
                tok = f.read().strip()
            return tok or None
    except OSError:
        pass
    return None


def read_agent_token(agent: str):
    p = os.path.join(agent_token_dir(), f"{agent}.token")
    try:
        if os.path.exists(p):
            with open(p, encoding="utf-8") as f:
                tok = f.read().strip()
            return tok or None
    except OSError:
        pass
    return None


def write_agent_token(agent: str, tok: str) -> bool:
    p = os.path.join(agent_token_dir(), f"{agent}.token")
    try:
        os.makedirs(agent_token_dir(), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            f.write(tok)
        return True
    except OSError:
        return False


class BusClient:
    """同步的总线客户端。每次调用建立一条短连接（协议是 request/response）。

    短连接的代价是每次一次 TCP 握手（回环上 <1ms）；换来的是零事件循环耦合、
    零线程泄漏，且 MCP server 重启后自动连上，无需管长连接的生命周期。
    """

    def __init__(self, host: str = None, port: int = None,
                 timeout: float = DEFAULT_TIMEOUT):
        self.host = host or os.environ.get("MEMALL_BUS_HOST", DEFAULT_HOST)
        self.port = int(port or os.environ.get("MEMALL_BUS_PORT", DEFAULT_PORT))
        self.timeout = timeout
        self._sock = None
        self._buf = b""
        self._req_id = 0
        self.agent = ""
        self.server_proto = None

    # ── 连接 ──────────────────────────────────────────────────────────
    def connect(self, agent: str) -> dict:
        """建连 + 认证 + 注册身份。返回 welcome 负载。"""
        self.agent = agent
        try:
            self._sock = socket.create_connection((self.host, self.port),
                                                  timeout=self.timeout)
            self._sock.settimeout(self.timeout)
            self._sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except OSError as exc:
            raise BusUnavailable(
                f"连不上总线 {self.host}:{self.port}（{exc}）。"
                f"总线是独立常驻进程，需要先启动 memall_bus.py；"
                f"这不影响 MemALL 主链路（auto_inject 拉模式照常可用）。"
            ) from exc

        self._req_id += 1
        rid = self._req_id
        self._send({
            "op": "hello", "agent": agent, "req_id": rid,
            "token": read_install_token(),
            "agent_token": read_agent_token(agent),
        })
        welcome = self._await(rid, {"welcome"})
        if welcome.get("op") == "error":
            raise BusUnavailable(f"总线鉴权失败: {welcome.get('msg')}")
        self.server_proto = welcome.get("proto")
        if self.server_proto != PROTOCOL_VERSION:
            raise BusUnavailable(
                f"总线协议版本不匹配：服务端 v{self.server_proto} / "
                f"客户端 v{PROTOCOL_VERSION}，请同步升级两端")
        issued = welcome.get("agent_token")
        if issued:
            # 服务端首次登记该 agent 时签发的，落盘保存
            write_agent_token(agent, issued)
        return welcome

    def close(self):
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # ── 收发 ──────────────────────────────────────────────────────────
    def _send(self, obj: dict) -> None:
        data = (json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8")
        try:
            self._sock.sendall(data)
        except OSError as exc:
            raise BusUnavailable(f"总线发送失败: {exc}") from exc

    def _readline(self) -> dict:
        while b"\n" not in self._buf:
            try:
                chunk = self._sock.recv(65536)
            except socket.timeout as exc:
                raise BusUnavailable(f"总线读取超时（{self.timeout}s）") from exc
            except OSError as exc:
                raise BusUnavailable(f"总线连接断开: {exc}") from exc
            if not chunk:
                raise BusUnavailable("总线连接已关闭")
            self._buf += chunk
            if len(self._buf) > MAX_LINE:
                self._buf = b""
                raise BusUnavailable("总线响应超过 16MB 上限")
        line, self._buf = self._buf.split(b"\n", 1)
        return json.loads(line.decode("utf-8"))

    def _await(self, rid: int, stop_ops: set) -> dict:
        """读到属于 req_id=rid 的终止响应为止。

        期间收到的**实时推送**（op=msg 且无 req_id）会被收集起来一并返回 ——
        它们本来就是给本 agent 的消息，不该被丢掉。
        """
        pushed = []
        while True:
            msg = self._readline()
            if msg.get("req_id") == rid:
                if msg.get("op") == "msg":
                    # inbox 的历史消息（带 req_id）——继续等终止符
                    pushed.append(msg)
                    continue
                msg["_pushed"] = pushed
                return msg
            if msg.get("op") == "msg" and msg.get("req_id") is None:
                pushed.append(msg)          # 实时推送，收着
                continue
            # 其它 req_id 的响应：忽略（同步客户端不会并发）

    # ── 公开操作 ──────────────────────────────────────────────────────
    def publish(self, to: str, payload, mtype: str = "msg") -> dict:
        """发消息。from 由服务端按连接身份判定。"""
        self._req_id += 1
        rid = self._req_id
        self._send({"op": "publish", "to": to, "from": self.agent,
                    "type": mtype, "payload": payload, "req_id": rid})
        r = self._await(rid, {"pub_ok"})
        if r.get("op") == "error":
            raise BusUnavailable(f"publish 失败: {r.get('msg')}")
        return r

    def inbox(self) -> list:
        """拉本 agent 的未读消息。"""
        self._req_id += 1
        rid = self._req_id
        self._send({"op": "inbox", "req_id": rid})
        msgs = []
        while True:
            m = self._readline()
            if m.get("req_id") == rid:
                if m.get("op") == "msg":
                    msgs.append(m)
                    continue
                if m.get("op") == "inbox_end":
                    break
            elif m.get("op") == "msg" and m.get("req_id") is None:
                msgs.append(m)      # 实时推送也一并交付
        return msgs

    def ack(self, ids: list) -> dict:
        """标记已读。"""
        self._req_id += 1
        rid = self._req_id
        self._send({"op": "ack", "ids": list(ids or []), "req_id": rid})
        r = self._await(rid, {"ack_ok"})
        if r.get("op") == "error":
            raise BusUnavailable(f"ack 失败: {r.get('msg')}")
        return r

    def ping(self) -> bool:
        try:
            self._req_id += 1
            rid = self._req_id
            self._send({"op": "ping", "req_id": rid})
            r = self._await(rid, {"pong"})
            return r.get("op") == "pong"
        except BusUnavailable:
            return False


def bus_health(host: str = None, port: int = None) -> dict:
    """总线健康探测（不登记身份，只 ping）。供诊断用。"""
    out = {"available": False, "host": host or os.environ.get(
        "MEMALL_BUS_HOST", DEFAULT_HOST),
        "port": int(port or os.environ.get("MEMALL_BUS_PORT", DEFAULT_PORT))}
    try:
        with socket.create_connection((out["host"], out["port"]), timeout=2.0) as s:
            s.settimeout(2.0)
            s.sendall((json.dumps({"op": "ping", "req_id": 1}) + "\n").encode())
            buf = b""
            while b"\n" not in buf:
                chunk = s.recv(4096)
                if not chunk:
                    break
                buf += chunk
            if buf:
                m = json.loads(buf.split(b"\n", 1)[0].decode("utf-8"))
                out["available"] = m.get("op") == "pong"
                out["proto"] = m.get("proto")
    except Exception as exc:  # 探测失败不是错误，只是不可用
        out["error"] = str(exc)
    return out
