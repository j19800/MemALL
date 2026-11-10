# ADR 0028 P0–P2 变更追踪日志

> 目的：让任何改动都能被追踪（时间、范围、原因、验收、风险/债务）。  
> 规则：每次推进追加条目；不删除历史；提交/验收/债务状态显式记录。

## 2026-11-10 (cycle: P1 continuity_pack MCP E2E + resolve_status-absent contract strengthened)
- **P1 contract strengthened** (`tests/test_kb_ingest_p2_expanded.py`): `test_p1_continuity_missing_resolve_status_column_safe` now asserts bucket field presence (`active_state`, `conflicts`) and REVIEW/CONFLICT eligibility via `memory_status` without `resolve_status` column; KB exclusion invariant preserved.
- **P1 MCP E2E test added** (`tests/test_kb_ingest_p2_expanded.py`): `test_p1_continuity_pack_mcp_end_to_end_via_memall_read` validates `_handle_read` → `handle_continuity_pack` routing contract: all contract keys present, `kb_excluded=True`, `excluded_source_pipelines` includes `document.import`, `why_selected.filters` contains `kb_excluded`/`temporal_validity`.
- **Acceptance**: scoped regression `tests/test_kb_ingest_p2.py tests/test_kb_ingest_p2_expanded.py tests/test_kb_enrich_worker_p2.py tests/test_capture_resolve_engine.py tests/test_session.py tests/test_session_sweep.py -q` → **71 passed** (including 3 continuity_pack MCP contract tests).


## 2026-11-10 — 边界审计与 P2-4 基础（工作区，未提交）
- **时间**：2026-11-10 Asia/Shanghai（本轮会话）
- **范围**：
  - `src/memall/mcp/federation_tools.py`：`auto_inject` KB SQL/category 排除 + additive `kb_excluded`/`excluded_source_pipelines`。
  - `src/memall/pipeline/session.py`：`session_start(auto_inject=True)` 全量 injection 递归 KB 防御过滤；`build_continuity_pack` temporal validity SQL 过滤；`excluded_source_pipelines` 来自 `KB_SOURCE_PIPELINES`。
  - `src/memall/mcp/tools/retrieve.py`：continuity/recap 与 KB policy 对齐（工作区状态）。
  - `src/memall/mcp/tools/memory_write.py`：`handle_kb_ingest` 成功 chunk capture 后 `_fire_kb_enrich`（best-effort, non-blocking）。
  - `src/memall/core/db.py`：新增 `pipeline_jobs` 持久化表 DDL（P2-4 队列/worker 基础）。
  - `src/memall/mcp/tools/__init__.py`：`memall_system` 新增 `kb_enrich_stats` action + schema enum。
  - `tests/test_kb_ingest_p2_expanded.py`：portable/additive MCP continuity contract；`auto_inject` KB contract；whole-injection session KB exclusion；validity 边界断言。
  - `docs/p0-p2/*`：TECH_DEBT_LEDGER、COMMIT_CHECKLIST、TRANSFORMATION_DOC、README、p1/p2 文档更新。
- **原因**：P1/P2 KB 证据不得进入 continuity/recap/session/auto_inject user-state；P2-4 为真异步队列打基础；满足“记录技术债务与各种文档”要求。
- **验收**：`py_compile` OK；`pytest tests/test_kb_ingest_p2.py tests/test_kb_ingest_p2_expanded.py tests/test_capture_resolve_engine.py tests/test_session.py tests/test_session_sweep.py -q` → **46 passed**。
- **风险/债务**：P2-4 尚未实现持久化 enqueue/worker claim/retry/backoff/跨进程幂等消费/reprocess/队列 metrics；P1 仍有 include_conflicts/superseded×validity 交叉用例；P3 默认 off。未提交/未推送。
- **提交状态**：未提交。stage 前按 COMMIT_CHECKLIST 排除 13 个预存无关修改、`thin_waist.py.bak_phase1`、`test_capture_quality_contract.py`；确认 `test_capture_resolve_engine.py` 是否纳入。

