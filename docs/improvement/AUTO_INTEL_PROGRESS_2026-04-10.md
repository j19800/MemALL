# Auto-ingest / Semantic Dedup / Self-evolution / Intelligence Progress (2026-04-10, local, uncommitted)

## Scope of this pass
- Continued boundary audit + contract hardening for auto-ingest, semantic dedup config, and self-evolution correction surfacing.
- No commits/pushes made.

## Code changes
- `src/memall/config.py`
  - Added `capture.auto_capture_min_content_len: 20` with explicit fallback-chain comment (`capture.auto_capture_min_content_len` → `quality_gate.lenient_min_len` → legacy 20).
  - Kept semantic dedup disabled by default and retained v2 keys + legacy-key compatibility behavior.
- `src/memall/auto_capture.py`
  - Uses `_auto_capture_min_len()` (configurable, clamped 10..500) instead of hardcoded short-content gate.
- `src/memall/pipeline/session.py`
  - Session injection formatting now renders improve-engine active corrections from `injection["active_corrections"]` as `[EVOLVE] ...` (personal+global, severity-aware, bounded), alongside existing `[CORRECTIONS]` L6 reflection tips and `[LESSONS]`.
  - Preserves `session_end` as unreliable/read-only compliance-check path; maintenance remains opt-in via `session.auto_improve`/`session.auto_identity`.

## Tests added
- `tests/test_auto_capture.py`
  - Contracts for ask/reflect/discussion extraction, error/short/non-target filtering, in-process coarse dedup, and configurable min content length.
- `tests/test_auto_inject_corrections.py`
  - Contracts that `auto_inject` returns improve-engine `active_corrections` and `session_start(auto_inject=True)` formats `[EVOLVE]` + payload.
- `tests/test_semantic_dedup_config_contract.py`
  - Contracts for semantic dedup defaults/v2 keys presence and legacy-key precedence in capture-time merge behavior (being tightened in this pass).

## Validation (this pass)
- `py_compile` OK for touched source/tests.
- `pytest -q tests/test_auto_capture.py tests/test_capture_quality_contract.py` → `8 passed`.
- `pytest -q tests/test_auto_capture.py tests/test_capture_quality_contract.py tests/test_full_feature_acceptance.py tests/test_capture_semantic_dedup.py tests/test_semantic_dedup_safety.py tests/test_capture_thread_inference.py tests/test_capture_resolve_engine.py tests/test_capture_project_inference.py tests/test_capture_quality_workline.py tests/test_improve.py tests/test_session_sweep.py tests/test_pipeline.py` → `73 passed`.
- `pytest -q tests/test_session_sweep.py tests/test_improve.py tests/test_pipeline.py` → `26 passed` (post session-format change).
- `pytest -q tests/test_auto_inject_corrections.py tests/test_semantic_dedup_config_contract.py tests/test_improve.py tests/test_session_sweep.py tests/test_auto_capture.py` → `19 passed` (initial run); semantic config precedence test under refinement.

**2026-11 local pass results** (P0-1/P0-2 completion):
- Targeted suite: `tests/test_auto_capture.py tests/test_semantic_dedup_config_contract.py tests/test_capture_quality_contract.py tests/test_capture_semantic_dedup.py tests/test_capture_thread_inference.py tests/test_auto_inject_corrections.py tests/test_improve.py` → `54 passed, exit 0`
- Full regression: `683 passed, 2 skipped, 192 warnings` (exit 0)
- Hash-dedup observability: `_build_capture_contract` now emits exact-hash dedup metadata (`layer`, `reason`, `decision`, `target_id`, `dedup_similarity`) in `status="dedup_hash"` path (P0-1 P1 audit item completed)
- Information-increment gate: ask-path low-signal detection via `_looks_low_information` + `_REASON_SKIPPED_LOW_INFORMATION`; config-aware `_auto_capture_info_increment_min_ratio` toggle (`default 0.15`); `auto_capture_info_increment_enabled` defaults enabled
- Persistent short-window dedup: 300s window, MIN_HITS=2, agent-scoped keys with legacy-compatible empty-agent fallback; contract test `test_auto_capture_persistent_dedup_across_restart_and_reports_reason` passes
- Min-content-length gate: `_auto_capture_min_len()` config chain (`capture.auto_capture_min_content_len` → `quality_gate.lenient_min_len` → 20; clamped 10–500)
- All new contract tests appended and passing

