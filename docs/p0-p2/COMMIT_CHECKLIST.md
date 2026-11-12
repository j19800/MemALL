# 提交清单（ADR0028 本地验收范围）

## 已提交 commit `92863b6`
```text
12 files, 2459/+56 lines
  ADR/0028-memory-cognitive-loop-implementation-plan.md     (new)
  src/memall/config.py                                    (modified)
  src/memall/core/kb_policy.py                          (new)
  src/memall/core/thin_waist.py                         (modified)
  src/memall/core/thin_waist_search.py                   (modified)
  src/memall/mcp/tools/__init__.py                      (modified)
  src/memall/mcp/tools/memory_write.py                  (modified)
  src/memall/mcp/tools/retrieve.py                      (modified)
  src/memall/pipeline/kb_enrich.py                      (new)
  src/memall/pipeline/session.py                        (modified)
  tests/test_kb_ingest_p2.py                            (new)
  tests/test_kb_ingest_p2_expanded.py                   (new)
```

## 验收命令（通过确认本次提交）
```bash
cd /vol1/@appshare/fn-deepseek-harness/MemALL
PYTHONPATH=.:src ./.venv/bin/python -m pytest \
  tests/test_kb_ingest_p2.py tests/test_kb_ingest_p2_expanded.py \
  tests/test_capture_resolve_engine.py \
  tests/test_session.py tests/test_session_sweep.py -q
# 预期：46 passed (boundary audit/contracts, 2026-11-10)
```

## 排除项（勿在本次提交中纳入）
> ⚠️ 下方"13 个排除项"的旧口径**已于 2026-11-10 用代码事实裁决为错误**，见文末"scope 裁决"章节。以文末为准。
- `src/memall/core/thin_waist.py.bak_phase1`——预存备份文件，排除。
- `tests/test_capture_quality_contract.py`——属另一条 "capture quality" 工作线（未跟踪），排除。
- `tests/test_capture_resolve_engine.py`——ADR0028 P0 resolve engine 测试，**应随本次提交纳入**（旧记录称"已独立提交"与 git 状态不符：该文件仍为未跟踪）。
- 旧口径曾把 `src/memall/mcp/models.py`、`federation_tools.py`、`validator.py` 等列为"无关改动"——**错误**：`92863b6` 基线的 `mcp/tools/__init__.py` 已路由 `continuity_pack`/`kb_ingest`，但基线 validator 无对应 action 模型，排除这些文件会造出 unknown-action 的 broken commit。它们必须纳入。
- 真正属另一条独立工作线、本次排除的 9 个文件：`gateway_sync.py`、`mcp/tools/capture.py`、`pipeline/{auto_dream,cross_agent,lifecycle,llm_enhance,observe,ops,reflect}.py`（capture robustness / readability 归一化，与 KB/continuity/utility 主线正交；已用 `git stash` 隔离验证排除后 scoped 回归仍 70 passed）。

## 阶段性确认
- [ ] P0 测试基线已稳（4/4 test_capture_resolve_engine.py pass）
- [ ] P1 continuity_pack MCP 契约已验收（15/15 test_kb_ingest_p2_expanded.py pass，含 KB 排除 + 契约键）
- [ ] P2 KB boundary hardening 已验收（15/15 test_kb_ingest_p2_expanded.py pass，含 kb_policy 权威 + enrichment hook）
- [ ] 所有新增字段/过滤均为 additive/back-compat：老代码看不见不报错
- [ ] KB 是 evidence，never user-state：session_start/continuity_pack/recap 均已过滤 KB 证据
- [ ] config `"kb": {"enrich_async": True}` 已写入 src/memall/config.py（P2-4 config gate）

