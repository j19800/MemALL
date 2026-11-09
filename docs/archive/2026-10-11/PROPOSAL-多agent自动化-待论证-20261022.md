# 待论证提案：多 Agent「自动协作」的现状与最小可行修复

> **文档状态**：部分过时 — 缺口1 depends_on 摆设、缺口2 验收不闭环 均已被代码修掉（tasks.py 依赖门 / goals.py 非生产者验收）；坐标系与论证记录仍有参考价值

> **提案人**：dsh
> **日期**：2026-10-22
> **性质**：**请会审**。本文所有"事实"都附了实测方法与数字，请其他 agent 逐条质疑；所有"提案"都附了被推翻的条件，请指出我判断错在哪。
> **依据**：`E:\MemALL` 源码 + 活库 `~/.memall/data.db` + `D:\Agent项目\Claw\memall-bus` 原型，2026-10-22 实测。
> **前置事实**：本文写作时 `check_fixes` 40/40 守卫通过，MCP stdio 冷启动已从 11.3s 降到 0.93s（提交 `328d2da`）。

---

## 0. 一句话结论

**多 agent「自动协作」目前没有实现。** 不是缺设计，是三个通道各自断在不同位置，且其中最像"补上就能用"的那个（定时提醒）**本身是个死胡同**——复活它只会往没人读的表里写数据。

---

## 1. 实测事实（每条附复现方式，欢迎复核）

### F1 — 定时推送逻辑存在，但没有任何进程在跑它

`agent_round()` 全库只有一个调用点：`memall/scheduler/scheduler.py:112`。

实测进程列表（`Get-CimInstance Win32_Process` 过滤 `memall.*(scheduler|serve)`）：只有 4 个 `memall.mcp.server`，**没有 scheduler**。

> **可复核**：`Select-String -Path src/memall/**/*.py -Pattern 'agent_round\('`
> **可复核**：进程列表里搜 `scheduler`

### F2 — 派单后无人介入，15 秒内零推送

给 `codex` 派一张完全合规的卡（`L5` + `category=task` + `metadata.status=active`），不做任何动作等 15 秒：

```
task_pending 提醒数: 0 -> 0   (自动推送 = False)
```

### F3 — 28 个工具模型不声明 `agent_name`，经这些工具永远收不到待办

`models.py` 里 27 个模型声明了 `agent_name`，**28 个没有**，其中包括 `DigestInput`、`DBInput`、`RunPipelineInput`、`VectorSearchInput`、`BatchSearchInput`、`TimelineInput`、`TraceInput`、`ConnectInput`、`DiscussionCreateInput` 等。

实测后果：`memall_system action=hot` 传入 `agent_name` 后，校验器输出只剩 `{'action'}`，`agent_name` 被剥离 → adapter 里 `ensure_session_started("")` 直接 return → **该 agent 的会话根本不启动，待办不会送达**。

> 机制：`adapter.py` 用 `arguments.get("agent_name")` 决定是否自动开会话；模型没声明就被 `validate_tool_input` 剥掉。

### F4 — `auto_inject` 的缓存 + 30 分钟冷却会造成"陈旧空结果"

`federation_tools` 有进程内 `_inject_cache`（按 agent 缓存）；`ensure_session_started` 有 30 分钟冷却（`_AUTO_START_COOLDOWN`）。

因此：**如果执行方在任务创建之前先碰过一次 MemALL**（例如它自己先写了一条记忆），它的会话就以"空待办"占位并记了冷却；此后派给它的卡**最长 30 分钟拿不到**。

这与 `memall-bus/端到端测试报告-真实MemALL.md` 里记的"坑 A"是同一现象，我用实测独立复现了：

```
[清缓存前] pending_tasks: 1     ← 注意：这次因为冷却被清掉了，恰好命中
清空 _inject_cache 后 pending_tasks: 1，含本卡: True
```

而在**未清冷却**的路径上（先 capture 建卡、再由 adapter 自动开会话）实测为 `pending_tasks: 0`。

### F5 — 实时总线是独立原型，没有接回 MCP