## Known gaps / tech debt (still open)
- **Auto-ingest coverage**: hook source still limited to `memall_persona.ask`, `memall_system.reflect`, `memall_discussion.respond`; no conversation/git/error/task event sources yet (aspirational v2 modules not implemented).
- **Semantic dedup v2**: v2 config keys exist but `semantic_dedup_use_embeddings`, cross-agent/cross-session windows, and dedicated v2 module are not fully wired/validated; must preserve `_NEG_RE`, `_QTY_RE`, CJK char-level fallback, conservative legacy bar behavior, and project/thread semantics.
- **Self-evolution**: active corrections now surfaced in session injection, but effectiveness tracking, L4→L6 extraction, cross-agent consensus evolution config, and richer lifecycle metrics remain partial/unwired.
- **Intelligence**: subject generation improved but no full intelligence layer (auto summary/edge creation/utility-driven retention) or dedicated tests (`tests/test_intelligent*`, `tests/test_dream*` absent); `[Lx ...]`/`who_label` compatibility must remain intact.
- **Docs/tracking**: `docs/improvement/AUTO_INTEL_UPGRADE.md` remains aspirational; this progress note + commit checklist/ledger need continued updates before commit.

## Commit checklist (draft, do not commit yet)
- [ ] Finalize and pass semantic dedup config contract (legacy precedence) and rerun focused regression.
- [ ] Re-run broader scoped regression relevant to capture/dedup/improve/session/pipeline/intelligence before commit.
- [ ] Review all diffs for invariant/contract preservation (dedup guards, arc/decisions semantics, subject prefix compatibility, session maintenance flags).
- [ ] Update `TECH_DEBT_LEDGER` / improvement docs with final status and remaining gaps.
- [ ] User approval required before any commit/push.


## 2026-04 follow-up (this pass): semantic dedup v2 wiring (local, uncommitted)
- `src/memall/core/thin_waist.py` capture semantic dedup gate now resolves v2 keys when legacy keys are absent:
  - `semantic_dedup_threshold_cosine` -> TF-IDF/cosine bar
  - `semantic_dedup_window_same_agent` -> candidate window
  - legacy `semantic_dedup_threshold` / `semantic_dedup_window` still take precedence when present (backcompat)
- `semantic_dedup_window_cross_agent`, `semantic_dedup_window_cross_session`, `semantic_dedup_use_embeddings` remain reserved/contracted (default 0/False): capture stays same-agent local-text similarity only; cross-scope and embedding merges are not enabled by default.
- `_char_level_dup_lookup` continues to use legacy-derived conservative bar when legacy threshold is present, and dedicated jaccard threshold (`semantic_dedup_threshold_jaccard`) when legacy threshold is absent.
- Safety guards preserved: exact-hash dedup always runs, semantic dedup default off, `_NEG_RE`/`_QTY_RE` guards, CJK char 3-gram/Jaccard fallback.

## Validation (this pass)
- `pytest -q tests/test_semantic_dedup_config_contract.py tests/test_capture_semantic_dedup.py tests/test_semantic_dedup_safety.py` → 12 passed.
- `pytest -q tests/test_auto_capture.py tests/test_auto_inject_corrections.py tests/test_improve.py tests/test_session_sweep.py` → 17 passed.
- `pytest -q tests/test_auto_inject_corrections.py tests/test_session.py tests/test_session_sweep.py` → 17 passed.
- Added contract tests in `tests/test_semantic_dedup_config_contract.py`: v2 threshold/window apply when legacy absent; v2 same-agent window respected; reserved cross-scope/embedding knobs do not enable cross-agent merges.