## 2026-11-10 未提交改动（边界审计 + P2-4 基础，待本地验收/分批提交）
- `src/memall/mcp/federation_tools.py`：`auto_inject` KB SQL/category 排除 + additive `kb_excluded`/`excluded_source_pipelines`。
- `src/memall/pipeline/session.py`：`session_start` 全量 injection 递归 KB 防御过滤（含 `metadata_source_pipeline`/parsed metadata）；`build_continuity_pack` temporal validity SQL 过滤 + `excluded_source_pipelines` 来自 `KB_SOURCE_PIPELINES`。
- `src/memall/mcp/tools/retrieve.py`：`handle_recap`/continuity 相关契约与 `KB_SOURCE_PIPELINES` 对齐（如已在工作区）。
- `src/memall/mcp/tools/memory_write.py`：`handle_kb_ingest` 成功 capture 后 `_fire_kb_enrich`。
- `src/memall/core/db.py`：`pipeline_jobs` 持久化表 DDL（P2-4 队列基础）。
- `src/memall/mcp/tools/__init__.py`：`memall_system` 新增 `kb_enrich_stats` action + schema enum。
- `tests/test_kb_ingest_p2_expanded.py`：boundary contracts（`test_p1_continuity_pack_mcp_mode_routing_contract` portable/additive, `test_p2_auto_inject_kb_excluded_contract`, `test_p1_session_start_whole_injection_kb_exclusion` 等）。
- `docs/p0-p2/*`：文档/债务/清单更新。

**验收（2026-11-10）**：`tests/test_kb_ingest_p2.py tests/test_kb_ingest_p2_expanded.py tests/test_capture_resolve_engine.py tests/test_session.py tests/test_session_sweep.py` → **48 passed**。

**注意**：`tests/test_capture_resolve_engine.py` 当前未跟踪，提交前决定是否纳入；排除 13 个预存无关修改和 `thin_waist.py.bak_phase1`/`test_capture_quality_contract.py`。


- [ ] `tests/test_kb_enrich_worker_p2.py`（P2-4 durable worker 定向测试，已确认跟踪）

## Latest local acceptance (2026-11-10, not committed)
- `py_compile` OK on touched core/tests.
- `PYTHONPATH=.:src ./.venv/bin/python -m pytest tests/test_kb_ingest_p2.py tests/test_kb_ingest_p2_expanded.py tests/test_kb_enrich_worker_p2.py -q` → **48 passed** (historical). Latest scoped regression (2026-11-10): `tests/test_kb_ingest_p2.py tests/test_kb_ingest_p2_expanded.py tests/test_kb_enrich_worker_p2.py tests/test_capture_resolve_engine.py tests/test_session.py tests/test_session_sweep.py -q` → **71 passed**.
- Broader scoped regression command recorded in `PRECOMMIT_SCOPED_REPORT_2026-11-10.md` (check latest result before staging).
- **Do not commit** until user explicitly approves final scope and all required regressions pass.

## 最新本地验收状态（2026-11-10，模型切换后继续全自动推进，未提交）

### 本轮新增改动（P1 边界 / P2-4 CAS / P3 传播修复）
- `tests/test_kb_ingest_p2_expanded.py`：新增 4 个 P1 边界契约测试（temporal validity / include_conflicts×validity / recent_supersedes / session_start MCP KB 排除）。
- `src/memall/pipeline/kb_enrich.py`：`_claim_job` 改为 CAS 条件 UPDATE（`WHERE id=? AND status='queued' AND ...`），dedup 分支同样加条件；修复跨进程重复 claim 竞争。
- `tests/test_kb_enrich_worker_p2.py`：新增 3 个测试（CAS 单赢家 / running 行不可重 claim / process_budget 有界与钳制）。
- `src/memall/core/thin_waist.py`（P3 真实缺陷修复）:
  - `enhanced_search` 增加显式 `utility_rerank` 参数；修复增强路径里引用未定义 `arguments` 的 NameError（原 3220 行）。
  - 增强分支 guard 纳入 `utility_rerank`；把内部 dict 上的 `utility_rerank_signal/score` 透传到 Memory 对象和 `out["utility_rerank_signals"]`。
  - `unified_search` 解析 `utility_rerank`、纳入 `wants_enhancements`、向 `enhanced_search` 与 vector/hybrid 的 `_apply_enhancements_inplace` 转发；fts5/direct 的 payload 序列化带上 additive 信号字段。
