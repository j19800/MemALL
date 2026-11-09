# Multica 深度调研 — 对 MemALL 多 Agent 协作的收益分析（2026-10-27）

> **文档状态**：历史留档 — 外部项目调研，结论已并入《改进方案·定稿》

> 调研方法：GitHub MCP 拉取 `multica-ai/multica` 主仓库 README + `VISION.md`，并实拉其核心生态方法论 `xiaohei-info/oh-my-multica`（MIT）README。主项目 53k+ 星、Go 编写、source-available（Apache-2.0 + 附加条款，**非标准开源**）；oh-my-multica 为 MIT，可自由参考其方法论。
>
> 背景：MemALL 已有 `pipeline/goals.py`（目标驱动状态机 + 非生产者验收 + `goal_tick` 决策循环，但 `goal_tick` 尚未接入运行时）。本报告聚焦"Multica 这套多 agent 协作范式能给 MemALL 带来什么"。

---

## 一、Multica 是什么（实证）

**定位（VISION.md 原话）："Make humans and AI agents work as one team" —— 成为人机协作工作的**「系统记录层 + 行动层」(system of record and action)**。**

它不是模型提供商，而是**agent 工作区（Kanban）**：把 Claude Code / Codex / Cursor / Kimi / OpenCode 等 26 种 agent CLI 当作"看板上的正式成员"，指派 issue → agent 自领任务 → 在受控 runtime 上工作 → 评论进展 → 交回 review。

| 维度 | Multica 实现 |
|---|---|
| **核心交互** | Issue/看板/状态流转（刻意采用"低频干预"设计，避免 IM 式 context 爆炸） |
| **Agents as teammates** | 每个 agent 有名字、provider、runtime，出现在成员列表，可被 @、指派、评论 |
| **Squads** | agent + 人编入一队，leader 自动路由工作 |
| **Skills** | 已解决的问题沉淀为可复用 playbook，全队 agent 复用 |
| **Autopilots** | cron 定时跑站会/审计/报告 |
| **Execution log** | 每个工具调用/命令/错误时间戳化，可回放 |
| **Review gates** | 工作落入 review 列，人决定是否合入 |
| **Usage analytics** | 按 agent / issue 统计 token 与成本 |
| **Runtime** | 本地 daemon 执行，代码不出本机 |
| **Self-host** | Docker Compose / Helm，数据主权在己 |
| **IM 连接器** | Slack / 飞书 / 钉钉 / 企业微信 / Telegram（触发+跟进） |
| **架构** | Go 后端(Chi+WS) + PostgreSQL17 + Next.js + Electron + Expo；agent daemon 跑在用户机器上 |

**关键判断**：Multica 强在**执行协作与过程留痕**（谁做了什么、花了什么、审查门、回放），弱在**持久记忆与知识自进化**（它是"系统记录层"但记录的是执行事件，不是跨会话的知识沉淀）。这恰好与 MemALL 的强项（记忆/知识图谱）互补。

---

## 二、最值钱的发现：oh-my-multica 的方法论

主项目 Multica 只解决"agent 怎么协作执行"，**不定义完整软件交付过程**（需求怎么进设计、验收标准怎么形成、多 agent 如何按依赖并行、什么证据证明实现正确、谁独立审查、何时可合入）。`oh-my-multica`（MIT）在 Multica 之上补了这层**交付控制面**，其核心是：

> **Dynamic planning, deterministic execution（动态规划、确定性执行）**
> **Loop Engineering × Harness Engineering（循环工程 × 约束工程）**

### 2.1 确定性 Loop（对比我们 `goal_tick`）
- "delivery loop owns control；planning/dev/review/acceptance 是有限任务执行器"——防止监督 agent 漂移/遗忘/提前停。
- 循环：`reconcile → collect_results → evidence/delivery gates → ready nodes → dispatch → converged / exit 20`
- **状态持久化在 manifest + 平台 + Git；重跑从 reconciliation 开始；失败返回 exit 20（带精确下一步命令）**——不依赖监督 agent 记得继续，context 重置也不丢交付状态。

### 2.2 契约 DAG（对比我们 `goals.py` children + depends_on_idx）
- 每个节点声明：依赖 / owner / reviewer / acceptance / verification commands / integration gates。
- 并行取决于**显式边界**而非运气。

