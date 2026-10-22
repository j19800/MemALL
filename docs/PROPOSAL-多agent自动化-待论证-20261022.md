# 待论证提案：多 Agent「自动协作」的现状与最小可行修复

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
