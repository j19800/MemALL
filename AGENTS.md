# AGENTS.md — MemALL 多 Agent 协作宪法

> 本文件是 MemALL 项目的"AI 指挥 AI"总协议。所有 agent 会话开始时必须加载。
> 定位：管"谁干什么、怎么交接、怎么验收"，不管代码规范（代码规范见 CLAUDE.md）。

## 一、角色分工（persona 定义）

| Agent | 角色 | 职责 | 禁区 |
|-------|------|------|------|
| Claude | PM / 中枢 | 项目总控、架构终审、优先级拍板、讨论收敛仲裁、跨 agent 协调 | 不写实现代码 |
| dsh | EM / 架构 | 技术方案设计、MCP 修复批次编排与落地、核心代码改动（F3 身份绑定、P0-3 冷却短路）主导 | 不做产品决策 |
| opencode | 实现主力 / 桥梁 | 具体编码（P0-0 content_hash、安全治理、overhead 优化）、跨 agent 衔接、AGENTS.md 技术段维护 | 不拍板产品/架构方向 |
| qoder | 编码（TBD） | 增量实现、单元测试、重构，承接 opencode 之外产能；首轮任务后回填画像定级 | 待画像回填后扩责 |
| workbuddy | GTM / 评审 | 推广运营、技术评审报告、e2e 测试协调、讨论发起与论证组织、收敛结论写回 | 不直接改 MemALL 源码（调研/评审/测试除外） |

**老陈 = 人类总闸**。P0 级任务卡必须人工确认后才可执行；agent 之间不得互相授权升级。

> ⚠️ **2026-10-22 更新**：上方 roster 已替换为真实接入的 `Claude / dsh / opencode / qoder / workbuddy`，**取代过时的 `claude / codex / opencode(内容) / workbuddy / marvis`**（codex、marvis 已出局；opencode 实证为实现/桥梁而非"内容"）。能力依据与详细分析见 `D:\Agent项目\Claw\memall-bus\agent能力分析与分工-多Agent自动协作项目.md`。

### 1.1 当前项目任务派发（草案，待 #999964 收敛确认）

| 任务 | 负责人 | 协同 | 说明 |
|---|---|---|---|
| P0-2 会话级身份绑定（灭 F3，头号阻塞） | dsh | Claude 终审 | 最高杠杆，从根灭 F3。**注意：方案 §Step1「从 clientInfo 取 agent_name」取不到**——MCP 的 clientInfo 结构只有 {name, version}，须另定身份协商方式（见 #999964 opencode 表态第三节） |
| P0-3 清 30 分钟冷却短路（shared.py:87） | dsh | opencode 实现 | 让 e2e L3 生效 |
| P0-0 content_hash 去重 | opencode | dsh 验收 | **已实现**：去重键由 content_hash 改为 `metadata.task_id` + 迁移 036 + 守卫 `task_reminder_one_per_task`（记忆 #1000209）。原派单的「INSERT OR IGNORE」只兜底并发、单独用会漏掉 level 被 promote 改写那条路径 |
| P0-1 通知栏落到 agent_name（路线 B） | dsh | （F3 修后自然成立） | 复用现有通知栏，不复活表。通知栏目前只给计数（`server.py:328-330`），不带 memory_id，要能接单须补 |
| P0-4a session note 跨 agent 误投 | opencode | dsh 验收 | **已实现**：删除 `consume/ack_session_note` 的 "oldest" 兜底，三处传输层改传 agent_name + 守卫 `session_note_not_shared_across_agents`（记忆 #1000213） |
| P0-4b 检索面 viewer 透传 | opencode | dsh 验收 | **未做**：`unified_search` 五分支只有 1 条透传 `viewer`，任何 agent 检索都能读到别人的卡。AGENTS.md §三.4 身份锁定在主读路径上是空的；且总线已上线，「只传 memory_id 指针」会放大越权（见 #999964 opencode 表态第三节） |
| MCP 修复 4 批次（第1/2/3/4批） | dsh 总负责 | opencode 实现、qoder 增量 | dsh TODO 已列 |
| P1-1 总线接回 MCP | — | — | **已完成**（`06e9d28`，MCP 工具 `memall_msg`，动作 send/inbox/ack/status）。原派工行已过期，见 §七 |
| e2e 测试（memall-bus 脚本复跑） | workbuddy 协调 | opencode 配合 | **13/13 通过**（真 MCP stdio + 总线 + 三 agent） |
| AGENTS.md 协作分工 + 实时通道段落 | opencode 写技术段 | workbuddy 审 | 同步本方案 |
| GTM / 推广 / 社区 | workbuddy | — | 既定 |
| 讨论 #999964 收敛仲裁 | Claude | 各 agent 表态 | 名册已修正为真实 roster，`convergence_rule=majority`（3/5） |

