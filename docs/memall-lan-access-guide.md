# MemALL 局域网（LAN）接入操作流程

> 适用版本：v0.1.81+（commit `fd097e3` 之后；本仓库 `cc94614` 起自带 LAN 参数与 systemd 单元）
> 文档基线：2026-11-08 在 `192.168.1.247` 实测验证，所有响应码/报错均为真实抓取
> 维护：dsh（架构）｜审核：待 workbuddy

---

## 0. 先弄清楚：局域网里"能接什么、不能接什么"

| 通道 | 默认绑定 | LAN 可达性 | 说明 |
|------|---------|-----------|------|
| **Gateway（HTTP REST + MCP）** | `127.0.0.1:9920` | ✅ 可按流程开放 | 唯一正式的网络入口，一个端口同时提供 REST API、`/mcp`（Streamable HTTP）、Web 仪表盘、联邦端点 |
| 实时总线 memall-bus | `127.0.0.1:8770`（硬编码） | ❌ 不支持 | 服务端 `memall_bus.py` HOST 是常量，不是配置项；跨机走 Phase 4 的 M2 桥接方案（尚未实施），**不要**试图直接暴露 8770 |
| stdio MCP server | 本地进程 | ❌ 不适用 | 每个客户端会话 spawn 一个，天然本机 |
| SQLite 文件（`~/.memall/data.db`） | 本地文件系统 | ❌ 严禁 | **绝不允许**把库文件放网络盘/SMB 共享多机同写——SQLite WAL 不支持跨机锁，会静默损坏 |
| 独立 MCP HTTP server（9876） | `127.0.0.1` 硬编码 | ❌ 不动它 | `mcp/http_transport.py` 写死 loopback。LAN 场景一律走 Gateway 的 `/mcp`，两者不要混 |

**Gateway 端口对照（实测确认）**：文档/日志里的 `9920` 是 `start-gateway` 默认；`8199/9919` 是历史入口，新部署统一用 **9920**。

---

## 1. 服务端操作（MemALL 所在主机，以本机 Windows 为例）

### 1.1 固定认证 token（必做）

Gateway 的 token 解析顺序（`fd097e3` 起）：**显式 `--secret-key` > 环境变量 `MEMALL_SECRET_KEY` > 每次随机**。随机 token 只在日志里打前 8 后 4，进程一死就找不回来——LAN 接入前必须先固定它，否则每次重启所有客户端都要重配。

```powershell
# 生成并持久化（当前用户级环境变量，重启后仍生效）
$tok = -join ((48..57)+(97..102) | Get-Random -Count 64 | ForEach-Object {[char]$_})
# 更推荐 .NET 强随机：
$tok = [Convert]::ToHexString((1..32 | ForEach-Object { Get-Random -Max 256 }))
[Environment]::SetEnvironmentVariable("MEMALL_SECRET_KEY", $tok, "User")
```

Linux/NAS 上等位文件是 `/etc/memall/gateway.env`（`root:memall 640`，见 §5）。

> ⚠️ 红线（Phase 4 报告 S-SEC-1）：绑定 `0.0.0.0` 之后，**这个 token 是局域网和你的记忆库之间唯一的屏障**。不要提交进 git、不要发群聊、不要放 world-readable 路径。轮换 = 改环境变量 + 重启网关 + 通知所有客户端。

### 1.2 启动 LAN 网关

```powershell
memall start-gateway --host 0.0.0.0 --port 9920
```

行为（实测）：
- 监听所有网卡；启动横幅会明确提示 `(bound to all interfaces)` 和 API 需 Bearer token；
- 未设 token 时额外警告 `using a per-run random token`——看到这个警告说明 §1.1 没做完；
- 处理 `SIGTERM/SIGINT` 优雅退出（`cc94614` 加入，服务管理器友好）。

前台进程不适合长期运行，Windows 建议注册开机任务（管理员权限）：

```powershell
schtasks /create /F /TN MemALLGateway /SC ONSTART /RU <你的用户> `
  /TR "cmd /c cd /d E:\MemALL && memall start-gateway --host 0.0.0.0 --port 9920 >> %USERPROFILE%\.memall\gateway.log 2>&1"
```

### 1.3 放行防火墙端口

```powershell
netsh advfirewall firewall add rule name="MemALL Gateway 9920" `
  dir=in action=allow protocol=TCP localport=9920 remoteip=192.168.1.0/24
```

`remoteip` 按你的网段收窄，**不要**留 0.0.0.0/0（那是把门全开）。

### 1.4 服务端自检（每一步都有实测基线）

