# dsh-superagent 调研 —— DeepSeek 官方"主动型 Agent"原型对 MemALL 的参考价值

> **文档状态**：现行 — 外部项目机制调研（2026-11-04 实拉源码核验）。结论尚未并入《改进方案·定稿》，待 dsh/老陈确认后再决定取舍。

> 调研对象：`deepseek-ai/dsh-superagent-prototype`（GitHub，MIT）
> 调研方法：GitHub API 拉仓库元数据 + 逐篇读 `docs/000`–`008`（8 篇设计文档，共 ~18 万字符）+ 读机制层源码（`src/policy.ts`、`src/core/engine.ts`、`src/core/intake.ts`、`src/core/stage.ts`、`src/core/reasons.ts`、`src/admission.ts`、`src/app/store.ts`）+ 读 `prompts/*.md`。**不是读 README 得出的印象**。
> 配套：`Multica-代码深度分析-20261028.md`（同为外部协作引擎的机制级分析，可对照读）

---

## 0. 硬事实（2026-11-04 实拉）

| 项 | 值 |
|---|---|
| 仓库 | `deepseek-ai/dsh-superagent-prototype`，描述 "Towards a More Autonomous Agent System" |
| 规模信号 | **15,643 stars / 1,674 forks / 96 watchers**，创建于 2026-10-31，`pushed_at` 2026-11-03 |
| 许可 | **MIT**（对比 Multica 的 `NOASSERTION` source-available —— 这份**代码可直接复用**，不只是研究） |
| 语言/栈 | TypeScript ~2.0MB + CSS 40KB + Python 12KB（`scripts/dsh-superagent` 是 tmux 服务管理器）；pnpm 11.7 / Node ^22.19 / vitest 4 / zod 4 / React 18 |
| 外部依赖 | **只有 2 个运行时依赖**：`zod` + `proper-lockfile`。持久化用 Node 内置 **`node:sqlite`（`DatabaseSync`）**，单文件 `<stateDir>/orchestrator.sqlite` |
| 代码量 | 216 个文件，`src/` 111 个 TS 文件，**59 个 spec 测试文件**（`tests/*.spec.ts(x)`），`pnpm check` = typecheck + test + build |
| 提交历史 | **main 上只有一个 squash 提交**（2026-10-31 "add the dsh-superagent research prototype"）——这是"公开版"，不是开发历史 |
| 作者自述 | README 开篇："Research prototype for reference only, not an official DeepSeek product... much of the engineering done by coding agents... may still contain bugs" |

**它是什么**：一个**单仓库范围内的主动型编码 Agent**，以 DeepSeek Harness（DSH）bundle 形式分发。它监听 GitHub（可选飞书），有人请求就开工单，**每张工单一个独立 agent session + 独立目录**，做完在原会话里回话（比如开 PR）。带一个 Web 面板，只显示"需要人处理的事"。

---

## 1. 先澄清一件容易误读的事：它**不是**"多个 agent 协作"

这是本次调研最重要的一条边界判断，直接决定该不该拿它当 MemALL 多 agent 协作的样板：

| | dsh-superagent | Multica | MemALL |
|---|---|---|---|
| 协作主体 | **1 个 agent**（同一身份、同一账号），拆成 1 个 `orchestrator` 主 session + N 个 `worker` 执行 session | **N 个 agent 当队友**（26 种 CLI 适配、squad、leader 路由） | **4 个具名 agent**（dsh/opencode/qoder/workbuddy） |
| 并发单位 | 工单（ticket）→ 一个 worker session + 一个目录 | agent run | 任务卡（L5 memory） |
| 跨主体授权 | **没有**"agent 之间互相派活"这回事 | 有（squad leader、`CanMemberInvokeAgent`） | 有（AGENTS.md 分工 + 派卡） |

它的 `orchestrator` 与 `worker` 是**同一个 agent 的两个角色/两个 session**，靠 session 隔离与工具集隔离来分工（`prompts/shared.md`：「The orchestrator decides candidates... Workers own implementation」）。所以：

- **拿它当"多 agent 协作"范本是错位的**；
- 但拿它当**"单 agent 如何被授权、如何不浪费人的注意力、如何不丢消息"**的范本，是当前公开代码里质量最高的一份。