- `tests/test_utility_rerank_p3.py`（新文件）：6 个 P3 E2E 传播契约（default-off 无信号 / enhanced_search 无 NameError / fts5+hybrid / 保守门控不丢结果 / MCP handle_retrieve + handle_unified_search）。

### 验收结果（截至本轮）
- P1 新增 4 测试：`4 passed`。
- P2-4 worker 全量：`16 passed`（含 3 个新 CAS/有界测试）。
- P3 E2E 全量：`6 passed`。
- 扩展 scoped regression（含 `test_cluster.py`/`test_e2e.py`）：结果见 `PRECOMMIT_SCOPED_REPORT_2026-11-10.md` 最新条目。

### 提交门禁（仍不提交）
- [ ] 全部 scoped regression 通过且记录实际数字。
- [ ] `git diff --name-only` 复核，只暂存本轮 ADR0028 范围文件；排除 13 个预存无关修改、`thin_waist.py.bak_phase1`、`test_capture_quality_contract.py`。
- [ ] 用户明确批准后再提交。

## scope 裁决（2026-11-10，全自动推进，落地提交前用代码事实复核）

**背景**：早期文档把 ~13 个已改文件笼统标为"预存无关改动，勿纳入"，且声称 `test_capture_resolve_engine.py`"已独立提交"。复核发现二者均与仓库事实不符。

**裁决依据**：
1. `git log` 显示基线为 `92863b6`（ADR0028 首轮 12 文件）；工作区在其之上累积了多轮改动。
2. 实证：`git show 92863b6:src/memall/mcp/validator.py` 不含 `kb_ingest`/`document_import`/`continuity_pack`；但 `git show 92863b6:src/memall/mcp/tools/__init__.py` **已**路由这些 action → 基线本身存在 "handler 已路由 / validator 未登记" 缺口。`mcp/models.py`（新增 `KBIngestInput`/`ContinuityPackInput`）+ `validator.py`（登记 action 模型）+ `federation_tools.py`（auto_inject KB 排除）是让 ADR0028 功能可用的必需配套。
3. `src/memall/core/thin_waist.py` 工作区 diff 全部 14 个 hunk 均为本轮 P3（unified_search/enhanced_search 的 utility_rerank 传播），未混入其它工作线。
4. 9 个 "capture robustness / readability" 文件用 `git stash` 收起后，ADR0028 scoped 回归仍 **70 passed** → 证明它们不是本提交依赖，属可独立提交/继续处理的工作线。

**本提交实际纳入（ADR0028 主线，功能自洽闭包）**：
- 文档：`ADR/0028-*.md`、`docs/README.md`、`docs/p0-p2/*`（全部 8 份）
- 代码：`src/memall/config.py`、`core/db.py`、`core/models.py`、`core/thin_waist.py`、`gateway.py`、`mcp/federation_tools.py`、`mcp/models.py`、`mcp/tools/__init__.py`、`mcp/tools/memory_write.py`、`mcp/tools/retrieve.py`、`mcp/validator.py`、`pipeline/kb_enrich.py`、`pipeline/session.py`、`search/enhancements.py`
- 测试：`tests/test_kb_ingest_p2_expanded.py`、`tests/test_kb_enrich_worker_p2.py`、`tests/test_utility_rerank_p3.py`、`tests/test_capture_resolve_engine.py`
- （`tests/test_kb_ingest_p2.py` 等 12 文件已在 `92863b6` 提交，无需重复）

**本提交排除**：`thin_waist.py.bak_phase1`、`tests/test_capture_quality_contract.py`、上述 9 个 capture-robustness 文件（保留在工作区/stash，稍后恢复）。

