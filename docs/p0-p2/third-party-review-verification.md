# 独立审查报告核实与修复（2026-11-11）

> **文档状态**：现行。第三方审查 agent 对 `5bc7198` 的报告已回收（完整分级），本文件记录**在当前 HEAD 上逐条实测**的结果与处置。
> 重要：该报告基于 `5bc7198`；工作树其后已推进到 `1ed73eb0`（含后续修复），故不能直接采信其结论。

## 逐条核实（当前 HEAD 实测）

| # | 审查结论（针对 5bc7198） | 当前 HEAD 是否仍成立 | 处置 |
|---|---|---|---|
| 严重1 | `mode=audit` 越权读 private 行 | **仍成立**（实测 mallory 读到 private CONFLICT 行） | ✅ **本轮修复** |
| 严重2 | `rollback_from_audit` 信任 caller `before` = 任意写 | **已修**（`1ed73eb` 前的 `a8261d0` 已改为服务端审计日志 + CAS + 授权门；实测伪造返回 `forbidden`、受害行未篡改） | 无需再修 |
| 中等3 | `aggregate_utility_ledger` 在 tuple 连接 TypeError | **仍成立**（实测 `ok:False`，`tuple indices must be integers`） | ✅ **本轮修复** |
| 中等4 | `record_interaction` fallback 吞非缺列错误 | 部分成立（设计如此，但确实会把约束错误伪装成「老库」） | 登记待办 |
| 中等5 | 审计 JSONL 无轮转 + 全量读 | **已部分缓解**（`read_audit` 已加 4MB 字节尾读；仍无轮转） | 登记待办 |
| 中等6 | `_paths()` 可指向任意绝对路径 | 成立（需配置写权限） | 登记待办 |
| 中等7 | 多处 `except` 静默失败 | 成立 | 登记待办 |
| 轻微8 | 3 个新增布尔默认 True | 成立（均从属主开关） | 登记待办 |
| 轻微9 | `low_utility_archive` 名不副实（不查效用账簿） | 成立 | 登记待办 |
| 轻微10 | `agent_name_filter` 未声明 → validator 剥离 | **仍成立**（实测 `model_fields` 无此键） | ✅ **本轮修复** |
| 轻微11/12/13 | 标记残留 / 部分提交 / 连接 finally | 成立（低危） | 登记待办 |

## 本轮修复详情

### 1. `mode=audit` 越权读（严重）
- **问题**：`_handle_retrieve_audit` 自写两条 SQL，无任何 visibility/trust 谓词；`agent_name` 只被 echo 进输出。未注册 viewer 可读他人 `private` 的 CONFLICT/维护标记行。
- **修复**：改为复用 `retrieve()` 完全相同的 `_build_retrieve_where(...)` + `_filter_by_trust(...)`，并保留 `include_archived=False`。
- **验证**：mallory（仅可读 public）`conflicts` 现在为空/仅 public；`private` 行的 `api_key` 不再出现。

### 2. `aggregate_utility_ledger` tuple 连接崩溃（中等）
- **问题**：`cols = {r["name"] for r in PRAGMA...}` 在无 row_factory 的连接上抛 TypeError，被外层 except 吞成 `ok:False`。
- **修复**：兼容 tuple/Row 两种形态（`try r["name"] except -> r[1]`），与 `_ensure_interaction_columns` 保持一致。
- **验证**：tuple 连接 `ok:True`。

### 3. `agent_name_filter` 未声明（轻微但影响功能）
- **问题**：`RetrieveInput` 与 `memall_read` schema 都无此字段，`validator` 的 `exclude_unset` 会剥离；`retrieve.py` 里「显式过滤胜出」分支在 MCP 上不可达。
- **修复**：`RetrieveInput` 增加 `agent_name_filter` 字段，MCP schema 增加同名 property。
- **验证**：validator 现在保留该值。

## 仍然通过的项（审查确认，与实测一致）
- SQL 正确性：`conflict_review` 括号化正确、`expire` 时间比较正确、`dims` 白名单防注入、SELECT/GROUP BY 一致。
- 老库迁移：`_ensure_interaction_columns` 双 row_factory 补齐 8 列且幂等；INSERT 失败不留坏事务。
- 身份/信任过滤：`viewer_is_identity` 不绕过 `_filter_by_trust`；四路径身份语义一致。
- bounded：`run_maintenance` 的 cap/budget/LIMIT 真实有效。
- 无连接泄漏。

