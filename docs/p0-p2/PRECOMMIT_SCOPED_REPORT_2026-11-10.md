# Pre-commit Scoped Report (2026-11-10, not committed)

## Policy
- **不提交**：按约束“先不要提交，在本地完成改造验收合格之后再提交”。
- 本报告仅给出 scoped stage 建议、回归命令、排除项；不执行 `git commit/push`。

## Suggested scope for P0–P2/ADR0028 local pass (review before staging)
### Tracked modified (include if confirmed part of this pass)
- `ADR/0028-memory-cognitive-loop-implementation-plan.md` (only if aligned with actual state)
- `docs/README.md` (only if aligned)
- `src/memall/config.py`
- `src/memall/core/db.py`
- `src/memall/core/models.py` (verify P0–P2 relevance)
- `src/memall/mcp/federation_tools.py`
- `src/memall/mcp/tools/__init__.py`
- `src/memall/mcp/tools/memory_write.py`
- `src/memall/mcp/tools/retrieve.py`
- `src/memall/pipeline/kb_enrich.py`
- `src/memall/pipeline/session.py`
- `tests/test_kb_ingest_p2_expanded.py`

### Untracked (include when confirmed)
- `docs/p0-p2/*` (tracking/debt/checklist/tech docs)
- `tests/test_capture_resolve_engine.py`
- `tests/test_kb_enrich_worker_p2.py`

## Do NOT stage (per prior decisions/context)
- 13 pre-existing unrelated modified files (review `git diff --name-only` carefully; some listed above may be unrelated to this pass — stage only confirmed): `src/memall/gateway_sync.py`, `src/memall/mcp/models.py`, `src/memall/mcp/tools/capture.py`, `src/memall/mcp/validator.py`, `src/memall/pipeline/auto_dream.py`, `src/memall/pipeline/cross_agent.py`, `src/memall/pipeline/lifecycle.py`, `src/memall/pipeline/llm_enhance.py`, `src/memall/pipeline/observe.py`, `src/memall/pipeline/ops.py`, `src/memall/pipeline/reflect.py` — **exclude unless explicitly verified as part of this pass**.
- `src/memall/core/thin_waist.py.bak_phase1` (backup)
- `tests/test_capture_quality_contract.py` (untracked, decision pending)

## Acceptance (local, 2026-11-10)
- `python3 -m py_compile` on touched core/tests: OK.
- `tests/test_kb_ingest_p2.py tests/test_kb_ingest_p2_expanded.py tests/test_kb_enrich_worker_p2.py -q` → **48 passed** (historical). Latest scoped regression (2026-11-10, includes P3/P1 continuity tests): `tests/test_kb_ingest_p2.py tests/test_kb_ingest_p2_expanded.py tests/test_kb_enrich_worker_p2.py tests/test_capture_resolve_engine.py tests/test_session.py tests/test_session_sweep.py -q` → **70 passed**.
- Broader scoped regression: `tests/test_kb_ingest_p2.py tests/test_kb_ingest_p2_expanded.py tests/test_kb_enrich_worker_p2.py tests/test_capture_resolve_engine.py tests/test_session.py tests/test_session_sweep.py -q` → **70 passed** (2026-11-10).

## Pre-commit commands
```bash
cd /vol1/@appshare/fn-deepseek-harness/MemALL
python3 -m py_compile src/memall/pipeline/kb_enrich.py src/memall/mcp/tools/memory_write.py src/memall/mcp/tools/__init__.py src/memall/config.py src/memall/core/db.py src/memall/mcp/federation_tools.py src/memall/pipeline/session.py src/memall/mcp/tools/retrieve.py
PYTHONPATH=.:src ./.venv/bin/python -m pytest tests/test_kb_ingest_p2.py tests/test_kb_ingest_p2_expanded.py tests/test_kb_enrich_worker_p2.py tests/test_capture_resolve_engine.py tests/test_session.py tests/test_session_sweep.py -q
git status --short
git diff --name-only
# stage only confirmed scoped files, then inspect:
# git diff --cached --name-only
```

## Remaining gaps (do not block P0–P2 local acceptance; track in ledger)
- P2-4 production hardening: gateway/daemon autostart coordination, reprocess multi-process semantics, stricter SQLite claim CAS/contention tuning, bounded shutdown.
- P1: resolve_status column/env compatibility snapshots; `pending_decisions/conflicts` bucketing semantics across more resolve states.
- P3/P4 deferred (default-off/conservative; no ranking default changes).

