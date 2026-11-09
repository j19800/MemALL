# MemALL 部署与代码工作日报告 — 2026-11-08

> 执行者：DSH Agent（Team Lead，hy4free）
> 环境：Linux NAS `192.168.1.140`，MemALL 仓库 `/vol1/@appshare/fn-deepseek-harness/MemALL`
> 基线：`4cfcfcd`（v0.1.80），远端 `origin/master` 已超前至 `1705a63`
> 报告时间：2026-11-08 23:36 CST

---

## 0. 一句话总结

从「memall 是否正常」的体检出发，把**实时消息总线合并进 MCP 网关**、建立**独立运维数据表**、**修复一批既有代码债**、**恢复自动降级路由**、**落实 MCP 强制身份声明**，并完成了两份外部报告（技术债务巡检、部署一致性）的**实证核对与更正**。全程未提交、未推送任何 commit。

---

## 1. 起点：健康检查（发现 3 个降级项）

| 检查 | 结果 |
|---|---|
| 主库读写/检索/pipeline | ✅ 正常（schema v48） |
| 实时消息总线 | ❌ `degraded` — 独立 TCP 进程 `127.0.0.1:8770` 不存在，`memall_bus.py` 本机根本没有 |
| gateway status | ❌ `stopped`（进程内 gateway）；但 9920 HTTP 网关其实**已在跑局域网** |
| 读操作 | ⚠️ 不带 `agent_name`/`viewer` 被信任过滤拒绝（设计如此） |

关键判断：DSH 的 MCP 客户端已通过 `streamable-http` 连 `http://192.168.1.140:9920/mcp`（带 Bearer token），**MCP 本身就在局域网上**；坏的只是另一套独立总线。

---

## 2. 核心改造：网关与实时总线合并

**要求**：和 MCP 一样、单端口、跑局域网。

| 新增/改动 | 作用 |
|---|---|
| `src/memall/gateway_bus.py`（新） | 网关内置 mailbox：sqlite `~/.memall/bus.db`、dedup_key + UNIQUE 索引、SSE 实时推送 |
| `src/memall/gateway.py`、`gateway_routes.py` | 挂载 `BusHandlersMixin` + `/bus/*` 六个路由，强制 Bearer 鉴权，60/min 限流对齐 `/mcp` |
| `src/memall/mcp/bus.py`（重写，178+/280-） | `BusClient` 从 TCP socket 改为网关 HTTP；`ensure_bus_running` 变兼容 shim |
| `src/memall/mcp/tools/msg.py` | 文档更新，工具接口不变 |
| `gateway_keepalive.sh` | 保活脚本，崩溃 5s 自愈 |

结果：**一个进程、一个端口 `0.0.0.0:9920`**，同时提供 `/mcp` 与 `/bus/*`，共用同一 token。8770 废弃。

实测：鉴权 401、dedup 幂等、inbox/ack 闭环、SSE `pushed=1`、MCP 工具层全通。

---

## 3. 运维数据隔离：`ops_facts` 表

- 迁移 `043_create_ops_facts.py`（标准迁移机制，自动备份 + 记入 `schema_version`，幂等）
- `src/memall/ops_facts.py`：CRUD + 变更留痕（superseded 历史）
- `gateway_ops_facts.py`：`/ops/facts` REST，局域网可读写
- 与 `memories` 物理隔离：不进 FTS、不被 pipeline 分类/蒸馏、不受信任过滤

用途：把架构事实、巡检处置、修复记录等 21 条运维数据独立存放，不污染记忆库。

---

## 4. 数据卫生与代码债修复

### 4.1 记忆库清理
- 原库 10 条记忆中 **9 条是噪音**（6 条 pipeline 自生成观察、2 条派生蒸馏、1 条测试探针）
- 删除 9 条 + 10 条边，FTS 同步；保留并**改写 #1** 为当前架构事实
- 止血：`run_pipeline` 新增 `include_observation=False`（默认），修 `observe.py` growth_log 的 `content_hash` UNIQUE 冲突
- 验证：`status: ok`、`discipline.healthy: true`、耗时 31s → 5.1s

### 4.2 session 接线（真实代码债）
- `improve_step`/`identity_step` 原本错挂在死路径 `session_end`（sessions 全 active，从不结束）
- 移到 `session_start` 的 `_run_session_start_maintenance()`，用 `session.auto_improve`/`auto_identity` 开关（默认 False）
- 实测：默认零副作用，开启后两步均执行

