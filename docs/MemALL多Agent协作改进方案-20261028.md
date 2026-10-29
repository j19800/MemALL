# MemALL 多 Agent 协作改进方案

> **文档状态**：已被取代 — workbuddy 版；已合并进 -定稿-20261029，后续只读定稿
> 依据：Multica 代码深度分析（`docs/Multica-代码深度分析-20261028.md`）+ 前序 7 项机制调研（记忆 #1000774）+ 本地 `E:\multica` 源码核验。
> 原则：**借鉴 Multica 的协作决策范式，不引入其执行后端**（Go/Postgres/daemon/26 个 CLI 适配层一律不碰）。MemALL 保持"记忆 + 目标协作 OS"定位。
> 验收基线：库内 `category='goal%'` 当前 0 行 —— 本方案的目标不只是"代码通电"，而是**让至少一个真实 goal 走完 create→decompose→distribute→verify 全程并留下数据**。

---

## 0. 方案总览（P0→P2，含依赖关系）

```
P0-① goal_tick 通电 + reconcile        （地基，被 ②③ 依赖）
P0-② goal 状态机补 triage + 责任人归因  （叠加在 ① 上）
P0-③ 技能层 = 蒸馏产物 → 宿主 skills 目录 （与 ① 并行，独立）
P1-④ 拉模型认领去重 + 决策可观测        （依赖 ① 的 reason 枚举）
P1-⑤ fail-closed 授权门                （依赖 ② 的责任人）
P2-⑥ handlerless 合并 + 并发交接安全网
P2-⑦ 低频干预范式（协作锚定 goal 而非群聊）
```

**交付物**：改动集中在 `pipeline/goals.py`、`scheduler/agent_round.py`、新增 `pipeline/skill_export.py`；守卫全部进 `check_fixes.py`；验收标准逐条可机器判定。

---

## P0-① goal_tick 通电 + 幂等 reconcile（最高杠杆，最小改动）

**现状**（已核验）：
- `goals.py:goal_tick()` 已写好且**自声明幂等**（"running it twice with no state change produces the same list"），但它只有 2 个消费点：`fallback.py`（兜底路径）+ `goals_handlers.py`（`memall_write action=goal_tick` 手动入口）。
- **`scheduler/agent_round.py`（常驻调度主循环）不调它** —— 这就是"代码写了没通电"的根因。
- 库内 goal 数据 0 行，通电后若无真实 goal 流转，等于空转。

**Multica 印证**：`daemon/reconcile.go`（边沿触发+单槽重放+1s 防抖）证明"重跑从对账开始、context 重置不丢进度"是生产级正解。`WillEnqueueRun`（纯函数单一真相源）证明确定性派单可收敛。

**改动**（小、聚焦）：

1. `agent_round()` 末尾追加 goal 推进（与现有 `check_pending_discussions` / `notify_pending_tasks` 并列第三段）：
   ```python
   # agent_round.py: 现有 stats 增加 goal_actions / goal_delivered
   for name in agents: ...  # 保持原样
   # 新增（全库级，不 per-agent，因为 goal_tick 本就跨 agent 计算 next_action）：
   try:
       from memall.pipeline.goals import goal_tick
       tick = goal_tick()
       delivered = deliver_goal_actions(tick)   # 新增，见下
       stats["goal_actions"] = len(tick["actions"])
       stats["goal_delivered"] = delivered
   except Exception as e:
       logger.warning("agent_round: goal_tick error: %s", e)
   ```
2. `deliver_goal_actions` 走**已有的** `push_session_note`（`mcp/shared.py`）投递，key 用 `goal_id + action`（沿用 fallback.py 的去重键，天然幂等，不堆积）。
3. **reconcile 语义**：不新写广播器，而是把"幂等"落实为——`goal_tick` 每轮重读 goal 快照（它本就 `ORDER BY id` 全量重算），`push_session_note` 按 key 去重。这正对应 Multica 的"replay flag + 单槽脏标志"：重跑不会重复推进。

**验收标准**（全部进 `check_fixes.py`）：
- 守卫 `goal_tick_wired_into_agent_round`：agent_round 源码里存在 goal_tick 调用（静态检查）+ 跑一次 agent_round 后 session_note 表出现 goal 相关记录。
- 守卫 `goal_tick_idempotent`：同一快照连跑 2 次，actions 列表逐字段相等；`push_session_note` 行数不增长。
- **端到端**：`check_fixes` 末尾跑一个临时 goal（create→decompose→attach_discussion→finalize→request_verification→verify_goal 全流程），断言库内出现 goal 行 + 子任务行 + verdict，最后清理。这条守卫就是"通电有电流"的证明。

**风险**：低。goal_tick 异常已被 try/except 包住，不影响原有两段 reminder；session_note 去重键复用现有机制。

---