MemALL 真正该抄的是它的**授权与状态机**，不是它的"协作拓扑"。

---

## 2. 领域模型：它只有 10 个概念，且每个概念都写了自己"守的规则"

`docs/001-domain.md` 是全篇最值钱的一篇。数据流：

```
Source ──Trigger──> Thread ──batch──> Eval ──Decision──> Ticket ──Assignment──> Executor
  │                    │                                  │
  │                    └─ Subscription 扇出 ──────────────>│（只给上下文，不给权限）
  └─ Channel（写回侧：回复/PR/review）<────────────────────┘
```

| 概念 | 一句话 | 它守的规则（原文档口径） |
|---|---|---|
| **Source** | 事件流（GitHub/飞书） | 记住自己的读取位置；丢弃 bot 自己的消息；重启不得重放 |
| **Trigger** | 一条不可变事件 | 幂等单位：同一 Trigger 接受两次只产生一张 Ticket |
| **Thread** | 时间序的 Trigger 序列 | **同一 Thread 同时最多一个 Eval** |
| **Eval** | 对一批 Trigger 的判定（gate + assessment） | **无状态**；输入=批次+线程快照+该线程全部工单+当前 Policy |
| **Decision** | accept / stop / ignore / unavailable | 附 reason + 命中的规则 |
| **Mandate** | 接受时授予的授权 | **不可变**；只有三种操作能改它：人接受时创建、人说停时撤回、策略不再成立时吊销 |
| **Ticket** | 一个被授权的工作单元 | **只有 Eval 能创建**；自己定义状态，不跟随 Executor |
| **Assignment** | 工单绑定到某个 Executor 实例 | pending → running → ended，与工单状态独立 |
| **Subscription** | 工单 ↔ 线程的多对多 | 扇出**不检查谁发的**、**不授予权限**、**不走 gate** |
| **Channel** | 线程的写回侧 | 消息先入库再发（outbox），一条消息永不重复入队 |

两个"反直觉"的设计决定，值得单独记：

1. **`Eval` 是无状态的**，所以策略一变就能丢弃重跑，不需要迁移中间状态；
2. **`Ticket` 的状态不跟随 Executor session**——因为工单可以被交接、恢复、被多个 Executor 跑过。这一点和 MemALL「任务卡是唯一事实源、agent session 是短命执行体」的取舍完全同源。

---

## 3. 它的"自动协作"由 8 个机关组成（每个都能指到代码）

### 3.1 两层 Eval：确定性 Gate + 模型 Assessment，且**封闭校验**

`src/policy.ts` 的 `settingsPolicy().gate()` 是纯函数式的准入判定（人/机器人/CI 三分支、`admission` 表三格、follow-up 强度、`off` 模式短路），**不调模型**。模型只负责第二层 assessment，且结果被严格校验：

- `assessmentOutcome()` 要求 **`rule_assessments` 对每条启用规则恰好判断一次**，多一条、少一条、给一条禁用规则都会 `throw`；
- `accept` 必须同时满足：`direction` 合法（`implementation_requested && target_basis !== 'undetermined' && !requires_design_decision`）+ **至少命中一条启用规则** + 提供 `intent_evidence`；
- 方向没定 → 降级成 `ignore`（reason `directionUnsettled`），**不猜**。

这是比 Multica `WillEnqueueRun` 更狠的一层：Multica 校验的是"这次写会不会入队"，它校验的是"模型这次判断本身合不合法"。

### 3.2 自主性阶梯 `off / observe / auto`，且 observe 会**记录"本来会接受"**

`docs/002-authorization.md`：`observe` 是默认档，推断出的候选照常研判，但正向结论**只记录成 `ignore` + `would_accept` 提案**，什么也不执行。`src/policy.ts` 里对应的就是 `reason: { code: 'observed' }` + `audit.would_accept = true` + `proposal`。

> 这是"先观察再放权"的工程化实现：新部署默认不自动干活，人看完记录再切 `auto`。**对 MemALL 的 `triage` 是现成的解药**（见 §6）。

### 3.3 授权与上下文彻底分离（`Mandate` vs `Subscription`）