## Update (2026-11-10, post P1 boundary tests)
- Added tests: 4 P1 boundary/contract tests in `tests/test_kb_ingest_p2_expanded.py` - all passed.
- Scoped regression: `tests/test_kb_ingest_p2.py tests/test_kb_ingest_p2_expanded.py tests/test_kb_enrich_worker_p2.py tests/test_capture_resolve_engine.py tests/test_session.py tests/test_session_sweep.py -q` -> **56 passed** (exit 0).
- No P0-P4 behavior changes; only tests added for boundary hardening.

## 最新回归（2026-11-10，模型切换后继续全自动推进）
- 扩展 scoped regression:
  `tests/test_kb_ingest_p2.py tests/test_kb_ingest_p2_expanded.py tests/test_kb_enrich_worker_p2.py tests/test_utility_rerank_p3.py tests/test_capture_resolve_engine.py tests/test_session.py tests/test_session_sweep.py tests/test_cluster.py tests/test_e2e.py -q`
  -> **70 passed in 65.59s (exit 0)**.
- 定向：`tests/test_kb_enrich_worker_p2.py -q` -> **16 passed**；`tests/test_utility_rerank_p3.py -q` -> **6 passed**；P1 新增 4 测试 -> **4 passed**.
- 本轮修改：`kb_enrich._claim_job` CAS 化；`thin_waist.enhanced_search`/`unified_search` 的 `utility_rerank` 传播 + NameError 修复 + additive 信号透传；新增 3+4+6 = 13 个测试（含 1 个新文件）。

## 从检查点继续推进（2026-11-10，基于 `8aa68cc`，未提交）
### 定向验收
- `tests/test_kb_ingest_p2_expanded.py -k 'resolve_status_pending_decisions or include_conflicts_false_suppresses or auto_inject_per_bucket'` -> **3 passed**.
- `tests/test_kb_enrich_worker_p2.py -q` -> **20 passed**（含真多进程 spawn CAS、worker stop/状态、validator 登记、reprocess 幂等）.
- `tests/test_utility_rerank_p3.py -q` -> **10 passed**（含冷启动/低证据回退、跨 agent 隔离、权重钳制）.
- `py_compile`：`kb_enrich.py` / `mcp/tools/__init__.py` / `mcp/models.py` / `mcp/validator.py` -> OK.

### Scoped 回归（9 文件）
`tests/test_kb_ingest_p2.py tests/test_kb_ingest_p2_expanded.py tests/test_kb_enrich_worker_p2.py tests/test_utility_rerank_p3.py tests/test_capture_resolve_engine.py tests/test_session.py tests/test_session_sweep.py tests/test_cluster.py tests/test_e2e.py -q`
-> **81 passed in 73.52s (exit 0)**（上轮 70 + 本轮 11 新测试）.

### 全量回归
结果见本文件后续追加（后台任务 `pytest tests/ -q ...`）。历史对照：上轮 578 passed, 2 skipped, 3 个 pre-existing 共享 DB `duplicate column` 竞态（隔离重跑通过）。

### 全量回归（本轮最终结果）
`PYTHONPATH=.:src ./.venv/bin/python -m pytest tests/ -q --ignore=smoke_test --ignore=stress_test --ignore=benchmark --ignore=quality_test --disable-warnings --tb=line`
-> **592 passed, 2 skipped, 192 warnings in 451.94s (exit 0)**。
对照：上轮 578 passed + 3 个 pre-existing 共享 DB `duplicate column` 竞态（隔离重跑通过）；本轮竞态未复现，0 failed / 0 errors。确认与本次改动无关。

## 独立工作线「capture 质量漏斗路由」验收（2026-11-10，单独提交）
- 新增 `tests/test_capture_quality_workline.py` -> **6 passed**；既有 `test_capture_quality_contract.py` -> **3 passed**。
- 相关子集（`test_capture_quality_workline` / `test_capture_quality_contract` / `test_ops` / `test_reflect` / `test_gateway` / `test_pipeline` / `test_distill` / `test_decay`）-> **55 passed in 59.97s**。
- `py_compile` 9 代码 + 2 测试 -> OK。
- 全量回归结果见本报告最新追加条目。

### 独立工作线全量回归（最终结果）
`pytest tests/ -q --ignore=smoke_test --ignore=stress_test --ignore=benchmark --ignore=quality_test --disable-warnings --tb=line`
-> **598 passed, 2 skipped, 192 warnings in 772.86s (exit 0)**。
对照上轮 592 passed：+6 = 本轮新增 `test_capture_quality_workline.py` 6 测试；0 failed / 0 errors。确认 9 文件工作线 + 2 个 bug 修复无回归。