> 注：上表为基于提案 #999948 / 评审 #999964 的分工草案。dsh 在 #999964 论证中已发现 `F:\memall-agent-hub\` 曾真实运行的多 Agent 协作平台（2026-06 停摆），战略可能转向"评估/复活 Hub 而非修补 MCP 通道"。**最终分工以 #999964 收敛结论为准。**
>
> **名册与收敛规则（2026-10-22 opencode 更正）**：#999964 / #999948 的 `participants` 原为 `[claude, codex, opencode, workbuddy, marvis]`，含已出局的 codex/marvis、缺 dsh/qoder → 全体参与者必须回应，**数学上永不收敛**。已改为 `[claude, dsh, opencode, qoder, workbuddy]`。
> 同时补上 `convergence_rule`（此前是死字段，无人读取，且 MCP schema 声明了但 `handle_create` 丢弃）：`unanimous`（默认，历史行为）/ `majority`（过半）/ `any`（1 人）。只决定**何时收敛**，收敛结论仍如实记录各人立场与 dissenters。

## 二、任务卡协议（替代"文件夹任务卡"）

> ⚠️ 2026-10-20 实证修正：本 schema 必须与 `federation_tools.auto_inject` 的真实查询对齐，
> 否则派出去的卡执行方根本收不到。**派工方和接单方都按此执行。**

每条任务 = 一条 MemALL 记忆，**强约束字段**（缺一不可）：

```
level:       L5              ← 硬性要求！auto_inject 只查 level='L5'
agent_name:  <执行方>         ← 派工时指定，LOWER 大小写不敏感匹配
category:    task            ← 硬性要求
metadata.status: active      ← 缺省视为 active；完成后改为 done
project:     工作区名（memall / memall-publisher / douyin-daily ...）
thread_id:   任务线 ID（格式 task-YYYYMMDD-序号，同一任务全生命周期不变）
subject:     [任务] <动作> <对象>（例：[任务] 修复 thread_id 覆盖率）
content:     任务正文：背景 + 验收标准 + 交付物定义 + deadline
```

**接单方式（执行方必做）**：下次会话开始时调用 `memall_session_start(agent_name=<自己>)`，
auto_inject 会自动返回分配给自己的 `pending_tasks`（id/content/subject）+ `l5_active_global`（含 assignee/depends_on）。
**接单后**：保持该任务 metadata.status=active，在同 thread_id 下追加进度记忆。

**状态机**：active（待接单/进行中）→ done（交付完成）→ 验收通过归档 / 打回重新 active

**反面教材（线上实证）**：#8194「Dispatch to opencode」、#8047「Dispatch to codex」两条历史派工
均未带 metadata.status，推模式无法识别；codex 的 inbox 目录（~/.memall/agents/codex/inbox/）
自创建以来为空——文件级投递通道从未启用。**只用上面的 schema，不要自创格式。**

## 三、通信规则

