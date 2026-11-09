# MemALL 交接报告

> 交接时间：2026-11-02
> 仓库：`E:\MemALL`（`src/memall/` 为源码根）
> 交接方：claude（已退出在册名册）｜ 接手方：新会话
> 上游文档：`memall_multiagent_analysis.md`、`memall_optimization_report.md`、`pending_b_tier/README.md`

> ⚠️ **2026-11-06 状态更新 —— 本文第二、四节关于 B 档"需从零重做"的描述已过期。**
> 见文末「九、状态更新（2026-11-06）」。接手前请先读那一节。第一节的「一句话现状」同理。

---

## 一、一句话现状

**A 档（止血修复）已全部合并并通过门禁；B 档（深度改进）的实现被误删，需按 `pending_b_tier/README.md` 从零重做。** 当前没有任何未提交的源码改动，只有一个未提交的 scratch 脚本和三份未入库的分析报告。

---

## 二、已完成的 A 档（不要重做）

**提交**：
- `df94713` — A 档主提交，v0.1.76，14 文件 +297/−42
- `6f9b2c6` — 交接提交

**落地内容**（13 处，按 E 节 9 条裁决执行）：

| 域 | 改动 |
|---|---|
| 收敛层 | roster 成员校验（非参与者拒绝表态）；`expired` 讨论可被迟到响应复活；零响应讨论超期置 `expired`；字符串 action_item 不再轮询指派 |
| 联邦层 | `_detect_contradiction` 改为对立词**跨两段文本**匹配；`fed_query` 增加 viewer 信任门槛；`fed_publish` 校验 trust_level 枚举并钳制不超过源记忆可见性；调用点传参修正 |
| 总线 | `publish(dedup_key=...)` 幂等键；`msg.handle_send` 自动派生 `sha256(from\|to\|type\|payload)`；失败返回 `retryable=True` |
| 写入质量 | classify cursor 方向修正（`id > cursor ASC`）；`project_infer` 默认 `""`；dream 阈值 0.15→0.25；procedure 仅 L3–L6 且 >40 字；质量门 relevance/source_traceability 分级 + `min_dim<2` 转 review；`hub_client` 补 `import urllib.parse` |

**A4-5 特别说明**：`nlp.py` **无 diff**。按裁决①完全回退，`tokenize_cjk` 不作为全局默认，只在 `agent_routing.py` / `search/enhancements.py` 显式使用。**所有基于旧 tokenizer 标定的阈值（语义去重 0.92、dream 0.25、integrate 0.85、conflict 0.85）保持不变，不要擅自改动。**

**验证状态**：
- `python tools/verify.py --plan fast` → 3/3 绿（import / 红蓝 9 局 / pytest-quick 82 通过），已确认门禁跑的是 `src/`
- 14 条 A 档守卫全过（`convergence_rule_respected`、`convergence_waits_for_action_items` 等）
- 22+13 项功能/行为断言在 `backups/daily/` 的一次性副本上全过，**线上库未碰**

**验证期间发现并修复的两个真实缺陷**：
1. A1-3 归档分支漏 `conn.commit()` — 计数器报 `expired=1` 但卡片仍 active，僵尸讨论一条没归档。已修。
2. `federation_tools.py` 一处 f-string 括号错位导致整文件无法编译 — 已修。

---

## 三、事故：B 档实现已丢失

**发生了什么**：B1/B3/B4/B5 的实现其实早已写进工作区，但为了让「只 merge A 档」成立，从 A 档文件中剥离时用了 `git checkout --` 修复被 PowerShell 损坏的文件 —— 而这些代码本就未提交，**当场销毁**。

**恢复尝试**：已穷尽 stash、dangling trees、188 个 unreachable blobs 全量扫描（含自检确认搜索有效），**git 中无副本，确认无法恢复**。

**幸存**：B2（未跟踪新文件）已移入 `pending_b_tier/`。

**代价**：B1/B3/B4/B5 需按 `pending_b_tier/README.md` 从零重做。该 README 记录了销毁前观察到的实现细节、每个坑、以及裁决⑧⑨的约束 —— **重做前必须读它**。

**教训已存**：记忆 `#1011271` — 未提交工作区禁用 `git checkout --`，定点修复用 edit 工具。

---

## 四、B 档待办（按裁决⑨顺序）

顺序：**B1 → B3 → B5 → B2 → B4**。每项独立提交，提交前跑 `verify.py --plan fast`。

### B1 收敛判定接入立场（P0，下一个该做）
`_rule_satisfied` 只数「谁回了」，不看「回了什么」。unanimous 下全员 reject 依然生成 L4 决策 + L5 任务卡。
- 改动：`pipeline/convergence.py` 增加 `rejecting` 参数；`mcp/tools/discussion.py` 收集已有 reject 立场传入。
- **坑**：① 判断块必须在 `rule = meta.get(...)` **之后**（旧实现放前面会 `NameError`）；② `confirm_discussion()` 与 `_converge_single()` 两条路径都要传，否则判定不一致；③ `observer: true` 的回复不算否决票；④ 守卫名建议 `convergence_honours_stance`。

