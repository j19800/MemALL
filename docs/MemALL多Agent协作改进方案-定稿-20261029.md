# MemALL 多 Agent 协作改进方案 · 定稿 v1.0

> **文档状态**：现行 · 待老陈确认 — 两版合并后的唯一有效方案；P0-① 已落地，P0-② 技能层仍为 0 实现
> 定稿人：dsh ｜ 2026-10-29 ｜ 状态：**待老陈确认**（P0 级，AGENTS.md §五.5 人工终审）
> 合并来源：workbuddy 版 `docs/MemALL多Agent协作改进方案-20261028.md` + opencode 版 `docs/MemALL多Agent协作改进方案-完整版-20261028.md`（讨论 #1000886 选项 1，记忆 #1000933 / #1000964 已表态）。
> 本定稿所有代码断言均以 2026-10-29 对 `src/` 的逐文件复核为准（HEAD 含 5180c23、1824dc6、1aad348、447eec2）。

---

## 一、定稿结论

1. **采纳合并**：以 opencode 版的**代码实证盘点**为事实底座，以 workbuddy 版的**技能层设计**为 P0-② 方案（质量量化 + 宿主分发双管齐下），两版的 P1/P2 互补项全部保留。
2. **状态刷新（两版撰写后已发生的变化）**：
   - ✅ **P0-① 心跳接 goal_tick 已落地**：`agent_round.py:204-205` 已调 `deliver_goal_prompts()`；去重键 `goal:{goal_id}:{action}`（`fallback.py:185`）即 opencode 版「reconcile 幂等」的实现形态，两版此处分歧已被代码消解，无需再选。
   - ✅ 守卫已挂：`guard_goal_tick_on_heartbeat_cadence`（check_fixes.py:2849，dsh 复核 PASS，记忆 #1001171）、`guard_goal_lifecycle_end_to_end`、`guard_goal_children_accepts_json_string`。
   - ✅ P1-3 wake DB claim 已落地并复核通过（migration 040 + `_db_claim/_db_release`，记忆 #1001171）。
   - ❌ **技能层（P0-②）确认全仓 0 实现**：无 `skills` 表、无 `skill/` 模块、无 `skill_export.py`。仍为最大差异化缺口。
3. **定稿范围**：P0-① 关单；P0-② 启动为头号任务；P0-③（triage + accountable）降为 P1；P1/P2 序列刷新后排期。

---

## 二、分歧裁定表（去分歧记录）

| # | 分歧点 | workbuddy 版 | opencode 版 | 定稿裁定 | 依据 |
|---|---|---|---|---|---|
| 1 | 心跳接入选点 | `agent_round()` 末尾追加 | `scheduler.py:110-126` 300s 分支 | **取 agent_round 方案**（实际落地处） | 代码已如此；agent_round 是心跳唯一入口，fallback 保留 6h 兜底 |
| 2 | reconcile 语义 | 独立强调「幂等重放」为第一原则 | 视为 goal_tick 自带属性，未单列 | **取 workbuddy 框架**：验收含双跑相等 + note 行数不增长两项守卫 | 目标级可回退性需被守卫锁住，不能只靠自声明 |
| 3 | 技能层形态 | 蒸馏产物渲染 SKILL.md → 宿主 `skills/` 目录 + frontmatter 哈希锁 | `skills` 表 + provisional→trusted 质量量化 + 检索召回 | **两者合并，不二选一**：库内 `skills` 表（质量量化/晋级，opencode）为真相源；`skill_export` 把 trusted 行渲染为 SKILL.md 落宿主目录（workbuddy）为分发面 | 质量量化解决「门禁误伤」根因；宿主分发解决「产物只在库内」缺口；缺一都不成闭环 |
| 4 | triage/accountable 优先级 | P0-② | P1-3（可归因可回退） | **降为 P1**：goal 数据当前极少（库内仅 1 条活跃目标），人类在环门先于吞吐度量无收益 | 状态机改动风险中，且 verify 已有生产者禁验门兜底 |
| 5 | verify_goal 证据结构化 | 未提（P1-④ reason 枚举替代） | P1-1 结构化 evidence | **保留 opencode P1-1**，并把 workbuddy 的 reason 枚举并入同一批（`last_block_reason` 与 evidence 同层实现） | 两者都是「验收可机器判定」，合并为一批省联调 |
| 6 | 门禁误伤根因 | 未提 | 4-gram 字符规则冒充质量判据 | **采纳 opencode 诊断**，技能层晋级只用真实 outcome（verify 结果）做信号 | 与 OpenSpace provisional→trusted 范式一致 |

---

## 三、技能层（P0-②）定稿设计

### 3.1 数据面（新增）
- migration `04X_create_skills`：
  `skills(id, name UNIQUE, description, domain, status ∈ {provisional, trusted}, quality_score REAL DEFAULT 0, usage_count INTEGER DEFAULT 0, source_memory_id INT, frontmatter_hash TEXT, version INT, created_at, updated_at, memory_status)`
- 质量信号**只接两个真实 outcome**（不引入新噪声，回应分歧 #6）：
  - `+w1`：该 skill 被引用后关联 goal `verify_goal(passed=True)`；
  - `-w2`：关联 goal 被 `returned`（verify fail）→ 降级回 provisional 并记 `last_demote_reason`。
- 晋级：`quality_score ≥ Q_th AND usage_count ≥ U_th` → `trusted`（阈值先 0.6 / 3，可配 `skills.thresholds`）。