## P0-② goal 状态机补 triage（人类在环）+ 责任人归因

**现状**：`_PHASES = (intake, decomposed, deliberating, assigned, executing, verifying, closed)`，**没有人类在环门**。`verify_goal` 已强制"生产者不得验收"，但缺"谁对失败负责"。

**Multica 印证**：`task_triage_guard.go`（Triage≠Backlog：系统派生 executor 在 triage 中拒绝、人 @named 则允许）+ `attribution_stamp.go`（每个 run 必须 stamp 责任人，fail-closed）。

**改动**：
1. `_PHASES` 插入 `triage`（介于 `intake` 与 `decomposed` 之间）：goal 自动分解后先进 triage，**owner 对人确认 acceptance/verifier 之前，`next_action` 不产出自动执行动作**（`next_action` 在 triage 阶段只返回"待人工确认"，不触发 decompose/deliberate 的自动推进）。
2. 规则对齐 Multica 的精确度：
   - 系统自动分解的 goal（`OriginDerived`）→ 停在 triage；
   - 人显式 `@agent` 指定的子任务（`OriginNamed`）→ 允许直接进 executing。
3. 责任人字段：`create_goal` / `decompose` 时写入 `meta["accountable"]`（一个具体的 agent 名，不是 owner 角色）；`verify_goal` 失败（returned）时把 `accountable` 进 verdict，让"谁负责"可追溯。
4. fail-closed：`create_goal` 若 `accountable` 解析为空 → 拒绝创建（不 fail-open）。

**验收**：
- 守卫 `goal_triage_gate`：自动分解的 goal 停在 triage，next_action 不产自动推进；named 子任务可直达 executing。
- 守卫 `goal_accountable_required`：accountable 为空时 create_goal 拒绝。
- 与 P0-① 协同：triage 是 `goal_tick` 的一个"等待态"（类似现有 deliberating 的 None 分支），不破坏 reconcile 幂等。

**风险**：中。改状态机枚举，需回归 `_PHASES` 全部现有分支；`accountable` fail-closed 可能卡住存量 0 行 goal（无存量，风险其实低）。

---

## P0-③ 技能层 = 蒸馏产物落到宿主 skills 目录（MemALL 差异化护城河）

**现状**：MemALL 有 L9/L10 自动蒸馏管线，但产物只在库内检索，**没有分发到 agent 宿主（OpenCode/Claude Code）的原生 skill 加载面**。宿主侧 `~/.claude/skills/` 已有 87 个目录（静态、人写）。

**Multica 印证**：`execenv` 演示了"把 skill 写到 provider 原生位置"（`.claude/skills/`、`.opencode/skills/`）；`skill/frontmatter.go` + `skills-lock.json` 证明 **SKILL.md frontmatter（name+description）+ 按 hash 锁定** 是 53k 星验证过的极简可移植格式。

**关键取舍**（与文档一致，但强调差异化）：
- **不**重建静态技能库，**不**替代自动蒸馏。
- 收益 = 把 MemALL 的 L9 蒸馏产物**渲染成 SKILL.md（frontmatter: name+description）** 落到宿主 skills 目录，零协议成本复用宿主已有的 skill 发现机制。
- 可选：加一个 `skills-lock.json` 式哈希锁，记录"该 skill 由哪个 L9 记忆 + 哪次蒸馏 commit 生成"，防漂移 + 可溯源。

**改动**：
1. 新增 `pipeline/skill_export.py`：`export_l9_to_skills(agent, target_dir)` —— 读某 agent 的 L9 记忆，渲染为 `<target_dir>/<agent>-<domain>/SKILL.md`（frontmatter name/description + 正文）。
2. `target_dir` 默认宿主约定路径（OpenCode → `.opencode/skills/`，可配）。
3. 写 `skills-lock.json`（可选，P0 先做，P1 再挂去重）。

**验收**：
- 守卫 `skill_export_writes_valid_frontmatter`：产出的 SKILL.md 能被 `ParseSkillFrontmatter` 等价逻辑（本地实现 frontmatter 解析）读回 name/description。
- 端到端：对某 agent 的 L9 记忆跑一次 export，宿主目录出现合法 SKILL.md，且正文非空。

**风险**：低。纯新增文件，不动现有管线；宿主目录不存在时自动创建或报错（fail 不写脏）。

---

## P1-④ 拉模型认领去重 + 决策可观测（reason 枚举）

**现状**：`mcp/tasks.py` 已有 `claim_task`（AC4 调用方门 + depends_on 门），认领去重靠内存 + 唯一约束。**缺"决策可观测"**——"为什么这个 goal/task 没推进"没有结构化原因。

**Multica 印证**：`task_claim_decision.go` 把所有不可见结果（no_tasks/no_runtime/no_capacity…）收敛到封闭 reason 枚举，且"reason 只陈述证据支持的因果"。

