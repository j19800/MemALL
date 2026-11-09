# MemALL 局域网客户端接入指南

> 网关与实时消息总线已合并：**一个进程、一个端口、一套鉴权**。
> MCP 与消息总线共用同一地址，和 DSH 里已配置的 MCP 完全一致。

---

## 1. 连接信息

| 项 | 值 |
|---|---|
| Base URL | `http://192.168.1.140:9920` （本机可用 `http://127.0.0.1:9920`） |
| MCP 端点 | `POST http://192.168.1.140:9920/mcp` |
| 总线端点 | `http://192.168.1.140:9920/bus/*` |
| 运维数据端点 | `http://192.168.1.140:9920/ops/facts/*` |
| 鉴权 | `Authorization: Bearer <TOKEN>` |
| TOKEN 位置 | 服务器文件 `/vol1/@appshare/fn-deepseek-harness/MemALL/.gateway-token` |
| 协议 | HTTP（非 HTTPS），端口 `9920` |
| 监听 | `0.0.0.0:9920`（NAS 防火墙需放行 9920；已实测局域网可达） |

**不要在文档/代码库里硬编码真实 TOKEN**，部署时从上述文件或环境变量注入。

获取方式（在 NAS 上）：
```bash
cat /vol1/@appshare/fn-deepseek-harness/MemALL/.gateway-token
```

---

## 2. 方式 A：作为 MCP 客户端接入（推荐，能力最全）

所有 MCP 工具（`memall_write` / `memall_read` / `memall_msg` / `memall_system` / …）
都通过 `/mcp` 暴露。任何支持 **Streamable HTTP** 的 MCP 客户端都能直连。

> **⚠️ 强制要求：MCP 握手必须声明 agent name。**
> 服务端在 `initialize` 时解析身份，解析不到（或名字非法）会直接返回
> JSON-RPC error `-32001` 拒绝连接，不会再静默变成 `actor_id: unknown`。
> 解析到的身份会自动注册进 `identities` 表并回显在 `serverInfo.agent_name`，
> 同时记入 `ops_facts`（`mcp/handshake.*`）作为审计。

**注册 / 声明步骤**

1. 选定一个合法名字：2–64 字符、小写字母数字 `_-.`、非保留词、不含长数字串
   （如 `dsh-assistant`、`my-agent`）。服务端用 `validate_agent_name()` 校验。
2. 通过以下任一渠道声明（优先级从高到低）：

| 渠道 | 适用客户端 |
|---|---|
| `initialize.params.agent_name` | 能自定义 params 的原生 MCP 客户端 |
| `initialize.params.clientInfo.agent_name` | 同上（嵌套形式） |
| HTTP header `X-MemAll-Agent: <name>` | **dsh-mcp-client**（其 `clientInfo` 硬编码，但 `headers` 会附加到每个请求，含 initialize） |

> **没有服务端兜底**：不再支持 `MEMALL_AGENT_NAME` 环境变量代客户端声明。
> 每个客户端必须自己声明，否则握手被拒。

3. 无需手动建身份：首次握手时服务端自动 `INSERT` 进 `identities`
   （`agent_type=ai`），幂等；重复握手只更新 `last_heartbeat`。
4. 验证：`initialize` 响应的 `serverInfo.agent_name` 应等于你声明的名字。

**被拒绝时的报错示例**
```json
{"jsonrpc":"2.0","id":1,"error":{"code":-32001,
 "message":"agent name is required at MCP initialize: set params.agent_name / clientInfo.agent_name, or send the X-MemAll-Agent header. Reason: missing."}}
```

### 通用 `mcp.json` 配置

```json
{
  "mcpServers": {
    "memall": {
      "url": "http://192.168.1.140:9920/mcp",
      "transport": "streamable-http",
      "headers": {
        "Authorization": "Bearer <TOKEN>",
        "X-MemAll-Agent": "my-agent"
      }
    }
  }
}
```

### Claude Desktop / Cursor / Cline / Windsurf

