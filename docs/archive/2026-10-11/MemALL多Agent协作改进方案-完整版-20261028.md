# MemALL 多 Agent 协作改进方案（完整版 v1）

> **文档状态**：已被取代 — opencode 版；已合并进 -定稿-20261029，后续只读定稿

> 编写时间：2026-10-28（GMT+8）
> 依据：对 `E:\MemALL\src` 当前代码的逐文件核查 + 四轮竞品调研（dsh 多 Agent 梳理文档、OpenSpace、GitHub 多 Agent 项目、Multica 代码深度分析）。
> 配套调研报告：`深度分析-多Agent协作梳理-20261027.md`、`OpenSpace-调研与MemALL收益分析-20261027.md`、`GitHub多Agent协作项目调研-20261027.md`、`Multica-调研与MemALL多Agent协作收益-20261027.md`、`Multica-代码深度分析-20261028.md`。

---

## 0. 重要更正（相对 2026-10-27 分析的偏差）

先前分析基于更早的代码快照，得出两个已**过时**结论，本方案先予以纠正：

| 先前判断（过时） | 2026-10-28 代码核查实情 |
|---|---|
| "goal_tick 未接入运行时，协作是半自动" | `fallback.py::deliver_goal_prompts()`（:148）已把 `goal_tick()` + `push_session_note` 会话便签 + `wake_for_actions` 串成完整驱动链，并在 `run_fallback_once()`（:210）中**每个 fallback tick 都执行**。 |
| "MCP 层未暴露 goal_tick / 目标生命周期" | 7 个目标工具**全部已注册**：`tools/__init__.py:83-87,209-211,223`；输入模型 `validator.py:58,92`；`goal_status` 已进 action 枚举。`goals_handlers.py` 七件套齐全。 |

**结论**：目标驱动协作的"引擎 + MCP 接口 + 安全唤醒"三大件**都已存在**。真正的问题不是"没建"，而是：
1. **驱动频率太低**——目标驱动只在 **6h** 的 pipeline tick 跑（`scheduler.py:79`，`INTERVAL_PIPELINE=21600`），没进 **300s** 的心跳；
2. **缺"技能层 + 质量量化"**——这是所有竞品调研共同指向的、MemALL 当前**唯一真正缺失的半壁江山**（记忆自进化有了，技能自进化没有）。

因此本方案把"通电"降为低风险小修（P0-1），把"技能层 + 质量量化"升为头号产品补齐项（P0-2）。

---

## 1. 当前能力盘点（代码实证，逐项有出处）

### 1.1 已具备的目标驱动协作引擎（核心护城河）
| 能力 | 实现位置 | 说明 |
|---|---|---|
| 目标状态机 intake→…→closed | `pipeline/goals.py` 模块 docstring :31-37 | 7 阶段确定性状态机，唯一两个需 LLM 判断的是 decompose / deliberate |
| 创建目标 + 验收标准 | `goals.py:128 create_goal` | 写入 L5/goal 记忆，强制 owner/verifier |
| 拆解 + 依赖图 + 指派 | `goals.py:165 decompose` | 校验每个 child 必须有 acceptance 和 depends_on_idx，owner 专属 |
| 论证关联 | `goals.py:235 attach_discussion` | 进入 deliberating |
| 落任务卡（仅论证收敛后） | `goals.py:265 finalize` | 校验 discussion 状态=converged 才物化子任务卡 |
| 提交验收 | `goals.py:336 request_verification` | 强校验"所有子任务已交付"才进 verifying |
| **独立验收（生产者不自验）** | `goals.py:372 verify_goal` | :394 生产方（含 owner）**禁止**验收自己的交付——比 Multica 核心层更强 |
| 下一步决策（确定性，非 LLM） | `goals.py:443 next_action` | 从存储状态算出"谁该做什么" |
| 批量驱动（幂等） | `goals.py:482 goal_tick` | :485 明确幂等；无状态变化两次运行结果一致 |
| 会话便签推送（去重） | `fallback.py:186 push_session_note(dedup_key=...)` | 同一 goal+action 不重复入队 |
| 安全唤醒子系统 | `pipeline/agent_waker.py` | :71 默认 `enabled=False`；:75 默认 `dry_run=True`；argv-only 绝不 shell；disabled-agent 不唤醒；per-(agent,goal,action) 冷却 + 每小时上限 + 超时 kill |