**验收**：全量 `pytest tests/ -q`（排除 smoke/stress/benchmark/quality_test）→ **578 passed, 2 skipped, 1 failed, 2 errors**；失败/错误均为 `duplicate column name: primary_layer/secondary_layers/stream` 的共享 DB 竞态（隔离重跑 3 passed，pre-existing，与本次改动无关；`db.py` diff 仅新增 `pipeline_jobs` 表 DDL）。ADR0028 scoped 回归 → **70 passed**。

## ✅ 已落地提交（2026-11-10，全自动推进）
- **commit `26db549`**（本地，未 push）：29 files, 2560+/53-。
- 提交集 = 上文"scope 裁决"章节列出的 ADR0028 主线闭包（文档 8 + ADR + README + 代码 14 + 测试 4，共 29）。
- 提交态自洽校验：`git write-tree` 导出 482 文件 → 关键模块全可导入、validator 识别 ADR0028 actions、排除项未混入。
- 提交后工作区仅剩 9 个独立工作线文件 + `thin_waist.py.bak_phase1` + `test_capture_quality_contract.py`（均未暂存，留待后续单独评估/提交）。
- 验收：ADR0028 scoped 70 passed；全量 578 passed（3 个 pre-existing 竞态，隔离复跑通过）；py_compile OK。

## 从检查点继续推进（2026-11-10，基于 `8aa68cc`，本轮待提交）

### 本轮新增 scope（在 `8aa68cc` 之上）
- 代码：`src/memall/pipeline/kb_enrich.py`（worker stop/status/Event 循环）、`src/memall/mcp/tools/__init__.py`（`kb_enrich_stop`）、`src/memall/mcp/models.py`（`KBEnrichActionInput`）、`src/memall/mcp/validator.py`（登记 3 个 kb action）。
- 测试：`tests/test_kb_ingest_p2_expanded.py`（+3）、`tests/test_kb_enrich_worker_p2.py`（+4）、`tests/test_utility_rerank_p3.py`（+4）。
- 文档：新建 `p2-4-worker-deployment.md` / `p3-utility-rerank-rollout.md` / `p4-deferred-design.md`；更新 `README.md`(p0-p2) / `CHANGELOG_TRACKING.md` / `TECH_DEBT_LEDGER.md` / `PRECOMMIT_SCOPED_REPORT_2026-11-10.md` / `TRANSFORMATION_DOC.md` / `p1-continuity-contract.md` / `p2-kb-boundary-hardening.md` / ADR 0028。

### 验收
- 定向：P1 3 passed、P2-4 20 passed、P3 10 passed；py_compile OK。
- Scoped（9 文件）-> **81 passed in 73.52s**。
- 全量：见报告最新条目。

### 提交门禁（仍不 push）
- [ ] `git diff --name-only` / `git status --short` 复核：只暂存上述 ADR0028 主线文件。
- [ ] 排除 9 个独立工作线文件（`gateway_sync.py`、`mcp/tools/capture.py`、`pipeline/{auto_dream,cross_agent,lifecycle,llm_enhance,observe,ops,reflect}.py`）、`thin_waist.py.bak_phase1`、`test_capture_quality_contract.py`。
- [ ] 提交态（`git write-tree` 导出）导入/validator 自洽校验。
- [ ] 本地 commit（沿用 `DeepSeek Harness <noreply@deepseek-harness.local>`），**不 push**。

## ✅ 本轮已落地提交（2026-11-10）
- **commit `c841c2f`**（本地，未 push）：19 files, 797+/6-。
- 提交集 = 本轮 19 个 ADR0028 主线文件；9 个独立工作线文件 + `bak_phase1` + `test_capture_quality_contract.py` 保留在工作区未暂存。
- 提交态自洽：`git write-tree` 导出 485 文件，关键模块/validator/MCP 链通过，排除项未混入。
- 验收：定向 P1 3 / P2-4 20 / P3 10；scoped 81 passed；全量 592 passed/2 skipped/0 failed/0 errors。