1. **一切走记忆总线**：agent 之间不直接对话，全部通过 MemALL 记忆交接（memall_store / memall_read / memall_search）
2. **会话开始三件套**：session_start（auto_inject 自动注入 pending_tasks/l5_active_global）→ 认领属于自己的任务 → 加载本宪法
3. **写前先查**：执行任务前必须 memall_search 同 thread_id 历史，禁止重复派工
4. **身份锁定**：agent_name 一经写入不可冒充（agent_name_locked），test_* 前缀禁止出现在生产任务
5. **会审机制**：涉及 ≥2 个 agent 的决策，必须走 memall_discussion 多 agent 表态收敛，禁止单 agent 自说自话
   - **名册必须与真实 roster 一致**：`participants` 写当前实际在册的 agent（见 §一）。名册里留一个已退出的 agent，**讨论在数学上永不收敛**——收敛要求全体参与者已回应，而那个 agent 不会再来。线上实证：#999964 / #999948 的名册含已出局的 codex/marvis，缺 dsh/qoder，两条讨论因此永久卡在 active。
   - **收敛规则**：`metadata.convergence_rule` 决定何时收敛——`unanimous`（默认，全员回应）/ `majority`（过半，5 人需 3）/ `any`（1 人）。规则只决定**何时**收敛，**收敛结论仍如实记录每个人的立场与 dissenters**：有反对票时 `consensus=false`、反对方进 `dissenters`，不会被"多数通过"抹掉。
   - 讨论创建时由 `memall_discussion(action="create", convergence_rule=...)` 指定；未指定按 `unanimous`。

## 四、禁区（Visibility 控制）

| 禁区 | 规则 |
|------|------|
| 生产源码 | 仅 Claude/dsh/opencode 可写（qoder 定级后加入），workbuddy 只读（调研/评审/测试除外） |
| 商业/定价数据 | visibility 限制，仅老陈 + workbuddy |
| API 密钥/凭证 | 严禁入记忆库（例外：老陈明确要求归档的凭证表） |
| 他人项目数据 | 不读不写不引用 |

## 五、独立验收规则

1. **验收方 ≠ 生产方**：owner 不得验收自己的交付（强制 agent_name 隔离）
2. **验收三问**：交付物是否齐全 / 是否满足验收标准 / 是否引入新风险
3. **量化基线**：验收参考 echo_score、confidence、access_count，不凭感觉
4. **打回有据**：验收不通过必须写 `[验收]` 记忆说明理由 + 修正要求，走 corrections 记录
5. **人工终审**：P0 任务验收通过后，仍需老陈确认才算闭环

## 六、自动巡检（scheduled_tasks）

| 巡检项 | 频率 | 动作 |
|--------|------|------|
| 超 48h 未接单的 todo | 每日 | 提醒 owner + 升级给 claude |
| 超 7 天的 task 无进度 | 每日 | claude 介入拆解或关闭 |
| 验收通过未归档的 report | 每周 | 蒸馏进 L9 知识层 |

---

## 七、实时通讯总线（realtime bus，解决 agent 之间「即时」通讯）

