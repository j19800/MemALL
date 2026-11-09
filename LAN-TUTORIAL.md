# MemALL 局域网接入教程（照着做就能通）

> 适用：局域网内另一台机器想连上 NAS 上的 MemALL。
> 服务端现状（2026-11-09 实测）：一个进程、一个端口 `9920`，同时提供 MCP、实时消息总线、运维数据表，共用同一个 Bearer token。
> 本文是**入门教程**；更全的参数/坑位说明见 `LAN-CLIENT-GUIDE.md`。

---

## 第 0 步：先拿到三样东西

在**客户端机器**上开工前，先确认：

| 项 | 值 / 怎么拿 |
|---|---|
| 服务器地址 | `http://192.168.1.140:9920`（本机可用 `http://127.0.0.1:9920`） |
| Token | 在 NAS 上执行 `cat /vol1/@appshare/fn-deepseek-harness/MemALL/.gateway-token`，得到一串 42 字符的密钥 |
| 客户端工具 | `curl`（必装）；MCP 客户端可选（Claude Desktop / Cursor / Cline / DSH 等） |

> ⚠️ Token 是活的凭证，**不要写进代码库、不要贴到聊天里**。下面的示例统一用 `$TOKEN` 变量表示。

在客户端 shell 里设好变量（把 `<你的TOKEN>` 换掉）：
```bash
BASE=http://192.168.1.140:9920
TOKEN='<你的TOKEN>'
```

---

## 第 1 步：确认能连上（30 秒）

```bash
curl -sS "$BASE/health"
```

**预期**：
```json
{"status": "ok", "uptime": 41938.2, "memory_count": 1}
```

- 连不上 → 检查 NAS 防火墙是否放行 `9920`、IP 是否是 `192.168.1.140`、`ping` 通不通。
- 这个端点**不需要 token**，故意公开用来做存活探测。

---

## 第 2 步：确认 token 有效

```bash
# 不带 token（预期 401）
curl -sS -o /dev/null -w '%{http_code}\n' "$BASE/bus/health"          # → 401

# 带 token（预期 200）
curl -sS -H "Authorization: Bearer $TOKEN" "$BASE/bus/health"        # → {"available":true,...}
```

> 除 `/health` 外，**所有 `/mcp`、`/bus/*`、`/ops/facts/*` 都必须带 token**，包括从 127.0.0.1 本机访问，没有 localhost 豁免。

---

## 第 3 步：接入 MCP（最常用，能力最全）

### 3.1 最关键的一条：必须声明 agent name

MemALL 的 MCP 握手**强制**要求声明身份，不声明直接拒绝：

```bash
# 不声明 → 报错（这是设计，不是故障）
curl -sS -X POST "$BASE/mcp" \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}'
# → error.code = -32001 "agent name is required ..."

# 声明后 → 成功
curl -sS -X POST "$BASE/mcp" \
  -H "Authorization: Bearer $TOKEN" \
  -H 'X-MemAll-Agent: my-agent' \
  -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26"}}'
```

成功时 `serverInfo` 会回显：
```json
{"actor_id":"my-agent","agent_name":"my-agent","agent_source":"X-MemAll-Agent", ...}
```

三种声明方式，任选其一（优先级从高到低）：
| 方式 | 适用 |
|---|---|
| `initialize.params.agent_name` | 能自定义 params 的原生 MCP 客户端 |
| `initialize.params.clientInfo.agent_name` | 同上（嵌套形式） |
| HTTP header `X-MemAll-Agent: <name>` | **DSH 的 dsh-mcp-client 用这个**（它无法改 initialize params，但 headers 会附加到每个请求） |

> 名字要合法：2–64 字符、小写字母数字 `_-.`、非保留词。首次握手会**自动注册**进 `identities` 表，无需手动建号。

### 3.2 客户端配置模板

**通用 `mcp.json`**（带身份 header）：
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

**DeepSeek Harness**（`profiles/web/cordis.patch.yml`）：
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
改完**要重启 DSH** 才生效。

### 3.3 验证工具可用

```bash
curl -sS -X POST "$BASE/mcp" \
  -H "Authorization: Bearer $TOKEN" -H 'X-MemAll-Agent: my-agent' \
  -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/list"}'
```
预期列出 8 个工具：`memall_write` / `memall_read` / `memall_persona` / `memall_discussion` / `memall_federation` / `memall_system` / `memall_hooks_recent` / `memall_msg`。

> 读操作（`memall_read`）记得在参数里带 `agent_name` 或 `viewer`，否则信任过滤会拒绝。

---

## 第 4 步：用实时消息总线（REST + SSE）

总线已**合并进网关**，不需要另起 8770 进程。消息**只带 `memory_id` 指针**，正文用 `memall_read` 按 id 取。

### 4.1 发一条消息
```bash
curl -sS -X POST "$BASE/bus/publish" \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{
        "from": "pm",
        "to": "worker",
        "type": "task",
        "payload": {"memory_id": 123},
        "dedup_key": "task-123-v1"
      }'
```
预期：`{"op":"pub_ok","id":5,"deduplicated":false,"pushed":0}`
- `pushed=1` = 对方在线且通过 SSE 立即收到；`pushed=0` = 离线，进了 mailbox。
- `dedup_key` 相同会幂等（返回 `deduplicated:true`）。

