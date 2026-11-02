# B 档实现状态与重做清单

> 生成时间：2026-11-02
> 关联：A 档合并提交 `df94713`（v0.1.76）、讨论 #1007956（E 节 9 条裁决）
> 依据：`fix_execution_plan_draft.md`

## 为什么需要这份文件

计划写的是「B 档一律未动手」，但实际工作区里 **B1/B3/B4/B5 已有实现**。为了让
「只 merge A 档」这句话成立，dsh 把这些代码从 A 档文件中剥离；剥离过程中
（为修复被 PowerShell 破坏的文件）执行了 `git checkout --`，**销毁了这些未提交
的实现，无法从 git 恢复**（已查 stash 与 dangling object，均无副本）。

B2 是未跟踪新文件，未受影响，已移入本目录。

**这是一次操作失误，代价是 B1/B3/B4/B5 需重做。** 下面记录销毁前观察到的实际内容，
以及重做时必须避开的坑。

## 执行顺序（dsh 裁决⑨已认可）

`B1 → B3 → B5 → B2 → B4`

---

## B1 收敛判定接入立场（P0，下一个该做）

**问题**：`_rule_satisfied` 只数「谁回了」，不看「回了什么」。unanimous 下全员
reject 依然生成 L4 决策 + L5 任务卡。

**销毁前的实现**：
- `pipeline/convergence.py`：`_rule_satisfied(meta, responded, participants, created_at=None, agreeing=None, rejecting=None)`，新增 `rejecting` 参数；unanimous 且 `rejecting` 非空 → `return False`；majority 下 `len(rejecting) >= len(agreeing)` → `return False`。
- `mcp/tools/discussion.py`：`handle_respond` 先 `get_discussion()` 收集已有 reject 立场，再传 `rejecting=already_rejected`。

**重做时必须注意的坑**：
1. 销毁前的版本里，reject 判断块被放在 `rule = meta.get(...)` **之前**，引用了尚未定义的
   `rule` 变量 → 走到该分支必 `NameError`。新实现务必先取 `rule` 再判断。
2. `confirm_discussion()` 与 `_converge_single()` 两条路径都要传 `agreeing`/`rejecting`，
   否则两条路径判定不一致（历史上就出现过 sweep 与 confirm 结论不同）。
3. 与本次 A1-1 的 `observer` 标记交互：**局外人的 reject 不应算否决票**（已在 A1-1 落地的
   计票排除里覆盖），实现 B1 时 `rejecting` 集合必须同样跳过 `observer: true` 的回复。
4. 验证：构造 3 人全员 reject 的讨论，确认不收敛、不生成任务卡；再验证「2 agree + 1 reject」
   在 unanimous 下也不收敛。守卫建议命名 `convergence_honours_stance`。

---

## B3 完成即通知（P1）

**问题**：`depends_on` 前置完成后不通知后继负责人，只能等 `goal_tick` 轮询。

**销毁前的实现**（`pipeline/task_lifecycle.py`，约 +82 行）：
- `resolve_task()` 成功提交后调用 `_notify_task_dependents(conn, task_id)`，包在 try/except 里
  （通知失败不得影响 resolve 本身）。
- `_find_dependents(conn, task_id)`：扫 `level='L5' AND category='task' AND NOT_ARCHIVED`，
  解析 `metadata.depends_on`（兼容标量与列表），返回引用该 task_id 的卡。
- `_notify_task_dependents()`：对每个后继的 `agent_name` 走 `BusClient` 发 `type="verdict"`、
  `payload={"memory_id": ...}`；引用 `core.task_identity.can_mutate` 做身份校验。

**重做时必须注意的坑**：
1. 通知必须在 resolve 的 **commit 之后**，否则收到通知的 agent 立刻去读会读到旧状态。
2. 防通知风暴：一次 resolve 扇出多条时，同一 assignee 应合并成一条。
3. 总线不可用时必须静默降级（mailbox 语义），不能让 resolve 失败。
4. 计划正文写的是 `memall_msg send`，销毁前实现直接用 `BusClient`——重做时统一走
   `mcp/shared.py` 的 `push_session_note(..., dedup_key=...)` 或 `memall_msg`，
   避免绕过 A3-2 刚落地的幂等键逻辑。

---

## B5 Hub 同步游标（P1）

**问题**：`hub_sync` 每轮重拉最近 N 条，无去重。