## Remaining gaps
- No true cross-agent/cross-session semantic dedup candidate selection or embedding-backed dedup path yet (reserved knobs only).
- Auto-ingest sources still limited to `ask/reflect/discussion` on `memall_*` hooks.
- Self-evolution/intelligence broader lifecycle/effectiveness tracking and dedicated `tests/test_intelligent*`/`tests/test_dream*` still absent.

## 2026-11 follow-up (this pass, local uncommitted): boundary fixes + self-evolution evidence idempotency

### Code fixes
- `src/memall/core/thin_waist.py`
  - `_semantic_dedup_lookup(..., project=None)`: optional project-scope filter (`COALESCE(project,'') = COALESCE(?, '')`) for callers that must preserve project boundaries.
  - `_infer_thread_root` semantic fallback now passes `project=data.project` to `_semantic_dedup_lookup` (prevents cross-project semantic thread linkage; temporal path behavior unchanged).
- `src/memall/pipeline/improve.py`
  - Personal corrections: merge unique `source_l6_ids` and derive `hit_count`/severity from merged evidence on repeated runs (no inflation/dup evidence).
  - Global corrections: repeated promotion merges/updates existing global rule (unique `source_l6_ids`, `hit_count`, severity escalation/keep, summary, `updated_at`) instead of leaving evidence stale/insert-only.
- `src/memall/config.py` / `src/memall/auto_capture.py`
  - Added `capture.auto_capture_enabled` (default `True`, backward-compatible opt-out) and gated `_auto_capture_hook` (fail-open on config read error).

### Tests added/updated
- `tests/test_capture_thread_inference.py`: `test_thread_inference_semantic_fallback_respects_project_boundary`.
- `tests/test_improve.py`: `test_improve_global_correction_merges_on_repeat`, `test_improve_personal_correction_merges_unique_hits`.
- `tests/test_auto_capture.py`: `test_auto_capture_can_be_disabled_by_config`.

### Validation
- Targeted: `PYTHONPATH=.:src ./.venv/bin/python -m pytest -q tests/test_improve.py tests/test_auto_capture.py tests/test_capture_thread_inference.py` → `19 passed`.
- Full suite (background job collected): `PYTHONPATH=.:src ./.venv/bin/python -m pytest -q` → `683 passed, 2 skipped, 192 warnings in 853.79s (exit 0)`.

### Remaining gaps (not addressed this pass)
- `semantic_dedup_window_cross_agent/cross_session` and `semantic_dedup_use_embeddings` remain reserved/default-off (no cross-scope/embedding dedup).
- Auto-ingest sources still limited to `ask/reflect/discussion` on `memall_*` post-tool hooks.
- Self-evolution still lacks richer effectiveness tracking/L4→L6 extraction/cross-agent consensus evolution config and dedicated `tests/test_intelligent*`/`tests/test_dream*`.
- Tracking/docs in `docs/p0-p2/*` should be synced in commit-prep pass if this work is included in that scope; no commit/push performed.



## 2026-11-12 本轮：P2-3 KB 策略集中化收敛完成（本地验收，未提交）

### 变更摘要
将 7 个 pipeline/MCP 模块中的硬编码 `source_pipeline` 排除 SQL 片段全部收敛到单一维护点 `kb_policy.kb_sql_exclusion_clauses()`：