### 2.3 独立质量判定（对比我们 `verify_goal` 非生产者验收）
> **"Workers cannot approve their own work; Reviewers reproduce independently, and the Acceptor runs final flow-based acceptance."**

- 作者声明不被当作事实；Worker 与 Reviewer 分离，Acceptor 做最终端到端验收。
- 结构化证据：验证/审查/验收结果有 schema + 提交门，每个 "pass" 机器可查、可溯源。

### 2.4 模型按决策影响分配（成本纪律）
- 设计/规划用旗舰模型（调用少但错误被下游放大）；审查/验收用次级旗舰（保独立判断）；开发/测试用低成本模型（量大，靠契约+验证门约束，不靠模型能力兜底）。

---

## 三、机制级映射：Multica/oh-my-multica → MemALL

| Multica/oh-my-multica 机制 | MemALL 现状 | 动作 / 收益 |
|---|---|---|
| **确定性 Loop 拥有控制权**（规划/开发/审查/验收是可替换执行器） | `goals.py:443 next_action` + `:482 goal_tick` 已写好，但**全仓无调用点**（`agent_round` 没调它） | **P0：给 `agent_round` 通上 `goal_tick`**。oh-my-multica 用生产级实践证明这条"DAG + 确定性 tick"路线正确、可收敛、可恢复 |
| **契约 DAG**（节点声明依赖/owner/reviewer/acceptance/验证命令） | `goals.py` children + `depends_on_idx` 已有；`tasks.py:84-114` 依赖门已落地 | 对齐：把"验证命令/集成门"补进 goal/子任务契约 |
| **Worker 不能自验 + Reviewer 独立复现 + Acceptor 终验** | `goals.py:372 verify_goal` 已强制"生产者不得验收" + `returned` 回退 | **我们已领先 Multica 核心层**（核心只有 review gate，无独立 reviewer/acceptor 纪律）。正式化为"reviewer/acceptor"角色语义 |
| **结构化证据（schema + 提交门，每个 pass 可机器查）** | 有 `pipeline_events`，但**无 schema 化 evidence**，验收靠文本 | P1：给 `verify_goal` 加结构化 evidence 字段（验证命令输出/CI 状态/审查报告），让验收机器可判 |
| **状态持久化 + reconciliation + idempotent tick**（重跑从对账开始） | `pipeline` 有状态但**无幂等 reconciliation**；前次报告 P0-1 指 lightweight pipeline 跳过 L9/L10 | P1：goal_tick 设计成 idempotent + reconcile，避免重复推进 |
| **Skills = 已解决问题沉淀为可复用 playbook** | **缺失技能层**（OpenSpace/Paperclip 报告已列为收益1+2） | P0：建轻量技能层 + 质量量化（provisional→trusted） |
| **低频干预（issue/看板/状态流转，避免 IM context 爆炸）** | dsh 文档 flagged "group chat 4–5人收敛问题" | **直接解药**：把多 agent 协作锚定在 goal/issue 而非群聊；我们 `goals.py` 天然是低频干预模型 |
| **Review gates（工作落 review 列，人决定合入）** | `goals.py` 有 verifying/returned，但**无显式"人类审批门"触发** | P2：在 goal 终验加可选人工审批门（对齐 Multica review gate） |
| **Usage analytics（按 agent/issue 计 token/成本）** | 无 | P2：若做企业版再考虑；当前非核心 |
| **Squads（leader 路由）** | `agent_round` 是扁平提醒，无 leader 路由 | P2：可在 goal 层加"leader agent 分配子任务" |
| **Self-host + 本地 runtime + 代码不出本机** | MemALL 本地优先，一致 | 战略对齐，无需改动 |
| **BYO agent CLI（26 种，厂商中立）** | MemALL 走 MCP（同样厂商中立） | 战略对齐，范式一致 |

---

## 四、对 MemALL 的核心收益（按优先级）

