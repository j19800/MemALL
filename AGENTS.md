# AGENTS.md — MemALL 多 Agent 协作宪法

> 本文件是 MemALL 项目的"AI 指挥 AI"总协议。所有 agent 会话开始时必须加载。
> 定位：管"谁干什么、怎么交接、怎么验收"，不管代码规范（代码规范见 CLAUDE.md）。

## 一、角色分工（persona 定义）

| Agent | 角色 | 职责 | 禁区 |
|-------|------|------|------|
| claude | PM | 拆单派工、方案评审、讨论收敛、最终验收 | 不写实现代码 |
| codex | EM | 工程实现、重构、性能、部署 | 不做产品决策 |
| opencode | 内容 | 文档、教程、社区内容 | 不碰核心代码 |
| workbuddy | GTM | 调研、竞品分析、发布系统、运营数据 | 不直接改 MemALL 源码 |
| marvis | 腾讯助手 | 腾讯生态协同、外部对接 | 不参与内部工程 |

**老陈 = 人类总闸**。P0 级任务卡必须人工确认后才可执行；agent 之间不得互相授权升级。

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

## 四、禁区（Visibility 控制）

| 禁区 | 规则 |
|------|------|
| 生产源码 | 仅 claude/codex 可写，workbuddy/marvis 只读 |
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
> 代码：`D:\Agent项目\Claw\memall-bus\`（纯标准库 asyncio+sqlite3，**零外部依赖**，**8/8 精确测试通过**）。

- **当前状态**：原型已实证可用（在线延迟 p50≈6ms/p99≈13ms、1000 条全送达 154msg/s、离线 mailbox 不丢、1MB 完整），**尚未接回主库 MCP server（P1 待做）**。
- **协议**：TCP 行分隔 JSON（127.0.0.1:8770）；op 含 hello/publish/inbox/ack；响应用 req_id 三队列路由（实时推送 / inbox 历史 / 请求回包）。
- **三条修复底线（改代码前必跑 test_suite.py）**：① 单连接禁止多协程抢读 reader；② inbox 响应必须带 req_id 与实时推送区分；③ asyncio readline 默认 64KB 行限制，大 payload 须设 limit=16MB。
- **接回主库设计**：MCP 新增 `memall_msg_send` / `memall_msg_inbox`；AGENTS 派单改为 publish 实时推；与 auto_inject 拉取共读 mailbox，去重靠 status/read。
- **开放调研项**：安全鉴权与 agent 隔离（当前无 auth）、常驻封装、跨机后端（Redis/NATS）、A2A 互通桥接——详见报告第 8 节。


_本协议自身也是记忆：level=L4, category=decision, project=memall。修改须走 memall_discussion 收敛 + 老陈批准。_