```json
{
  "mcpServers": {
    "memall": {
      "type": "http",
      "url": "http://192.168.1.140:9920/mcp",
      "headers": { "Authorization": "Bearer <TOKEN>", "X-MemAll-Agent": "my-agent" }
    }
  }
}
```

### DeepSeek Harness（本会话已在用）

`profiles/web/cordis.patch.yml`：

```yaml
- insert:
    - id: mcp-memall
      name: '@deepseek-ai/dsh-mcp-client'
      config:
        serverName: memall
        transport: streamable-http
        url: http://192.168.1.140:9920/mcp
        headers:
          Authorization: 'Bearer <TOKEN>'
          X-MemAll-Agent: 'dsh-assistant'
        failOnStartupError: false
        reconnect:
          enabled: true
          maxAttempts: 10
```

### 身份隔离（重要）

`initialize` 时把 agent 名字放进 `params.agent_name`（或
`clientInfo.agent_name`），否则所有客户端会被归到同一个身份桶，
`memall_read` 的信任过滤会拒绝返回或串数据：

```json
{
  "jsonrpc": "2.0", "id": 1, "method": "initialize",
  "params": {
    "protocolVersion": "2025-03-26",
    "capabilities": {},
    "clientInfo": { "name": "my-client", "version": "1.0" },
    "agent_name": "my-agent"
  }
}
```

若客户端无法自定义 `initialize.params`（如 DSH 的 `dsh-mcp-client`），
改用 HTTP header `X-MemAll-Agent: my-agent`（`cordis.patch.yml` 中已设为
`dsh-assistant`，DSH 重启后生效）。**没有服务端兜底**，未声明即拒绝。
读操作（`retrieve` / `search` / `unified_search`）仍需传 `agent_name` 或
`viewer`，声明的握手身份会被自动用作回退。

### 裸 MCP 调用示例（curl）

```bash
TOKEN=$(cat /vol1/@appshare/fn-deepseek-harness/MemALL/.gateway-token)
curl -sS -X POST http://192.168.1.140:9920/mcp \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
```

---

## 3. 方式 B：只用实时消息总线（REST + SSE）

不需要 MCP 协议时，直接调 `/bus/*`。**所有 `/bus/*` 都要 Bearer token
（包括 127.0.0.1 本机）**。

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/bus/health` | 健康探测 + 统计 |
| GET | `/bus/ping` | `{"op":"pong","proto":2}` |
| POST | `/bus/publish` | 发消息（只带 `memory_id` 指针） |
| GET | `/bus/inbox?agent=<name>` | 拉未读 |
| POST | `/bus/ack` | 标记已读 |
| GET | `/bus/subscribe?agent=<name>` | SSE 实时推送 |

### publish

```bash
curl -sS -X POST http://192.168.1.140:9920/bus/publish \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{
        "from": "pm",
        "to": "worker",
        "type": "task",
        "payload": {"memory_id": 123},
        "dedup_key": "optional-idempotency-key"
      }'
```

响应：
```json
{"op":"pub_ok","id":6,"deduplicated":false,"pushed":0}
```
- `pushed=1` = 收件人在线且通过 SSE 立即收到；`pushed=0` = 离线，消息进 mailbox。
- `dedup_key` 相同会幂等去重（返回 `deduplicated:true`）。
- **`payload` 只放 `memory_id` 指针**，任务正文用 `memall_read` 按 id 取 ——
  L5 任务卡（`memories` 表）是唯一事实源。

### inbox / ack

```bash
curl -sS "http://192.168.1.140:9920/bus/inbox?agent=worker" \
  -H "Authorization: Bearer $TOKEN"

curl -sS -X POST http://192.168.1.140:9920/bus/ack \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"agent":"worker","ids":[6]}'
```

`inbox` **不会**自动标记已读，处理完必须 `ack`。

### SSE 实时订阅

```bash
curl -N "http://192.168.1.140:9920/bus/subscribe?agent=worker" \
  -H "Authorization: Bearer $TOKEN"