| 文件 | 修改查询点 | 说明 |
|------|------------|------|
| `auto_dream.py` | 5 (defrag, patterns, distill, forget-P2, forget-isolated) | 夜间梦境管线不应把 KB 证据当作用户记忆参与聚类/蒸馏/遗忘 |
| `cross_agent.py` | 1 (`knowledge_distill_step` L6 lessons) | 跨 Agent 知识蒸馏排除 KB 证据 |
| `lifecycle.py` | 1 (`distill_l7_step` L4/L6 read) | L7 蒸馏读取用户态记忆，KB 证据不应参与 |
| `llm_enhance.py` | 1 (`_group_by_agent_category` L4/L6 read) | LLM 增强聚类读取用户态记忆，KB 证据不应参与 |
| `federation_tools.py` | 6 (auto_inject: L7, L1-suppl, L7-suppl, workflow_skills, L4_decisions) + cache refresh | 会话注入各桶 KB 排除全部收敛；移除手工 `_kbm/_kbs` fallback |
| `session.py` | 2 (`build_continuity_pack` base_where; `session_start` excluded_sps fallback) | 连续性包与会话启动 KB 排除直连 helper；literal fallback 移除 |
| `retrieve.py` | 1 (`handle_recap` recent L4/L5) | 会话回顾只面向用户态决策/任务，KB 证据排除 |

### 验收结果
- 所有修改文件 `py_compile` 通过
- 目标回归套件 54/54 通过 (`test_kb_ingest_p2_expanded`, `test_auto_capture`, `test_semantic_dedup_config_contract`, `test_capture_thread_inference`, `test_auto_inject_corrections`, `test_improve`)
- 扩展回归套件 68/68 通过 (含 `test_capture_quality_contract`, `test_session`)
- 全量回归见后续完整运行
- `grep -rn "document\.import\|kb_ingest" src` 仅剩 `kb_policy.py` 定义、注释、测试契约、必要输出导出（如 `session_start` `excluded_source_pipelines` 输出）

### 影响
- 单一变更点：`kb_policy.kb_sql_exclusion_clauses()` 修改即全局生效
- 无行为变更：仅收敛实现，语义不变
- 可观测性：所有自动注入/连续性/回顾输出仍包含 `kb_excluded=true`、`excluded_source_pipelines=["document.import","kb_ingest"]`

### 仍在账技术债务（本轮不解决）
- TD-DEDUP-V2-01: 跨智能体/跨会话语义去重（保留键默认关）
- TD-AUTO-SRC-01: 自动入库来源扩展（仅 3 个 extractor）
- TD-EVOL-TRACK-01: 自进化有效性追踪/生命周期深度
- P2-4: kb_enrich 持久化 enqueue/worker/gateway/CAS/metrics/backend
- P3 (planned, default-off): record_interaction → utility ledger → 检索策略开关

## 2026-11-12 追加：P1 continuity/session KB 元数据收敛微修（本地验收，未提交）
- 修复 `session.py::build_continuity_pack` 中 `excluded_source_pipelines` 误用 `kb_sql_exclusion_clauses` helper 别名的问题，统一输出 `list(KB_SOURCE_PIPELINES)`。
- 简化 `session_start` 注入返回的 `excluded_source_pipelines` 组装逻辑，移除不必要的 try/except/helper 误用分支，保持 `kb_excluded=true` 与 `KB_SOURCE_PIPELINES` 一致。
- 回归：`tests/test_kb_ingest_p2_expanded.py tests/test_session.py tests/test_auto_capture.py tests/test_semantic_dedup_config_contract.py tests/test_auto_inject_corrections.py` 通过；全量基线此前本轮已 `689 passed, 2 skipped`。


## 2026-11-12 本轮：四项开放债务推进（本地验收，未提交）

### TD-AUTO-SRC-01（自动入库来源白名单）
- `auto_capture.py` 新增显式扩展点：`register_auto_capture_extractor()` / `unregister_auto_capture_extractor()` / `auto_capture_extractor_whitelist()`。内置三源（ask/reflect/respond）保持默认不变。
- 新增配置 `capture.auto_capture_extractors`（默认 None）：只能**收窄**白名单（部署策略），不能隐式放宽；放宽必须走注册函数并配套契约测试。
- 契约测试：`test_auto_capture_extractors_config_restriction_narrows_whitelist`（含解除限制后恢复捕获的正向断言）、`test_auto_capture_extractor_registration_extension_point`。