### 1.2 已具备但未启用的能力
- **agent_waker**：默认关闭。开启前绝对安全（dry_run 先打印 argv）。这是"把半自动变全自动"的开关，属**运维决策**，不是代码缺口。

### 1.3 已具备的多 agent 通信/感知层
- 讨论系统 + 收敛 `convergence.py`（论证收敛机制）
- `agent_round()`（:159）每 300s 给活跃 agent 推讨论/任务提醒（pull 模型）
- `identities` 表 + 写拒绝名单 `denylist.py`（disabled_agents）
- 通知栏 live query（每次工具响应实时查）

---

## 2. 真正剩余的缺口（按价值/风险排序）

| 编号 | 缺口 | 严重度 | 调研依据 | 现状出处 |
|---|---|---|---|---|
| **G1** | 目标驱动仅在 6h pipeline tick 跑，未进入 300s 心跳 → 协作滞后最长 6h | 高 | Clawith/Paperclip 验证"通电"方向；Multica reconcile 验证幂等可行 | `scheduler.py:79` vs `:110-126` |
| **G2** | **无技能层 + 质量量化**（provisional→trusted）—— 最大差异化缺口 | 高 | OpenSpace、hermes、Paperclip Skill Studio、Multica 极简技能模型 | 全仓无 skill 表/模块 |
| **G3** | `verify_goal` 证据为非结构化自由文本（note[:2000]），无 schema | 中 | oh-my-multica 结构化证据 + 提交门 | `goals.py:399-400` |
| **G4** | 裸 L5 任务卡 vs 目标子任务卡：验收/状态推进语义不统一 | 中 | 内部一致性；早前 2026-10-20 审查 P1-16（classify 覆盖 category） | `task_lifecycle.py`、`classify.py` |
| **G5** | 自动推进的可归因/可回退性（triage / human-in-loop）未显式化 | 中 | Multica triage_guard + attribution_stamp（fail-closed 绑责任人） | `goals.py` 无 triage 态 |
| **G6** | `identities` 表被 20+ 测试身份污染 → `get_active_agents` 噪音 | 低（运营） | 早前审查 P2 | `agent_round.py:32` |
| **G7** | 缺目标吞吐度量（phase 时长/返工率/验收通过率）→ 不可观测 | 低 | Multica usage analytics | 无埋点 |

---

## 3. 改进方案（P0 / P1 / P2，逐条可执行）

### P0-1｜把目标驱动接入 300s 心跳（低风险、高回报的"通电"）
- **改什么**：在 `scheduler.py:110-126` 的 `INTERVAL_PROXY_SESSION`（300s）分支内，追加调用 `deliver_goal_prompts()`；保留 `run_fallback_once`（含目标驱动）作为 6h 兜底。
- **在哪**：`scheduler.py` 心跳分支（紧接 `agent_round()` 之后）。
- **为什么安全**：
  - `goal_tick()` 幂等（`goals.py:485`）；
  - `push_session_note` 按 `goal:goal_id:action` 去重（`fallback.py:185-186`）——重复 tick 不堆叠；
  - `wake_for_actions` 自带冷却/每小时上限/超时 kill，且 `agents.wake.enabled` 默认 False（`agent_waker.py:71`）——心跳不会触发 agent 进程，除非运维显式开启。
- **验收**：
  - 单测：让一个 goal 停在 `assigned` 态，手动调 `deliver_goal_prompts()` 两次，断言 `delivered` 第二次为 0（去重生效）；
  - 集成：开启 scheduler，制造一个待办目标，断言 ≤300s 内该 agent 的会话便签里出现对应 `next_action` 提示。
- **工时**：≈0.5 人天（含测试 + 配置项 `scheduler.goal_tick_on_heartbeat` 默认 true）。
- **风险**：极低。

### P0-2｜技能层 MVP + 质量量化（根治"字符门禁误伤"，补齐双自进化 OS）
> 这是所有竞品调研的**最大公约数**，也是 MemALL 当前唯一的"半壁江山缺口"。