## 2026-11-10 — P2-4 持久化 enqueue/worker 推进（进行中）
- **计划范围**：`kb_enrich.py` 接入 `pipeline_jobs` 持久化 enqueue；worker claim/lease + 重试/backoff；跨进程幂等 key（doc_id+chunk_hash+doc_version+chunk_index）；reprocess/metrics；config `kb.enrich_worker.backend`（默认 sqlite+queue/noqueue 兼容）；hook 兼容/非阻塞/容错。
- **状态**：进行中（本轮自动推进）。

## 2026-11-10 — P2-4 enqueue 落地（持续推进）
- **范围**：`memory_write._fire_kb_enrich` 增加 `kb.enrich_worker.backend=sqlite+queue` 持久化 enqueue 到 `pipeline_jobs`（best-effort、吞异常、不阻塞 capture）；保留原 daemon hook；`config.py` 增加 `kb.enrich_worker`（backend/max_attempts/backoff/claim_ttl/batch_size/poll_interval，默认 sqlite+queue）。
- **原因**：把 P2-4 从“仅进程内 hook/统计”推进到 durable queue 基础，为后续 worker claim/retry/backoff/跨进程幂等/reprocess 打底。
- **验收**：`py_compile` OK（含 memory_write/kb_enrich/config/db/mcp tools）；`pytest tests/test_kb_ingest_p2.py tests/test_kb_ingest_p2_expanded.py -q` → **27 passed**。全量 scoped 集（含 session/capture resolve）待本轮末重跑。
- **债务/风险**：enqueue 未做跨进程唯一幂等约束（仍需 DB 级 key/去重或 worker claim 去重）；worker 未实现；`pipeline_jobs` 与 `capture` DB 路径/连接生命周期需在 worker 落地时统一；metrics/reprocess/MCP 完整暴露待做。
- **跟踪确认**：`tests/test_capture_resolve_engine.py` 纳入 P0-P2 跟踪；`docs/p0-p2/` 纳入跟踪；排除 `thin_waist.py.bak_phase1`、`test_capture_quality_contract.py`、13 个预存无关修改（提交时复核）。

## 2026-11-10 — P2-4 durable worker 落地与修复（工作区，未提交）
- **时间**：2026-11-10 05:30+ Asia/Shanghai
- **范围**：
  - `src/memall/pipeline/kb_enrich.py`：重写 P2-4 worker 段（清理 heredoc/转义引入的语法问题）；保留 hook/stats/in-process 幂等契约；新增 `_now_iso`、`_ensure_pipeline_jobs_schema`、`_claim_job`（`next_retry_at` + `claimed_at` lease TTL + 同 key 已完成跨进程去重，NULL-safe）、`_mark_complete`（`ok/failed`、清 `handled_by`、`completed_at`）、`kb_enrich_worker_loop`（指数退避、`attempts/max_attempts`、失败 `error_msg`、连接关闭、config `kb.enrich_worker.backend` 守卫）、`start_kb_enrich_worker`、`ensure_kb_enrich_worker_started`（单进程单线程、config-gated，默认 `autostart=false` 安全）；`kb_enrich_stats` 扩展 additive `pipeline` 指标（`total/queued/running/ok/failed/completed/queue_depth/last_completed_at/last_error/recent_errors`）；`reprocess_kb_enrich`（按 `doc_id`/`failed_only`/`all` 重置 queued 并 best-effort 触发 worker）。
  - `src/memall/config.py`：`kb.enrich_worker.autostart=false`（默认不自动常驻，避免非预期后台线程）。
  - `src/memall/mcp/tools/__init__.py`：`memall_system` 新增 additive action `kb_reprocess`（schema enum + handler）。
  - `tests/test_kb_enrich_worker_p2.py`（新）：claim/complete 生命周期、future `next_retry_at` 跳过、同 key completed 跨进程去重、failed 标记、pipeline stats、reprocess 重置、autostart-off 不启动。
- **验收**：
  - `python3 -m py_compile`：`kb_enrich.py`, `config.py`, `mcp/tools/__init__.py`, `core/db.py`, `mcp/tools/memory_write.py` ✅
  - `PYTHONPATH=.:src ./.venv/bin/python -m pytest tests/test_kb_ingest_p2.py tests/test_kb_ingest_p2_expanded.py -q` → **27 passed (33.80s)** ✅
  - `PYTHONPATH=.:src ./.venv/bin/python -m pytest tests/test_kb_enrich_worker_p2.py -v` → **7 passed (5.90s)** ✅