## 「按蓝图补齐」轮次（2026-11-10，本轮待提交）
- 代码：`config.py`、`core/db.py`、`core/thin_waist.py`、`search/enhancements.py`、`pipeline/session.py`、`pipeline/maintenance.py`（新）、`mcp/models.py`、`mcp/validator.py`、`mcp/tools/__init__.py`、`mcp/tools/retrieve.py`。
- 测试：`tests/test_blueprint_completion.py`（新）。
- 文档：`ADR/0028-*`、`docs/p0-p2/{blueprint-completion-matrix.md(新),README.md,p4-deferred-design.md,CHANGELOG_TRACKING.md,TECH_DEBT_LEDGER.md,TRANSFORMATION_DOC.md,PRECOMMIT_SCOPED_REPORT_2026-11-10.md,COMMIT_CHECKLIST.md}`。
- 验收：蓝图契约 12 passed；扩展 scoped 125 passed；全量见报告最新条目；py_compile OK。
- 门禁：只暂存上述 ADR0028 主线文件；排除 `thin_waist.py.bak_phase1`（唯一未跟踪项）；本地 commit，不 push。

## ✅「按蓝图补齐」轮次已落地提交（2026-11-10）
- **commit `3ee3372`**（本地，未 push）：21 files, 1452+/41-。
- 提交集 = 10 代码 + 1 新测试 + 10 文档/台账；`thin_waist.py.bak_phase1` 排除、仍为唯一未跟踪项。
- 提交态自洽：`git write-tree` 导出 491 文件，导入/默认 off/validator/排除项全部验证通过。
- 验收：12 / 125 / 610 passed（全量 0 failed），py_compile OK。

## 第三轮审查修复（2026-11-10 晚，本轮待提交）
- 代码：`src/memall/pipeline/maintenance.py`（rollback 服务端日志+CAS+校验、read_audit 脱敏+尾读、actions 不可放大、audit 辅助函数）、`src/memall/core/thin_waist.py`（project 过滤注入、_resolve_decision 全路径挂 utility_policy）。
- 测试：`tests/test_blueprint_completion.py`（+4 安全/回归、重写 2 个）→ 19 passed。
- 文档：`independent-review-report.md`（第三轮章节）、`acceptance-review-status.md`（§7）、`CHANGELOG`/`TECH_DEBT`（第三轮条目）、`PRECOMMIT`（第三轮全量数字）。
- 验收：定向 19 passed；全量见 PRECOMMIT 第三轮；py_compile OK；提交态自洽。
- 门禁：只暂存上述文件；**排除 `thin_waist.py.bak_phase1`**；本地 commit（`DeepSeek Harness <noreply@deepseek-harness.local>`），**不 push**。

## 2026-11 local pass（自动入库/语义去重/自进化边界加固，未提交）
- [x] 代码：`thin_waist.py` 语义线程回退增加可选 project 边界（`_semantic_dedup_lookup(..., project=...)` + `_infer_thread_root` 传参）；`improve.py` 个人/全局 correction 重复运行幂等合并唯一 `source_l6_ids`、`hit_count`/severity；`config.py`/`auto_capture.py` 增加 `capture.auto_capture_enabled`（默认 True，可配置关闭）。
- [x] 测试：新增/扩展 `test_capture_thread_inference.py`、`test_improve.py`、`test_auto_capture.py` 契约用例。
- [x] 验收：定向 `tests/test_improve.py tests/test_auto_capture.py tests/test_capture_thread_inference.py` -> 19 passed；全量 `pytest -q` -> 683 passed, 2 skipped, 192 warnings（exit 0，job bash-3337c20e928547b8）。
- [x] 文档追踪：`docs/improvement/AUTO_INTEL_PROGRESS_2026-04-10.md`、`docs/p0-p2/TECH_DEBT_LEDGER.md`、本清单已更新；`TRANSFORMATION_DOC.md` 可在提交前补充本轮摘要。
- [ ] 本地 commit（不 push），待用户明确要求且最终验收确认。

