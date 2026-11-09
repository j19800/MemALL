# GitHub 多 Agent 协作项目调研 — 对 MemALL 的借鉴价值（2026-10-27）

> **文档状态**：历史留档 — 外部项目调研，结论已并入《MemALL多Agent协作改进方案·定稿》

> 调研方法：GitHub MCP `search_repositories`（关键词：multi-agent collaboration / agent memory / self-improving agent / agent orchestration）+ WebSearch 交叉验证。对最相关的 4 个仓库（hermes-agent / Clawith / Paperclip / Agent-MCP）实拉 README 做实证，而非只看摘要。
>
> 背景：MemALL 已有 `pipeline/goals.py`（目标驱动多 agent 状态机 + 非生产者验收 + `goal_tick` 决策）+ `mcp/tools/tasks.py`（依赖门 `claim_task`）+ 11 层记忆体系，但 `goal_tick` 尚未接入运行时。本报告聚焦"哪些外部项目能补我们的短板 / 验证我们的方向"。

---

## 一、一句话结论

**多 agent 编排赛道（Paperclip / Clawith / Agent-MCP）普遍强在"组织层"（org chart、任务原子认领、预算、治理），弱在"持久记忆与知识自进化"；hermes-agent 强在"记忆+技能自进化"但非团队导向。MemALL 的独特定位 = 把已有的持久记忆/知识图谱（强项）与 `goals.py` 目标驱动协作（已建待通电）合起来——这恰好是竞品都没做满的空档，是对的方向，应加速把 `goal_tick` 接入 `agent_round`。**

---

## 二、项目全景（按与 MemALL 研究方向的相关度排序）

| 项目 | Stars | 类型 | 一句话 | 与 MemALL 的关系 |
|---|---|---|---|---|
| **hermes-agent**（NousResearch） | **257k** | 自进化单体 agent | 唯一内置学习闭环：经验生成技能、用中自改、记忆跨会话、FTS5 检索+LLM摘要 | **直接竞品**（记忆+技能自进化），可抄"nudge 持久化""技能自改"范式 |
| **Paperclip**（paperclipai） | **107k** | 多 agent 编配控制面 | "OpenClaw 是员工，Paperclip 是公司"——给 agent 团队分配目标、跟踪工作与成本 | **组织层范式最强**；其 Skill Studio/质量分=我们缺的"技能层+质量量化"；Roadmap 里 Memory/Knowledge 仍是 🟡 进行中=我们的切入点 |
| **Clawith**（dataelement，国产） | **4.2k** | 多 agent 协作平台 | 每个 agent 有持久身份(soul.md)+长期记忆(memory.md)+独立工作区；Focus Items 状态机+触发器 | **最贴近 `goals.py` 设计**：Focus 状态机≈task 状态机，6 类触发器≈我们的 cron/agent_round |
| **Agent-MCP**（rinadelph） | **1.3k** | 基于 MCP 的知识图谱协作 | "给 agent 的 Obsidian"——共享知识图谱+任务依赖+文件锁防冲突 | **MCP 原生多 agent**，直接复用我们已有 MCP 层；"短生命周期 agent+共享记忆"是可选范式 |
| AutoGen（微软） | ~61k | 对话协商框架 | 多 agent 互发消息直到收敛 | 通用编排，非记忆导向，参考价值低 |
| CrewAI | ~60k | 角色分工框架 | crew 式串行任务 | 同上 |
| LangGraph | ~44k | 图编排 | 生产级状态机编排 | 其"状态机"思想可参考，但非记忆系统 |
| OpenHands | ~83k | 多角色软件开发 | planner/executor/verifier 分工写码 | 角色分工范式，可参考 verifier 角色 |
| OpenSpace（HKUDS，前次已调研） | — | 技能管理层 | FIX/DERIVED/CAPTURED 进化+质量量化 | 技能层+渐进信任范式（见前次报告） |
| vibecode-pro-max-kit | 1.1k | 自改进上下文记忆 | 15 agents/33 skills，`/goal`、`agent-team` 自动循环 | 小但概念贴合：`/goal` 指令≈我们的 goal 驱动 |
| thread-keeper | 14 | 跨 agent 共享脑 | 跨 Claude/Codex/Gemini 的共享记忆+自改进技能循环 | 概念近，但体量太小、未验证 |

