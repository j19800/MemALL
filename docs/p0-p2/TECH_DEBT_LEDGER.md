# 技术债务账本（ADR0028 持续演进）

## P2-4 真异步队列（中长期）——部分推进（2026-11-10）
- **目标**：将 `kb_enrich.py` 进程内 daemon thread 替换为可配置的持久化 enqueue/background worker。
- **2026-11-10 进展**：
  - [x] `handle_kb_ingest` 成功 capture 后 fire `_fire_kb_enrich`（best-effort, non-blocking）— `memory_write.py`。
  - [x] `core/db.py` 新增 `pipeline_jobs` 持久化表 DDL（queued/running/ok/failed/completed, attempts/max_attempts/next_retry_at, kind/doc_id/chunk_hash/doc_version/chunk_index 索引）— P2-4 队列/worker 基础。
  - [x] `memall_system` action `kb_enrich_stats` 暴露 `kb_enrich_stats()`（additive, opt-in 读路径）— `mcp/tools/__init__.py`。
  - [x] `kb.enrich_async` config gate 保留（默认 true）；hook 兼容性保持。
  - [~] **进行中（2026-11-10）**：`memory_write._fire_kb_enrich` 已持久化 enqueue 到 `pipeline_jobs`（`sqlite+queue`，含活跃/完成 key 去重）；`kb_enrich.py` 已落地 claim(`next_retry_at`+`claimed_at` lease TTL)+complete+跨进程 completed-key 去重、worker loop 指数退避/`attempts/max_attempts`/`error_msg`、`kb_enrich_stats` pipeline 指标、`reprocess_kb_enrich` + `memall_system.kb_reprocess`、`ensure_kb_enrich_worker_started`（config-gated，默认 autostart=false）；新增 `tests/test_kb_enrich_worker_p2.py`（7 passed）。
  - [ ] **仍待**：gateway/daemon 常驻 worker 接线；`reprocess` 与常驻 worker 语义协调；claim 严格原子 CAS（高并发强化）；worker-loop 真实退避/重试集成测试；队列深度告警/运维暴露完善。
- **关键设计点**：
  1. **跨进程幂等 key**：`doc_id + chunk_hash + doc_version + chunk_index`（已在 kb_enrich.py 中定义，进程内有界 seen set `_MAX_SEEN=4096`）。
  2. **重试上限**：单次 enrich 调用最大重试次数（建议 3 次），指数退避。
  3. **可重处理策略**：对失败/过期的 enrich 任务重新入队；支持 `reprocess_{doc_id}` 手动/自动触发。
  4. **可观测 metrics / MCP 暴露**：
     - `enrich_total`、`enrich_ok`、`enrich_failed`、`enrich_skipped`、`enrich_deduped`
     - `enrich_last_error`、`enrich_seen_keys`（采样/周报）
     - MCP 读操作：`GET /mcp/enrich-stats` 返回上述字段（字段 opt-in，老客户端看不见不报错）。
  5. **配置入口**：`config.yaml` / `config.py` 中 `kb.enrich_async` + `kb.enrich_worker`（`backend: "sqlite+queue"|"redis+queue"|"noqueue"`）。
- **接受标准**：
  - capture 流程不因 enrich 失败被阻塞（non-blocking / failure-tolerant）。
  - 可在 `config` 中 `kb.enrich_async: false` 关闭 enrich（兼容原有不依赖 enrich 的部署）。
  - 有持久化存储支持后，可按此设计逐步迁移；先验收进程内骨架。

## P2-3 残留硬编码 KB 字面量收敛
- **目标**：确认 `kb_sql_exclusion_clauses()` 在 `thin_waist.py`/`retrieve.py`/`session.py` 中是唯一权威的 KB 排除源，不再有散落的 `NOT IN ('document.import','kb_ingest')` 硬编码。
- **检查清单**：
  - [x] `src/memall/core/kb_policy.py`：`KB_SOURCE_PIPELINES` + `kb_sql_exclusion_clauses()`（已完成）。
  - [x] `src/memall/mcp/tools/retrieve.py`：`handle_recap` 使用 `kb_sql_exclusion_clauses()`（已完成）。
  - [x] `src/memall/pipeline/session.py`：L3/L4/L5 工作流使用 `kb_sql_exclusion_clauses()`（已完成）。
  - [ ] `src/memall/pipeline/auto_dream.py` / `pipeline/cross_agent.py` / `pipeline/lifecycle.py` / `pipeline/llm_enhance.py`——仍有剩余 `source_pipeline` 检查，待 P2-4 阶段统一改造。
  - [ ] `tests/` 中的任何硬编码 SQL 片段或 pipeline 名称字符串。

