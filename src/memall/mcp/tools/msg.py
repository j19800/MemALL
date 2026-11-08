"""
memall_msg — 与 memall-bus 实时总线的 MCP 工具
==============================================
三个动作：

  send   把「有一张新任务卡」的指针实时推给收件人
  inbox  拉当前 agent 的未读消息（含实时推送）
  ack    标记已读

**载荷只带 memory_id 指针，不带任务正文** —— L5 任务卡（memories 表）是唯一
事实源，总线只是通知层。这样两个库不会各存一份任务状态，重复投递也能按
memory_id 幂等去重。

**from 由服务端按连接身份判定**，客户端自报的值被忽略 —— 否则任何进程都能
冒充 PM 向执行方派假指令（这是 2026-10-21 安全审计实证过的漏洞）。

总线是**独立常驻进程**，沒起時會先嘗試自動啟動（see `ensure_bus_running`）；
啟動失敗時返回清晰報錯並附上排查提示，而 MemALL 主鏈路（auto_inject 拉模式）
完全不受影響。
"""
import hashlib
import json
import re

from memall.mcp.bus import (
    BusClient,
    BusRejected,
    BusUnavailable,
    ensure_bus_running,
)


def _agent_of(arguments: dict) -> str:
    return (arguments.get("agent_name") or "").strip()


def _err(msg: str, **extra) -> str:
    out = {"status": "error", "error": msg}
    out.update(extra)
    return json.dumps(out, ensure_ascii=False)


def _bus_op(fn, agent: str, *args, **kwargs):
    """執行總線操作；BusUnavailable 時先嘗試自動啟動總線，再重試一次。

    `fn` 應自行建立 BusClient（用 `with BusClient()`），不應接受 bus 參數。

    BusRejected（在線但被協議拒絕，如非法 dedup_key）不屬於「总线没起」，
    重啟重試只会以同一個錯誤再失敗一次，直接向上拋。
    """
    try:
        return fn()
    except BusRejected:
        raise
    except BusUnavailable:
        start = ensure_bus_running()
        if not start.get("available"):
            raise
        return fn()


_DEDUP_KEY_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")


def _dedup_key(agent: str, to: str, mtype: str, payload) -> str:
    """Legacy CLIENT-side idempotency key — kept for import compatibility only.

    ``handle_send`` no longer calls it.  Two real defects made the client the
    wrong place to derive the key (research report §4.5 D-1/D-2):

      1. ``default=str`` serialized unserializable payloads as ``repr()``, so
         the same logical message hashed differently across processes (and the
         repr of most objects embeds their id) — dedup silently never fired.
      2. It mixed in the *caller-supplied* ``agent_name`` while the bus takes
         ``from`` from the **authenticated connection** — when the two differ
         the key binds to the wrong sender.

    The server now derives the canonical ``v1:`` key itself when publish omits
    the field, using the connection identity, with no ``default=str`` (an
    unserializable payload is an error, not a silent repr).
    """
    blob = json.dumps(
        {"from": agent, "to": to, "type": mtype, "payload": payload},
        ensure_ascii=False, sort_keys=True, default=str,
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:32]


