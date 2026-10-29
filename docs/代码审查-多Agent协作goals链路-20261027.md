# 代码审查报告：多 Agent 协作子系统（goals 链路）

> **文档状态**：历史留档 · 已闭环 — 提出的 3 高危 + 1 真 bug 全部已修（1824dc6 覆盖空身份/verifier 为空/前向依赖，migration 040 覆盖跨进程唤醒互斥）

> 审查人：opencode ｜ 日期：2026-10-27 ｜ 状态：**待 dsh 复核**
> 范围：`pipeline/goals.py`、`pipeline/fallback.py`、`pipeline/agent_waker.py`、`mcp/tasks.py`、`mcp/shared.py`、`mcp/tools/goals_handlers.py`、`mcp/tools/memory_write.py`、`pipeline/task_lifecycle.py`、`check_fixes.py`
> 证据基线：主分支截至 `92373ee`（2026-10-23）；活库 `~/.memall/data.db` 实测于 2026-10-27
> 关联文档：`docs/重新梳理-多Agent自动协作-20261027.md`（dsh）、`docs/深度分析-多Agent协作梳理-20261027.md`（workbuddy）——两份文档的事实主张在本报告 §七 一并修正

---

## 0. 结论摘要

1. **引擎方向正确、e2e 守卫优秀**：`goals.py`（10-20 提交 `203f03a`）实现了完整 Magentic 状态机，`guard_goal_lifecycle_end_to_end`（`check_fixes.py:2577`，已注册于 `:2778`）用 7 条断言覆盖全链路——这是全系统目前对"协作契约"唯一可信的可执行证据。
2. **但有 3 个高危问题**：空身份可绕过 goal 授权门（安全洞）、verifier 为空的 goal 进入 verifying 后永久卡死（无投递无升级）、wake 链路跨进程无互斥（启用后同 agent 会被 N 个 MCP 进程重复 spawn 并发跑）。
3. **1 个破坏协作语义的真 bug**：decompose 允许前向依赖，finalize 按序物化时**静默丢弃**该依赖——顺序约束在落库时蒸发且无报错。
4. **修正一个此前所有人（含我）的误判**：workbuddy 评审的核心结论"`goal_tick` 没通电"是**错的**——`fallback.py:162/197` 每个 tick 都调用，`server.py:143-145` 在每个 MCP 会话内启动 300s 定时器，MCP 经 `memall_read action=goal_status`（无 goal_id 时返回 tick 结果）可达。**引擎是通电的**。
5. **活库真相**：6 条 goal 全部是合成探针（`gpm_/gw1_/goal_pm` 等合成身份），**至今没有一条真实业务目标被引擎驱动过**。引擎"能跑通"的证据全部来自探针，"会卡死"的模式（如 verifier 为空）尚未被真实流量检验。

---

## 一、高危（P1，建议立即修）

### P1-1 空身份绕过授权门（安全洞，四处同模式）

```python
# goals.py:186 (decompose)、:249 (attach_discussion)、:280 (finalize) —— 同一写法：
if agent_name and owner and agent_name.lower() != owner.lower():
    return {"status": "forbidden", ...}   # agent_name 为空 → 短路 → 放行
```

未配置 `MEMALL_AGENT_NAME` 的 MCP 客户端（agent_name=""）可以：拆解任何人的目标、关联任意讨论、落任务卡。同模式还存在于 `memory_write.py:120` 的 ownership 门：

```python
if caller and not _is_supervisor(caller):   # 空 caller → 跳过 ownership gate
```

对比：`verify_goal`（goals.py:392-393）与 `claim_task`（tasks.py:57-59）对空身份**显式拒绝**。同仓两种立场并存，属遗漏而非设计。

**修复**：`agent_name` 为空一律拒绝；需要内部可信路径时仿照 `task_lifecycle._caller_gate`（task_lifecycle.py:65-81）区分 `caller is None`（trusted internal）与 `""`（拒绝）。

### P1-2 verifier 为空的 goal 在 verifying 阶段永久卡死

链路实证：