```powershell
# ① 本机 loopback 健康检查（永远公开，无 token）
curl http://127.0.0.1:9920/health
#    → {"status": "ok", "uptime": ..., "memory_count": ...}

# ② 从 LAN IP 自访，无 token —— 必须 401
curl http://192.168.1.247:9920/memories
#    → 401 {"error": "unauthorized", "message": "valid Bearer token required"}

# ③ 带 token —— 必须 200
curl -H "Authorization: Bearer $env:MEMALL_SECRET_KEY" http://192.168.1.247:9920/memories
```

②③ 结果反了 = 鉴权配置有问题，**停止，不要继续发给客户端**。

---

## 2. 客户端接入操作

### 2.1 普通 REST API（脚本 / 服务集成）

所有非 loopback 请求必须带 `Authorization: Bearer <token>`（或 `?token=`，不推荐入历史）。

```python
import requests

BASE = "http://192.168.1.247:9920"
H = {"Authorization": "Bearer <MEMALL_SECRET_KEY>"}

# 检索
r = requests.post(f"{BASE}/retrieve", headers=H,
                  json={"query": "总线幂等规则", "limit": 5})
# 写入一条记忆
r = requests.post(f"{BASE}/memories", headers=H, json={
    "content": "……（≥50 字符，否则质量门告警）",
    "level": "L4", "category": "decision", "agent_name": "lan-client-01",
})
```

限流（网关中间件，按客户端 IP）：GET 100/分钟、POST 30/分钟、`/mcp` 60/分钟；超限返回 429 + `Retry-After`。破坏性操作（vacuum / import / bulk）从**任何**来源都要凭证，包括 loopback。

### 2.2 MCP 客户端（Agent 框架接入，推荐路径）

Gateway 的 `/mcp` 是 Streamable HTTP MCP 端点，initialize 握手即可拿到全工具表。通用配置形态：

```json
{
  "mcpServers": {
    "memall": {
      "type": "http",
      "url": "http://192.168.1.247:9920/mcp",
      "headers": { "Authorization": "Bearer <MEMALL_SECRET_KEY>" }
    }
  }
}
```

**身份声明（关键，否则收不到派工）**：任务路由、通知栏、会话注入都依赖 agent 身份。LAN 客户端必须**二选一**：
1. 在 `initialize` 的 `params.agent_name`（或 `clientInfo.agent_name`）里带上自己的名字；
2. 或在网关进程环境设 `MEMALL_AGENT_NAME`——注意这是**进程级**身份，一台网关只服务一个 agent 时用。

多 agent 共用一台远程网关时优先方式 1；每个 agent 的名字要与 `identities` 表登记一致（大小写不敏感，由 `canonical_agent` 归一）。客户端软件名（如 `dsh-mcp-client`）**不会**被当作身份——那是历史上 F3 事故的根源，网关会拒绝用它做路由。

实测握手响应：带 token 的 `initialize` → 200 + `serverInfo.name: "memall"`；无 token → 401。

### 2.3 Web 仪表盘从 LAN 浏览器访问 —— **不支持，如实说明**

实测（2026-11-08）：从 LAN 浏览器打开 `http://192.168.1.247:9920/` 会收到

```
403 {"error": "forbidden", "message": "cross-origin request blocked"}
```

原因：`gateway_utils._CORS_ALLOWED_ORIGINS` 只放行 `127.0.0.1/localhost` 的 9919/9920/8199 Origin（DNS-rebinding 防线，故意如此）。**结论与替代方案**：
- 仪表盘请在**网关主机本机**浏览器打开（`http://127.0.0.1:9920/`）；
- LAN 设备要用数据 → 用 §2.1 的 REST API 自建页面（API 对带 token 的非 loopback 客户端完全开放）；
- 确实需要 LAN 浏览器跑官方 SPA → 这是一个代码变更（把你的 LAN Origin 加进白名单），须走讨论 + 老陈批准，不属于"操作流程"能解决的范畴。**不要**用关掉 `open_api_loopback_only` 来"解决"它——那个开关管的是另一层（无 token 读权限），关掉等于对 LAN 匿名开放 SPA 读写。

---

## 3. 机器对机器：联邦配对（MemALL ↔ MemALL）

两台都跑 MemALL 的局域网设备之间做跨机检索，用内建配对协议，不是共享数据库。

**A. 被接入方（服务端）**：启动网关后，一次性配对码打印在日志/本机 UI：

```
[Gateway] pairing code: <8位hex> (one-time, rotate after pairing)
```