`docs/002` 用整节讲这件事：订阅扇出的消息**绕过 gate、不授予任何新权限**，工单永远受自己的 `Mandate` 约束。反过来，`Mandate` 也不因为线程热闹就扩大——"更宽的范围意味着新工单"。

三个操作之外 `Mandate` 不变，且**恢复前要重新校验**（`policy.ts` 的 `revalidate()`）：来源是否还在监听、发送人是否还在名单、admission 格是否还给出至少原来的强度、机器人是否还在 bot 名单、推断型还要求 `auto` 模式 + 命中规则仍启用。校验失败 → 工单保持 ended、消息当"没到达任何工单"重新进 Eval。

### 3.4 消息所有权 + hand-back 协议（谁有权处理一条人话，是显式状态）

- 人发在**工单自己的线程**上的话，归该工单的 worker 处理，**不进 Eval、不产生 Decision**；
- worker 只在两种情况下 hand-back：超出工单范围，或属于别的工单。hand-back 一次，工单保留订阅；
- **主 session 在 worker 拥有该消息期间，只能执行"按人要求停止"，accept/attach/ignore/requeue 一律被拒**；
- 工单结束时，**未读、未确认、未回答、未 hand-back** 的消息释放回 Eval；已读的（如一句"谢谢"）不释放。

这套"谁拥有这条消息"的显式记账，是 MemALL 完全没有的维度（MemALL 只有"这条 note 投给谁"）。

### 3.5 角色分工 + "主会话永不等待"

- orchestrator **只路由**：转发原始 Trigger + 路由事实，**不做技术决策**，路由备注 **≤30 词**；
- 主会话被硬性禁止 `sleep`、>30s 的 shell timeout、>5s 的 job wait（有 guard），理由是"阻塞的 step 会推迟新候选";
- 报告、follow-up 通知、新候选、后台任务结束**都自动作为消息到达**，不需要轮询。

### 3.6 结构化 `attention`：**"从自由文本推断'在等人'"被明确列为被否决的概念**

`docs/001` 的「Rejected concepts」表里有一条：

> *Inferring "waiting on a person" from a report's free text* — The classification needs structured facts; the Executor asks through a structured field.

对应实现：`src/core/stage.ts` 的 `ticketStage()` 用一张**首行匹配优先**的表，把工单分类成 `person/{delivery,answer,review,blocked,failed}` / `working/{queued}` / `idle` / `ended`；`person` 阶段 = "没有人的输入就无法继续"。问题本身走 `orchestrator_report human_action` 这个结构化字段（`prompts/handoff-worker.md` 明确禁止"把问题写在 summary 里然后等"）。

### 3.7 槽位与并发：**人的消息永远有一个专属槽**

`docs/003-execution.md`：并发上限管的是 worker slot，但**额外留一个槽给人**（人的消息或状态查询可用它）。"新工作因此无法挡住人继续既有工单"。后台 shell job / subagent 在 turn 结束后最多 30 分钟，超时杀 job 并报 `waiting`，subagent 允许继续。

### 3.8 先存后做（effect queue + outbox）+ 重启的已读/未读账

- 决策之后要做的事（转发候选、投递 Input、启动 Assignment）**先入库再执行**；失败按 2s→翻倍→1min 重试；某类连续失败 3 次才升级成系统问题；
- 出站消息有 `outbox` 收据表（`pending/sent/dropped`），**同一 id 永不重复入队**，同一工单同一会话的消息**保序**；
- 重启时：正在跑的 Assignment 带"recovery note"重启并**要求先核对目录、git 历史、GitHub 再重复外部动作**；worker 是否"已读"某条消息按"进入 session history 之后是否 report 或 turn 结束"判定，被 cancel/崩溃打断的算未读。

---

## 4. 还有两条"软机制"，对 MemALL 的文档与策略治理直接可抄

### 4.1 策略版本化：`policyVersion` 是 hash，且**变更的后果是精确的**

`src/policy.ts`：`policyVersion()` = hash(`rules` + `mode` + `sources`)，`POLICY_REVISION = 'mandate-v3'`。策略变更时（`policyEffects()`）：