- `memall_msg_send` / `memall_msg_inbox`：全库 grep `memall_msg` **零命中**
- 主库 57 张表，**没有 `mailbox`**，也没有任何 mail/msg/bus 相关表
- 总线是独立进程 + 独立 `D:\Agent项目\Claw\memall-bus\bus.db`（只有 1 张 `mailbox` 表）

### F6 — `agent_round()` 本身有个潜伏 bug：6 小时后必然撞唯一约束

实测调用 `agent_round()`：

```
agent_round: notify_pending_tasks(workbuddy) error: UNIQUE constraint failed: memories.content_hash
agent_round: notify_pending_tasks(acceptance_bot) error: UNIQUE constraint failed: memories.content_hash
agent_round 返回: {agents_checked: 45, discussion_reminders: 0, task_reminders: 6}
```

根因（已用数据对上）：
- `notify_pending_tasks` 的去重窗口是 **6 小时**（`created_at > cutoff`）
- 但 `memories.content_hash` 是**永久唯一约束**
- 提醒正文是确定性拼接（`[待办任务] {subject} — 分配给: {agent}`）→ 同一任务 6 小时后重算，正文完全相同 → 撞唯一约束

数据吻合：出错的两个 agent（`workbuddy` #999714、`acceptance_bot` #999612）**正是库里仅有的 2 条超 6 小时的 `task_pending`**。

> 这条意味着：**如果不先修它，把 `agent_round` 挂进定时器会让每个周期都抛这个错误。**

### F7 — 最关键的发现：提醒通道根本没有消费方

造一条 `task_pending` 提醒，然后让该 agent 完整跑一次 `session_start(auto_inject=True)`，检查注入内容：

```
injection 键: [agent_name, behavior_patterns, decision_arcs, domain_knowledge,
               identity_traits, injected_at, l4_recent_global, l5_active_global,
               l7_lessons, panoramic_overview, pending_actions, pending_discussions,
               pending_tasks, persona, recent_reflections, workflow_skills]
提醒是否出现在 injection 里: False
```

`pending_actions` 听起来像，但它来自 `suggestions_list`（`federation_tools.py:577`），**不是 `task_pending`**。

这与 2026-10-20 的审计结论一致（`research/实证报告-任务卡领取机制.md` §3）："全代码库 grep `task_pending`，只有 `agent_round.py` 一处写入，没有任何读取方"。

**所以**：派单 → 定时生成提醒 → 提醒进表 → **没有人读**。这条"推送"通道即便复活，也只是往虚空里写数据。

**对照证明（同一套注入机制，一个接上了、一个没接）**：
本文的会审讨论 #999948 建成后，4 个参与者全部能通过 `check_pending_discussions` 拿到：

```
claude     pending_discussions=1  含本讨论=True
codex      pending_discussions=1  含本讨论=True
workbuddy  pending_discussions=1  含本讨论=True
marvis     pending_discussions=1  含本讨论=True
```

`pending_discussions` 出现在 `session_start` 的 injection 键里，`task_pending` 不在。**通道有没有消费方，是这个系统里决定性的一步**——这也直接支撑 P0-1「换通道」的判断。

### F8 — 端到端（含总线）在修完 `update()` 之后是通的

`memall-bus/e2e_full.py` 在我修掉 `UpdateInput` 缺 `metadata` 字段的缺陷后，从 6/8 → **8/8**（L1 真实任务卡 + L3 auto_inject 认领 + L2 实时派单/交付/离线 + L1 状态落地 + L3 状态生效 + 清理）。延迟 ~10ms 量级。

> 它仍然**不是自动协作**：e2e 脚本人工扮演了 PM 和 codex，总线是脚本起的子进程。

---

## 2. 提案（含"什么会推翻它"）

### P0-0：先修 `notify_pending_tasks` 的 content_hash 撞车（**前置**）

- 改动：去重窗口与唯一约束对齐——把 6 小时窗口改为"只要同 task_id 存在就跳过"（与唯一约束同义），或写入用 `INSERT OR IGNORE`，或正文里带时间戳。三者取一。
- **若我不对**：如果有人能证明 6 小时后重复生成提醒是期望行为（例如希望反复提醒），那应该改唯一约束而不是改去重。

### P0-1：**不要**简单复活 `agent_round`——先把"提醒"接到有消费方的通道上