```

事件流：
```
data: {"op": "welcome", "agent": "worker", "proto": 2}

data: {"op": "msg", "id": 7, "from": "pm", "to": "worker",
       "type": "task", "payload": {"memory_id": 123}, "ts": ...}

: heartbeat          ← 25 秒空闲时
```

浏览器 `EventSource` 无法设置 Authorization 头，**改用查询参数传 token**：

```js
const es = new EventSource(
  `http://192.168.1.140:9920/bus/subscribe?agent=worker&token=${TOKEN}`
);
es.onmessage = (e) => {
  const msg = JSON.parse(e.data);
  if (msg.op === 'msg') console.log('新任务卡 id =', msg.payload.memory_id);
};
```

> 已实测 `?token=` 可用；但请见下方「跨源(Origin)限制」。

---

## 4. 方式 C：Python / Node 客户端

### Python（标准库，无依赖）

```python
import json, urllib.request, urllib.parse

BASE = "http://192.168.1.140:9920"
TOKEN = "<TOKEN>"

def call(path, payload=None):
    data = json.dumps(payload).encode() if payload is not None else None
    headers = {"Authorization": f"Bearer {TOKEN}", "Accept": "application/json"}
    if data:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(
        BASE + path, data=data, headers=headers,
        method="POST" if data else "GET")
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())

print(call("/bus/health"))
print(call("/bus/publish", {
    "from": "py-client", "to": "js-client", "type": "task",
    "payload": {"memory_id": 1001}, "dedup_key": "demo-1"}))
print(call("/bus/inbox?agent=" + urllib.parse.quote("js-client")))
```

### 直接用现成的客户端类（MemALL 源码内）

```python
from memall.mcp.bus import BusClient, bus_health

# 默认读 http://127.0.0.1:9920 + .gateway-token
# 可用环境变量覆盖：
#   MEMALL_BUS_URL=http://192.168.1.140:9920
#   MEMALL_GATEWAY_TOKEN=<TOKEN>
with BusClient() as bus:
    bus.connect("my-agent")
    bus.publish("worker", {"memory_id": 42}, "task", dedup_key="k1")
    print(bus.inbox())
    bus.ack([1])
```

### Node / TypeScript（fetch）

```ts
const BASE = 'http://192.168.1.140:9920';
const TOKEN = '<TOKEN>';
const headers = {
  Authorization: `Bearer ${TOKEN}`,
  'Content-Type': 'application/json',
};

await fetch(`${BASE}/bus/publish`, {
  method: 'POST', headers,
  body: JSON.stringify({
    from: 'node-client', to: 'worker', type: 'task',
    payload: { memory_id: 42 }, dedup_key: 'k1',
  }),
});

const inbox = await (
  await fetch(`${BASE}/bus/inbox?agent=worker`, { headers })
).json();
```

---

## 4b. 方式 D：运维数据表（ops_facts）

运维/部署/变更记录存在主库里一张**独立表** `ops_facts`（迁移 043），
与 `memories` 物理隔离：不进 FTS、不被 pipeline 分类/蒸馏、不受信任过滤影响。
同样走 9920 + 同一个 Bearer token。

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/ops/facts` | 列出 active 事实（`?category=`、`?history=true`） |
| GET | `/ops/facts/categories` | 分类与数量 |
| GET | `/ops/facts/{category}/{key}` | 取单条（`?history=true` 含变更历史） |
| PUT | `/ops/facts` | 新增/更新（按 `(category,key)` 幂等，变更留历史） |
| DELETE | `/ops/facts` | 软删（标记 superseded，保留历史） |

```bash
# 写入（重复写相同内容返回 unchanged；内容变化则旧行 superseded）
curl -sS -X PUT http://192.168.1.140:9920/ops/facts \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"category":"deployment","key":"my.key","value":"v1",
       "detail":{"note":"example"},"source":"my-client"}'

# 读取
curl -sS -H "Authorization: Bearer $TOKEN" \
  "http://192.168.1.140:9920/ops/facts/deployment/my.key?history=true"
```