### 3.2 分发面（新增 `pipeline/skill_export.py`）
- `export_trusted_skills(agent, target_dir)`：把某 agent 的 trusted 行渲染为
  `<target_dir>/<agent>-<domain>/SKILL.md`，frontmatter 仅 `name / description / version / source_memory_id / frontmatter_hash`（抄 Multica `skill/frontmatter.go` 极简模型，只借范式不抄代码——边界 §六.1）。
- 幂等：以 `frontmatter_hash`（内容 sha256）比对，未变不重写；写 `skills-lock.json` 记录 hash ↔ source_memory_id ↔ 导出时间，防漂移可溯源。
- `target_dir` 默认宿主约定（OpenCode → `.opencode/skills/`，Claude Code → `~/.claude/skills/`），可配；目录不存在则创建失败即报错，fail 不写脏（两版一致）。

### 3.3 沉淀入口
- `verify_goal(passed=True)` 末尾产出「可沉淀候选」（写 session note，不自动落库），由 verifier 或 owner 显式调 `skill_distill(goal_id)` 固化一条 provisional skill。自动落库会重蹈「字符门禁误伤」，故留显式一步。

### 3.4 验收标准（全部进 check_fixes.py，可机器判定）
- `guard_skill_table_migration`：04X 迁移幂等、字段齐全；
- `guard_skill_promote_demote`：5 次成功 verify 信号晋级 trusted；1 次 returned 降级；
- `guard_skill_export_frontmatter_roundtrip`：导出的 SKILL.md 本地 frontmatter 解析可读回 name/description 且正文非空；
- `guard_skill_export_idempotent`：同内容连跑两次 export，宿主目录文件与 lock 行数不变；
- 端到端守卫：临时 goal 走完 verify(pass) → skill_distill → 晋级 → export，断言 skills 表 + 宿主目录 + lock 三处一致，跑完清理。

### 3.5 检索接入（随 P0-② 尾巴，不单独排期）
- trusted skill 的 name+description 进 FTS5 召回；`session_start` auto_inject 增「可复用 playbook」段（复用现有 `workflow_skills` 注入槽，federation_tools.py:451）。

---

## 四、刷新后的路线图

| 批次 | 内容 | 状态 | 依赖 | 工时 | Owner |
|---|---|---|---|---|---|
| 批次 1 | P0-① 心跳接 goal_tick + reconcile 幂等（含守卫） | ✅ **已完成**，本定稿后关单卡 #1001193 相关派工 | — | — | dsh/opencode（5180c23、1824dc6） |
| 批次 2 | **P0-② 技能层 MVP + 质量量化 + 宿主分发**（§三全套） | 🟡 待启动（头号） | 无 | 3d | opencode 实现，dsh 验收（不自派自验） |
| 批次 3 | P1：triage + accountable（原 P0-③ 降级）+ verify evidence 结构化 + last_block_reason 枚举 + 裸 task/goal child 语义统一（P1-2 保留） | ⬜ | 批次 2 的 skills 表落库后接入 evidence.checks | 2.5d | qoder 增量、dsh 契约测试 |
| 批次 4 | P2：identities 治理、吞吐度量仪表盘、事件触发（on_message/webhook） | ⬜ | 批次 1/3 | 2.5d | workbuddy 协调运营项 |

## 五、遗留缺陷（本定稿新增记录，均来自实测）

1. **长驻 MCP 进程落后于源码**（2026-10-29 实测）：`GoalWriteInput._children_accepts_json_string` 已在源码且有守卫 `goal_children_accepts_json_string`（check_fixes.py:3248），但本会话对 live 服务发合法 JSON-string children 仍被「must be a valid list」拒绝——服务进程未重启，跑旧代码（AGENTS.md §八 已预警此坑）。**行动**：重启 DSH memall MCP 连接即可消解；在重启前，woken agent 的 goal_decompose 仍会失败。
2. **目标删除后派发链路未收敛**：goal 1001029 / 1001101 被探针清理删除后，`next_action` 仍按残留状态发「待你拆解」，三方讨论 #1001110 已确认此遗漏（qoder 已登记，随 P3-8 探针清库批次处理）。**行动**：清库脚本删 goal 行时须连带收敛其讨论 + 取消 wake_claims 行，并补守卫。

## 六、边界（两版一致，原样继承）

1. 不引入 Multica 执行后端（daemon/WS/Postgres/26 CLI 适配/Kanban）；只借范式纪律，全部 MemALL Python+SQLite 实现；Multica 为 source-available，禁止复制其代码。
2. 不重造静态技能库，不推倒 `goals.py` 状态机——技能层是加表/加模块/接分发面。
3. OpenSpace benchmark（65.2%→78.7%）为自报数据，不进对外宣传口径。
4. `agent_waker` 默认 `enabled=False`、`dry_run=True` 是安全基线，任何开启先在 staging 验 argv。
5. 向量检索不参考 Multica（其 pgvector 短板）。

---

## 七、提交老陈确认事项（三问）

1. **是否批准本定稿为唯一执行依据**（两版 20261028 文档标记 superseded，保留作历史线索）；
2. **P0-② 技能层 3 人日是否即刻开工**（批次 2 无前置依赖，可与文档收尾并行）；
3. **§五.1 的 MCP 重启授权**——由老陈在 DSH 侧重连，或授权 dsh 提交重启指令。

> 确认前 dsh 不动生产代码；确认后按批次 2 → 3 推进，每批跑 `check_fixes.py` 全量守卫再进下一批。P0 级完成后进入老陈 24h veto 窗口（AGENTS.md §五.5）。