### 4.3 scheduler 保活（P0-1）
- 本环境**从未有 scheduler 进程**；两个保活脚本都是孤儿（PPID=1），无开机自启
- 修 `scheduler.py` 的 Linux 兼容 bug：`_validate_our_pid` 用 `/proc`、`daemon_stop` 用 POSIX 信号（原为 Windows `tasklist`/`taskkill`）
- 新建 `scheduler_keepalive.sh`（崩溃 7s 自愈实测通过）
- `crontab @reboot` 同时纳入 scheduler + gateway 保活

### 4.4 4 个既有 pytest 失败（全绿）
| 根因 | 修复 |
|---|---|
| `ops.py` 用了未定义的 `logger` + 重复 import | 补 `logger = logging.getLogger(__name__)` |
| `test_backup` patch 目标过时 | 改为 `memall.core.db.DB_PATH` |
| `test_conflict` 断言与 `auto_resolve(apply=False)` 设计冲突 | 改测 dry-run + `apply=True` 两段 |
| `test_level_and_owner_invariants` | 由 ops.py 修复一并解决 |

**单跑 4 passed；全量 `524 passed / 2 skipped / 0 failed`**。

### 4.5 `DEEP_AUDIT_REPORT.md` §8 残留修复（14 文件）
- CORS：`gateway_mcp_handlers.py`(9919) 与 `http_transport.py`(9876) 硬编码 → 统一 `_cors_headers(request)`；实测无 Origin 不回显 / 白名单 9920 回显 / 外部 403
- 端口口径：`config.py` 9919→9920，7 处运行时默认值 + 3 处文档统一；**刻意保留**独立 MCP-HTTP 的 9876（与网关不同端口，已加注释）
- 2 处 subprocess 补 timeout（`tasklist`/`taskkill`）
- `AGENTS.md` 过期的 P0-4b 看板与读路径警告更正
- 验证：`compileall` OK、相关测试 **85 passed**、SSE CORS 独立端口实测通过

---

## 5. MCP 强制身份声明

- `src/memall/mcp_identity.py`（新）：`initialize` 必须解析出合法 agent name，来源 `params.agent_name` / `clientInfo.agent_name` / `X-MemAll-Agent` header；解析不到返回 `-32001` 拒绝
- 解析到的身份自动注册进 `identities` + 记入 `ops_facts`，回显 `serverInfo.agent_name`
- `http_transport.py` 与 `gateway_mcp_handlers.py` 两个入口都接入
- `cordis.patch.yml` 加 `X-MemAll-Agent: 'dsh-assistant'`（DSH 重启后加载）
- 正式身份 `dsh-assistant` 注册；清理 `dsh-healthcheck`/`lan-probe` 测试身份

**最终状态**（DSH 重启 + 网关 keepalive 重启后）：无声明 `-32001`、带 header `source=X-MemAll-Agent`、env 兜底彻底移除、本会话 MCP 工具正常。

---

## 6. our-free-model 自动降级路由（另建 Kilo 链路）

**排查链**（多次更正，如实记录）：
1. 初判「combo 消失」→ 错。实际 `combo:recommended-free` 一直在 `/v1/models`（40 个模型里第 40 个）
2. 真正根因：链路 7 个模型**全部拨不通**。批量实测 38 个模型：13 个可用（Kilo + agnes 文本），OpenCode 免费车道全挂，EAC/seal 需授权
3. OpenCode 免费车道要本地 Bun → 本机 CPU 是 **AMD A4-3300M（2011，无 AVX2）**，官方与 baseline Bun 二进制均 `SIGILL`；GitHub release 下载也超时 → **Bun 路线硬件上不可行**
4. 直连 `kilo-auto/free` 立即返回 OK，证明网关/鉴权/转发端口（18899/33501）全正常

**修复**：新建 `combo:kilo-verified`（13 个实测可用模型、fallback 策略），原 combo 保留不动；写入 `settings.json`（数据文件，非插件字节）。

**生效方式**（遵循托管约束）：插件目录是 pnpm 硬链接（link count=2），`installationIsManaged()` 禁用插件内升级/热重载；改文件不热生效。用户重启 DSH 后，插件重新 `apply()` 读取配置 → `/v1/models` 出现 41 个模型 → **实测 `combo:kilo-verified` 返回 "OK"**，旧链路仍超时。

---

## 7. 两份外部报告的实证核对

### 7.1 技术债务巡检（9 条 P0-P2）
核对结论：**报告来自另一套 Windows 数据面**（`E:\MemALL`、27 身份、913 条 L6、任务卡 #1000805 等本机全不存在的证据）。按用户确认「按本环境证据办」，只处理 4 项真实项（文档漂移、测试身份、session 接线、scheduler 保活），其余不适用或已闭环。