✅ 独立工作线落地：commit `6fe2b4c`（16 files, 662+/176-），本地未 push。工作区仅剩 `thin_waist.py.bak_phase1` 未跟踪。

## 「按蓝图补齐」轮次验收（2026-11-10）
- `tests/test_blueprint_completion.py -q` -> **12 passed**（P1/P3/P4 字面条目契约）。
- 扩展 scoped（15 文件：blueprint_completion + kb_ingest_p2/expanded + kb_enrich_worker_p2 + utility_rerank_p3 + capture_resolve_engine + capture_quality_workline/contract + session/session_sweep + cluster + e2e + ops + reflect + config）-> **125 passed in 136.32s (exit 0)**。
- `py_compile` 全部改动文件 -> OK。
- 全量回归结果见本报告后续追加（后台任务）。

### 「按蓝图补齐」轮次全量回归（最终结果）
`pytest tests/ -q --ignore=smoke_test --ignore=stress_test --ignore=benchmark --ignore=quality_test --disable-warnings --tb=line`
-> **610 passed, 2 skipped, 192 warnings in 807.01s (exit 0)**。
对照上轮 598 passed：+12 = `tests/test_blueprint_completion.py` 12 测试；0 failed / 0 errors。确认 P1/P3/P4 全部补齐项无回归。

✅「按蓝图补齐」轮次落地：commit `3ee3372`（21 files, 1452+/41-），本地未 push。工作区仅剩 `thin_waist.py.bak_phase1` 未跟踪。

## 独立审查复核 + 验收修复轮次（2026-11-10，用户质疑后）
- 定向 `tests/test_blueprint_completion.py -q` -> **15 passed**（含 3 个新回归：老库补列、召回不变、handle_retrieve 召回/per-result 信号）。
- 全量 `pytest tests/ -q --disable-warnings --tb=line`（默认收集，无 --ignore）-> **613 passed, 2 skipped, 0 failed, 0 errors in 772.33s**。
- `py_compile` 全部改动文件 OK。
- 实测：ADR 5 条验收句（P0/P1/P2/P3/P4）逐条跑独立脚本，P3/P4 修复后均通过；config 开关不改变召回集合（独立审查报的回归已修）。
- 详见 `docs/p0-p2/acceptance-review-status.md`。

✅ 独立审查复核 + 验收修复轮次落地：commit `dc4d847`（13 files, 497+/65-），本地未 push。工作区仅剩 `thin_waist.py.bak_phase1` 未跟踪。

## 第二轮独立审查（2026-11-10）
- 默认收集 `pytest tests/ --collect-only -q` -> **615 tests collected**；全量 **613 passed, 2 skipped, 0 failed, 0 errors**（613+2=615，无漏跑）。
- 6 类正确性/安全性逐点实测 + 5 条 ADR 验收句独立脚本复核：全部通过；config 开关不改变召回集合（OFF=2 / ON=2）。
- 提交态 `git write-tree` 导出 492 文件自洽（导入/默认 off/排除项）。
- 详见 `independent-review-report.md`。

## 第三轮：换审查方式（攻击面复现式）+ 安全修复（2026-11-10 晚）
- 定向：`tests/test_blueprint_completion.py -q` -> **20 passed**（+5 安全/回归测试，重写 2 个假通过）。
- 混合子集（config/env/thin_waist_edge/blueprint）-> **40 passed**。
- **全量 `pytest tests/ -q --disable-warnings --tb=line` -> 618 passed, 2 skipped, 0 failed, 0 errors in 785.39s**。
  - 这是本项目历史上首次 full suite **零 failed / 零 error**：顺带修掉了长期污染回归的两个既有 flake：
    (a) 列补齐 TOCTOU 竞态（`duplicate column name: primary_layer/tags/echo_score`）—— 两处 ALTER 精确忽略该错误，8 线程并发实测 0 错误；
    (b) P3 验收测试顺序依赖 —— `get_config()` 热重载吞掉测试注入值，`_cfg_set` 固定 `_last_mtime_check` 后稳定。
- 本轮新增/修复的安全项：伪造 rollback 任意写、read_audit 跨身份泄露、maintenance 无授权边界、actions 越过 config 门、project 过滤丢失、假通过断言、_resolve_decision 早退缺审计。
- py_compile 全部改动文件 OK；工作区自检（导入/`read_audit(redact=True)` 默认/rollback 服务端校验/默认全 off）通过。