### B3 完成即通知（P1）
`depends_on` 前置完成后不通知后继负责人，只能等 `goal_tick` 轮询。
- 改动：`pipeline/task_lifecycle.py`，`resolve_task()` 提交后调 `_notify_task_dependents()`。
- **坑**：① 通知必须在 **commit 之后**；② 同一 assignee 合并成一条防通知风暴；③ 总线不可用必须静默降级；④ **走 `memall_msg` / `push_session_note` 而非裸 `BusClient`**，避免绕过 A3-2 的幂等键。

### B5 Hub 同步游标（P1）
`hub_sync` 每轮重拉最近 N 条，无去重。
- 改动：`mcp/federation_tools.py` 加 `_cursor_load/_cursor_save/_cursor_ensure`，用 `pipeline_cursors` 记 `hub_sync:to_hub:<group_id>`。
- **坑（最重要）**：① **不要插进 `hub_sync()` 函数体中部** —— 旧实现就是因提前 `return` 把原函数截成孤儿代码，导致整个文件 `IndentationError`；请整函数替换或做成函数入口局部变量。② 旧实现顺手删了 `_sanitize_inbound()` 但 pull 分支仍在调用 → `NameError`，删函数前先 grep。③ 游标 key 别撞 `classify` 的 `step` 主键。④ pull 方向需独立游标 `hub:msg:<group_id>`，旧实现只做了推送方向。

### B2 死信队列（P1）— 文件幸存
`pending_b_tier/bus_dlq.py`（3.7 KB）、`pending_b_tier/042_bus_dlq.py`（907 B），**尚未接入任何代码**。
- **坑**：① 移出 `src/memall/migrations/` 是因为加载器会扫目录，未跟踪的 042 让每次开库都报 `No module named 'alembic'`；② **编号冲突**：目录里已有 `042_create_skills.py` 与 `042_create_wake_audit.py`，重做先 `ls` 取真正空闲编号；③ 本仓迁移是自定义 `apply(conn)` 协议，按 `041_create_session_notes.py` 风格重写，**不能引入 alembic**。

### B4 `fed_deliver` 停止冒充（P1，成本最大，放最后）
`capture(agent_name=target_agent)` 让 Hub 事件变成「收件人自己写的记忆」。
- **裁决⑧**：先走改 `agent_name` 方案，独立 `hub_events` 表留作后续迭代。
- **坑**：① **`hub:<sender>` 当前会被拒** —— `normalize_agent_name` 正则不允许冒号，会被重定向到 `"system"`（比冒充更糟）。必须先扩正则，否则 sender 只能放 metadata。② 改身份影响 `auto_inject` 读取路径与通知归属，改完必须跑 `empty_identity_rejected_everywhere`、`precise_task_handoff`、`session_note_not_shared_across_agents` 三条守卫。

---

## 五、遗留事项清单

| 项 | 状态 | 处置 |
|---|---|---|
| **推送** | GitHub 不可达（TCP 通但 TLS 被重置，多次重试含 HTTP/1.1 均失败，未配代理） | 本地领先 2 个提交，网络恢复后 `git push origin master` |
| **运行时生效** | 本机 `memall` 解析到 **site-packages 副本而非 `src/`** | MCP server 吃到 A 档改动需 `pip install -e .` 或重新 build；重启前先确认 `python -c "import memall; print(memall.__file__)"` |
| **讨论名册** | #1007945 / #1007956 都已 converge（decision 1013000/1013005，tasks 1013001/1013002），但名册含已退出的 `claude` | 后续用 `amend` 改为在册 4 人 `[dsh, opencode, qoder, workbuddy]` |
| **`src/check_fixes.py`** | scratch 清理改动留在工作区未提交 | 与 B 档批次一起处置，不属 A 档范围 |
| **未入库报告** | `memall_optimization_report.md`、`memall_multiagent_analysis.md`、`memall_memory_quality_research.md`、`memall_functional_design_report.md`、`fix_execution_status.md` | 视需要提交或归档 |

---

## 六、硬性约束（AGENTS.md）

1. **禁止在未提交工作区上使用 `git checkout --` / `git restore`**（本次事故根源）。定点修复用 edit 工具；需要保存现场用 `git stash push`。
2. **禁止用 PowerShell `Set-Content -Encoding UTF8` 改写 Python 源文件** —— 会写 BOM，且 `Get-Content`/`Set-Content` 往返破坏中文注释。
3. B 档任一项动手前，按 `AGENTS.md` §三.5 用**当前在册 4 人名单** `[dsh, opencode, qoder, workbuddy]` 发起讨论；`claude` 已退出，名册留它会导致 unanimous 永不收敛。
4. 先分析根因再考虑 workaround；异常数据分布先查数据流路径的 WHERE 条件链。
5. 改完做完整验证（import / parse / 干运行），不分步 debug。

---

## 七、关键上下文