字段：`category` / `key` / `value` / `detail`（任意 JSON）/ `source` /
`valid_from` / `superseded_at` / `created_at`。**别把真实 token 值写进去**，
只记录其位置（当前库里 `deployment/gateway.auth` 就是这么做的）。

Python 侧可直接用：`from memall import ops_facts`（`put/get/list_facts/delete/categories`）。

---

## 5. 必须知道的坑

| 坑 | 说明 / 对策 |
|---|---|
| **跨源 Origin 会被 403** | 网关拒绝非白名单 `Origin`（`/mcp` 与 `/bus/*` 策略一致）。**原生客户端 / curl / Python 不带 `Origin` 头即可**。浏览器前端直连会被挡：需走同源反向代理，或把你的 Origin 加进 `gateway_utils._CORS_ALLOWED_ORIGINS` 后重启网关。 |
| **所有 `/bus/*` 都要 token** | 没有 localhost 豁免，本机调用也必须带 Bearer。 |
| **速率限制** | `POST /mcp` 与 `POST /bus/*` 共享 **60 次/分钟/IP**；GET 100 次/分钟。超限返回 `429` + `Retry-After: 60`。高频场景做客户端节流/批量。 |
| **载荷只带指针** | 消息体不放任务正文，只放 `memory_id`；正文用 `memall_read` 取。 |
| **ack 不自动** | `inbox` 读完必须 `ack`，否则每次都会重复返回。 |
| **agent 名字** | `from` / `to` / `?agent=` 用一致的命名；MCP 侧用 `agent_name` 建立身份。 |
| **单实例共享** | 所有客户端连的是同一个网关、同一个 `~/.memall/bus.db`；消息不会跨机器同步到另一个 MemALL 实例（如需多机，走 federation/export-import）。 |
| **HTTP 明文** | 仅限可信局域网。跨公网请套 TLS 反向代理 + 防火墙白名单。 |
| **MCP 握手必须声明 agent name** | 未声明即返回 `-32001` 拒绝，无服务端兜底；见第 2 节「注册 / 声明步骤」。 |
| **非 loopback 读操作** | `memall_read` 的信任过滤需要 agent 身份；不带 `agent_name`/`viewer` 会直接拒绝。 |

---

## 6. 连通性自检（在客户端机器上跑）

```bash
BASE=http://192.168.1.140:9920
TOKEN='<TOKEN>'

# 1) 网关活着
curl -sS "$BASE/health"
# 2) 总线健康（应为 available:true, transport:gateway-http）
curl -sS -H "Authorization: Bearer $TOKEN" "$BASE/bus/health"
# 3) MCP 握手
curl -sS -X POST "$BASE/mcp" \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"agent_name":"probe"}}'
# 4) 不带 token 应返回 401
curl -sS -o /dev/null -w '%{http_code}\n' "$BASE/bus/health"
# 5) 不带 agent name 应被拒（JSON-RPC error -32001）
curl -sS -X POST "$BASE/mcp" \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":2,"method":"initialize","params":{}}'
```

预期：`health` → `{"status":"ok",...}`；`bus/health` → `available:true`；
MCP → `serverInfo.agent_name` = 你声明的名字；第 4 条 → `401`；
第 5 条 → `error.code = -32001`（Reason: missing）。

---

## 7. 服务端运维速查

```bash
M=/vol1/@appshare/fn-deepseek-harness/MemALL
cat $M/.gateway-token                      # token
tail -f $M/gateway.log                     # 日志
ps -ef | grep gateway_keepalive            # 保活进程（崩溃 5 秒自动重启）
# 手动重启：kill 掉 MemAllGateway 进程，keepalive 会自动用新代码拉起
```

改动源码后必须重启网关进程（保活脚本会自动拉起，约 8 秒恢复）。