- **剩余债务**：独立常驻 worker 启动入口（gateway/daemon 侧）未接线；`reprocess_kb_enrich` 当前 best-effort 触发后台线程，生产语义需与常驻 worker 协调；claim 仍非严格 `BEGIN IMMEDIATE`+单 SQL 原子 CAS（SQLite 单连接场景可接受，跨进程高并发需强化）；补失败→退避→重试→max_attempts 的真实 worker-loop 集成测试；P1 边界/P3 默认-off 后续推进。
- **提交状态**：未提交。

## 2026-11-10 (cycle: P2-4 durability tests + P1 boundaries + MCP contract smoke)
- **Tests added/updated** (`tests/test_kb_enrich_worker_p2.py`): deterministic retry→requeue then `max_attempts` exhaustion (no flaky timing; direct branch simulation with controlled state), claim TTL recovery for stale queued `claimed_at`, `kb_enrich_stats` noqueue/backend config fallback does not crash, `reprocess_kb_enrich` additive contract shape. Helper usage fixed to `init_temp_db()` tuple contract.
- **P1 boundary tests** (`tests/test_kb_ingest_p2_expanded.py`): `include_conflicts=True` must still exclude KB evidence; superseded excluded × temporally valid active kept × expired excluded (cross case). Fixed KB ingest return parsing in new test (MCP handlers may return JSON strings).
- **MCP contract smoke**: `memall_system` `kb_enrich_stats` returns hook/pipeline stats dict (`fired` present); `kb_reprocess` returns additive `{ok,reprocessed,...}` via `handle_tool_call`.
- **P2-3 scan**: no hardcoded KB exclusion SQL literals remain in `src/memall` outside `core/kb_policy.py` (pycache excluded). `auto_dream/cross_agent/lifecycle/llm_enhance` only set positive `source_pipeline` tags (not exclusion) — no convergence change required; `thin_waist.py` positive filter remains intentional.
- **Acceptance**: `py_compile` OK (touched core/tests). `tests/test_kb_enrich_worker_p2.py -q` → 12 passed. `tests/test_kb_ingest_p2.py tests/test_kb_ingest_p2_expanded.py tests/test_kb_enrich_worker_p2.py -q` → **41 passed (45.49s)**.

## 2026-11-10 (cycle: batch_size alignment + P1 routing/REVIEW contracts + json scope fix)
- **P2-4 batch_size 对齐** (`src/memall/pipeline/kb_enrich.py`, `config.py` 已有 `kb.enrich_worker.batch_size=10`): `kb_enrich_worker_loop` 增加 `batch_size`（默认 10），每次 poll 最多 claim/process `batch_size` 个任务后再 sleep；`ensure_kb_enrich_worker_started`/`start_kb_enrich_worker` 透传 `batch_size`；`reprocess_kb_enrich` 短生命周期 worker 透传 `batch_size`（上限 100）。
- **P1 端到端/契约测试** (`tests/test_kb_ingest_p2_expanded.py`): `memall_read` action `continuity_pack` 路由契约（KB 排除、`temporal_validity`/`kb_excluded` filters、核心字段齐全）；`REVIEW` 状态在 `include_conflicts=False` 排除、`True` 时 eligible（仅用现有 `memory_status`，不依赖缺失 `resolve_status` 列）。
- **修复** (`src/memall/mcp/tools/__init__.py`): `_handle_system` 中移除局部 `import json,...`（改为模块级 `json`），修复 `kb_enrich_stats/kb_reprocess` 等分支在后续局部导入存在时的 `UnboundLocalError`。
- **Acceptance**: `py_compile` OK；`tests/test_kb_ingest_p2.py tests/test_kb_ingest_p2_expanded.py tests/test_kb_enrich_worker_p2.py -q` → **43 passed (44.82s)**。MCP 冒烟：`_handle_system` `kb_enrich_stats`/`kb_reprocess` OK。

