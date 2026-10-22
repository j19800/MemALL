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

总线是**独立常驻进程**，没起时这两个动作返回清晰报错并附上排查提示，而
MemALL 主链路（auto_inject 拉模式）完全不受影响。
"""
import json

from memall.mcp.bus import BusClient, BusUnavailable


def _agent_of(arguments: dict) -> str:
    return (arguments.get("agent_name") or "").strip()


def _err(msg: str, **extra) -> str:
    out = {"status": "error", "error": msg}
    out.update(extra)
    return json.dumps(out, ensure_ascii=False)


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
    try:
        with BusClient() as bus:
            bus.connect(agent)
            r = bus.publish(to, payload, mtype)
    except BusUnavailable as exc:
        return _err(f"bus unavailable: {exc}", delivered=False, to=to)
    return json.dumps({
        "status": "ok",
        "to": to,
        "message_id": r.get("id"),
        "pushed": r.get("pushed", 0),
        "note": ("pushed=0 表示收件人当前不在线；消息已进 mailbox，"
                 "对方下次上线用 inbox 动作拉取"),
    }, ensure_ascii=False)


def handle_inbox(arguments: dict) -> str:
    agent = _agent_of(arguments)
    if not agent:
        return _err("agent_name is required")
    try:
        with BusClient() as bus:
            bus.connect(agent)
            msgs = bus.inbox()
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
    try:
        with BusClient() as bus:
            bus.connect(agent)
            r = bus.ack(ids)
    except BusUnavailable as exc:
        return _err(f"bus unavailable: {exc}", marked=0)
    return json.dumps({
        "status": "ok",
        "marked": r.get("marked", 0),
        "ids": r.get("ids", []),
    }, ensure_ascii=False)


def handle_status(arguments: dict) -> str:
    from memall.mcp.bus import bus_health
    h = bus_health()
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