- **设计**（直接借鉴 Multica 极简技能模型 + OpenSpace 质量范式）：
  1. 新增 `skill/` 模块 + `skills` 表（`id, name, description, frontmatter_hash, version, status, quality_score, usage_count, created_at, updated_at`）。
  2. **SKILL.md frontmatter**（抄 Multica `skill/frontmatter.go`）：`name` / `description` / `version` + `status ∈ {provisional, trusted}`；`frontmatter_hash` 按内容锁定（抄 `skills-lock.json` 的 commit-hash 锁思路）。
  3. **沉淀入口**：`verify_goal` 验收通过（passed）后，提供"沉淀为 skill"动作——把该 goal 的 `plan` + 执行轨迹 + 验收证据固化为一条 `provisional` skill。
  4. **质量量化 + 渐进信任**（抄 OpenSpace `evolver.py` 的 provisional→trusted）：
     - 每次 skill 被真实使用并产出正向 outcome（对应 MemALL 的 verify 通过 / 下游目标达标），`quality_score += w`，`usage_count += 1`；
     - `quality_score` 与 `usage_count` 达阈值后自动晋级 `trusted`；失败用例触发降级 / 回退 provisional。
  5. **检索接入**：skill 描述进 FTS5/向量召回，agent 在执行前可被推荐"已有可复用 playbook"。
- **为什么根治门禁误伤**：此前"4-gram 字符规则门禁"误伤正常叙事，根因是**用字符规则冒充质量判据**。质量量化改用**真实 outcome 打分 + provisional→trusted 渐进信任**，与 OpenSpace 范式一致，天然不误伤。
- **验收**：
  - 单测：构造一条 provisional skill，模拟 5 次成功使用，断言晋级 trusted；
  - 集成：一个 goal 验收通过后调用沉淀接口，断言 `skills` 表新增一行 status=provisional。
- **工时**：≈3 人天（表迁移 + 模块 + 沉淀/晋级逻辑 + 召回接入 + 测试）。
- **风险**：中（需定义"正向 outcome"信号源；建议先用 verify 结果作为信号，避免引入新噪声）。

### P1-1｜`verify_goal` 结构化证据
- **改什么**：在 `goals.py:399` 的 `verdict` 之外，要求/可选录入 `evidence` schema：`{checks:[{name,passed}], command, output_tail, verifier_agent, at}`。
- **在哪**：`verify_goal` 入参新增 `evidence`（可空，向后兼容）；`handle_goal_verify`（`goals_handlers.py:56`）透传。
- **依据**：oh-my-multica "结构化证据 + 提交门，每个 pass 机器可查"。
- **验收**：传入结构化 evidence 后，`goal_status` 返回体含 `evidence` 且可机器校验。
- **工时**：≈0.5 人天。

### P1-2｜统一验收/状态语义（裸任务卡 vs 目标子任务）
- **改什么**：让裸 `category='task'` 的任务卡与目标子任务卡共用同一套 `advance` / `verify` 入口；消除 `classify.py` 覆盖 `category` 导致状态机错配（早前 P1-16）。
- **在哪**：`task_lifecycle.py`（统一状态推进）、`classify.py:297-303`（保护 category 不被误覆盖）。
- **验收**：构造一个裸 task 卡和一个 goal child，断言二者 status 推进路径一致；classify 不再把 task 误改 testing/fix。
- **工时**：≈1 人天。

### P1-3｜自动推进可归因 + 可回退
- **改什么**：`goal_tick` / `next_action` 产出的每个 action 记录 `who/why/at`，并提供 `revert_action(goal_id, action)` 回退接口；新增可选 `triage` 态区分"系统推导的下一步"与"人显式指派"。
- **在哪**：`goals.py` 的 `phase_history` 已记录 phase 变迁（:160 等），扩展为 action 级审计；`agent_waker` 的唤醒也应记入 action 审计。
- **依据**：Multica `attribution_stamp`（每个 run 绑责任人、fail-closed）+ `triage_guard`（Derived 在 triage 被拒、Named 放行）。
- **验收**：每条自动推进在 `phase_history` 可见触发源与可逆入口。
- **工时**：≈1 人天。

### P2-1｜`identities` 表治理
- **改什么**：清理 20+ 测试/审计身份；给 identities 加 `created_by` / `source` 字段；`get_active_agents`（`agent_round.py:32`）默认只返回 `source='production'` 或显式启用的身份。
- **验收**：`get_active_agents()` 返回数显著下降且不含测试身份。
- **工时**：≈0.5 人天。

### P2-2｜目标吞吐度量 + 仪表盘
- **改什么**：在 `phase_history` 基础上，计算每 goal 的 phase 停留时长、返工率（returned 次数）、验收通过率；输出到 dashboard 插件。
- **依据**：Multica usage analytics。
- **工时**：≈1 人天。