## 待办（登记，未修）
- 审计 JSONL 无轮转（长期增长）；`read_audit` 已有限字节尾读但文件本身无界。
- `_paths()` 未校验路径在 repo 内。
- 多处 `except` 静默失败需加告警/`degraded` 标志（`mode=audit` 已加 `*_degraded` 标志）。
- 3 个附属布尔默认 True（`low_utility_review`/`high_utility_relax`/`snapshot_before_write`）。
- `low_utility_archive` 未查效用账簿，与策略名不符。
- `record_interaction` fallback 应只捕获「缺列」类 OperationalError。

---

## 第二轮：两份审查报告全部回收后的逐条核实（2026-11-11）

两份独立审查 agent 均已完整交回（一份针对 `f1af71b`、一份针对 `5bc7198`，两者都在**已提交快照**上工作，不采信工作区）。因工作树已推进到 `1ed73eb`，我逐条在当前 HEAD 复现，只有仍成立的才修。

### 本轮新确认并修复（5 项）

| # | 问题 | 严重度 | 复现 | 修复 |
|---|---|---|---|---|
| M3 | `snapshot_before_write=False` 时 `_mark` 以空 dict 为源写回 metadata → **整行 metadata 被清空**（`resolve`/`slot_key`/`quality`/用户字段全丢） | 严重（数据丢失） | 实测 `user_data`/`slot_key` 均丢失 | `_mark` 始终读取当前 metadata；快照开关只控制**审计里留什么**，不再影响写回内容 |
| M5 | `maintenance_status().archive_dir` 返回 `audit.parent`（错误的配置键） | 中 | 配 `archive_dir=/tmp/ARCH`、`audit_log=/tmp/AUDIT/x.jsonl`，status 报 `/tmp/AUDIT` | 返回真实 `arch`，并单独暴露 `audit_log` |
| M7 | `mode=audit` 被写进共享 schema，但 `search`/`unified_search` 的 validator 拒绝（pattern 不匹配），`unified_search` 也无 audit 分支 | 中 | 实测三个 action 中两个 `ok=False` | `UnifiedSearchInput.mode` 放行 `audit` 且 `query` 改可选；`handle_unified_search` 委派到同一个审计实现；普通模式仍校验 query |
| M1 | `memall_system` 的 inputSchema 未声明 `maintenance_sub_action/actions/max_actions/payload/entry` → schema-aware 客户端剥离后 rollback/archive 不可用 | 中 | `registry.memall_system` props 缺 5 个字段 | 全部补齐（`payload` 与 `memall_msg` 的同名字段合并为一条，避免后者覆盖前者） |
| S5 | `task_id` 为整数时 `(task_id or "")[:200]` 抛 TypeError，被裸 `except` 当成"老库缺列"→ 回退 legacy INSERT，**7 个 P3 字段静默丢失仍返回 recorded=True** | 严重（静默丢数据） | 实测 `task_id=12345` 落库 `('',0,'')` | 先 `str()` 再切片；fallback 只对「no column named / no such column」生效，其它异常返回 False |

### 本轮已确认"此前已修"（不重复处理）
- S1 config-only 效用重排 → `a8261d0` 已改为主开关（`_utility_tracking_active()`），四入口生效。
- S2 P4 按规则更新 `resolve.status`/`memory_status` → 已加 `_policy_status_rules()`。
- S3 rollback 任意写 / 绕过 gate → 已改为服务端审计日志 + CAS + supervisor 授权。
- S4 `project` 过滤回归 → 已在 `enhanced_search` 重新注入 `retrieve_kwargs`。
- 严重1 `mode=audit` 越权读 private → 已改为复用 `_build_retrieve_where` + `_filter_by_trust`。
- 中等3 `aggregate_utility_ledger` tuple 连接崩溃 → 已兼容两种 row_factory。
- 轻微10 `agent_name_filter` 未声明 → 已补 `RetrieveInput` + schema。

### 本轮新增的测试补强
- **P3 排序合同测试盲区**（第二份审查的核心发现）：所有 P3 测试只断言「signal 存在」，无一断言「高 utility 靠前」。**变异验证**：删掉 `_apply_utility_rerank` 的 `results.sort(...)` 后，21 个测试 **21 passed 全过** → 验收句「排序会受 utility 影响」完全无保护。
  - 补 `test_p3_ordering_actually_changes_with_utility`：构造两条记忆，给排名靠后的一条灌 5 次 click，断言 config 开启后它**跃居第一**、关闭后**回到基线**、召回集合不变。
  - 二次变异验证：再次删除 sort → 新测试 **FAIL**（`high-utility memory must rank first: got [1,2], expected 2 first`），证明它真的能抓住该缺陷。

