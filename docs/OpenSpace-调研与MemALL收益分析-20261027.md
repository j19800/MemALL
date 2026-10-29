# OpenSpace 调研与对 MemALL 的收益分析

> **文档状态**：部分过时 — 范式借鉴仍有效；其中"OpenSpace 无多 agent 协作状态机"一处比较未验证源码，对外话术需改为可得证伪的措辞

> 调研人：WorkBuddy ｜ 日期：2026-10-27
> 对象：GitHub `HKUDS/OpenSpace`（港大 HKUDS 开源，MIT，v2 于 2026-07-17 发布）
> 方法：拉取仓库中英文 README + `openspace/skill_engine/evolver.py` 源码实证，对照 MemALL 现有代码（`goals.py` / `tasks.py` / `task_lifecycle.py` / `agent_round.py`）分析收益。

---

## 0. 一句话结论

**OpenSpace 不是 MemALL 的竞品，而是上游互补层——它做"技能（怎么做）的自进化管理"，MemALL 做"记忆（是什么）+ 目标驱动协作的自进化"。** 它给 MemALL 最值钱的不是代码，而是三个**可借鉴的设计范式**：① 用真实 outcome 给技能/记忆打质量分（而非字符规则）；② `provisional→trusted` 渐进信任；③ 本地优先 + 可选云端社区的双轨变现结构。这三点直接对应我们之前踩过的门禁误伤坑，且能把 MemALL 从"记忆 OS"补成"记忆 + 技能双自进化 OS"。

---

## 1. OpenSpace 是什么（实证，非摘要）

| 维度 | 事实 |
|---|---|
| 定位 | "The Skill Management Layer for AI Agents"——Agent 的**技能管理层** |
| 来源 | 港大 HKUDS，MIT 协议，v2 发布 2026-07-17 |
| 核心命题 | 今天的 agent 有"金鱼记忆"：skill 越堆越多，但弱 skill 和强 skill 看起来一样，坏经验被重复选，进化不可控，共享无信任 |
| 接入 | 一个 MCP server（`openspace-mcp`，支持 stdio / SSE / streamable HTTP），宿主放 2 个 helper skill（`delegate-task`、`skill-discovery`）教 agent 何时用 |
| 兼容宿主 | Claude Code / Codex / Cursor / OpenClaw / nanobot / Hermès 等 |
| 本地优先 | 执行/搜索/进化全本地；云端只做发现与共享，导入才复用，本地信任状态不上传 |
| Benchmark | 同模型 Hy3，Terminal-Bench 2.1：cold 65.2% → warm 78.7%（**自报，非独立验证**） |

### 1.1 四层架构（v2）
1. **Skill Quality Layer（质量层）**：记录 skill 是否 selected / applied / completed / fell-back，追踪 tool reliability，用真实任务行为而非描述判断质量。
2. **Controlled Skill Evolution（受控进化）**：`FIX`（修坏的）/ `DERIVED`（派生更好版）/ `CAPTURED`（从执行捕获新可复用工作流）；新 evolved skill 默认 `provisional`，独立成功使用才晋级 `trusted`，可归因失败降级。
3. **Local-First Skill Hub**：本地 skill folder 运行，云端按 package 组织、显式导入、带 lineage + 质量信号共享。
4. **Agent Harness with Quality Records**：每次执行留下 session / tool result / file change 作为 evidence，供给质量层与进化层。

### 1.2 进化引擎关键设计（`evolver.py` 实证）
- **准入受控**：运行时变更必须走 `TriggerJob → EvidencePacket → DecisionRationale → Admission → staged authoring → validation → commit`，不允许直接 mutation。
- **CAPTURED 严格**：要求执行轨迹 + **独立的后置条件验证**两者皆有；whole-task success 既非必要也非充分。
- **高/低风险字段分离**：`allowed-tools`/`hooks`/`shell`/`model` 等列为 high-risk，自动建议但 `approved=False`，人审后才生效。
- **重试与录音**：apply-retry 循环把错误回灌 LLM；每次进化对话存 `conversations.jsonl` 可回放。
- **信任可观测**：`SkillRecord` 带 `trust_state` / `trust_successes` / `trust_failures`，SQLite 版本 DAG + quality metrics。

---

## 2. 与 MemALL 的关系：互补，不是竞争

| 维度 | OpenSpace | MemALL |
|---|---|---|
| 进化对象 | **技能**（可复用操作程序，"怎么做"） | **记忆**（知识，"是什么"）+ **目标驱动协作**（goals.py） |
| 自进化维度 | skill 质量 + 版本 DAG | L1–L11 蒸馏 + 自进化管道 + goals 验收闭环 |
| 质量信号 | skill outcome + tool reliability | 目前**缺量化质量闭环**（蒸馏结果是一次性，无持续衰减/晋升） |
| 协作 | 单 agent 技能进化，**无多 agent 目标协作状态机** | goals.py 有 intake→…→closed + 非生产者验收（OpenSpace 没有） |
| 共享 | 本地优先 + 云端 skill 社区 | 本地优先，dsh 方针"不上架免费市场、50 星后公开" |
| 执行后端 | shell/gui/mcp/web 多后端 + WhatsApp/Feishu 通道 | 记忆 OS，不实现执行后端 |

**结论**：MemALL **不该**去抄 OpenSpace 的执行后端（那是重复造轮子）。真正该借鉴的是它的**质量与信任范式**，补上 MemALL 缺的"技能层 + 量化质量"。

---

## 3. 给 MemALL 的具体收益（按价值排序）