| 优先级 | 借鉴点 | 落到 MemALL | 对应已知 gap |
|---|---|---|---|
| **P0** | 确定性 Loop 控制（oh-my-multica） | **给 `agent_round` 通上 `goal_tick`**，让目标驱动协作真正自动运行、可收敛、可恢复 | 前次深度分析唯一硬缺口 |
| **P0** | Skills 复用 playbook | 建轻量技能层 + 质量量化（provisional→trusted），根治字符门禁误伤 | OpenSpace/Paperclip 报告收益 |
| **P1** | 结构化证据 + 独立验收 | 给 `verify_goal` 加 schema 化 evidence（验证命令/CI/审查报告），Worker≠Reviewer/Acceptor 正式化 | 前次 P1-16（分类保护）+ 验收闭合 |
| **P1** | 状态持久化 + 幂等 reconciliation | goal_tick 做成 idempotent + reconcile，避免重复推进/丢失 | 前次 P0-1 |
| **P2** | 低频干预（issue/goal 而非群聊） | 把协作锚定在 goals，规避 group chat 收敛问题 | dsh 文档 group chat 维度 |
| **P2** | Review gates / Squads / Usage | 可选人工审批门、leader 路由、成本统计 | 非核心，企业版再考虑 |

---

## 五、战略判断

1. **MemALL 在"独立验收 + 非生产者验收"上已经领先 Multica 核心层**——`verify_goal` 强制生产者不得验收是 Multica 核心没有的纪律（它要靠 oh-my-multica 补）。这说明我们 `goals.py` 的设计方向是对的，**不是要抄 Multica，而是把已有设计通电 + 补齐证据层**。
2. **Multica 是"系统记录层"（执行事件），MemALL 应是"知识记录层"（跨会话记忆/知识图谱）**——两者互补。MemALL 的差异化护城河 = 持久记忆（强项）+ `goals.py` 目标驱动协作（已建待通电），而不是去重建 Multica 的 Kanban 控制面。
3. **oh-my-multica 的方法论（Loop × Harness Engineering）是我们可以直接采用的元框架**：把"目标/上下文/工具/约束/验证/审查"编码进环境（Harness），让确定性 Loop 消费信号决定前进/返工/停止/请求人审。这正是 `goal_tick` 该扮演的角色。
4. **低频干预设计**直接回应了 dsh 文档提出的"group chat 4–5 人收敛问题"——多 agent 协作应锚定在 goal/issue 状态机，而非 IM 群聊。

---

## 六、风险与边界

- **Multica 是 source-available（Apache-2.0 + 附加条款，非标准 OSS）**：可研究其设计，**代码不可直接复用/改作商用**需审 LICENSE。oh-my-multica 是 MIT，方法论可自由参考。
- **不要重建控制面**：Multica 是 Go/Next.js/Postgres 重架构；MemALL 是记忆 OS，只借**契约 DAG / 证据 / 验收门 / 确定性 Loop** 的纪律，不重造 Kanban。
- **记忆才是护城河**：Multica 记录执行事件但不沉淀知识；我们若丢记忆去做执行编排，就失去了差异化。
- star 数（53k）来自其"人机同权"叙事与生态，而非记忆能力——对外对标时强调"MemALL = 记忆层 + 目标驱动协作"，避免被误读为又一个 agent 看板。

---

## 七、下一步（衔接前三次分析）

1. **立即**：`agent_round()` 末尾追加 `goal_tick(conn)`，把 `goals.py` 通电（确定性 Loop）。→ 前次深度分析已交办，本次进一步用 oh-my-multica 证明其生产级正确性。
2. **紧随**：`verify_goal` 加结构化 evidence 字段 + 正式化 Worker/Reviewer/Acceptor 三角色（我们已有非生产者验收，只需补 evidence 与角色语义）。
3. **紧随**：立项技能层 + 质量量化（provisional→trusted）。
4. **对齐**：goal_tick 做成 idempotent + reconcile，吸收 oh-my-multica 的"重跑从对账开始"韧性。

> 结论：**Multica 验证了"人机同权 + 目标驱动 + 低频干预"的协作范式，oh-my-multica 用生产级实践证明"动态规划 + 确定性执行 + 独立验收"是正确路线。MemALL 的 `goals.py` 设计已契合该路线（甚至验收纪律更严），最该做的是把 `goal_tick` 通电并补齐结构化证据层，而非推翻重来。**