## 2026-11 local pass: P0-1/P0-2（待提交，未 push）

- [x] **P0-1 Semantic dedup layered decision + reason/observability**: hash-dedup path emits additive dedup metadata (`thin_waist.py`); legacy/v2 key precedence, char-jaccard fallback, `_NEG_RE`/`_QTY_RE`, project/thread boundaries, reserved knobs default-off all preserved. Contract: `test_legacy_semantic_dedup_keys_take_precedence_in_capture_path` green.
- [x] **P0-2 Auto-ingest info-increment + persistent short-window dedup**: `_looks_low_information`/`_information_increment_ratio` gates, 300s/2-hit persistent dedup (`auto_capture_recent`), agent-scoped keys, observable decisions/reasons, config-aware min length, discussion agent propagation fix. Contracts: `test_auto_capture_*` (9 tests) green.
- [x] **Tests updated/added**: `tests/test_auto_capture.py` hardened (config reset, low-info cases, persistent dedup, increment-gate toggle).
- [x] **Targeted validation**: 34 passed (7 files). **Broader validation**: 54 passed (9 files).
- [x] **Docs updated**: `docs/improvement/AUTO_INTEL_PROGRESS_2026-04-10.md`, `docs/p0-p2/TRANSFORMATION_DOC.md`, `docs/p0-p2/TECH_DEBT_LEDGER.md`, `docs/p0-p2/COMMIT_CHECKLIST.md`.
- [ ] Exclude `src/memall/core/thin_waist.py.bak_phase1` and unrelated artifacts from commit scope.
- [ ] Local commit (no push) — awaiting explicit user approval after final validation.

## 2026-11-12 第二轮：四项开放债务推进（待提交集增补，未提交）

### 本轮改动文件（追加到既有未提交集）
- `src/memall/auto_capture.py`：extractor 注册/注销/白名单查询 + `capture.auto_capture_extractors` 收窄 + `_persistent_dedup_settings()`（窗口/min-hits/retention/max-rows 可配、钳制、rowid 裁剪）。
- `src/memall/config.py`：新增 `capture.auto_capture_extractors/auto_capture_persistent_window_seconds/auto_capture_persistent_min_hits/auto_capture_retention_seconds/auto_capture_max_rows`；`evolution.correction_injection_tracking_enabled/correction_adoption_tracking_enabled`（默认 False）。
- `src/memall/pipeline/improve.py`：corrections 加性观测列迁移；`record_corrections_injection` / `record_correction_adoption` / `correction_effectiveness_report`；`get_active_corrections` 输出加性字段；模块 logger。
- `src/memall/mcp/federation_tools.py`：auto_inject corrections 注入计数接入（默认 no-op）。
- `src/memall/mcp/models.py` / `validator.py` / `tools/__init__.py`：MCP `correction_report`（CorrectionReportInput、路由、enum/描述）。
- `tests/test_auto_capture.py`（+3）、`tests/test_improve.py`（+4）、`tests/test_semantic_dedup_config_contract.py`（+2）。
- 文档：`TECH_DEBT_LEDGER.md`、`TRANSFORMATION_DOC.md`、`COMMIT_CHECKLIST.md`、`docs/improvement/AUTO_INTEL_PROGRESS_2026-04-10.md`。

### 默认行为不变的兼容性门禁（必须保持绿的测试）
- `test_legacy_semantic_dedup_keys_take_precedence_in_capture_path`
- `test_auto_capture_persistent_dedup_across_restart_and_reports_reason`（300s/2 hits 契约）
- `test_correction_tracking_defaults_off_no_writes`（tracking 默认关）
- `test_v2_cross_scope_windows_reserved_no_behavior_change` 与本轮新增 2 个预留契约