**B. 接入方（客户端机器）**：

```bash
# 1) 发现同网段广播的实例（UDP 9920 beacon）
memall gateway discover --timeout 5

# 2) 用对方的一次性配对码配对（此后对方 token 存进本机 peers.json）
memall gateway pair --address 192.168.1.247:9920 --code <8位hex>

# 3) 验证
memall gateway peers

# 4) 联邦检索
memall gateway federated --query "跨机问题" --max-peers 3
```

要点（代码实证）：配对失败关闭（缺码/错码 → 403）；配对成功**立即轮换**配对码防重放；联邦调用用 `Authorization: Bearer <peer_token>`。配对码本身就是"端口可达 ≠ 自动入网"的带外信任锚——不要把它当密码保管，它是一次性的。

---

## 4. 实时总线为什么不走 LAN（防踩坑）

同事的机器想让 `memall_msg` 派单到你这台？**做不到，且不该做**：总线 HOST 硬编码 `127.0.0.1`，鉴权模型（installation token 文件共享）明确"挡不住同 OS 用户进程"，与跨机信任完全不匹配。Phase 4 报告的既定路线是 **M2 机间 TLS 桥接进程**（本地总线保持不动，桥接只做游标复制），尚未实施。跨机协作现在的正确姿势 = §3 联邦 + 主库记忆交接（L5 任务卡本来就是唯一事实源，总线只是加速层）。

---

## 5. Linux / NAS 部署（对应 `contrib/`）

仓库自带单元文件，五步（`contrib/README.md` 原文可执行）：

```bash
useradd --system --home /var/lib/memall --shell /usr/sbin/nologin memall
pip install -e /path/to/MemALL            # 或 venv，注意 systemd 里的解释器路径
install -d -o memall -g memall -m 750 /etc/memall
echo "MEMALL_SECRET_KEY=$(python3 -c 'import secrets;print(secrets.token_hex(32))')" > /etc/memall/gateway.env
chmod 640 /etc/memall/gateway.env && chown root:memall /etc/memall/gateway.env
install -m 644 contrib/memall-gateway.service /etc/systemd/system/
systemctl daemon-reload && systemctl enable --now memall-gateway
```

LAN 暴露：把单元里 `ExecStart` 改成 `--host 0.0.0.0`，`daemon-reload + restart`。单元自带 `Restart=on-failure`、`NoNewPrivileges`、`ProtectSystem=strict` 等加固。老 CPU（SSE2-only）NAS 注意 numpy 已钉在 <2.0（`fd097e3`，防 OpenBLAS SIGILL）。

---

## 6. 接入验收清单（客户端侧，逐条打勾）

- [ ] `GET /health` 无 token → 200（证明网络可达、防火墙已放行）
- [ ] `GET /memories` 无 token → **401**（证明鉴权生效；返回 200 是事故，立刻下线排查）
- [ ] `GET /memories` 带 Bearer → 200
- [ ] MCP `initialize` 带 Bearer + `params.agent_name` → 200，且随后 `memall_system action=session_start` 能返回自己名下的 `pending_tasks`
- [ ] 用错误 token → 401；连续 >60 次 POST/分钟 → 429（限流在工作）
- [ ] （若跨机记忆协作）`memall gateway federated` 返回对端结果

## 7. 安全运维备忘

1. token 每季度轮换（§1.1 重生成 → 重启网关 → 更新全部客户端）；
2. `remoteip` 网段收紧 + 只开 9920 一个端口；8770/8771、9876、数据库文件一律不出主机；
3. 网关日志留意 `Pairing rejected`（有人在试配对码）与异常 429 洪水；
4. 破坏性 API（vacuum/import/scan）从设计上就要凭证（loopback 也不例外），但建议 LAN 场景下再叠一层：客户端 token 与运维 token 分离需要改代码（当前一个 token 全权限），列入 Phase 4 后讨论项；
5. 本文件描述的绑定/鉴权行为在 `gateway.py / gateway_utils.py / gateway_peers.py`，改动这些路径的 PR 必须重跑 §1.4/§6。

---

### 附：本文实测数据来源
- 活体探测：`_lan_probe.py` / `_lan_probe2.py`（2026-11-08 19:xx，本机 `0.0.0.0:19921/19922`，LAN 192.168.1.247，六组响应码全记录于 §1.4/§2.3）
- 代码基线：`fd097e3`（token 三级解析、--host/--secret-key）、`cc94614`（systemd 单元、SIGTERM）、`gateway.py` 鉴权中间件六层顺序、`gateway_utils.py` Origin 白名单