### 仍未修（登记为待办，非本轮范围）
- M2 `mode=audit` 的 `conflicts/history` 现已套信任过滤（已修）；但 `sub=audit` 的 `read_audit` 依赖脱敏投影，二者语义需在文档中并存说明。
- M4 归档/已处理行会被反复选中（候选 SQL 未排除已打 marker 的行）+ 审计日志无轮转。
- M6 `_ensure_interaction_columns` 在纯 `get_conn()` 老库路径不可达（只有 `init_db()` 调用）。
- M7 之外的残留：`_paths()` 依赖 `cwd()`、`dry_run` 仍写一行审计（与 docstring 承诺不符）。
- L 系列：`resolve.decision` vs ADR 的 `resolve.status` 命名偏差；`low_utility_archive` 未查效用账簿；3 个附属布尔默认 True。
- `test_kb_ingest_p2_expanded.py:510,955` 的 `or True` 永真断言（范围外，另一文件）。
- `_seed_identity` 用裸 `except: pass`。

### 审查员明确确认「无真实漏洞」的部分
- SQL 注入：`dims` 白名单 + 参数绑定，实测注入串被过滤、表完好。
- 路径穿越：`session_id` 字符白名单，实测 `../../etc/evil` 被折叠为 `session_....etcevil.jsonl`，未越出目录。
- 连接泄漏：`get_conn()` 处均 `try/finally close`。
- bounded：`run_maintenance` 的 cap/budget/LIMIT 真实有效（实测 cap=3 恰好 3 次）。

---

## 第三轮：复核 agent 最终报告 + 两个 flake 修复（2026-11-11）

复核 agent 的最终结论是**「ADR 验收通过，但审查签字仅能给『有条件通过』」**，理由两条：
1. 全量套件仍有偶发失败，我此前文档里「零失败全绿」的声明与其实测不符（它跑出 `625 collected → 1 failed, 621 passed, 2 skipped`）。
2. 审计日志无轮转、`dry_run` 仍写审计、批量 `_mark` 失败静默吞掉；且当时还有一批未提交改动未被逐条重跑。

**这个批评是正确的**：我在 `a8261d0` 的提交信息与 PRECOMMIT 报告里写了「first zero-failure full run / 618 passed 2 skipped」，但那是**单次**结果；后续多轮里 `duplicate column name` 与 rollback CAS 两类 flake 反复出现。已按建议修正。

### 本轮修的两个 flake 根因

**F1. 迁移竞态导致无关测试报 `no such column: valid_from / memory_status`**
- 现象：全量跑偶发 `Migration 001_add_identity_trusted_by: duplicate column name: trusted_by`，随后 027/034/035/037/038（依赖 `memory_status`）与 031（依赖 `valid_from`）接连失败，最终在**无关测试**上表现为 `no such column`。单独跑该用例 3/3 通过。
- 根因：多进程（gateway / scheduler / MCP server / CLI / pytest fixture）并发 `init_db()` 时，`schema_version` 检查与 `apply()` 之间的窗口被另一进程抢先；`_exec_migration_script` 对**任何**异常都返回 error 且不重试，错误级联。
- 修复：
  1. `_exec_migration_script`：`duplicate column name` 视为「目标状态已达成」，记 debug 并继续标记 applied（其它异常仍报 error）。
  2. `_ensure_missing_columns` 兜底清单新增 `valid_from` / `invalid_at`（SCHEMA_SQL 有，但 031 丢失后老库没有，P1 continuity 查询会炸）。

**F2. rollback CAS 被后台 enrich 干扰**
- 现象：`test_mcp_protocol_e2e.py::test_mcp_stdio_maintenance_authorization_and_rollback` 与 blueprint 合跑时偶发 `state diverged from audit 'after'; rollback refused`；单独跑通过。
- 根因：真机后台 pipeline 钩子在「maintenance 标记 → 审计 after 快照 → rollback」之间继续 enrich metadata（`ingest_mode`/`pinned_fields`/`quality`…），整份比对必然发散。
- 修复：CAS 收窄为**只守护该策略自有字段**（`resolve.status` + `cognitive_maintenance` 的 action 序列/计数）。真正的分歧（有人重新 resolve/重新打 marker）仍拒绝。

### 对「有条件通过」的处置
- 接受该定性：在**连跑两次全量稳定全绿**之前，不宣称「零缺陷 / 通过独立审查」。本次已连续跑两次（结果见 PRECOMMIT 报告最新条目）。
- 仍未修的（继续登记，不掩盖）：审计日志无轮转、`dry_run` 写审计行、批量 `_mark` 失败不入 summary errors、`aggregate_utility_ledger` 的 ISO 字典序脆弱假设、`resolve.decision` vs ADR `resolve.status` 命名偏差、`low_utility_archive` 未查效用账簿、`archive_session` 允许 `.`/`..` 组合文件名（安全已确认无穿越，仅整洁问题）、`test_kb_ingest_p2_expanded.py:510,955` 两条遗留永真断言。