### 7.2 `DEEP_AUDIT_REPORT.md` 深度阅读
- **16 个修复全部真实落地**（逐条代码核对），三条「复核更正」基本正确
- 发现 4 处问题：§1-3「所有 subprocess 带 timeout」漏 2 处、§8-5 行号/内容不准（实际是 `9919` 不是 `9876`）、§6 pytest 缺失已过期、未覆盖我们修的 4 个测试失败
- 与我们的改动**互补无冲突**

### 7.3 部署一致性报告
- 行为一致性结论**正确**（302 /explorer、401 语义、/pair、/health、Origin 防线、仅 9920 端口、独立库）
- 更正 3 处事实错误：`memory_count=1/uptime 5.9h` 实际是 `4/54min`；`1705a63`/`fd097e3` 是**远端** commit（本地 git 不存在），报告误当本地；numpy 钉版来源不是那个 commit
- 最大风险「token 随机化」**本机证伪**：`.gateway-token` 固定文件 + keepalive 读取，与 DSH 配置 token 逐字节一致，多次重启验证有效
- 「本机无总线」过时：已合并进 9920

---

## 8. 两个社区插件调研（未安装）

| 仓库 | 结论 |
|---|---|
| `yjh051108/dsh-routing-suite` | 实际是「运行时注入器 + agent preset 提示词路由 + graded 协议」，**不改模型**；要求 DSH `<0.2.0`，本机 0.2.1-rc.5 不兼容 |
| `T-Auto/dsh-ops` | 省 token 的文件工具集（含 Rust worker）；要求 DSH `>=0.2.2-rc.1`，不兼容 |

两者都会改 `cordis.patch.yml` 且要求重启 DSH → **未安装、未改配置**。顺带核实本机模型选择机制：固定默认 `workbuddy/hy4free` + 手动切 + 失败重试同一模型，**没有按任务自动选模型**的能力。

---

## 9. 当前状态（23:36 复核）

| 项 | 值 |
|---|---|
| 网关 | pid 1185679，`/health`、`/bus/health`、`/ops/facts` 全 200 |
| MCP 握手 | 无声明 `-32001`，带 `X-MemAll-Agent` 通过 |
| 数据面 | 1 条记忆（架构事实）、3 个身份、0 边、21 条 ops_facts |
| scheduler | keepalive=1130666 daemon=1130671，heartbeat 推进中 |
| 开机自启 | crontab 2 条 @reboot（scheduler + gateway） |
| pytest | 全量 524 passed / 2 skipped（装了 pytest 9.2.0） |
| 降级路由 | `combo:kilo-verified` 实测可用 |
| git | 本地 `4cfcfcd`，远端 `1705a63`，**51 个文件未提交、未推送** |

> 注：跑测试会污染生产库（`category=test` 记忆 + 测试身份），本次已再次清理。属测试隔离问题，建议后续给测试统一用临时 DB。

---

## 10. 未决事项（推送）

用户要求「推送今天所有改动」→ 选择「先看清单不推」。已完成：
- `.gitignore` 加上 `.gateway-token`、`*.pid`、`*.log`、`*.db*`，5 个敏感文件全部排除（同步到 `MemALL-repo` 副本）
- 整理 51 文件清单，按归属分为：我方 ~31、另一 agent 18、双方重叠 7
- 风险：**本地落后远端 5 个提交**；7 个文件与远端功能级冲突（`mcp/bus.py` 本地整体重写 vs 远端 M1 dedup；`session.py`/`scheduler.py` 双方各改同区域）；两个仓库独立（主仓库无凭证、副本 URL 内嵌 PAT 且历史不同）

**没有提交、没有 push**。需先决定基线（rebase 到 1705a63 / 另开分支）、人工解决 7 个冲突、明确推哪个仓库。

---

## 11. 全天改动文件索引

- 新增：`gateway_bus.py`、`gateway_ops_facts.py`、`mcp_identity.py`、`ops_facts.py`、`migrations/043_create_ops_facts.py`、`LAN-CLIENT-GUIDE.md`、`scheduler_keepalive.sh`、`gateway_keepalive.sh`、`requirements-dev.txt`（另一 agent）、`DEEP_AUDIT_REPORT.md`（另一 agent）、`WORK_REPORT_20261108.md`（本文件）
- 修改：网关层 9 个、客户端/身份 6 个、债务修复 7 个、测试 2 个、文档 4 个、配置 2 个、端口/CORS 统一 7 个
- 全部同步到 `/vol1/@apphome/fn-deepseek-harness/MemALL-repo` 副本

*报告完 · 2026-11-08 23:36 CST · 全程未提交任何 commit*