**销毁前的实现**：`_cursor_load(conn, key)` / `_cursor_save(conn, key, value)` /
`_cursor_ensure(conn)` 三个 helper（放在 `mcp/federation_tools.py` 顶部），
`hub_sync()` 用 `pipeline_cursors` 记 `hub_sync:to_hub:<group_id>` 的最大 memory id，
推送改 `WHERE id > ? ORDER BY id ASC`，多取一条判断 `has_more`。

**重做时必须注意的坑（重要）**：
1. **不要放进 `hub_sync()` 函数体中部。** 销毁前的插入位置正好落在原 push 逻辑中间，
   提前 `return result` 把原函数体截成孤儿代码，导致整个 `federation_tools.py`
   `IndentationError` 无法编译。改 `hub_sync` 请整函数替换，或把游标逻辑做成函数入口
   的局部变量。
2. 那次插入还顺手删掉了 `_sanitize_inbound()`，而 pull 分支仍在调用它 → `NameError`。
   删函数前先 grep 调用点。
3. 游标 key 命名要与 `pipeline_cursors` 现有约定一致；`classify` 用的是 `step` 主键，
   B5 用的是自定义 key，注意别撞。
4. 拉取方向（from_hub）需要独立游标（`hub:msg:<group_id>`），销毁前只实现了推送方向。

---

## B2 死信队列（P1）— 文件仍在，未销毁

已移入本目录，**尚未接入任何代码**：
- `bus_dlq.py`（3.7 KB）
- `042_bus_dlq.py`（907 B，建 `bus_dlq` 表 + `memall_msg action=dlq/replay`）

**为什么移出 `src/memall/migrations/`**：
1. 迁移加载器会扫目录自动加载，未跟踪的 042 让**每次开库**都报
   `Failed to load migration 042_bus_dlq.py: No module named 'alembic'`，污染运行日志
   （本次跑守卫时刷屏的就是它）。
2. **编号冲突**：`src/memall/migrations/` 里已存在 `042_create_skills.py` 与
   `042_create_wake_audit.py`。重做时先 `ls` 该目录，取真正的下一个空闲编号。
3. 该迁移写法依赖 `alembic`，而本仓迁移是自定义 `apply(conn)` 协议——需按现有
   `041_create_session_notes.py` 的风格重写，不能引入 alembic。

---

## B4 `fed_deliver` 停止冒充（P1，成本最大，放最后）

**问题**：`capture(agent_name=target_agent)` 让 Hub 事件变成「收件人自己写的记忆」。

**dsh 裁决⑧**：先走改 `agent_name` 方案（`hub:<sender>` + `metadata.original_sender`
保留原值），独立 `hub_events` 表留作后续迭代。

**销毁前的实现**：`fed_deliver` 里 `agent_name="hub"`，`metadata` 增
`"original_sender": target_agent`。

**重做时必须注意的坑**：
1. 计划里写的 `hub:<sender>` **当前会被拒**：`normalize_agent_name` 的正则不允许冒号，
   校验失败的名字会被重定向到 `"system"`（见 `core/agent_names.py` 与
   `tests/test_thin_waist_edge.py`）。必须先扩展正则再改调用点，否则身份静默变成
   `system`，比冒充更糟。销毁前的实现退而用 `agent_name="hub"`（不带冒号）正是绕开了这点，
   但这丢失了 sender 维度——重做时要么扩正则、要么把 sender 只放 metadata。
2. 改身份会影响 `auto_inject` 的读取路径与通知栏归属，改完必须跑
   `empty_identity_rejected_everywhere`、`precise_task_handoff`、
   `session_note_not_shared_across_agents` 三条守卫。
3. 独立表方案（约 120 行 + migration + auto_inject 改造）留到 B4 主体落地后再评估。

---

## 交接要求

- B 档任一项动手前，先按 `AGENTS.md` §三.5 用**当前在册 4 人名单**
  `[dsh, opencode, qoder, workbuddy]` 发起讨论；`claude` 已退出，名册里留它会导致
  `unanimous` 永不收敛。
- 每项独立提交，提交前跑 `python tools/verify.py --plan fast`。
- **禁止在未提交工作区上使用 `git checkout --` / `git restore`**（本次就是这条把 B1/B3/B4/B5
  的实现删掉的）。定点修复用 edit 工具；需要保存现场用 `git stash push`。
- 禁止用 PowerShell `Set-Content -Encoding UTF8` 改写 Python 源文件：会写 BOM，且
  `Get-Content`/`Set-Content` 往返会破坏中文注释。