- `goals.py:346-369` `request_verification` 允许 verifier 为空时推进到 `verifying`（返回文案："任何非生产方均可验收"）；
- `goals.py:475-478` driver 在 verifying 阶段产出 `{"agent": verifier(=""), "action": "verify"}`；
- `fallback.py:176-177` 投递时 `if not agent: continue` —— **静默丢弃**。

结果：该 goal 每 5 分钟被 tick 一次、每次产出一个永远无人收到的空投递，无升级、无超时、无可见性（唯一线索是 `goal_status` 手动查询）。

**修复（任选其一，建议 a）**：
a. `request_verification` 时 verifier 为空 → 拒绝并要求先指定（或自动从非生产方在册 agent 中选定）；
b. `next_action` 的 verifying 分支 fallback：verifier 为空 → agent=owner，消息写明"请指定验收人或自行指定非生产方验收"。

### P1-3 wake 链路跨进程无互斥（开启 wake 前必修）

`agent_waker` 的冷却/小时上限（`agent_waker.py:52-55` 的 `_last_wake`/`_HOURLY`）与 `shared.py:108` 的 `_NOTE_DEDUP` 均为**进程内存**。而 stdio MCP server 按客户端会话各 spawn 一个（AGENTS.md §七 既定事实），每个进程：各自跑 fallback timer → 各自 `goal_tick` → 各自为**全体 agent** push note → 各自尝试 wake。当前 `enabled=false` + `dry_run=true` 时无害；一旦开启：N 个活跃 MCP 会话 = 同一 agent 同一动作最多被 spawn N 次，且跨进程并发——两个实例会同时认领、同时写库。

**修复**：wake 决策改为 DB 级 claim（复用 `pipeline_runs` 的 claim 模式，`fallback.py:88-139` 是现成模板），进程内存冷却仅作第二道。

---

## 二、中危（P2）

### P2-1 前向依赖被静默丢弃（真 bug，破坏协作语义）

- `goals.py:200-211` decompose 只拒绝**自依赖**（`di == i`）与**越界**（`di >= len`），**允许 `di > i`（依赖后面的兄弟）**；
- `goals.py:298-302` finalize 按 idx 升序处理，`idx_to_id` 只含已处理项 → `di > i` 的依赖因 `i not in idx_to_id` 被**无声丢弃**；
- 落库卡没有 `depends_on` → `tasks.py:84-114` 的依赖硬门形同虚设；拆解时的顺序约束在物化时蒸发，且无环检测（A↔B 互依可通过）。

**修复**：decompose 拒绝 `di > i`（拆解单天然隐含拓扑序，报错文案说明"只能依赖前面的兄弟"）；或 finalize 两遍物化 + 未解析依赖显式报错。

### P2-2 `create_goal` 无重复防护（有活库实证）

今日活库实测：5 条内容近重复的探针 goal（"验证全链路"×3、"验证落卡"×2，id 1000656/1000690/1000703/1000716/1000743）。唯一兜底是 capture 的 content_hash（`thin_waist.py:1042-1103`），但 body 内嵌 owner/verifier，字段稍异即漏。多进程 tick 或 agent 重试即产生重复目标，每个副本独立驱动一轮拆解-论证-落卡。

**修复**：按 `objective` 规范化哈希做浅去重（同 objective 且未 closed → 返回既有 goal_id 并提示），或 owner+objective 维度幂等。

### P2-3 `finalize` 非原子 + 失败静默缺卡

子卡逐条 `capture`（各带 commit），全成才写 `meta["task_ids"]`。中途异常时：goal 停在 `deliberating`、**无 marker 记录已落几张**；重跑靠 content_hash 幂等救回（body 相同 → 返回旧 id），但两次之间 plan 若被改过（decompose 允许在 decomposed 阶段重跑）→ **重复子卡**。且单张 capture 失败时 goal 照常推进 `assigned`，`task_ids` 缺项，driver 无感知。

**修复**：capture 返回非 int 即中止并返回显式 `partial` 状态（goal 停在 deliberating + 缺卡清单）；或子卡创建收敛为单事务。

### P2-4 `claimed_by` 是幻影概念 + 双 `_addressed_to` 重复实现