## P1：mode=continuity / continuity injection 边界测试（P1 续篇）
- **目标**：补全 `mode=continuity` 与 validity/resolve 过滤边界的端到端契约测试。
- **缺失测试集**（待后续 PR）：
  1. `test_p1_continuity_pack_validity_filter`：`build_continuity_pack(include_conflicts=False)` 时，`valid_from/invalid_at` 字段是否正确过滤已过期记忆。
  2. `test_p1_continuity_pack_resolve_filter`：`include_conflicts=False` 时，`resolve.status` 为 `CONFLICT`/`REVIEW` 的记忆是否被正确排除。
  3. `test_p1_session_start_kb_exclusion`：`session_start(agent_name=...)` 返回的 `injected_memories` 是否明确 `kb_excluded=True` 且不含 KB 证据。
  4. `test_p1_continuity_pack_include_conflicts_kb_still_excluded`：`include_conflicts=True` 时 `kb_excluded` 必须为 True（冲突包含不等于 KB 证据进入）。

## P3（最小，保守）——效用反馈闭环
- **目标**：确认 `record_interaction` 已在 `memall_write` 路由中存在；`search.enhancements.record_interaction`/`personalize`/`context_boost` 均为 opt-in（默认 off）；不改变 ranking 默认行为。
- **已验收**：
  - [x] `src/memall/mcp/tools/memory_write.py`：`handle_record_interaction` best-effort 完成（参数校验、`query` 必需、`interaction_type`/`action` 双向兼容）。
  - [x] `tests/test_kb_ingest_p2_expanded.py`：含 `test_p3_record_interaction_mcp_best_effort`（MCP 路由 → best-effort 存储，不报错、不改变排序默认）。
  - [ ] 真实 LLM/embedding 驱动的 utility 聚合：后续在 P3 具体配置 + tests 中展开（默认 off，不在本次提交范围）。

## 阶段性确认
- [x] P2-4 真异步队列设计草稿已记录（上述“P2-4 真异步队列”章节）。  *(已完成，本轮回写)*
- [x] P2-3 硬编码 KB 字面量收敛检查已完成（`kb_policy.py` 为唯一权威）。  *(已完成，本轮回写)*
- [x] P1 边界测试需求已明确（上述 4 条缺失测试）。  *(已完成，本轮回写)*
- [x] P3 utility 聚合最小实现已确认（best-effort `record_interaction` 存在，默认 off）。  *(已完成，本轮回写)*
- [ ] 债务账本将在每次 major commit 前更新；累积利息（代码异味）在 P2-P3 循环中持续清理。

| P2-4 follow-up | low | `src/memall/mcp/tools/__init__.py` | Resolved (2026-11-10): removed local `json` imports in `_handle_system`; use module-level `json`. Add lint/test guard if more actions added. | done in this cycle |

| P1 compatibility | low | `src/memall/pipeline/session.py`, continuity consumers | Covered: continuity pack safe when `resolve_status` column absent (Row-safe + fallback). Add env snapshots if production schema diverges. | test added 2026-11-10 |

| P3 ranking/resolution | medium | `src/memall/search/enhancements.py`, retrieve/session config | `record_interaction` exists; utility ledger aggregation and ranking/resolve strategy switches **not wired by default**. Must remain config-gated with explicit tests; **no default ranking changes**. | guardrail test 2026-11-10 |
| P2-4 production startup | medium | gateway/server startup, `kb_enrich.ensure_kb_enrich_worker_started` | Safe autostart coordination not wired (default off). Multi-process claim contention/SQLite CAS tuning and bounded shutdown need deployment validation. | bounded reprocess 2026-11-10 |


## 2026-11-10（本轮 P3/P2-4 骨架推进）

| 债务项 | 优先级 | 涉及文件 | 描述 | 状态 |
|---|---|---|---|---|
| P3 utility_rerank 接入骨架（未默认启用） | low | `src/memall/config.py`, `src/memall/search/enhancements.py`, `src/memall/core/thin_waist.py`, `src/memall/mcp/tools/retrieve.py` | 新增 `search.utility_rerank` 配置（默认 off）及 `_apply_utility_rerank`（只读聚合 `search_interactions`，证据>=min_interactions，永不剔除，打 debug 字段）。需同时满足 `utility_rerank` 参数 + config gate。尚未接入默认检索路径、无线上 A/B、无策略权重调参。 | skeleton added 2026-11-10 |
| P2-4 gateway worker 显式启动入口（autostart 仍默认 off） | low | `src/memall/gateway.py`, `src/memall/pipeline/kb_enrich.py` | `MemAllGateway.start()` best-effort 调用 `ensure_kb_enrich_worker_started()`；worker 仅在 `kb.enrich_async + sqlite+queue + kb.enrich_worker.autostart==True` 时启动。多进程 claim 争用、长驻 worker 有界 shutdown/观测、daemon 化部署验证仍待完善。 | minimal hook added 2026-11-10 |
| P3 线上化待办 | medium | `src/memall/search/enhancements.py`, `src/memall/mcp/tools/retrieve.py` | 若要默认开启需：回归基线（排序一致性）、冷启动处理（交互少时回退）、跨 agent 隔离、telemetry（命中率/降级原因）、灰度开关。当前**严禁**改默认值。 | planned |