**改动**：
1. 认领/推进失败路径产出**封闭 reason 枚举**（写入 `meta["last_block_reason"]`）：`no_capacity` / `blocked_by_dependency` / `forbidden_addressee` / `runtime_offline` / `unknown`。
2. `goal_tick` 的每个"未推进"也产 reason（与 P0-① reconcile 协同：reason 是 reconcile 的观测面）。
3. 认领继续走**拉模型 + 唯一索引/行锁**（现状已对，补文档说明，不重写）。

**验收**：守卫 `claim_decision_reason_enum` —— 所有认领/推进失败返回值必含合法 reason 枚举值；不存在"静默无因"。

---

## P1-⑤ fail-closed 授权门（叠加在 verify_goal 之上）

**Multica 印证**：`agent_invocation.go` 的 `CanMemberInvokeAgent` fail-closed（查询没回答=无权限，读错误=拒绝但**不把瞬时 DB 错误当成"你没权限"**，两者分开返回）+ 枚举不泄露私有 agent 存在性。

**改动**：给"agent 间互派 / 人授权 agent"补 fail-closed 校验：
- 调用方身份解析失败 → 拒绝（fail-closed）；
- 但**区分**"授权明确没有"（拒绝）与"查询暂时没回答"（返回 degraded 而非 forbidden），避免把瞬时错误变成长期锁死。
- 错误信息不泄露"目标 agent 是否存在/是否私有"（对齐 Multica 的存在性隐藏）。

**验收**：守卫 `authorization_fail_closed` + `authorization_no_existence_leak`。

---

## P2-⑥⑦（低优先，仅列设计，不本期实现）

- **P2-⑥ 并发安全**：`StepMovedError`（Multica）→ 多人/agent 同时改同一 goal 时后者被拒并留评论；`handlerlessSpan` → 合并"无 handler 连续状态"防过度拆碎。
- **P2-⑦ 低频干预**：把多 agent 协作锚定在 goal/issue 状态机而非群聊（治 #999964 式名册永不收敛 + 刷屏）。`goals.py` 天然是低频模型，本项主要是**收敛判定不再依赖"全体名册在场"**（已在 AGENTS.md 讨论机制部分做了一半，goal 层复用同一规则）。

---

## 实施顺序与依赖

| 步 | 内容 | 依赖 | 预估 | 验收守卫 |
|---|---|---|---|---|
| 1 | P0-① goal_tick 通电 agent_round + reconcile | 无 | 0.5d | goal_tick_wired / idempotent / 端到端 |
| 2 | P0-② triage + accountable | 1 | 0.5d | goal_triage_gate / accountable_required |
| 3 | P0-③ skill_export（蒸馏→宿主 skills） | 无（并行） | 0.5d | skill_export_frontmatter / 端到端 |
| 4 | P1-④ reason 枚举 | 1 | 0.5d | claim_decision_reason_enum |
| 5 | P1-⑤ fail-closed 授权 | 2 | 0.5d | authorization_fail_closed / no_leak |
| 6 | P2（本期不做，留设计） | 1-5 | — | — |

**总工作量**：P0 三项 ≈ 1.5 人日，P1 两项 ≈ 1 人日。所有改动带守卫进 `check_fixes.py`，符合本仓"改动必挂守卫"的约定。

---

## 明确不做（边界）

1. **不引入 Multica 执行后端**（daemon/WS/runtime/26 CLI 适配/Kanban UI）—— 与 MemALL 纯 SQLite 零依赖定位硬冲突。
2. **不改许可证敏感代码**（Multica 是 source-available 带商用限制，只研究范式，不复制其 Go 代码）。
3. **不重造静态技能库**（P0-③ 只做"蒸馏→SKILL.md 分发面"，护城河是自动蒸馏，不是静态文档）。
4. **向量检索不参考 Multica**（它自述 pgvector 装了没用，是其短板）。
5. **不夹带他人未提交改动**（P0 改动集中在 goals.py / agent_round.py / 新文件 skill_export.py / check_fixes.py，提交前 `git status` 核 dsh 在途的 033-036 / status.py / website/ 不被卷进）。

---

## 立即可启动（建议本 PR 范围）

P0 三项（①②③）—— 被 Multica + oh-my-multica 双重代码验证、MemALL 已有 `goals.py` 骨架、改动小、全挂守卫、且**直接消除"代码写了没通电 + goal 数据 0 行"的历史病**。P1 两项随后跟进。

> 下一步：确认本方案后，我按 P0-① → ② → ③ 顺序实现，每步跑通 `check_fixes.py` 对应守卫 + 端到端 goal 验证再进下一步；P0 全部落地后写 [验收] 记忆，P0 级交老陈人工终审（AGENTS.md §五.5）。