- `tasks.py:43-45` 与 `task_lifecycle.py:59-62` 两份相同的 `_addressed_to`，语义不一致：**tasks.py 版无 supervisor 豁免**（claim 无 supervisor 旁路，resolve/block 有）；
- claim 只允许 addressee（tasks.py:48-59）→ `claimed_by` 恒等于 assignee → `claim_task` 的 `"reclaimed"` 分支（tasks.py:129）是死代码；`verify_goal` producer 集合（goals.py:388）的 `claimed_by or assignee` 冗余。

**修复**：要么让 claim 支持真正的受托执行方（claimed_by ≠ assignee 有意义），要么删概念统一为 assignee 单轨；`_addressed_to` 合并为单实现（含 supervisor 豁免策略显式化）。

### P2-5 goal 元数据并发更新无保护

`_save_meta`（goals.py:116-118）+ commit 是裸 read-modify-write。N 个 MCP 进程并发 tick / agent 并发转换（verifier 与 owner 同时 request_verification / verify_goal）时 `phase`、`phase_history` 会丢更新——这正是 dsh 文档 §1.1 引用的 Azure 反模式（"并发 agent 共享可变状态"）在自家代码里的实例。

**修复**：CAS 式更新（`UPDATE ... WHERE id=? AND metadata=?`）或转换级 DB 互斥。

---

## 三、低危 / 卫生（P3）

| # | 位置 | 问题 | 建议 |
|---|---|---|---|
| 1 | goals.py:358-359 | `if verifier == caller: pass` 死代码 | 删除或改为记录审计 |
| 2 | goals.py:346-362 | `request_verification` 可重复调用，重复追加 `phase_history` | verifying 已在当前 phase 时返回幂等提示 |
| 3 | goals_handlers.py:27,37,45 等 | `int(gid)` 未捕获 ValueError → MCP 层裸异常 | 包 try/except 返回结构化 error |
| 4 | shared.py:108/128 | `_PENDING_SESSION_NOTES`/`_NOTE_DEDUP` 无 TTL；每个进程 tick 为全体 agent push，别的 agent 的 note 在本进程永久滞留 | 加 TTL 或仅 push 本进程可见身份 |
| 5 | goals.py:399 vs 435 | verdict 截断不一致（2000/300） | 统一 |
| 6 | goals.py:54 / 417 | `_CHILD_TERMINAL` 含 `"verified"` 但无产生路径（死枚举）；`returned` 恢复不含 `archived` → archived 子卡永久视为已完成 | 对齐枚举与恢复集合 |
| 7 | goals.py:468-474 | `assigned` 阶段子卡全空（finalize 全失败）时 next_action 返回 None，goal 无声滞留 | 与 P2-3 同根，补一条防御断言 |
| 8 | 活库 | 合成探针身份（gpm_/gw1_/goal_pm…）混入生产 `identities` 表与 L5 库，长期生成无效 tick/通知 | 一次探针清库（dry_run 先行）+ 探针写库规范（复用 check_fixes 的 `_fresh_db()` 模式） |
| 9 | fallback.py:148-207 | goal driver 每 tick 为全体 agent push 全量 note，跨进程放大 P3-4 | 与 P1-3 一并设计 |

---

## 四、亮点（复核时请一并确认，避免误伤）

1. **`guard_goal_lifecycle_end_to_end`**（check_fixes.py:2577-2700+，注册于 :2778）：7 断言覆盖 intake→decompose 拒绝空验收→收敛前 finalize 阻断→落卡依赖链→blocked_by_dependency→生产者禁自验→独立验收关闭。**两份 10-27 文档都没引用它，建议后续所有"协作契约"讨论以守卫为准绳。**
2. **agent_waker 纵深防御**（agent_waker.py 全文）：默认关 + dry_run + argv 单元素免 shell RCE + denylist + 冷却 + 小时上限 + 硬超时 kill + 全部留痕（:153-156 仅留 50 条）。值得作为"agent 触发 agent"类功能的模板。
3. **fallback 并发治理**：`claim_run` 的 DB 级互斥 + Python 侧 ISO cutoff 比较修复（:113-132 注释完整记录了 SQL 字典序比较导致"永远不 claim"的旧 bug）。
4. **session note 语义**（shared.py:134-183）：不消费不丢、ack 才释放 dedup key、空身份拒绝猜测——对超时重试与身份隔离的处理正确（P0-4a 的修复质量高）。