---

## 三、四大重点实证分析

### 1. hermes-agent（257k）— 记忆+技能自进化的标杆
**核心机制（README 实证）：**
- **Closed learning loop**：agent 策展记忆 + 周期性 nudge 持久化；复杂任务后**自主创建技能**；技能在**使用中自我改进**；FTS5 会话检索 + LLM 摘要做跨会话召回；Honcho 辩证式用户建模；兼容 `agentskills.io` 开放标准。
- 子 agent 隔离并行；定时自动化（cron，自然语言无人值守）。

**对 MemALL 的借鉴（映射已有 gap）：**
| hermes 机制 | MemALL 现状 | 动作 |
|---|---|---|
| 周期性 nudge 促持久化 | 我们有 auto_inject（session 级），但无"周期性主动提醒沉淀" | 在 `agent_round` 加"沉淀提醒"钩子 |
| 复杂任务后自主创建技能 | **缺失**（OpenSpace 报告已列"技能层一等公民"为收益1） | 复用 `goals.py` 验证通过的流程→沉淀 skill |
| 技能用中自改 + 质量反馈 | 缺失质量量化 | 引入 provisional→trusted 渐进信任（见 OpenSpace 报告收益2） |
| FTS5 + LLM 摘要跨会话检索 | 我们有 FTS5+vec0+RRF，但**检索结果不进 LLM 摘要再召回** | 可选增强 retrieve 层 |
| agentskills.io 标准 | 自有格式 | 评估双轨兼容（对外生态） |

> ⚠️ hermes 的 benchmark 多为自报，对外口径谨慎。但它证明了"记忆+技能自进化"是 257k 星级的刚需方向，**验证 MemALL 赛道成立**。

### 2. Clawith（4.2k，国产）— 最贴近 `goals.py` 的多 agent 实现
**核心机制：**
- 每个 agent 有 `soul.md`（人格）+ `memory.md`（长期记忆）+ 私有工作区（沙箱执行），跨会话持久。
- **Aware 自主意识系统**：**Focus Items**（结构化工作记忆，带 `[ ]/[/]/[x]` 状态标）+ **Focus-Trigger 绑定**（任务触发必须对应一个 Focus，完成后取消触发器）+ **自适应触发器**（agent 自创/调/删触发器，人给目标、agent 管排程）+ **6 类触发器**（cron/once/interval/poll/on_message/webhook）+ **Reflections**（触发会话中的自主推理留痕）。
- The Plaza：组织级活知识流（agent 发更新、评论彼此工作）。
- 自进化：运行时发现/安装工具（Smithery+ModelScope MCP）、自创技能。

**对 MemALL 的借鉴（这是最直接的映射）：**
| Clawith 机制 | MemALL 对应 | 差距/动作 |
|---|---|---|
| Focus Items `[ ]/[/]/[x]` 状态机 | `goals.py` children 状态 | **我们已有，且更强**（含 `verifying`/`returned` 非生产者验收） |
| Focus-Trigger 绑定 + 自适应触发 | `goal_tick` + `agent_round` 提醒 | **我们的 `goal_tick` 写好了但没通电**——Clawith 证明这条路线对 |
| 6 类触发器（多 on_message/webhook） | 我们只有 cron + agent_round | 可在 `agent_round` 增加 on_message/事件驱动唤醒 |
| soul.md/memory.md 持久身份 | MemALL `identities` 表（被 20+ 测试身份污染，前次报告 P2-13） | 先治理 identities 污染，再谈"每个 agent 身份" |
| The Plaza 活知识流 | 我们有 identities/clusters 但无"组织级 feed" | 可选，非核心 |

