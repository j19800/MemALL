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