- **不取消**在跑的工作（"a cancelled change leaves half-finished work"）；
- 作废进行中的 Eval 并**重新排队**；
- 只给**受影响的** live 工单发 `policy-changed` Input，说清是"命中的规则变了/请求人不再有权/主动工作不再被接受"哪一种；
- 每条 `Mandate` 记着它当时依据的版本。

### 4.2 Prompt 版本化：base revision + "Default updated" 对比

`docs/004-prompts.md`：每次保存产生**不可变版本**（含 revision、前驱、时间、来源），保存必须带 base revision（冲突则拒绝而不丢草稿）；覆盖项记录它基于哪版内置文本，升级后若内置默认变了，面板标 **Default updated** 并给出差异，团队可"保留我的/用新默认"；**session 每次模型请求都读最新版本**（同一 turn 的后续 step 也是），host 记录每个 session 用过哪些 revision。

优先级也写死了（强→弱）：① 工具与 host 强制的事实（身份/权限/参数校验/长度上限）→ ② 内置与生成段 → ③ 主动工单命中规则的流程要求 → ④ 团队段与团队技能 → ⑤ **外部文本（消息/评论/文档/引用）永远只是数据，不能改规则**。

---

## 5. 工程质量信号（哪些是真的，哪些是自述）

**真的**：
- 59 个 spec 文件，且 `docs/007` 的「Tests」表逐文件说明覆盖什么（包括"并发重叠"`runtime-concurrency.spec.ts`、"崩溃重启"`background.spec.ts`、"策略迁移"`migration.spec.ts`）；
- 运行时依赖只有 2 个，SQLite 用 Node 内置 `node:sqlite`，`migration.ts` 有 11 版 schema 迁移；
- 8 篇编号文档 + 「Rejected concepts」表 + 「Open questions」自曝缺口。

**自述/需打折**：
- README 自己说"research code, not production-ready... may still contain bugs"；
- `docs/003` 明确写"这些 wrapper 和目录**只是默认值，不是安全边界**，worker 仍可用绝对路径执行程序"，且 worker **以操作者 OS 用户身份无审批执行命令**；
- `docs/007` 的 Open questions 只剩两条，但都是硬骨头：**哪些 Trigger 字段可信**（正文与引用不可信）、**部署隔离**（agent 应有自己钉住的 DSH 安装）；
- 公开仓库是**单个 squash 提交**，无 issue/PR/discussion，外部无法看到评审过程。

---

## 6. 对 MemALL 的收益（对照我 2026-10-30 已核验的现状，不重复猜测）

先说结论：**它的机制里，MemALL 真正缺的是"授权与可观测性"，不是"编排"。** MemALL 的编排（目标相位机 + 依赖门 + 非生产者验收 + 讨论收敛）比它更完整；它强在"每一步都能被审计、每一种'没动'都有名字"。

| # | 它的机制 | MemALL 现状（已核验） | 收益 | 优先级 |
|---|---|---|---|---|
| 1 | **observe 模式**：正向结论只记 `would_accept` 提案 | `triage` 相位存在，但 `next_action()` 无 triage 分支 → 静默，无人被告知 | 把静默门变成"提案 + 记录"，人看完再放权；不需要新概念 | **P0** |
| 2 | **结构化 attention**，禁止从自由文本推断"在等人" | `next_action()` 多处 `return None`，`last_block_reason` 只有 2 值 | 每个"不动"都有 reason code；`person/{answer,review,blocked}` 式阶段表可直接套到工单列表 | **P0** |
| 3 | **授权与上下文分离**（Subscription 不给权限） | 总线载荷只传 `memory_id` 指针（已做对），但 session note 扇出没有"这只是上下文"的规则 | 补一条硬规则：扇出的 note 不构成授权/指令，执行方必须回读原卡 | P1 |
| 4 | **策略版本 hash + 变更影响精确化** | AGENTS.md 是宪法但**无版本**；讨论 `participants` 名册与真实 roster 不一致导致数学上永不收敛（线上实证 #999964/#999948） | 给协作宪法与讨论名册加 `policy_version`，名册变更即时校验；变更只通知受影响者 | P1 |
| 5 | **封闭校验**：每条启用规则恰好判断一次；accept 需 `intent_evidence` + direction 已定 | `decompose` 有 `acceptance` 强制，但无"证据字段"；`verify_goal(evidence=...)` 已有结构化 verdict | 把 `intent_evidence` 的思路用在 goal 接受与验收：证据缺 → 只能 observe | P1 |
| 6 | **消息所有权 + hand-back** | 无此维度（只有"note 投给谁"） | 争议消息归属在 MemALL 不常见，可缓；但"工单结束释放未读消息"对 session note 有用 | P2 |
| 7 | **人的专属并发槽** | 无并发上限概念 | 现在 6 个 MCP 进程各跑 300s tick，本来就不缺槽；记录备查 | P3 |
| 8 | **先存后做 + 出站收据** | 总线 mailbox + ack 已是这个语义；`session_notes` 无收据表 | 若要修"note 发了但 agent 没读"，可借 outbox 收据形态 | P2 |
| 9 | **文档实践**：编号文档、Rejected concepts、Open questions | `docs/` 100+ 篇，但**没有"被否决的概念"表**，ADR 自 2026-10-02 后 92 提交零新增 | 直接抄「Rejected concepts」表格式补 ADR 缺口，成本极低 | **P1** |