### 4.2 收信 / 标记已读
```bash
# 拉未读
curl -sS -H "Authorization: Bearer $TOKEN" "$BASE/bus/inbox?agent=worker"

# 处理完必须 ack，否则下次还返回
curl -sS -X POST "$BASE/bus/ack" \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"agent":"worker","ids":[5]}'
```

### 4.3 实时订阅（SSE）
```bash
curl -N -H "Authorization: Bearer $TOKEN" "$BASE/bus/subscribe?agent=worker"
```
会先收到 `welcome`，有新消息时收到 `op: msg`，空闲 25 秒发一个心跳。

浏览器里 `EventSource` 不能设 header，改用查询参数传 token：
```js
const es = new EventSource(
  `http://192.168.1.140:9920/bus/subscribe?agent=worker&token=${TOKEN}`
);
es.onmessage = (e) => {
  const m = JSON.parse(e.data);
  if (m.op === 'msg') console.log('新任务卡 id =', m.payload.memory_id);
};
```

---

## 第 5 步：读写运维数据表（可选）

`ops_facts` 是与记忆库**物理隔离**的运维表（不进 FTS、不被 pipeline 处理）。

```bash
# 列出全部
curl -sS -H "Authorization: Bearer $TOKEN" "$BASE/ops/facts"

# 分类
curl -sS -H "Authorization: Bearer $TOKEN" "$BASE/ops/facts/categories"

# 取单条 / 含变更历史
curl -sS -H "Authorization: Bearer $TOKEN" \
  "$BASE/ops/facts/deployment/gateway.url?history=true"

# 写一条（重复写相同内容幂等；内容变化会留历史）
curl -sS -X PUT "$BASE/ops/facts" \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"category":"deployment","key":"my.key","value":"v1",
       "detail":{"note":"from lan"},"source":"my-client"}'
```

---

## 第 6 步：自检清单（在客户端机器上跑一遍）

```bash
BASE=http://192.168.1.140:9920
TOKEN='<你的TOKEN>'

curl -sS "$BASE/health"                                              # → 200 {"status":"ok",...}
curl -sS -o /dev/null -w '%{http_code}\n' "$BASE/bus/health"         # → 401（无 token）
curl -sS -H "Authorization: Bearer $TOKEN" "$BASE/bus/health"       # → 200
curl -sS -X POST "$BASE/mcp" -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}'    # → -32001（无声明，符合预期）
curl -sS -X POST "$BASE/mcp" -H "Authorization: Bearer $TOKEN" \
  -H 'X-MemAll-Agent: my-agent' -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":2,"method":"initialize","params":{}}'    # → agent_name = my-agent
```

全对 = 接入成功 ✅

---

## 常见问题速查

| 现象 | 原因 / 对策 |
|---|---|
| `401 unauthorized` | 漏了 `Authorization: Bearer`；或 token 抄错（注意别带空格/换行） |
| MCP 返回 `-32001` | 没声明 agent name → 加 `X-MemAll-Agent` header（或 params） |
| 带 `Origin` 头被 `403` | 网关只放行白名单里的 loopback Origin；**curl / Python / 原生客户端不带 `Origin` 即可**。浏览器前端直连需走同源反代或把 Origin 加进白名单 |
| `429 rate limit exceeded` | `POST /mcp` 与 `POST /bus/*` 共享 **60 次/分钟/IP**，GET 100 次/分钟；做客户端节流 |
| `memall_read` 返回空/被拒 | 读操作要带 `agent_name` 或 `viewer`；且该身份要与记忆的 visibility 匹配 |
| SSE 连上但没消息 | 检查 `?agent=` 名字和 `publish` 的 `to` 是否一致；离线消息用 `/bus/inbox` 拉 |
| 连不上 9920 | NAS 防火墙放行 9920；确认网关进程活着（服务端 `curl 127.0.0.1:9920/health`） |
| 重启后客户端全掉线 | token 是固定文件 `.gateway-token`，正常不会变；若变了说明服务端换了 token 文件 |

---

## 数据在谁那边？（重要）

**集中式**：所有记忆（`~/.memall/data.db`）、消息 mailbox（`~/.memall/bus.db`）、运维表都存在 **NAS 上**，客户端本地**不存任何库**。
- 所有客户端看到的是**同一份数据**，A 写入 B 立刻能读到。
- 断网/网关挂了 = 全体不可用（网关有保活脚本，崩溃 5 秒自动重启）。
- 想让另一台机器也有自己的库 → 那得**另跑一个独立的 MemALL 实例**（不同端口、不同 `~/.memall/`），两者互不自动同步，只能走 federation / export-import。

---

## 服务端速查（在 NAS 上）

```bash
M=/vol1/@appshare/fn-deepseek-harness/MemALL
cat $M/.gateway-token                 # token
tail -f $M/gateway.log                # 日志
ps -ef | grep gateway_keepalive       # 保活进程（崩溃 5s 自动重启）
curl 127.0.0.1:9920/health            # 本机自检
```

---

*教程版本：2026-11-09 · 服务端网关 0.0.0.0:9920（MCP + 总线 + 运维表合并）· 全量参数见 `LAN-CLIENT-GUIDE.md`*