---

## 五、对"全自动推进"目标的工程结论

- 驱动层**已存在**（MCP 进程内 300s timer → goal_tick → 幂等 session note 投递），此前"缺驱动层"的判断修正为"驱动层存在但为每进程一份、跨进程无互斥"。
- 全链路（创建→拆解→论证→落卡→认领→执行→验收→关闭）在守卫环境已跑通（check_fixes e2e + 今日探针 #1000743 至 phase=closed）。
- **距离"真实任务可全自动推进"还差 P1×3 + P2-1/P2-2 五个必修点**；修完前，建议真实 goal 一律由人（老陈）创建而非 agent 自发，并强制 verifier 必填。

---

## 六、建议修复顺序（可与 dsh 文档 §6 合并）

| 序 | 项 | 对应 | 守卫建议 |
|---|---|---|---|
| 1 | 空身份拒绝统一 | P1-1 | `empty_identity_rejected_everywhere` |
| 2 | decompose 拓扑约束 | P2-1 | `forward_dependency_rejected`（decompose 层拒绝）+ finalize 层 `unresolved_dependency_fails_loudly` |
| 3 | verifier 必填/自动选定 | P1-2 | `verifying_requires_verifier` |
| 4 | create_goal 去重 | P2-2 | `duplicate_objective_rejected` |
| 5 | finalize 原子性 | P2-3 | `partial_finalize_leaves_goal_in_deliberating` |
| 6 | wake 跨进程互斥 | P1-3 | `wake_claimed_in_db` |
| 7 | 双语义统一 + 死代码清理 | P2-4 + P3 批量 | 并入既有守卫 |

## 七、对三份既有文档的事实修正（供 dsh 一并定稿）

1. **dsh 文档**：缺口 1（依赖门）已于 10-22 `98b3c96` 落地（tasks.py:84-114，注释含同款"并行≠协作"论证）；"Magentic 完全没有 ✗"不成立（goals.py 10-20 已实现）。§6 第 1 项应标"已落地"，落地顺序从第 2 项起重排。
2. **workbuddy 评审**：核心贡献（指出 goals.py 被漏）成立且重要；但"`goal_tick` 无运行时调用点/未暴露 MCP"不成立（fallback.py:162/197、goals_handlers.py:86、validator.py:92）。其 §7 行动 1/2 应撤销，行动 3（双语义统一）成立。
3. **共同教训**：三份分析各自停在不同时点的代码快照上，且均未引用 commit hash。建议制度化：**文档事实主张必须引用 `file:line + commit`，并附可复跑验证命令**；可加一条守卫断言文档引用的代码行仍成立。
4. **我的自我修正**：我前两轮分析同样漏了 goals.py、误判"缺驱动层"，本报告 §0.4/§五 已修正。请 dsh 复核时同样核对我的 file:line 引用。

---

## 八、请 dsh 复核的 5 个具体问题

1. P1-1 的修法：空身份一律拒绝，是否会破坏某些现有部署（gateway/http_transport 是否存在合法的空 agent_name 调用方）？请给出配置面清单再定稿。
2. P1-2 选 a（request_verification 拒绝）还是 b（driver fallback 到 owner）？我倾向 a，理由：驱动层的"任何非生产方"文案会诱导 owner 之外的人抢验收，违反验收人先定的宪法语义。
3. P2-1 选"decompose 拒绝前向依赖"还是"finalize 两遍物化"？我倾向前者（拆解单即拓扑序，语义更简单，守卫更薄）。
4. P1-3 的 wake 互斥，用 pipeline_runs 表还是新 claim 表？涉及"不加第二存储"原则，请拍板。
5. 探针数据清库（P3-8）的授权：涉及生产库删除操作，按宪法需老陈确认，是否先出 dry_run 清单？

---
*本报告所有 file:line 均对应主分支 `92373ee`（2026-10-23）之后的现状；复核请先 `git log -1` 确认无更新提交。*