def handle_send(arguments: dict) -> str:
    to = (arguments.get("to") or "").strip()
    if not to:
        return _err("to is required")
    payload = arguments.get("payload")
    if payload is None:
        payload = {}
    mtype = arguments.get("type") or "msg"
    agent = _agent_of(arguments)
    if not agent:
        return _err("agent_name is required — the bus derives the sender "
                    "from the authenticated connection, not from the payload")
    # Three states that must stay distinguishable (A3-2 dsh ruling + D-1/D-2):
    #   * dedup_key ABSENT -> send nothing; the SERVER derives the canonical
    #     key from (authenticated connection identity, to, type, payload).
    #     Derivation belongs on the server — only it knows the real sender.
    #   * dedup_key ""     -> dedup explicitly DISABLED.  Two deliberate sends
    #     of the same payload must both land.
    #   * dedup_key "<k>"  -> caller-named business key, e.g. "999766:dispatch".
    if "dedup_key" in arguments:
        raw = arguments.get("dedup_key")
        if raw is not None and not isinstance(raw, str):
            return _err("dedup_key must be a string, or null/omitted to let the "
                        "bus derive it", delivered=False, to=to)
        key = (raw or "").strip()
        if key and not _DEDUP_KEY_RE.match(key):
            return _err("invalid dedup_key: must match [A-Za-z0-9_.:-]{1,128}",
                        delivered=False, to=to)
    else:
        key = None            # 服务端派生
    def _do_send():
        with BusClient() as bus:
            welcome = bus.connect(agent)
            r = bus.publish(to, payload, mtype, dedup_key=key)
            # welcome 的能力位：服务端是否真的实现了 dedup_key（M1 之前的
            # 老服务端会静默忽略键 —— 那正是这次要修的「已存在但未声明」缺口）。
            r["_dedup_supported"] = bool(welcome.get("dedup"))
            return r

    try:
        r = _bus_op(_do_send, agent)
    except BusRejected as exc:
        return _err(str(exc), delivered=False, to=to, dedup_key=key,
                    retryable=False)
    except BusUnavailable as exc:
        return _err(f"bus unavailable: {exc}", delivered=False, to=to,
                    dedup_key=key, retryable=True)
    deduplicated = bool(r.get("deduplicated"))
    out = {
        "status": "ok",
        "to": to,
        "message_id": r.get("id"),
        "pushed": r.get("pushed", 0),
        "dedup_key": r.get("dedup_key", key),
        # True 表示服务端**没有新建行**：这是同键重试的副本，返回的是已有行的
        # id（echo 语义），所以 message_id 依然可直接用于 ack。
        "deduplicated": deduplicated,
        "dedup_supported_by_bus": r.get("_dedup_supported", False),
    }
    if deduplicated:
        out["note"] = ("去重命中：与之前同键的未读消息属同一次逻辑发送，"
                       "服务端未新建行，返回原行 id；等该行被 ack 后同键会重新可用。")
    else:
        out["note"] = ("pushed=0 表示收件人当前不在线；消息已进 mailbox，"
                       "对方下次上线用 inbox 动作拉取")
    return json.dumps(out, ensure_ascii=False)


def handle_inbox(arguments: dict) -> str:
    agent = _agent_of(arguments)
    if not agent:
        return _err("agent_name is required")
    def _do_inbox():
        with BusClient() as bus:
            bus.connect(agent)
            return bus.inbox()

    try:
        msgs = _bus_op(_do_inbox, agent)
    except BusUnavailable as exc:
        return _err(f"bus unavailable: {exc}", messages=[])
    out = []
    for m in msgs:
        out.append({
            "id": m.get("id"),
            "from": m.get("from"),
            "type": m.get("type"),
            "payload": m.get("payload"),
            "ts": m.get("ts"),
        })
    return json.dumps({
        "status": "ok",
        "count": len(out),
        "messages": out,
        "note": ("消息未自动标记已读；处理完后用 ack 动作传 ids 标记。"
                 "payload 里通常只有 memory_id —— 请用 memall_read 取出任务正文"),
    }, ensure_ascii=False)


def handle_ack(arguments: dict) -> str:
    agent = _agent_of(arguments)
    if not agent:
        return _err("agent_name is required")
    ids = arguments.get("ids") or []
    if not isinstance(ids, list) or not ids:
        return _err("ids is required and must be a non-empty list")
    def _do_ack():
        with BusClient() as bus:
            bus.connect(agent)
            return bus.ack(ids)

    try:
        r = _bus_op(_do_ack, agent)
    except BusUnavailable as exc:
        return _err(f"bus unavailable: {exc}", marked=0)
    return json.dumps({
        "status": "ok",
        "marked": r.get("marked", 0),
        "ids": r.get("ids", []),
    }, ensure_ascii=False)


def handle_status(arguments: dict) -> str:
    from memall.mcp.bus import bus_health, ensure_bus_running
    h = bus_health()
    if not h.get("available"):
        start = ensure_bus_running()
        h["started"] = start.get("started", False)
        h["pid"] = start.get("pid")
        if start.get("error"):
            h["start_error"] = start["error"]
        h = bus_health()  # re-probe
    return json.dumps({"status": "ok" if h.get("available") else "degraded",
                       **h}, ensure_ascii=False)


HANDLERS = {
    "send": handle_send,
    "inbox": handle_inbox,
    "ack": handle_ack,
    "status": handle_status,
}


def handle(arguments: dict) -> str:
    action = (arguments.get("action") or "inbox").strip()
    fn = HANDLERS.get(action)
    if fn is None:
        return _err(f"unknown action: {action}; expected one of "
                    f"{', '.join(sorted(HANDLERS))}")
    args = dict(arguments)
    args.pop("action", None)
    return fn(args)