### auto_capture_recent 保留与高负载加固
- 持久去重参数全部可配（默认值与既有契约完全一致：300s 窗口 / 2 次命中跳过 / retention=max(window*12,3600)）：
  - `capture.auto_capture_persistent_window_seconds`（钳制 1..86400）
  - `capture.auto_capture_persistent_min_hits`（钳制 1..100）
  - `capture.auto_capture_retention_seconds`（0=按窗口推导；且永不小于窗口本身）
  - `capture.auto_capture_max_rows`（默认 10000，rowid 最旧裁剪；0=关闭）
- 新增 `_persistent_dedup_settings()` 统一解析（config 异常 fail-open 到默认）。
- 契约测试：`test_auto_capture_persistent_dedup_config_tuning`（默认=旧契约、可调、retention>=window、越界钳制）。

### TD-EVOL-TRACK-01（自进化有效性/生命周期观测）
- `corrections` 表新增加性观测列（自动迁移）：`injected_count` / `last_injected_at` / `adopted_count` / `last_adopted_at`（默认 0/空，老行老读法不受影响）。
- 写路径默认关：`evolution.correction_injection_tracking_enabled` / `correction_adoption_tracking_enabled`（默认 False）。`record_corrections_injection()` 在 `federation_tools.auto_inject` 取到 active corrections 后调用（关时严格 no-op、fail-open）；`record_correction_adoption()` 留给会话合规检查/人工标注/未来 LLM 评估接入。
- 只读报告 `correction_effectiveness_report()` + MCP `memall_system action=correction_report`（validator 登记 `CorrectionReportInput`、enum/描述同步），输出 by_status/injected/adopted/adoption_rate/top_injected/tracking。
- 契约测试：defaults_off_no_writes、enabled_counts、schema migration、MCP route/validator contract（4 个）。

### TD-DEDUP-V2-01（跨 agent/session/embedding 预留默认关）
- 既有 `test_v2_cross_scope_windows_reserved_no_behavior_change` 保留；新增两个加固契约：
  - `test_v2_embedding_knob_requires_no_embedding_backend`：`semantic_dedup_use_embeddings=True` 不得让 capture 依赖/激活 embedding 后端，且 dedup 观测 layer 永不为 embeddings/vector。
  - `test_v2_cross_session_window_never_narrows_same_agent_dedup`：cross_session=0 预留旋钮不得改变 same-agent 窗口行为。

### 验收
- 定向：`tests/test_improve.py tests/test_auto_capture.py tests/test_semantic_dedup_config_contract.py tests/test_auto_inject_corrections.py tests/test_session.py tests/test_kb_ingest_p2_expanded.py` → **68 passed**；新增 8 测试全绿。
- 全量：**698 passed, 2 skipped, 192 warnings in 878.59s (exit 0)**（基线 689 + 本轮新增 9，零回归）。


## 2026-11-12 第三轮：TD-EVOL-TRACK-01 自动采纳判定器落地（本地验收，未提交）

### 背景
上一轮遗留的开放点：「采纳」没有自动判定信号源（`record_correction_adoption` 只有测试在调）。本轮实现三态自动判定并挂上两个可靠钩子点。

### 实现
- `improve.evaluate_session_corrections(agent, started_at, ended_at, session_id)`：对会话窗口内每条 active correction 产出三态信号——
  - **adoption**：窗口内有注入证据（`last_injected_at` 落在窗口内）**且**会话内容未再现 pattern 关键词 → 尝试 `record_correction_adoption()`；
  - **violation**：再现关键词 → 不计数、只上报（不减记、不触发过期，保守）；
  - **no_signal**：无注入证据 → 一律不动任何计数（防"没注入也记采纳"的假阳性）。
- 分层门控（全部默认关、fail-open）：
  - `evolution.correction_auto_adoption_eval`（主门，默认 False）；
  - 实际写计数仍需 `evolution.correction_adoption_tracking_enabled=True`，否则输出 **dry_run**（只出 verdict、`written=0`）——即灰度预览模式。