> **关键判断**：Clawith 用 4.2k 星做了一个 MemALL `goals.py` 想做的事且已落地跑通。这**反向验证**我们"给 `goal_tick` 通电"是正确且低风险的下一步（前次深度分析结论）。

### 3. Paperclip（107k）— 组织层范式最强，记忆是短板
**核心机制（README 实证）：**
- 四支柱：Agentic Task Manager / Org Chart / **Agent Employee Training（Skill Studio、evals、质量指标、技能版本历史与回滚）** / Agentic OS。
- **Atomic task checkout**：单一 assignee + 执行锁，防竞争认领同一任务——**与我们 `tasks.py:84-114` 依赖门解决的是同一问题**。
- **Goal-aware execution**：关联任务携带 goal 祖先链，agent 看到"why"而非仅标题——**即我们 `goals.py` children + `depends_on_idx` 的设计意图**。
- Runtime skill injection（运行时注入技能，无需重训）、Governance with rollback、Budget & Cost Control、Routines 定时例程。
- **Roadmap 明确 `Memory / Knowledge` 仍为 🟡 进行中**——他们自己承认记忆/知识层没做满。

**对 MemALL 的借鉴：**
| Paperclip 机制 | 映射 MemALL | 动作 |
|---|---|---|
| Atomic task checkout + 执行锁 | 我们的 `claim_task` 依赖门 | **已被我们实现**，可对照其对齐语义 |
| Goal-aware execution（goal 祖先链） | `goals.py` | 确认方向一致；可补"祖先链透传上下文" |
| **Skill Studio + evals + 质量指标 + 版本历史** | **我们完全缺失的技能层** | = OpenSpace 报告收益1+2 的落地形态，优先级最高 |
| Runtime skill injection | 我们的 auto_inject | 已具备，可对照其"无需重训" |
| Governance with rollback | 我们有 pipeline_events 但无显式回滚 | 可选 |
| Budget & Cost Control | 无 | 若做企业版再考虑 |

> **战略空档**：Paperclip 用 107k 星证明"agent 公司"是强需求，但它 Memory/Knowledge 没做满。MemALL 若把记忆（强项）+ `goals.py`（已建）+ 轻量技能层（补）三件合起来，就是 Paperclip 缺的那一块——**这是我们的差异化护城河**。

### 4. Agent-MCP（1.3k）— MCP 原生多 agent，范式可选
**核心机制：**
- "给 agent 的 Obsidian"：持久知识图谱 + 实时可视化 + 多 agent 并行 + 智能任务管理（依赖防冲突）。
- **哲学：短生命周期 agent + 共享记忆**——每个 agent 只为单任务而生，最小上下文，完成后即销毁；共享知识图谱 RAG 做跨 agent 知识传递；最多 10 agent，空闲 60s 回收。
- 文件级锁防并发覆写；MCP server 模式可被 Claude Desktop 等消费。

**对 MemALL 的借鉴：**
- **MCP 原生**：直接复用我们已有 MCP 层做多 agent 总线，无需另起炉灶。
- "共享知识图谱做跨 agent 上下文" = 我们的 `edges/identities/clusters` 知识图谱的可视化/查询面。
- "短生命周期 agent"是**与 Clawith 持久身份 agent 相反的范式**：MemALL 若做协作，需二选一或在 goals 层混合（建议：记忆/身份持久，任务执行用短生命周期 worker）。

---

## 四、跨项目共性规律（可借鉴的设计范式）