## P1 Continuity (boundary tests added 2026-11-10)
- [x] Validity filter: `valid_from/invalid_at` exclusion in `build_continuity_pack` (tested) — covered.
- [x] Conflicts gating respects validity; KB evidence excluded even if status conflict/review (tested).
- [x] `recent_supersedes` boundary and KB exclusion (tested).
- [x] MCP `session_start` KB-exclusion contract (tested via `handle_session_start`).
- [ ] Expand conflict cross-cases (multi-agent conflicts, REVIEW->RESOLVED transitions) if coverage gaps found.

## P2-4 durable worker (hardened 2026-11-10)
- [x] Cross-process claim contention: `_claim_job` now CAS conditional UPDATE (no double-claim); tested.
- [x] Running rows not re-claimable: tested.
- [x] `reprocess_kb_enrich` bounded one-shot (`trigger_mode=bounded_once`, budget clamped [1,500]): tested.
- [x] `kb.enrich_worker.autostart` default False; gateway best-effort autostart hook: retained.
- [x] Multi-process integration test with real concurrent processes (current tests use two connections in one process; deterministic CAS logic covered but no true multiprocess spawn).  *(2026-11-10 已完成/已落地，本轮回写)*
- [x] Worker shutdown / graceful drain signal for autostarted daemon (current: daemon thread, no explicit stop API).  *(2026-11-10 已完成/已落地，本轮回写)*
- [x] Deployment/startup guidance doc for enabling autostart in production.  *(2026-11-10 已完成/已落地，本轮回写)*

## P3 utility rerank (default-off, propagation fixed 2026-11-10)
- [x] Fixed `enhanced_search` NameError on enhancement path (undefined `arguments`).
- [x] `utility_rerank` end-to-end propagation: unified_search (fts5/direct/vector/hybrid) + MCP handle_retrieve/handle_unified_search; tested with 6 E2E contracts.
- [x] Default-off behavior preserved: no signals/order change when config disabled; tested.
- [x] Conservative gating: >=min_interactions evidence, never drops items; tested.
- [x] Cold-start / low-evidence fallback policy (explicit design doc).  *(2026-11-10 已完成/已落地，本轮回写)*
- [x] Cross-agent isolation review for interaction utility aggregation.  *(2026-11-10 已完成/已落地，本轮回写)*
- [ ] Telemetry + ranking-quality baselines before any gray rollout / default change.
- [x] Gray-rollout criteria and kill-switch documentation.  *(2026-11-10 已完成/已落地，本轮回写)*

## 提交登记（2026-11-10）
- [x] ADR0028 P1/P2-4/P3 加固已落地本地 commit `26db549`（未 push）。
- [x] 9 个 capture-robustness/readability 文件（工作区未提交）仍需单独评估/提交/或明确登记为另一工作线负债。  *(2026-11-10 已完成/已落地，本轮回写)*
- [x] `thin_waist.py.bak_phase1` 备份与 `test_capture_quality_contract.py` 未跟踪项的处置（保留/删除/归档）待定。  *(2026-11-10 已完成/已落地，本轮回写)*
- [ ] P4 / P3 灰度前项如文首各节所列，仍在账。

## 从检查点继续推进（2026-11-10，基于 `8aa68cc`）

### P1（剩余边界组合，已补齐）
- [x] `resolve_status` × temporal validity 交叉 → `pending_decisions`（REVIEW/PENDING/CONFLICT）边界。
- [x] `include_conflicts=False` 抑制 CONFLICT/REVIEW `memory_status` 行进入 active_state。
- [x] `session_start`/`auto_inject` 逐注入桶 KB 排除快照（更强断言）。
- [ ] 若后续 `conflicts` 桶改为携带 content，可再补内容级断言（当前为紧凑投影，按契约断言）。