- 挂接点（遵循仓库自己的教训 `session_end_unreliable`，双钩子覆盖）：
  - `session_end`：结束时评估，结果加性放入 `corrections_eval`（adopted/violations/no_signal/written/dry_run）；
  - `session_start` stale 关闭路径：对被静默关闭的过期会话评估，结果放入 `stale_corrections_eval`。
- 启发式局限已在 docstring 说明：关键词是复发的代理而非证明（如"已验证"含"验证"），因此采纳必须叠加注入证据、违规只阻断计数。

### 测试（+5）
- `test_corrections_auto_eval_default_off_is_noop`（默认纯 no-op）
- `test_corrections_auto_eval_three_state_verdicts`（三态+计数写入正确性+global/personal 作用域）
- `test_corrections_auto_eval_dry_run_without_adoption_gate`（开评估关写门=零写入预览）
- `test_session_end_reports_corrections_eval_when_enabled`（端到端：session 窗口→adopted/written≥1、DB 计数变化）
- `test_session_end_corrections_eval_absent_when_disabled`（默认关时 session_end 形状不变）
- 定向回归：`tests/test_improve.py tests/test_session.py tests/test_session_sweep.py tests/test_auto_capture.py` → **44 passed**。

### 效果
`correction_effectiveness_report()` / MCP `correction_report` 的 `adoption_rate` 自此有了自动数据来源；开启两枚门后可先用 dry_run 核对 verdict 质量，再开写门灰度。


## 2026-11-12 第四轮：网关热重载（watcher+execv）+ 两个真缺陷修复（本地验收，未提交）

### 热重载功能（用户问"能否给网关配置热重载"）
- 结论：进程内 importlib.reload 对 MemALL 不安全（连接池/HookRegistry/锁等模块级状态）；采用 uvicorn --reload 同款的 **watcher + os.execv 原地重启** 方案。
- 新增 `src/memall/gateway_autoreload.py`：轮询 src/memall 的 .py mtime；**3s 稳定窗（防抖）+ compile 守卫（半写坏文件绝不触发重启，等下一次保存）**；execv 保持同 PID，systemd/keepalive 兼容；watcher 崩溃只杀 watcher 不杀网关。
- 门控：`MEMALL_AUTORELOAD=1` 或 config `gateway.autoreload.enabled`，**默认 False**（重启会切断在途请求，dev-only）。
- 入口：新增 `gateway_start_dev_reload.py`（dev 专用 wrapper）。
- 修复启动竞态：首轮 snapshot 同步在 `watch_and_restart()` 内取（原在线程里取，调用后立刻写文件会被吞掉，表现为测试 1/5 概率挂）。
- 测试：`tests/test_gateway_autoreload.py` 5 个（env 门控/snapshot diff/compile 守卫/防抖触发/坏代码跳过+修复后触发），5x 连跑稳定。

### 真缺陷修复 1：config DEFAULTS 重复 "gateway" 键（本会话自引入，自曝）
- 上一批给 config.py 插 `"gateway"` 段时没发现 DEFAULTS 第 39 行已有 `"gateway"` 段（含 port 9920），dict 字面量后键覆盖前键 → `gateway.port` 变 None。全量 `test_get_config_default` 抓到。
- 修复：autoreload 配置合并进原 gateway 段，删除重复段。教训已实践"改动必须过全量"。

### 真缺陷修复 2：tests/test_gateway.py::test_gateway_capture 顺序依赖假绿
- 根因：jieba 在 /capture 请求内懒加载（冷载 ~4-6s），超过测试 3s 客户端超时；全量里绿是因为更早的测试进程内已加载 jieba，单跑必挂。HEAD 同样挂（非本会话引入）。
- 修复：setup_module 预热 jieba + 请求超时 3s→15s（仅测试侧，产品代码零改动）。

### 验收
- 全量：**708 passed, 2 skipped, 192 warnings in 929.94s (exit 0)**（703 基线 + autoreload 5）。