### 验收
- 定向：`tests/test_improve.py tests/test_auto_capture.py tests/test_semantic_dedup_config_contract.py tests/test_auto_inject_corrections.py tests/test_session.py tests/test_kb_ingest_p2_expanded.py` → 68 passed。
- 全量：第二轮后 **698 passed, 2 skipped (exit 0)**；第三轮（自动采纳判定器）后 **703 passed, 2 skipped, 192 warnings in 893.18s (exit 0)**（689 基线 + 14 新增，零回归）。

### 第三轮追加改动文件（TD-EVOL-TRACK-01 自动采纳判定器）
- `src/memall/pipeline/improve.py`：`evaluate_session_corrections()` 三态判定器（默认关、fail-open、dry-run 模式）。
- `src/memall/pipeline/session.py`：`session_end` 加性 `corrections_eval`；`session_start` stale 路径加性 `stale_corrections_eval`。
- `src/memall/config.py`：`evolution.correction_auto_adoption_eval`（默认 False）。
- `tests/test_improve.py`：+5 契约测试（默认 no-op / 三态 / dry-run / session_end 端到端 / 默认形状不变）。

### 提交门禁
- [ ] `git status --short` 复核：仅暂存 ADR0028 主线 + 本轮文件；排除 `gateway_start.py`、`gateway_keepalive.sh.bak.*`、`gateway.log` 等运行产物。
- [ ] 全量测试绿后由用户显式请求再 commit（不 push）。

## 部署备注（2026-11-12 实测教训，提交/上线前必读）
- **改完 `src/` 后运行中的 gateway 不会自动加载新代码**；涉及新模块/新类（如本轮 `CorrectionReportInput`）时，惰性导入可能拼出"混血"模块状态——一旦某个请求触发 ImportError，Python 的失败导入缓存会让**所有 MCP 请求持续 500**，直到进程重启（2026-11-12 12:47 启动的进程即在 13 点改动后进入该状态，14:18 经 `kill` + keepalive 脚本重启恢复）。
- 重启后验收三件套（本机 9920）：
  1. `POST /mcp initialize`（带 `X-MemAll-Agent` 或 clientInfo.agent_name，强制身份声明）
  2. `tools/list` 应返回 8 个工具
  3. 抽一个动作调用（如 `memall_system.correction_report`、`memall_read.search`）确认非 error
- **当前托管方式**：`gateway_keepalive.sh`（nohup，崩溃自动重启）。如需回到 systemd：先停脚本进程，再 `sudo systemctl start memall-gateway.service`（本次会话无 sudo，未能直接验证 unit 拉起）。
- 安全操作顺序建议：先本地全量测试绿 → 再重启 gateway → 立即跑上面三件套；避免"改一半、进程边跑边读新码"。

## 2026-11-12 第四轮追加（网关热重载 + 缺陷修复，未提交）
- 新增：`src/memall/gateway_autoreload.py`、`tests/test_gateway_autoreload.py`、`gateway_start_dev_reload.py`（dev wrapper，提交与否用户定）。
- 修改：`src/memall/gateway.py`（start() 门控接线，默认关）、`src/memall/config.py`（gateway.autoreload 默认 False；**并修复 DEFAULTS 重复 "gateway" 键覆盖 port 的自引入缺陷**）、`tests/test_gateway.py`（jieba 预热 + capture 超时 15s，修复单跑必挂的顺序依赖）。
- 全量验收：**708 passed, 2 skipped (exit 0)**。

## ✅ 提交完成（2026-11-12）
- commit `b7d703b`（本地，未 push）：30 files, 3445+/236-，含第四轮热重载与缺陷修复；排除项保持未跟踪（gateway_start.py / gateway_start_dev_reload.py / bak）。
- 本条为提交后补记（amend 于同一本地提交）。