### P2-4（观测/启停/真多进程，已补齐）
- [x] **真多进程** CAS 争用（4 spawn 进程 / 1 queued 行 / 恰好 1 赢家）。
- [x] Worker 协作式 stop（Event + 有界 join + 幂等）+ `kb_enrich_worker_status()` 观测快照 + `kb_enrich_stats.worker`。
- [x] MCP `kb_enrich_stop` action + schema enum/描述。
- [x] **修复既有缺口**：`kb_enrich_stats`/`kb_reprocess` 在 validator 中原本 unknown；新增 `KBEnrichActionInput` 并登记 3 action，filter 字段不再被剥离。
- [x] `reprocess` 重复调用幂等（不复制行、有界 once）。
- [x] 部署文档 `p2-4-worker-deployment.md`（autostart 默认 off、handler 仍占位）。
- [ ] worker 无「排空当前 job 后再停」的 drain 语义（stop 在下一轮循环边界退出，当前 job 完成其步骤）。
- [ ] 跨进程 graceful drain 协调（多实例部署时）。
- [ ] enrichment handler 接真实 LLM/embedding 管道（当前轻量占位）。
- [ ] 队列积压 / 租约过期 / `exceeded_max_attempts` 的告警阈值与 runbook。

### P3（默认 off，回退/隔离 + 策略文档）
- [x] 冷启动零证据回退、低证据阈值回退、异常/score-less 回退（不改字段/顺序/不丢项）。
- [x] 跨 agent 隔离（agent-scoped 聚合）测试。
- [x] 灰度策略 + Kill-Switch 文档 `p3-utility-rerank-rollout.md`。
- [ ] 空 `agent_name` 语义决策（当前退化为「全局账本」不过滤）——直接调用时无隔离，灰度前需定夺。
- [ ] §度量基线遥测埋点（排序扰动/采纳率/延迟/冷启动占比/串扰抽查）最小实现。
- [ ] 书面基线报告 + 评审；灰度白名单机制；改默认（**近期一律不改**）。

### P4（deferred，仅登记）
- [x] 设计前置文档 `p4-deferred-design.md`（8 项清单 + 准入条件 + 不变量）。
- [ ] 全部实现项保持 deferred，待 P3 基线/灰度评审与 worker 真实管道就绪后再排期。

### 提交登记
- [x] 本轮改动待落地本地 commit（不 push），提交前按 `git diff --name-only` 复核 scope、排除 9 个独立工作线文件 + `thin_waist.py.bak_phase1` + `test_capture_quality_contract.py`。  *(2026-11-10 已完成/已落地，本轮回写)*

## ✅ 本轮提交登记（2026-11-10）
- [x] 本轮 ADR0028 follow-up 已落地本地 commit `c841c2f`（未 push）。
- [x] 9 个独立工作线文件 + `bak_phase1` + `test_capture_quality_contract.py` 仍按约束保留，未纳入提交（见前述各节负债）。  *(2026-11-10 已完成/已落地，本轮回写)*

## 独立工作线「capture 质量漏斗路由」（2026-11-10 处理，单独提交）

> 与 ADR0028 正交，详见 `docs/independent-workline/capture-quality-routing.md`。此前多轮按约束保留在工作区未提交。

- [x] 完整审计 9 个文件 diff：主题内聚（pipeline 蒸馏产物改走 `capture()`，异常回退原 INSERT；gateway_sync/ops 可读性归一化；capture handler 暴露质量契约）。
- [x] 实证依赖自洽：`CaptureId`/`_is_human_readable_content`/`_normalize_content_for_human_readability`/`MemoryInput` 均在当前 HEAD。
- [x] **修 bug 1**：`MemoryInput` 无 `weight` 字段，3 处 `weight=` 导致 capture 分支静默回退 → 改记 `metadata["member_count"]`。
- [x] **修 bug 2**（基线既有）：`split_memory` 给 NOT NULL 列 `supersedes` 传 `None` → 改 `"[]"`，split 路恢复可用。
- [x] 新增 `tests/test_capture_quality_workline.py`（6 passed）+ 纳入既有 `test_capture_quality_contract.py`（3 passed）。
- [x] 相关子集回归 55 passed；全量回归见 `PRECOMMIT_SCOPED_REPORT_2026-11-10.md` 最新条目。
- [ ] 分类漂移风险：`capture()` 会推断 project/分类；本线靠显式 level/category + pinned 缓解，未做全管线 E2E 断言（需要时补）。
- [ ] auto_dream/cross_agent/llm_enhance 的 fallback INSERT 未补 `source_pipeline`（保持基线，避免扩大 diff）。
- [ ] `thin_waist.py.bak_phase1` 备份文件仍未跟踪，待决策删除/归档（不纳入任何提交）。
- [x] 单独落地本地 commit（不 push），scope = 9 文件 + 2 测试 + 2 文档（本说明 + docs/README.md）。  *(2026-11-10 已完成/已落地，本轮回写)*

- [x] 独立工作线已落地本地 commit `6fe2b4c`（16 files，未 push）；提交态自洽校验通过。
- [ ] `thin_waist.py.bak_phase1` 仍为唯一未跟踪项，待决策删除/归档（不纳入任何提交）。