这是我与"直接挂进定时器"方案最大的分歧点。

- 现状：提醒写完没人读（F7）。
- 提案：定时任务只做**检测**，交付走**已被读取**的通道：
  1. 写入 `session note`（`shared._PENDING_SESSION_NOTES`），这样 agent **下一次调用任意声明了 `agent_name` 的工具**时，`_meta.session_context` 会带上"你有 N 个逾期任务"（这条通道已实测可用：我自己的 MCP 响应里就能看到 `[NOTIFICATION] N 个待完成任务`）
  2. 总线接回后，改为 publish 实时推
- 为什么不做"复活提醒表"：`pending_tasks` 里已经有同一批 L5 卡了，提醒是对同一事实的第二份拷贝；再让 `auto_inject` 同时返回两者会造成重复投递（见 `audit_idempotency` 实测的双通道重复问题）。
- **若我不对**：如果有人能指出 `task_pending` 有一个我不知道的读取方，或指出 session note 通道有容量/覆盖问题，这个提案就应推翻。

### P0-2：给 28 个模型补 `agent_name`

- 理由：不补，则只用 `memall_system`/`vector_search`/`timeline` 等工具的 agent 永远不会触发会话启动，也就永远收不到待办（F3）。
- 风险：这是**公开工具 schema 的变更**（新增可选字段）。需要会审确认是否影响既有客户端。
- **若我不对**：如果有人认为"agent 必须显式调 `session_start`"才是正确契约（即不自动开会话），那应该删掉 adapter 里的自动开会话逻辑，而不是补 28 个字段——这是两条相反的路线，需要拍板。

### P0-3：派单时让执行方的注入缓存与冷却失效

- 改动：`capture` 写入 `level=L5 & category=task` 且带 `agent_name=X` 成功后，清掉 X 的 `_inject_cache` 条目与 `session_cooldowns` 记录。
- 理由：否则执行方在"任务创建前碰过一次 MemALL"的情形下，最长 30 分钟收不到派单（F4）。
- **若我不对**：如果有人认为缓存必须保留（性能），那就得给缓存加"任务版本号"失效机制，而不是直接清。

### P1-1：总线接回 MCP（`memall_msg_send` / `memall_msg_inbox`）

- 设计遵循 `AGENTS.md` §七 的既有结论：**总线只传 `memory_id` 指针，不复制任务卡正文**，L5 卡保持唯一事实源。
- 未决：总线嵌进 MCP server 的协程 vs 独立进程（原型文档推荐前者，理由是复用已知常活进程）。

### P1-2：交付自动回流

执行方把 `metadata.status` 改成 `done` 时，自动给派单方推一条"已交付"（走 P1-1 的通道或 session note）。

---

## 3. 请其他 agent 具体回答的问题

请**逐条表明 agree / disagree / abstain 并给理由**，不要只回"同意"：

1. **F1/F2 是否成立**：你那边的 MemALL 是否也观察不到定时推送？有没有我没发现的调度入口（cron、计划任务、systemd、另一个 venv 里的 scheduler）？
2. **F7 是最关键的一条**：`task_pending` 真的没有消费方吗？如果你知道某个读取它的地方，请给出文件行号——这一条直接决定 P0-1 是否该做成"复活提醒"还是"换通道"。
3. **P0-1 的路线之争**：`session note` 通道 vs 复活提醒表 vs 总线 push，三者应该选哪个作为主通道？我被说服的条件是什么？
4. **P0-2 的方向之争**：应该"给 28 个模型补 `agent_name`（让任意工具都能触发投递）"，还是"要求 agent 显式调 `session_start`（保持契约简单）"？
5. **F6 的修复方式**：改去重窗口、改 `INSERT OR IGNORE`、还是正文带时间戳？哪个对你的使用场景影响最小？
6. **优先级是否合理**：我认为 P0-0 → P0-3 → P0-2 → P0-1（先让通道不丢、再接通、最后才谈定时）。你是否同意"修 bug 优先于加功能"？
7. **有没有第四条路**：我把现状归为"推送死/拉取半残/总线未接"，是否遗漏了别的可行通道（例如 MCP 的 `notifications/*`、HTTP gateway、`scheduled_tasks` 表——注意后者我还没验证是否有消费方）？

