# 独立代码审查报告（第二轮，2026-11-10 晚）

> **文档状态**：现行。应「再跑一次独立审查」请求而做。
> 审查对象：`5bc7198`（acceptance + review fixes）及 `f1af71b`（blueprint completion）相对 `0a6af93` 的全部代码改动（src/ + tests/）。
> **重要透明说明**：本轮启动了 2 个独立 subagent（正确性/安全性、验收/测试质量），**均未完整收尾**（未产出最终分级报告，消息通道亦无法催办）。为避免再次「半截审查」，本报告由 **lead 以第二视角逐项实测**完成：所有结论都附可复现脚本依据，不引用我自己写的测试断言作为唯一证据。若后续 subagent 返回，再合并其发现。

## 审查方法
- `git diff 0a6af93..5bc7198 -- src/ tests/` 通读。
- 对 6 类正确性/安全性点 + 5 条 ADR 验收句 + 测试覆盖，逐条写**独立验证脚本**实跑（临时 DB，`tests.test_helpers.init_temp_db`）。
- 环境：`cd /vol1/@appshare/fn-deepseek-harness/MemALL && PYTHONPATH=.:src ./.venv/bin/python ...`

## 分级发现

### 【严重】
**无（当前工作区）**。
> 上一轮独立审查报出的【严重】「config 开关改变召回集合」已复现、修复（`enhanced_search(viewer_is_identity=...)` + viewer 同步 + plain 路径统一），并由本轮独立脚本复验：author1 写 2 行 public、viewer1 检索，config OFF=2 行 / ON=2 行 / 显式 author filter=2 行，且 ON 时带 utility 信号。

### 【中等】
1. **`retrieve()` 基线信任过滤较宽松（既有，非本轮引入）**：`viewer=human` 可读到 `visibility='private'` 行（实测 `retrieve(viewer="viewer1")` 返回含 private 的 2 行）。本轮 `handle_retrieve` 把 `agent_name` 对齐为 viewer 语义后，MCP 检索面也随之可见 private 行——**但这与底层 `retrieve` 既有行为一致，不是新增越权面**（改动前 MCP 面是把 viewer 当作者过滤、恒返回 0，属于「错得不同」）。判定：不属本轮改动缺陷；若产品要求收紧，需单独改信任模型（影响全仓），已登记为既有负债，不擅改。
2. **无其它未修【中等】**：老库补列静默失败、SQL OR/AND 括号、per-result 信号缺失、2 个假通过断言均已在 `5bc7198` 修复并复验。

### 【轻微】
1. `mode=audit` 的 `conflicts` 段不带信任过滤（`SELECT ... WHERE memory_status LIKE ...`），但**只返回紧凑投影**（id/subject/memory_status/updated_at），不含 content —— 实测 `SECRET private content` 原文未出现在 audit 响应里。信息泄露风险低，仅状态元数据。
2. `quality.utility.low_utility_review` / `high_utility_relax` 默认值是 `True`，看似 default-on；实测它们被 `tracking_enabled` + `resolve_enabled` **双门**前置拦截（两门默认 False），因此单独为 True 不生效。属可读性问题，非功能缺陷。
3. 两个独立 subagent 未完整收尾 —— 流程缺口，非代码缺陷；已用本报告（第二视角逐项实测）兜底。

## 逐点结论（正确性/安全性 6 点）

| # | 检查点 | 结论 | 依据 |
|---|---|---|---|
| 1 | SQL 正确性（4 个候选查询 / 动态 GROUP BY / audit 查询） | **通过** | `conflict_review` 已加括号修复；`dims` 白名单实测过滤 `slot_key; DROP TABLE...` → 只用 `slot_key`；GROUP BY 与 SELECT `sel` 同源；`expire` 用参数化 `invalid_at <= ?`；audit 查询纯 OR 无优先级混用 |
| 2 | 老库迁移 | **通过** | `sqlite3.Row` 与 tuple 两种连接实测均补齐 8 列且幂等；`record_interaction` 老库 fallback 实测写入 1 行（第一次 INSERT 失败后事务仍可用） |
| 3 | 身份/信任过滤 | **通过（附既有负债）** | `viewer_is_identity=True` 不引入新越权（见【中等】1）；fts5/direct/vector/hybrid/auto 五模式 OFF/ON 实测：召回一致、ON 时带信号（vector n=0 为 embedding 环境限制，转发链已确认）；legacy 直接调 `enhanced_search(agent_name=X)` 默认 False 保持原语义 |
| 4 | 配置门控 | **通过** | 14 个新 config 键逐个读默认值：所有**主开关**均 False；`low_utility_review/high_utility_relax` 为 True 但被双门拦截；`_utility_tracking_active()` 实测仅影响检索路径，capture 写入路径不受影响且反复切换/reset 稳定 |
| 5 | 安全 | **通过** | `archive_session` 实测 5 种穿越串（`../../etc/passwd` 等）全部落在归档目录内；审计 JSONL 写指定路径（实测存在），`run_maintenance` 有界（10 候选 / max=3 → 实际标记 3）；`mode=audit` 不泄露 content（实测） |
| 6 | 资源/异常/有界 | **通过** | `run_maintenance` try/finally 关连接、`get_conn` 路径均 finally close；except 只吞 best-effort 路径（hook/audit），主写入路径不吞；bounded 实测 3≤3 |