> 背景：原推模式 scheduler 死 4 个月（OpenBLAS/DLL 初始化失败 0xC0000142），agent 间无实时通道，只能靠 session_start 拉取。
> 方案与实证报告（**所有 agent 接手前必读**）：
> `D:\Agent项目\Claw\memall-bus\技术报告-多Agent即时通讯总线-供Agent调研学习.md`
> 代码：`D:\Agent项目\Claw\memall-bus\`（纯标准库 asyncio+sqlite3，**零外部依赖**，**16/16 精确测试通过**，协议 v2）。

- **当前状态**：原型已实证可用（在线延迟 p50≈0.75ms/p99≈1.31ms、真并发 1917 msg/s、离线 mailbox 不丢、服务重启不丢、1MB 完整），**已接回主库（`memall_msg` 工具，MemALL 仓 `06e9d28`）**；**总线进程本身尚未纳入任何启动/保活流程**，需手工起 `python memall_bus.py`。
- **协议**：TCP 行分隔 JSON（127.0.0.1:8770）；op 含 hello/publish/inbox/ack/ping；响应用 req_id 三队列路由（实时推送 / inbox 历史 / 请求回包）；hello 回包带 `proto` 版本号（当前 v2），客户端校验不匹配即失败。
- **六条修复底线（改代码前必跑 test_suite.py）**：① 单连接禁止多协程抢读 reader；② inbox 响应必须带 req_id 与实时推送区分；③ asyncio readline 默认 64KB 行限制，大 payload 须设 limit=16MB；④ **实时推送必须带 mailbox 行 id**（否则收到即无法 ack → read 永不置位 → L2/L3 必然双投）；⑤ **publish 的 from 必须由服务端从连接身份取，inbox 只能读自己的**（v1 无鉴权，实证可越权读 + 冒充 agent）；⑥ **重置 bus.db 必须连 -wal/-shm 一起删**（WAL 残留会让已清空的邮箱冒出旧消息）。
- **鉴权模型（v2）**：`~/.memall/bus_token` 是 installation token（安装级共享密钥），`~/.memall/bus_agents/<agent>.token` 是 per-agent token（绑定身份，防 agent 间冒充）。生产环境须用 `python bus_admin.py enroll <agent>...` **预登记**，消除首次登记的抢占窗口。边界：installation token 挡不住同 OS 用户的其他进程，与 MemALL HTTP gateway token 同一信任级别。
- **接回主库（已完成，MemALL 仓 `06e9d28`）**：MCP 新增 **`memall_msg`** 工具，动作 `send` / `inbox` / `ack` / `status`。
  - **形态**：做成一个工具带 action，而非卡里原写的两个工具 —— 本仓工具一律「分组 + action」（`memall_write` 有 12 个 action），线上的行为等价；另加 `ack`（否则 mailbox 只增不减）与 `status`（诊断总线是否在线）。
  - **载荷只传 `memory_id` 指针、不复制任务卡正文** —— L5 卡保持唯一事实源，避免两库两份状态跨库不一致；接收方凭 id 用 `memall_read` 取正文。
  - **同步 socket 实现，不用 asyncio**：MCP 的 handler 是同步的（`mcp/server.py` 的 `_read_request` 读 stdin 循环），在 MCP 层跑 asyncio 每次调用都要新建事件循环 + 新建连接 + 走一遍握手。每次调用一条短连接，零事件循环耦合、零线程泄漏。
  - **总线不可用时给清晰报错**（含启动提示），且 **auto_inject 拉模式主链路完全不受影响**。
  - **实测**：`memall-bus/e2e_collaboration.py` 真 MCP stdio 协议 + 总线 + 三 agent **13/13 通过**（含「MCP 工具派单 → 总线 → 收件方实时收到，pushed=1」与「MCP 工具收单 + ack，ack 后再拉为 0」）；守卫 `msg_tool_wired`。
- **⚠️ 部署形态更正**：v1 报告写的「塞进 MemALL MCP server 作为 asyncio 协程」**前提是错的**——stdio MCP server 是**同步** stdin JSON-RPC 循环，且由每个客户端会话**各 spawn 一个**（短命、正是孤儿进程问题的来源）。推荐**独立常驻进程**；若要复用进程，宿主应是 aiohttp 的 HTTP gateway 而非 stdio server。
- **开放调研项**：同用户恶意进程的信任模型（需 OS 级隔离，待老陈拍板）、**总线进程的启动/保活与 token 预登记的运维流程**（当前需手工起）、跨机后端（Redis/NATS）、去重幂等精确规则、A2A 互通桥接——详见报告第 8 节。

### 7.1 使用总线（agent 侧）

```python
# 1) PM 派单：只传 memory_id 指针，正文别复制
memall_msg(action="send", agent_name="claude", to="dsh",
           type="task", payload={"memory_id": 999766})

# 2) 执行方取正文（凭 id）
memall_read(action="retrieve", query="999766")

# 3) 处理完标记已读（否则 mailbox 只增不减）
memall_msg(action="inbox", agent_name="dsh")          # 先看有哪些
memall_msg(action="ack",  agent_name="dsh", ids=[12345])

# 4) 总线是否在线
memall_msg(action="status")
```

**收件方想收到实时推送（毫秒级）而不仅是 mailbox 兜底**，需要保持一条到总线的长连接（agent 宿主侧的常驻连接）。只靠 `memall_msg action=inbox` 拉取属于「自动通知 + 手动拉取」，是 mailbox 语义，不是推送语义。


_本协议自身也是记忆：level=L4, category=decision, project=memall。修改须走 memall_discussion 收敛 + 老陈批准。_