### 收益 1：把"技能"提升为一等公民（补最大的结构缺）
- **现状**：MemALL 把"怎么做"和"是什么"都塞进 L1–L11 记忆，没有独立的技能生命周期。dsh 文档也说要"把成功模式固化"，但用的是"记忆蒸馏"——会被误当成普通知识。
- **借鉴**：引入**技能层**——`goals.py` 里 `verify_goal` 通过（生产方不自验）的流程，沉淀为可复用 **skill**，带质量分。这与 OpenSpace 的 `CAPTURED` 同构：执行成功 + 独立验证 → 捕获为 skill。
- **收益**：MemALL 从"记忆 OS"升级为"记忆 + 技能双自进化 OS"，直接区别于 7aos 等同类的纯记忆方案。

### 收益 2：质量量化层（最该抄的设计）
- **现状**：我们之前的门禁用"4-gram 高频"字符规则抓 AI 固化表达，**误伤正常叙事**（37 章几乎全红）。这正是"用字符规则而非证据"的反面教材。
- **借鉴**：OpenSpace 的 **skill outcome 量化**（selected/applied/completed/fell-back）+ `provisional→trusted` 渐进信任。
- **收益**：给记忆/技能加**效用评分 + 质量衰减 + 晋升/退役机制**。新记忆默认 `provisional`，真实被复用且有效才晋升 `trusted`，可归因失效降级。用"证据 + 可枚举"替代"字符规则"，彻底规避门禁误伤。

### 收益 3：`FIX / DERIVED / CAPTURED` 演化模型
- **借鉴**：把 MemALL 的自进化管道对齐到三态：失效记忆/技能走 `FIX`，验证有效的走 `DERIVED`（派生专门版），新工作流走 `CAPTURED`（需独立验证）。
- **收益**：让"自进化"可控、可审计、可回放（OpenSpace 把每次进化存 `conversations.jsonl`）。呼应我们之前 MCP 审查要的"审计 hook + 幂等"。

### 收益 4：本地优先 + 可选云端社区（商业化路径）
- **借鉴**：OpenSpace 的"本地执行永不上云，云端只做 package 化浏览，导入才复用，本地信任态不上传"——与 dsh"本地优先、50 星后再公开"方针一致。
- **收益**：MemALL 可设计"**记忆/技能市场**"作为变现层：本地免费、社区/企业版可选上云。这是清晰的双轨商业化结构。

### 收益 5：执行轨迹留痕 → 质量证据（feed 验收闭环）
- **借鉴**：OpenSpace 的 Harness 把每次执行变 evidence。
- **收益**：MemALL 的 `goals.py verify_goal` 已产出验收结论，但**没把"执行/verify 轨迹"留存为可量化证据**。把 goals 的轨迹存为 evidence，喂给收益 2 的质量层，形成"执行→验收→质量分→晋升"闭环。

### 收益 6：导入第三方前的 `check_skill_safety`
- **借鉴**：OpenSpace 加载 skill 前过 `check_skill_safety`（拦截 prompt injection / credential exfiltration）+ 沙箱。
- **收益**：若 MemALL 做记忆/技能市场，第三方导入前必须有等价检查。MemALL 已做 SQL 注入/API 绑定/插件白名单加固，可复用其模式扩展到"导入内容安全扫描"。

### 收益 7：onboarding 模式（2 个元技能）
- **借鉴**：OpenSpace 只放 `delegate-task` + `skill-discovery` 两个 skill 教 agent 何时用，零额外 prompt。
- **收益**：MemALL MCP 接入也可借鉴——放 2 个"元技能"教宿主 agent 何时调 `memall_write/read/system`，降低接入摩擦（我们现在靠文档，摩擦大）。

---

## 4. 给 MemALL 的落地建议（按性价比）

| 序 | 动作 | 借鉴自 | 工作量 | 收益 |
|---|---|---|---|---|
| 1 | 给记忆/技能加效用评分 + `provisional→trusted` 渐进信任 | 质量层 + 渐进信任 | 中 | 根治门禁误伤，质量可量化 |
| 2 | `goals.py` 验证通过的流程 → 沉淀为 skill（技能层） | CAPTURED | 中 | 记忆+技能双自进化，差异化 |
| 3 | goals 执行/verify 轨迹存为 evidence，feed 质量层 | Harness 留痕 | 小 | 闭环：执行→验收→质量分 |
| 4 | 设计"本地优先 + 可选云端记忆/技能市场"双轨 | Local-First Hub | 大 | 商业化路径 |
| 5 | 若做市场，加 `check_skill_safety` 等价物 | 安全层 | 中 | 第三方导入安全 |
| 6 | MCP 接入放 2 个"元技能"做 onboarding | host_skills | 小 | 降低接入摩擦 |

**优先级**：1、2、3 是"用最小改动补最大短板"，应优先；4、5 是商业化，可后置。

---

## 5. 风险与注意

- **别误当竞品去抄执行后端**：OpenSpace 的 shell/gui/mcp/web 执行后端、WhatsApp/Feishu 通道，MemALL 不该重复实现。MemALL 是记忆 OS，执行交给宿主 agent。
- **Benchmark 自报**：cold→warm 65.2%→78.7% 是 HKUDS 自家测量，非独立公开基准，宣传时谨慎。
- **多 agent 协作是 MemALL 独有优势**：OpenSpace 没有 goals / 目标驱动 / 非生产者验收的状态机。这是 MemALL 相对 OpenSpace 的差异化卖点，对外应突出，不要被 OpenSpace 的"自进化"叙事带偏。

---

## 6. 对外话术建议（呼应 dsh §7 独特处）

> MemALL 不是另一个 skill 库。我们在记忆 OS 之上，用 OpenSpace 式的质量量化与渐进信任范式，补上了"技能自进化"层；同时保留 OpenSpace 没有的**多 Agent 目标驱动协作 + 非生产者验收闭环**。记忆即总线、验收结论蒸馏进 L9/L10、跨会话身份可追溯——这是 MemALL 的三条护城河。