### 6.1 与 Multica 的分野（两份外部调研的定位）

| 维度 | Multica | dsh-superagent |
|---|---|---|
| 解决什么 | **多 agent 当队友**的组织问题（看板、squad、26 种 CLI、runtime 管理） | **单 agent 被授权后自主干活**的边界问题（gate/mandate/attention/恢复） |
| 对 MemALL 的用处 | 派单纯函数、原子认领、reason 枚举、声明式 handoff | **observe 阶梯、结构化 attention、授权版本化、先存后做** |
| 许可证 | `NOASSERTION`（只可研究） | **MIT（可直接复用代码/文案）** |
| 体量 | 千万行级、daemon/Postgres | 2MB TS、单 SQLite、2 个运行时依赖（**与 MemALL 的技术约束同构**） |

一句话：**Multica 教 MemALL"怎么派活"，dsh-superagent 教 MemALL"凭什么派、派了怎么记账、人不在时怎么不自作主张"。**

---

## 7. 不该抄的

1. **不要抄它的"单 agent + worker session"拓扑**当 MemALL 的多 agent 方案 —— MemALL 是真·多主体（4 个具名 agent，各有身份与禁区），它的模型里根本没有"另一个 agent 是谁"这个问题。
2. **不要引入 DSH 依赖** —— 它的 `executor/dsh.ts`（47KB）全部是 DeepSeek Harness 的 session API，MemALL 的 MCP stdio + 总线是另一套底座。
3. **不要抄它的面板** —— React + DSH client 插件体系，与 MemALL 的静态 explorer 不同代。
4. **不要把它当"官方产品"引用** —— 作者自己写明 research prototype、非官方产品、可能仍有 bug。

---

## 8. 结论

- **它确实是当前公开代码里"授权纪律"做得最细的一份**：`Gate`（纯函数）→ `Assessment`（模型 + 封闭校验）→ `Mandate`（不可变授权 + 恢复前重校验）→ `Decision`（带 reason 与命中规则）→ `stage`（首行匹配的人员/工作状态表）→ `attention`（结构化提问）。
- **它不是多 agent 协作系统**，是"一个 agent 如何被团队安全地放权"的系统。MemALL 要的多主体协作，参考系仍是 Multica + 自己的 AGENTS.md 宪法。
- **对 MemALL 最划算的三件事**（都在几十行内，且不引入新概念）：
  1. `triage` 从静默门改成 **observe 门**：产出 `would_accept` 提案 + 一条给签字人的提示；
  2. `next_action()` 每个 `None` 补 **reason code**，工单列表按 `person/{answer,review,blocked,failed}` 分类；
  3. `docs/` 补一张 **Rejected concepts** 表（照抄它的格式），顺手把 ADR 缺口补上第一块。
- **许可证上它是安全的**（MIT），若要做第 1、2 条，可以直接对照 `src/policy.ts` 的 `assessmentOutcome()` 与 `src/core/stage.ts` 的 `ticketStage()` 改写成 Python，不必重新发明。