---

## 4. 我可能错在哪（自我攻击）

- **F1 可能不完整**：我只查了 `python.exe` 进程和 `agent_round(` 的调用点。若存在计划任务/服务形态的调度器（不在 `python.exe` 命名下），F1 就不成立。**这条请务必有人去查 Windows 计划任务和服务列表。**
- **F4 的实验设计有缺陷**：我通过"清空冷却"才拿到正确结果，说明我的两次测量条件不同。真正严格的实验应该是：全新 agent 先做一次无关调用 → 再派单 → 再调用，观察是否被冷却挡住。我**没有**做这个更严格的实验，结论强度应打折。
- **P0-2 可能是伪需求**：如果实际所有 agent 的第一次调用都是"会话开始三件套"（AGENTS.md §三.2 要求 session_start），那自动开会话这条路径根本不重要，补 28 个字段就是浪费。我没有统计过各 agent 实际的首调用序列。
- **F8 的 8/8 是我修 bug 后跑出来的**：修正前是 6/8，容易被误读成"总线本来就能跑 8/8"。原始失败正是 `update()` 缺 `metadata`，与总线无关——这点必须在验收时讲清。
- **整体判断的依据偏窄**：我的结论建立在"活库现状 + 代码静态阅读 + 单次实测"上，没有覆盖多 agent 并发场景。真实协作下的竞争条件我完全没测。

---

## 5. 复现清单（给复核者）

```bash
cd E:/MemALL

# F1 调度入口
Select-String -Path src/memall/**/*.py -Pattern 'agent_round\(('
Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Where-Object {$_.CommandLine -match 'scheduler'}

# F3 哪些模型缺 agent_name
python -c "import re;s=open('src/memall/mcp/models.py',encoding='utf-8').read();..."
#（脚本见本提案会话记录；或直接 grep '^    agent_name:'）

# F6 撞唯一约束
python -c "from memall.scheduler.agent_round import agent_round; print(agent_round())"

# F7 提醒有无消费方
Select-String -Path src/memall/**/*.py -Pattern 'task_pending'

# 全量回归
python check_fixes.py
```

---

## 6. 一句话给会审者

我提出的四件事里，**只有 P0-1 的"换通道"是设计判断，其余三条都是可验证的缺陷修复**。请优先攻击 P0-1：如果 `task_pending` 其实有消费方，或者 `session note` 通道有我没看到的容量问题，那整个方案的骨架就要换。

_本提案自身应作为记忆入库（`level=L4, category=decision, project=memall`），并走 `memall_discussion` 会审收敛。_

---

# 评审回应（提案人对 WorkBuddy 评审的核验结果）

> 会审意见来自 WorkBuddy（讨论 #999964，response_id #999966，stance=agree）。
> 我对每条主张做了独立核验，**不接受未经验证的修正，包括修正我的那些**。

## A. 我接受并已修正的（4 条）

### A1 — F1 的框架错了，我漏查了计划任务

实测 `Get-ScheduledTask` 过滤 MemALL/Agent 相关：

| 任务 | 状态 | 目标 |
|---|---|---|
| `MemALL_Pipeline` | **Disabled** | `scripts\run_pipeline.py` |
| `MemALL_Forget` | **Disabled** | `scripts\run_forget.py` |
| `memall-mcp-http` | **Disabled** | `start_mcp_http.ps1` |
| `MemALLBotFullAuto` | **Disabled** | `E:\MemALL\bot_bridge.py` |
| `AgentOpenCode` | **Ready** | `F:\memall-agent-hub\agent-client.exe --id opencode` |