## 2026-11-10 (cycle: P1 bucketing/KB-temporal cross + scoped commit prep)
- **P1 test** (`tests/test_kb_ingest_p2_expanded.py`): conflict bucketing (`memory_status` CONFLICT/REVIEW) with `include_conflicts=True/False`, KB exclusion preserved, expired conflict excluded by temporal validity (`test_p1_continuity_conflict_bucketing_and_kb_temporal_cross`).
- **Schema note**: test DB `memories` table has `memory_status` but not `resolve_status`; continuity/session code uses `sqlite3.Row`-safe access / fallback patterns — tests aligned to existing columns.
- **Acceptance**: scoped `tests/test_kb_ingest_p2.py tests/test_kb_ingest_p2_expanded.py tests/test_kb_enrich_worker_p2.py -q` → **44 passed (45.43s)**.

## 2026-11-10 (cycle: P1 read-only semantics + resolve_status compatibility contract)
- **Tests** (`tests/test_kb_ingest_p2_expanded.py`): read-only `pending/conflicts/recent_supersedes/active_state` bucket semantics contract (KB+temporal exclusion preserved); `resolve_status`-missing-column continuity safety contract (no crash, contract fields present).
- **Acceptance**: scoped `tests/test_kb_ingest_p2.py tests/test_kb_ingest_p2_expanded.py tests/test_kb_enrich_worker_p2.py -q` → **46 passed**. Broader regression baseline remains **63 passed** when rerun.
- **Docs**: `TECH_DEBT_LEDGER.md` notes P1 `resolve_status` absent-column coverage; precommit docs/checklist counts updated.

## 2026-11-10 (cycle: reprocess bounded hardening)
- **P2-4 hardening** (`src/memall/pipeline/kb_enrich.py`): `reprocess_kb_enrich` now triggers a **bounded one-shot** background pass (`process_budget`, default 50, max 500) instead of starting an infinite `kb_enrich_worker_loop` thread. Additive return includes `trigger_mode: bounded_once`. Best-effort/non-blocking; long-lived processing still requires explicit worker/gateway startup (default `autostart=False`).
- **Test**: `test_reprocess_bounded_trigger_is_additive_nonbreaking` (unique ids, schema-correct insert).
- **Acceptance**: scoped `tests/test_kb_ingest_p2.py tests/test_kb_ingest_p2_expanded.py tests/test_kb_enrich_worker_p2.py -q` → **47 passed (50.19s)**.

## 2026-11-10 (cycle: P3 guardrail + bounded reprocess validation)
- **P2-4**: `reprocess_kb_enrich` hardened to bounded one-shot background pass (`process_budget`, max 500, `trigger_mode=bounded_once`), avoiding accidental infinite worker thread; best-effort. Long-lived worker remains explicit/config-gated (`autostart=False`).
- **P3 guardrail** (no default behavior change): added conservative contract test confirming enhancement helpers are callable with empty inputs and config-gated defaults remain intact.
- **Acceptance**: scoped KB/P1/P2 worker tests **47 passed**; broader regression **66 passed**.


## 2026-11-10 P3 utility_rerank skeleton + P2-4 gateway hook（本轮）
- `src/memall/config.py`: 新增 `search.utility_rerank` 默认配置（enabled=false）。
- `src/memall/search/enhancements.py`: 新增 `_apply_utility_rerank` 与 `apply_enhancements(..., utility_rerank=False)` 门控分支（config+flag 双门控，best-effort，不剔除结果）。
- `src/memall/core/thin_waist.py`, `src/memall/mcp/tools/retrieve.py`: 透传/识别 `utility_rerank` 增强标志。
- `src/memall/gateway.py`: `MemAllGateway.start()` best-effort 调用 `ensure_kb_enrich_worker_started()`（仍受 autostart/config 门控，默认不启动）。
- `tests/test_kb_ingest_p2_expanded.py`: 新增 `test_p3_utility_rerank_config_gated_default_off`、`test_p1_continuity_pack_include_conflicts_semantics_and_kb_excluded`。
- `docs/p0-p2/TECH_DEBT_LEDGER.md`: 登记 P3 骨架、P2-4 gateway 启动入口与 P3 线上化待办。
- 回归：作用域用例通过（69 passed，2026-11-10）。