### P2-3｜触发面扩展（on_message / webhook 替代群聊）
- **改什么**：在 `agent_round` 的 pull 模型之外，增加事件驱动触发（新记忆到达 / 外部 webhook）直接调 `deliver_goal_prompts`。
- **依据**：dsh 文档 group chat 4–5 人收敛问题 → 用"低频干预（goal/issue）而非群聊"化解。
- **工时**：≈1 人天。

---

## 4. 实施路线图（分批 + 依赖 + 工时）

| 批次 | 内容 | 依赖 | 工时 | 价值 |
|---|---|---|---|---|
| **批次 1（止血+通电）** | P0-1（心跳接目标驱动） | 无 | 0.5d | 协作滞后 6h→5min |
| **批次 2（补齐半壁江山）** | P0-2（技能层 + 质量量化） | 无 | 3d | 记忆+技能双自进化 OS；根治门禁误伤 |
| **批次 3（验收闭环加固）** | P1-1 + P1-2 + P1-3 | P0-2 | 2.5d | 证据可机器校验；语义统一；可归因可回退 |
| **批次 4（运营与可观测）** | P2-1 + P2-2 + P2-3 | 批次 1/3 | 2.5d | 身份干净；吞吐可视；事件触发 |

**合计 ≈ 8.5 人天**（不含联调与回归）。批次 1 与批次 2 可并行启动。

---

## 5. 验收标准总表（每条可测）

| 编号 | 验收口径 |
|---|---|
| P0-1 | 目标停滞后 ≤300s 出现会话便签；重复 tick 不堆叠；wake 默认不触发进程 |
| P0-2 | goal 验收通过可沉淀 provisional skill；5 次成功使用晋级 trusted；skill 可被召回 |
| P1-1 | verify 支持结构化 evidence 且机器可校验 |
| P1-2 | 裸 task 卡与 goal child 状态推进路径一致；classify 不再误改 category |
| P1-3 | 每条自动推进可溯触发源且可回退 |
| P2-1 | `get_active_agents` 不含测试身份 |
| P2-2 | 仪表盘展示 phase 时长/返工率/通过率 |
| P2-3 | 新记忆/外部 webhook 可即时驱动目标推进 |

---

## 6. 风险与边界（务必遵守）

1. **Multica 是 source-available（Apache-2.0 + 附加条款），非标准 OSS**——只可研究设计范式，**禁止直接复用其 Go/Next.js/Postgres 控制面代码**。本方案仅借其"确定性派单 / 行级原子认领 / 声明式 workflow / triage / 极简技能模型"的**纪律**，全部用 MemALL 既有 Python + SQLite 实现。
2. **OpenSpace 的 benchmark（cold 65.2%→warm 78.7%）为自报、非独立验证**——对外口径谨慎，不写入营销材料。
3. **记忆层是 MemALL 护城河，绝不外包/弱化**——P0-2 的技能层是"补强"，不是"转向执行后端"。不要去抄 Multica 的 Kanban/预算/Org chart 重活。
4. **`agent_waker` 默认关闭是安全基线**——P0-1 接入心跳后，wake 仍受 `enabled=False` 保护；任何开启 wake 的部署必须先在 staging 跑 `dry_run=True` 确认 argv。
5. **不要另写第二套状态机**——`goals.py` 已完备，P0-2/P1 系列都是在其上"加字段 / 接心跳 / 沉淀技能"，不得推倒重来（早前 2026-10-27 分析已指出此坑）。

---

## 7. 战略收益（一句话）

把"目标已在跑、技能尚空缺"补齐后，MemALL 将成为**唯一同时具备「持久记忆自进化 + 目标驱动多 Agent 协作 + 技能质量量化渐进信任」三者**的开源记忆 OS——这正是 hermes（257k，无团队协作）、Paperclip（107k，Memory 仍 🟡）、Multica（53k，无知识层）都没做满的空档，是 MemALL 的差异化护城河。

---

### 附：本方案可直接交办的 action items（建议给 dsh）
1. `[P0-1]` scheduler 心跳接入 `deliver_goal_prompts`（0.5d）
2. `[P0-2]` 新增 skill 模块 + skills 表 + 沉淀/晋级逻辑（3d）
3. `[P1-1]` verify_goal 结构化 evidence（0.5d）
4. `[P1-2]` 统一裸 task 卡与 goal child 验收语义（1d）
5. `[P1-3]` 自动推进可归因 + 可回退（1d）
6. `[P2-1/2/3]` 身份治理 + 吞吐度量 + 事件触发（2.5d）