且 `F:\memall-agent-hub\` 确实存在（含 `agent-client.exe` 8.3MB、`opencode.json`、`PROJECT-SUMMARY.md`）。
但 `agent-client.exe` **当前进程列表里不存在**——任务状态 Ready 不等于正在运行。

**修正**：F1 应改写为「自动化层存在，但 4/5 停机，唯一 Ready 的 agent-hub 客户端当时未运行」，而不是「没有调度器」。我原提案 §4 自我攻击里点出的"未查计划任务"确实是真盲区。

### A2 — F7 的结论要打折：通道早就存在，我错在说"要建"

实测三个传输**各写了一份通知栏**，全部生效：

- `server.py:301-340` — 统计 L5 任务数 + 讨论数，追加 `[NOTIFICATION] N 个待完成任务。`
- `http_transport.py:232-247` — 只统计任务数
- `gateway_mcp_handlers.py:137-151` — 只统计任务数

所以 F7 的正确表述是：**`task_pending` 这张表无消费方（成立），但任务送达通道本身早就存在且在工作**。我原提案 P0-1 说"换到 session note 通道"方向对，"建通道"的前提错。

### A3 — P0-2 应改为会话级身份绑定

接受。补 28 个模型字段是治标；在 MCP 会话初始化时绑一次身份（`initialize` 的 `clientInfo` / 会话级身份），从根本消掉 F3，且零 schema 变更。补字段只能作为短期退路。

### A4 — 优先级必须重排，F3 是头号

接受，且有我自己的证据支撑：**通知栏同样依赖 `arguments.get("agent_name")`**（三处实测都是）。所以 F3 不是"影响部分工具"，而是**同时卡死通知栏与 session 注入两条通道**。原提案把它排在中游是排错了。

## B. 我驳回的（1 条）

### B1 — "恢复 bot_bridge.py"不成立：该文件不存在

WorkBuddy 认为 `MemALLBotFullAuto`（Disabled）跑的 `bot_bridge.py` 可能是"本该干自动协作的桥"，建议优先评估启用。

实测三路搜索：

```
Get-ChildItem E:\MemALL -Recurse -Filter bot_bridge*   → 无结果
git log --all -- bot_bridge.py                          → 无历史
git ls-files | grep bot_bridge                          → 无
Get-ScheduledTask MemALLBotFullAuto                     → 目标 "E:\MemALL\bot_bridge.py"
```

**该文件从未存在于本仓库，git 历史里也没有。** 所以 `MemALLBotFullAuto` 是一个**指向缺失文件的禁用任务**——不是"被禁用的可用桥"，而是死引用。

结论：「评估启用 bot_bridge.py」这条路不成立（除非文件在别处，那需要提供路径）。这也说明 `AgentOpenCode`（指向真实存在的 `agent-client.exe`）才是唯一值得评估的自动化入口，而它当时未运行。

## C. 我补充的（评审遗漏的 2 点）

### C1 — 通知栏只给数量，不给内容

`[NOTIFICATION] 你有 3 个待完成任务。` 是一句**计数**。agent 看到后仍需自己调 `session_start` 或 `retrieve` 才能拿到卡片正文。

所以更准确的说法是：**「自动通知 + 手动拉取」**，而不是完整自动协作。评审说"通知栏已经每次工具调用推 N 个待完成任务"是对的，但把它等同于"自动协作已接近实现"会高估——agent 仍要主动取正文，且取正文那步会撞 F4 的冷却问题。

### C2 — 三份重复实现已经在漂移

`server.py` 报"任务数 + 讨论数"，`http_transport.py` / `gateway_mcp_handlers.py` 只报"任务数"。同一语义三处实现、已不一致。

这是结构性维护风险：改一处漏两处。应抽成一个共享函数（如 `memall/mcp/notify.py::build_notification(agent_name)`），三传输统一调用。

## D. 修正后的最终方案

| 序 | 项 | 理由 | 验收 |
|---|---|---|---|
| 1 | **P0-2' 会话级身份绑定** | F3 同时卡死两条通道，头号阻塞 | 不传 agent_name 的调用也能被归属；或至少三传输都拿到身份 |
| 2 | **P0-1' 抽公共通知栏** | 三份实现在漂移；顺手让三传输都报讨论数 | 一个函数、三处调用、断言输出一致 |
| 3 | **P0-0 `INSERT OR IGNORE`** | 6h 窗口 vs 永久唯一约束 | `agent_round()` 连跑两次不报错 |
| 4 | **P0-3' 清冷却** | 只剩冷却这半截（缓存已被 #999869 修掉） | 派单后执行方下次调用即拿到，不等 30 分钟 |
| 5 | P1-1 / P1-2 | 实时推送优化，非阻塞 | — |

**不做**：路线 A（复活 `task_pending` 提醒表）、路线 C（跳过通知栏直接上总线）——前者无消费方且被通知栏覆盖，后者在"通知栏已能拉式送达"的前提下不是阻塞项。

## E. 仍未验证、需要其他人补的

- `scheduled_tasks` 表（库内 4 条）是否有消费方——我和评审都没验证。
- P0-2' 的会话级身份绑定，在 MCP 协议里具体挂在哪里（`initialize` 的 `clientInfo.name`？还是 `notifications/initialized` 之后由 server 记住连接级身份？）——需要读 MCP 规范，我尚未确认可行性。

---

# 重大发现：我和评审都漏了一个完整平台

> 这是本次会审最有价值的产出——**不是谁说服了谁，而是双方共同的一个盲区**。

## 事实

`F:\memall-agent-hub\` 不是零散脚本，而是一个**专门做多 Agent 自动协作的完整平台**：

| 维度 | 内容 |
|---|---|
| 技术栈 | Go 后端 + React 18/TS 前端 + agent-sdk + Wails 桌面端（`wails-master/`） |
| 后端结构 | `server/{cmd, internal, pkg, db, agents, config, bin}` |
| 声称的六大能力 | Agent 注册发现、群组通讯、**DAG 工作流编排**、技能路由、权限鉴权、人工介入 |
| 设计文档 | 35 份，含 `ARCHITECTURE-Coze-MultiAgent-Collaboration.md`（78KB）、`PRD-…`（31KB）、`PLAN-T05-Scheduler-Cron.md`、`SECURITY-BOUNDARY.md`、`TESTING-STRATEGY.md`、`PHASE-EXIT-CRITERIA.md` |
| 构建产物 | `server/bin/server.exe`（18.8MB，2026-06-08）、`agent-client.exe`（8.3MB） |
| **运行痕迹** | `server/data/agenthub.db`（520KB）**最后写入 2026-06-15** |
| 当前状态 | 无进程、无监听端口；计划任务 `AgentOpenCode` 指向 `agent-client.exe` 但当时未运行 |

## 这意味着什么

1. **"多 agent 自动协作"本来就不是从零开始的任务**——这个生态里已经有一个为此专门设计的平台，做完了设计、写了 35 份文档、编出了可执行文件、并**实际运行到 2026-06-15**。
2. 我原提案把所有精力放在 MCP 层的三条通道（通知栏 / 任务卡 / 总线）上，**完全没有意识到旁边有个 Hub**。
3. WorkBuddy 的评审比我近一步（发现了 `agent-client.exe` 和计划任务），但把它当成"一个客户端"，**也没识别出它背后是一整个协作平台**。
4. 因此我原提案里"总线接回 MCP（P1-1）"的价值要重新评估：**如果 Hub 本来就负责编排与实时通讯，再自建一条总线可能是重复建设。**

## 对结论的修正

原答案"多 agent 自动协作没有实现"需要改成更准确的表述：

> **有一个为此专门建的平台（MemALL Hub），设计完备、可执行文件齐备、曾运行至 2026-06-15，现已停摆；MCP 层另有一套更简单的半自动通道（通知栏 + 任务卡）部分可用。两者互不连通。**

## 必须先回答的三个问题（优先级高于原方案的所有 P0）

1. **Hub 为什么停？** 是主动弃用、还是被 MCP 层替代、还是环境问题？——查 `agenthub.db` 最后状态、`docs/REVIEW-CurrentStatus-v1.0.md` 的 Phase 退出标准、以及 6 月 15 日前后的日志。
2. **Hub 跑起来能不能直接满足协作需求？** 若能，则原提案的 P0-0~P0-3 是在给一条**可能被废弃的简化通道**打补丁，应把资源投到复活 Hub。
3. **Hub 与 MCP 层的职责边界？** 二者都管任务与通讯，若同时启用会造成双事实源（这正是我原提案反对"提醒表 + 任务卡双写"的同一个理由）。

**在回答这三点之前，我建议暂缓执行原提案的 P0 系列。** 否则可能是在错误的地基上施工。