- **DB 规模**：`C:\Users\Administrator\.memall\data.db`，约 6690 条记忆（normal 4273 / archived 2155 / conflict 189 / dormant 73）
- **三条僵尸讨论**：`#1000476` / `#1000481` / `#1000484`，自 2026-10-23 起 active，参与者是 `alice/bob/carol` 等不存在身份 —— A1-3 的 `expired` 归档正是为它们写的
- **联邦冲突**：21 条 open，其中 `#544`/`#546` 是关键词误报典型，A2-1 修复后应消失
- **无冲突解决入口**：联邦层只有 `conflicts`（列出），**没有任何 resolve 动作**，这 21 条会永久 open（对应 C4，不在 B 档范围）

---

## 八、建议的接手第一步

1. `cd E:\MemALL && git log --oneline -3` 确认 A 档两个提交在位
2. `python tools/verify.py --plan fast` 复跑门禁，确认基线绿
3. 读 `pending_b_tier/README.md` 全文（本文只摘了要点）
4. 从 **B1** 起步，改完单独提交 —— **⚠️ B1 已于 9e05809 完成，见第九节；实际下一步是 B5**

---

*本报告可整段复制给新会话作为起始上下文。详细分析见 `memall_multiagent_analysis.md` 与 `pending_b_tier/README.md`。*

---

## 九、状态更新（2026-11-06）

本文写成（11-02）后仓库前进了三个提交，其中两个直接改变了 B 档状态。以下为逐项核对结论（均在 `4ecd0ce` 之上实测）。

| 项 | 本文原描述 | 11-06 实际状态 |
|---|---|---|
| **B1** 收敛接入立场 | 需从零重做 | **已完成**，opencode 于 `9e05809`（11-05）落地。守卫 `convergence_honours_stance` 由我补于 `c77224d` |
| **B3** 完成即通知 | 需从零重做 | **已完成**，`f8e1de0`。守卫 `task_resolution_notifies_dependents` |
| **B5** Hub 同步游标 | 需从零重做 | 仍未做。`hub_sync` 仍是 `ORDER BY id DESC LIMIT ?` |
| **B2** 死信队列 | 文件幸存、未接入 | 仍未接入。`pending_b_tier/` 原样；下一个空闲迁移号是 **043** |
| **B4** `fed_deliver` 停止冒充 | 需从零重做 | 仍未做。仍是 `capture(agent_name=target_agent)` |

### B1 的实现要点（复审时对照）

`_stance_tally()` 把回复分成 agree/reject/abstain 并跳过 `observer`；`_rule_satisfied()` 收 `rejecting` 参数——unanimous 下任何 reject 即不收敛，majority 下 `len(rejecting) >= len(agreeing)` 不收敛，`any` 故意不否决；`rejecting=None` 保持历史纯计数行为。`_converge_single()`（后台 sweep）与 `_confirm_discussion_once()`（即时自动收敛）两条路径都已接入。实测：全员 reject 不收敛、不生成 L4/L5；2 agree + 1 reject 不收敛；全员 agree 正常收敛。

### B3 的实现要点（复审时对照）

`_find_dependents()` 扫未归档 L5 卡，`metadata.depends_on` 兼容数组与标量，跳过已终态的后继；`_notify_task_dependents()` 在 **commit 之后**调用，同 assignee 合并成一条，走 `push_session_note(dedup_key="dep-resolved:<id>:<ids>")`，note store 异常静默降级不影响 resolve 结果。无 assignee 的卡不猜收件人。

### 其余变化

- **门禁基线**：`verify.py --plan fast` 在沙箱内为 import 绿 / 红蓝 9/9 / pytest-quick 82 通过（依赖需从清华镜像装：`pip install --break-system-packages -i https://pypi.tuna.tsinghua.edu.cn/simple ...`；VM 直连 PyPI 超时）
- **工作区 82 个 "modified" 是 CRLF 假象**：index 是 LF、工作区是 CRLF、无 `.gitattributes`。`git diff --ignore-cr-at-eol` 实际为 0。**提交时必须 `git -c core.autocrlf=input add <file>`**，否则会把整文件行尾改写成 CRLF 混进提交
- **DB 规模**：交接时约 6690 条，现为 13,480 条（normal 11,188 / archived 2,020 / conflict 196 / dormant 76）
- **推送**：`origin/master` 已与本地同步（先前"本地领先 2 个提交"已解决）
- **名册**：11-06 已按在册 4 人 `[dsh, opencode, qoder, workbuddy]`、`majority` 发起讨论 `#1016066`（B 档续做）

### 下一步建议

**B5 → B2 → B4**（顺序不变，B1/B3 已出列）。B5 的最大坑仍是"不要把游标逻辑插进 `hub_sync()` 函数体中部"；B2 先 `ls src/memall/migrations/` 取空闲编号并按 `041_create_session_notes.py` 的 `apply(conn)` 协议重写；B4 必须先扩 `normalize_agent_name` 正则，否则 `hub:<sender>` 会静默变成 `system`。