1. **任务/目标必须有显式状态机 + 原子认领锁**——Paperclip、Agent-MCP、Clawith(Focus) 都独立验证了这点；MemALL 的 `tasks.py` 依赖门 + `goals.py` 状态机方向正确，只差通电。
2. **技能层 + 质量量化是"自进化"的事实标准**——hermes（技能自改）、OpenSpace（provisional→trusted）、Paperclip（Skill Studio+evals）三家独立印证；这是 MemALL 最大的未补短板。
3. **触发器多样化**——cron 只是最小集，成熟系统都有 on_message/webhook/poll（Clawith 6 类）；我们 `agent_round` 可扩展。
4. **运行时技能注入（auto_inject 类）已成标配**——hermes、Paperclip、Clawith 都有；MemALL 已有，是优势。
5. **记忆检索走向"FTS5/向量 + LLM 摘要再召回"**——hermes 明确此路径；我们检索层可补 LLM 摘要环节。
6. **身份/人格持久化是协作前提**——Clawith(soul.md)、hermes(persona)、MemALL(identities) 一致；但 MemALL 的 identities 表被测试污染，需先治理（前次 P2-13）。

---

## 五、对 MemALL 的具体收益清单（按优先级）

| 优先级 | 借鉴来源 | 收益 | 映射到 MemALL 现状 |
|---|---|---|---|
| **P0** | Paperclip + Clawith | **把 `goal_tick` 接入 `agent_round`**，让目标驱动协作真正自动运行 | `goals.py` 逻辑已有、`goal_tick` 写好了但未接线（前次深度分析唯一硬缺口） |
| **P0** | Paperclip Skill Studio / OpenSpace / hermes | **建轻量技能层 + 质量量化（provisional→trusted）** | 完全缺失；直接根治前次"4-gram 字符门禁误伤"的坑 |
| **P1** | Clawith / Agent-MCP | 扩展触发器：在 cron 基础上加 on_message/事件唤醒 | 仅 cron + agent_round |
| **P1** | Paperclip / 我们 tasks.py | 对齐"原子认领 + 执行锁"语义，统一裸 L5 task 卡与 goals 验收 | 已有依赖门，但两套验收语义需统一 |
| **P2** | hermes | 检索层补"FTS5/向量 + LLM 摘要再召回" | 已有 FTS5+vec0+RRF，缺 LLM 摘要环节 |
| **P2** | Clawith / hermes | 治理 `identities` 表污染，建立"每 agent 持久身份" | 前次 P2-13 未修 |
| **P2** | Agent-MCP | 复用 MCP 层做多 agent 总线，评估"短生命周期 worker"范式 | MCP 层已具备 |

---

## 六、风险与边界

- **不要抄执行后端**：Paperclip/Clawith 的 org chart、预算、治理 UI 是重活，且与 MemALL"记忆 OS"定位偏离；我们只借**设计范式**，不重造控制面。
- **记忆才是我们的护城河**：Paperclip 自己把 Memory/Knowledge 排 🟡 进行中——这是 MemALL 不能丢的差异化。
- **hermes 数据自报**：257k 星是事实，但其 benchmark 非独立验证，对外口径谨慎。
- **Agent-MCP 是 AGPL-3.0**：若借鉴其代码需注意传染性许可证；我们仅参考范式，不自建同协议实现。

---

## 七、下一步建议（衔接前两次分析）

1. **立即（最小改动最大收益）**：给 `agent_round()` 末尾追加 `goal_tick(conn)` 调用（~10 行），把 `goals.py` 通电。→ 前次深度分析已交办。
2. **紧随**：立项"技能层 + 质量量化"（provisional→trusted + Skill Studio 雏形），用真实 outcome 打分，根治字符门禁误伤。
3. **对齐验收语义**：统一裸 L5 task 卡（归属者自关）与 goals（非生产者验收），避免两套状态机混乱。
4. **治理 identities 污染**，为"每 agent 持久身份"打底。

> 结论：**赛道成立、方向正确、最该做的是把已建好的 `goals.py` 通电 + 补技能层质量量化**。外部项目一致印证了这条路，而不是要求我们推翻重来。