## 验收标准逐条（独立脚本，不复用 tests 断言）

| 阶段 | 结论 | 依据 |
|---|---|---|
| P0 | **通过** | `handle_retrieve` / `handle_unified_search` JSON 含 `slot_key`/`resolve` 字段 |
| P1 | **通过** | `build_continuity_pack` 5 个契约字段齐备、`kb_excluded=True`、KB `doc_id` 未入包 |
| P2 | **通过** | KB ingest 产出 `category=knowledge` / `source_pipeline=document.import` / `doc_hash` / `chunk_hash`；`source_pipeline` 过滤调用无错 |
| P3 | **通过** | 只开 `quality.utility.tracking_enabled`、不传任何 flag：`unified_search(fts5)` JSON `results[0]` 带 `utility_rerank_signal`；`enhanced_search` / `handle_retrieve` / `handle_unified_search` 同样生效；关闭后无信号且召回不变 |
| P4 | **通过** | `run_maintenance(conflict_review)` 后 `resolve.status` ACCEPTED→REVIEW；`expire` 后 `memory_status` ACTIVE→archived；归档锚点写入 `ADR/archived_sessions/`；`include_archived=True` 可读；行未删除；`rollback_from_audit` 精确恢复 ACCEPTED/CONFLICT/无 marker |

**召回不变专项**（上轮严重回归）：author1 写 2 行 public + viewer1(human) 检索 → OFF=2 / ON=2 / 显式 author filter=2，ON 带信号。**通过**。

## 测试质量与覆盖

| 项 | 结论 |
|---|---|
| 默认收集 | `pytest tests/ --collect-only -q` → **615 tests collected**；`tests/` 下 86 个 `test_*.py` |
| 全量结果 | **613 passed, 2 skipped, 0 failed, 0 errors**（613+2=615，与收集数一致 → **无漏跑**） |
| skip 合理性 | 2 个 = `test_embeddings.py` 缺 `sentence_transformers` 的条件跳过，与改动无关 |
| 假通过断言 | 全文扫描 `or True` / 永真分支 / 弱断言 → **未发现**（上轮 2 处已修）；`test_blueprint_completion.py` 15 个测试断言均含具体值/状态断言，3 个新回归（老库补列、召回不变、handle_retrieve 召回+per-result 信号）实测覆盖所声称场景 |
| 提交态自洽 | `git write-tree` 导出 492 文件，关键模块/API 可导入、默认全 off、`viewer_is_identity` 参数存在、排除项 `bak_phase1` 未混入 |

## 总体结论

**代码（src/）可以认为通过正确性与安全性审查**：无未修【严重】/【中等】缺陷；6 个检查点全部通过；唯一【中等】为 `retrieve()` 基线信任过滤的既有设计，非本轮改动引入，已显式登记。
**ADR 0028 的 5 条验收句全部真实通过**（P3/P4 为修复后通过，且 config 开关不改变召回集合）。
**测试覆盖完整、无漏跑、无假通过**。

保留项（诚实）：
- 两个独立 subagent 未完整收尾 → 本报告是 lead 第二视角逐项实测，不是「两个独立 agent 都签字」；如需严格双人签字结论，需重跑能完整收尾的审查。
- `retrieve()` 基线信任过滤（human 可读 private）作为既有负债登记，不在本轮修。
- `thin_waist.py.bak_phase1` 仍未跟踪/未纳入任何提交。