### 2026-11-10 (P1 continuity boundary contracts - full auto)
- Added P1 boundary tests to `tests/test_kb_ingest_p2_expanded.py`:
  - `test_p1_continuity_pack_validity_filter_excludes_expired` (valid_from/invalid_at exclusion)
  - `test_p1_continuity_pack_include_conflicts_respects_validity` (include_conflicts surfaces REVIEW/CONFLICT only when valid; KB still excluded)
  - `test_p1_continuity_pack_recent_supersedes_boundary` (recent_supersedes/supersession chain boundaries; KB excluded)
  - `test_p1_session_start_mcp_kb_exclusion_contract` (MCP `session_start` marks `kb_excluded` and excludes KB evidence from injected surfaces)
- Verification: all 4 new tests passed. Scoped regression `tests/test_kb_ingest_p2.py tests/test_kb_ingest_p2_expanded.py tests/test_kb_enrich_worker_p2.py tests/test_capture_resolve_engine.py tests/test_session.py tests/test_session_sweep.py -q` -> 56 passed (green). `build_continuity_pack` continues to enforce temporal validity, KB exclusion (via `kb_policy`/fallback), `include_conflicts` gating, and Row-safe behavior.

### 2026-11-10 (P2-4 CAS hardening + P3 propagation fix - full auto, model switch)
- **P2-4 (real bug fix)**: `src/memall/pipeline/kb_enrich.py::_claim_job` was SELECT-then-UPDATE without a conditional guard — two processes could both claim the same queued row. Now uses compare-and-set conditional UPDATE (`WHERE id=? AND status='queued' AND ...`); dedup branch is conditional too; lost races retry via recursion.
  - New tests in `tests/test_kb_enrich_worker_p2.py`: `test_claim_cas_single_winner_under_contention`, `test_claim_cas_skips_already_running_row`, `test_reprocess_budget_is_bounded_and_clamped` -> full file **16 passed**.
- **P3 (real bug fix + E2E wiring)**: `enhanced_search` referenced undefined `arguments` on the enhancement path (NameError whenever explain/cluster/personalize/context_boost used) and had no `utility_rerank` parameter, so MCP `handle_retrieve` leaked an invalid kwarg into `retrieve()`.
  - `src/memall/core/thin_waist.py`: `enhanced_search(utility_rerank=...)` explicit param; fixed NameError; enhancement guard includes utility_rerank; surfaces `utility_rerank_signal/score` on Memory objects + `out["utility_rerank_signals"]`; `unified_search` parses/forwards the flag into `enhanced_search` and vector/hybrid `_apply_enhancements_inplace`; fts5/direct payload serializes additive signals.
  - New file `tests/test_utility_rerank_p3.py`: 6 E2E contracts (default-off no signals, no NameError, fts5/hybrid, conservative gating with no drops, MCP handle_retrieve + handle_unified_search) -> **6 passed**.
  - Behavior remains default-off: `search.utility_rerank.enabled=False` in config.
- **Regression**: extended scoped suite -> **70 passed in 65.59s** (exit 0).

### 2026-11-10 落地提交（全自动推进，本地 commit，未 push）
- **commit `26db549`**：`feat(memall): ADR0028 P1/P2-4/P3 hardening - continuity boundary, CAS worker, utility rerank wiring`
- 29 files, 2560+/53-。基于 `92863b6` 之上的 P1/P2-4/P3 加固 + 文档/测试/台账全套。
- 提交前用 `git write-tree` 导出提交态自洽校验：482 文件、关键模块全可导入、validator 识别 `continuity_pack`/`kb_ingest`/`document_import`、排除项未混入。
- **scope 裁决**（见 COMMIT_CHECKLIST 文末）：用代码事实纠正了早期"13 个排除项"的错误口径——`models.py`/`validator.py`/`federation_tools.py` 是 ADR0028 功能必需配套（基线 92863b6 已路由 action 但 validator 未登记），必须纳入；真正排除的是 9 个 capture-robustness/readability 文件 + `bak_phase1` 备份 + `test_capture_quality_contract.py`（用 `git stash` 隔离验证排除后 scoped 回归仍 70 passed）。
- 全量回归：`578 passed, 2 skipped, 1 failed, 2 errors`；3 个问题均为 pre-existing 共享 DB `duplicate column` 竞态（隔离重跑 3 passed，与本次改动无关）。
- 提交后工作区仅剩：9 个独立工作线文件（未跟踪/未暂存，留待后续单独处理）+ 2 个排除项。
- **未 push**（按口径仅本地提交）。
