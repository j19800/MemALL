# MemALL Memory Cognitive Loop — Implementation Plan

**目标**：让 MemALL 从“能存文本”转向**智能化存入 + 智能化消费 + 自进化**的记忆系统。本计划基于在 DSH 插件市场的调研、对现有 Phase1/Phase2 的审计，以及对现有产品（mem0、context-continuum、dsh-local-memory 等）的机制提炼而成。

**站位**：
- 保持向后兼容：`CaptureId(int)` 不变、现有 `MemoryInput` 字段不改、现有 MCP 调用形状不变。
- 增量暴露：新增字段在现有消费者看不见时不报错、显式可选。
- 规则优先、LLM-on-demand、async-first、evidence preserved、non-destructive default。

---

## 1. 高层架构（MemoryRecord v2 视图）

```text
Ingest → Fast Capture (Phase1: normalize/readability/quality→Phase2: resolve/slot_key/validity/confidence/edges) 
        → Async Enrichment Queue (去重/归纳/画像/主题/实体——LLM-on-demand) 
        → Memory Store (SQLite+FTS5+sqlite_vec + validity/state) 
        → Memory Pack Assembler (任务/预算/scope 感知，validity/conflict/provenance-aware) 
        → Feedback & Policy Loop (used/accepted/corrected/rejected/ignored → utility → 策略自适应)
```

**MemoryRecord v2 视图**（在现有 DB/模型上增量）：

| 字段 | 位置 | 说明 | 变更性 |
|---|---|---|---|
| `slot_key` | `metadata.slot_key` | 将内容归入的槽（如 `slot:config:...`, `slot:decision:...`, `slot:profile:...`, `slot:task:...`）| 捕获时自动推断/注入 |
| `resolve_status` | `metadata.resolve.status` | `NOOP/MERGE/UPDATE/SUPERSEDE/CONFLICT/REVIEW` | Phase2 resolve 引擎输出 |
| `resolve_reason` | `metadata.resolve.reason` | 简短原因（关键词/SequenceMatcher 分数） | 可选；从 decision 推断 |
| `resolve_confidence` | `metadata.resolve.confidence` | 0.0-1.0，resolve 的置信度 | 可选 |
| `resolve_evidence` | `metadata.resolve.evidence` | 支持本 claim 的最新/最关键记忆的 hash/id 摘要 | 可选 |
| `valid_from` | `memories.valid_from` | 有效起始时间戳（ISO/epoch） | Phase2 已有 |
| `valid_at` | `memories.invalid_at` | 有效终止时间戳（空=当前有效） | Phase2 已有 |
| `memory_status` | `memories.memory_status` | `active/archived/superseded/expired/conflict/review` | 增补标签 |
| `source_pipeline` | `metadata.source_pipeline` | 源 pipeline 名称（如 `conversation.session_end`, `llm_enhance.summarize`, `cross_agent.integrate`, `observe`, `lifecycle.distill`）| 已有 |
| `ingest_mode` | `metadata.ingest_mode` | `auto/manual/batch/llm_enhance/cross_agent/observe`| 已有 |
| `project` | `memories.project` | 项目/范围 | 已有 |
| `tags` | `memories.tags` | JSON array | 已有 |
| `confidence` | `metadata.confidence` 或 `Memory.confidence` | 0.0-1.0 置信度（如非 resolve 明确覆盖）| 已有 |

> **关键**：所有新增字段均以 `metadata.X` 形式出现，保持 `MemoryInput`/`Memory` dataclass 与 `CaptureId` int 兼容。`_row_to_memory` 可通过 `row.get("metadata", "{}")` 兼容读取旧库。

---

## 2. 核心任务拆解（P0-P4）

### P0：稳住测试基线 + 增量暴露合同（最高优先级）

**目标**：
1. 修复 `tests/test_capture_resolve_engine.py::test_resolve_can_be_disabled`（坏了的 `set_config` 导入）。
2. 让 resolve/validity 字段在检索/MCP 层**增量暴露**，现有消费者不破坏。

**具体改动**：

| 文件 | 改动要点 |
|---|---|
| `tests/test_capture_resolve_engine.py` | 删掉末尾 `from memall.config import set_config, get_config` 报 ImportError；改用 `config.reset_config()` + `get_config()` 或直接 monkeypatch `os.environ`；或标记为 `@pytest.mark.skip` 并补全。 |
| `src/memall/mcp/tools/retrieve.py` | `handle_retrieve`、`handle_unified_search`、`handle_vector_search`、`handle_hybrid_search` 在返回的 `Memory` 物件或 JSON 里**增量**加入 `metadata.slot_key`、`metadata.resolve`（`status/reason/confidence`）字段；若字段不存在给默认值。 |
| `src/memall/core/thin_waist.py:_row_to_memory` | 新增可选字段从 `row["metadata"]` 里读取 `slot_key`、`resolve`，无则空串/`{}`；保持 `Memory` dataclass 字段不变（或按 `Optional` 增补）。 |
| `src/memall/search/enhancements.py` / `_seen_postprocess` | 在 postprocess 时保留这些字段，**不 strip**；或者在 JSON 序列化前 ensure_ascii 里带上它们。 |
| `src/memall/core/db.py` | `SCHEMA_SQL` 已有 `valid_from/invalid_at`，确认已迁移。如果缺列（老库）则 ALTER TABLE 加列（谨慎、离线迁移或已迁移库默认补空）。 |

> **验收**：`pytest tests/test_capture_resolve_engine.py -q --ignore=tests/smoke_test.py` 通过；MCP `retrieve`/`unified_search` 返回的 JSON 里至少有 `metadata.slot_key`、`metadata.resolve.status`（即使为 `{}` 或 `null`），旧代码解析不报错。

---

### P1：跨会话 Continuity Pack（最直接的感知价值）

**目标**：下次会话启动或任务开始时，系统能生成并暴露“有效的跨会话上下文”：已确认的偏好/决策/未决项/冲突提醒，而不是空白或整段历史。

**具体改动**：

| 文件 | 改动要点 |
|---|---|
| `src/memall/core/thin_waist.py:capture()` | 会话结束/手动触发时：在 capture 时自动注入 `session_id/thread_id/project/agent_name` + `source_pipeline=conversation.session_end`；resolve 时对 `slot_key` 为 `slot:config:` / `slot:decision:` / `slot:profile:` 的记忙走 `UPDATE/SUPERSEDE/CONFLICT/REVIEW`；普通事实走 `NOOP/MERGE`。 |
| `src/memall/pipeline/session.py` | `build_continuity_pack(agent_name, viewer, project, limit, include_conflicts) -> dict`（只读、validity-aware）。返回 ADR 契约字段：`active_state`、`conflicts`、`pending_decisions`、`recent_supersedes`、`why_selected`；同时保留兼容别名 `active_memories`、`key_decisions`、`open_reviews`、`project_state` 等，避免旧消费者中断。 |
| `src/memall/mcp/tools/retrieve.py` | `handle_continuity_pack` 调用 `build_continuity_pack` 暴露 continuity pack。 |
| `src/memall/search/enhancements.py` | 若有 `continuity` 开关，在 `retrieve` 前自动筛选有效 slot/状态并组装包（后续）。 |
| KB/用户状态分离 | continuity pack **排除 KB 证据**（`document.import`/`kb_ingest`/`category=knowledge`+`document.*` provenance），并显式返回 `kb_excluded=true`、`excluded_source_pipelines`、`why_selected.filters`。KB 不得被当作用户偏好/决策状态注入。 |

> **验收**：在测试 DB 里插入跨会话事实（含 `slot_key`、`resolve_status`）与 KB chunk，调用 `handle_continuity_pack`/`build_continuity_pack` 返回包含 `active_state` 与 `conflicts`（或显式空数组）、`pending_decisions`、`recent_supersedes`、`why_selected`；KB `doc_id`/chunk 不出现在 continuity state，且包含显式 `kb_excluded` 标记。

---

### P2：Knowledge Pack 与 KB 入库（知识库事实受控入库）

**目标**：支持外部/项目知识的受控入库、检索与 provenance 链；区分 KB 事实 vs 用户状态。

**具体改动**：

| 文件 | 改动要点 |
|---|---|
| `src/memall/mcp/tools/memory_write.py` | `handle_kb_ingest` / `document_import`：默认 `category=knowledge`、`source_pipeline=document.import`（或 `kb_ingest`）、`kb_kind=document_chunk`、`kb_user_state=False`；强 provenance（`doc_id/doc_version/doc_hash/chunk_hash/chunk_index/path/title/updated_at`）；按 `doc_id+chunk_index+chunk_hash/doc_version` 保守 dedup/supersede（非破坏性）。 |
| `src/memall/core/kb_policy.py`（新增） | 统一 KB 判定/标注/SQL 排除：`KB_SOURCE_PIPELINES`、`is_kb_source_pipeline`、`source_kind_from_pipeline`、`kb_sql_exclusion_clauses`。检索、continuity、session、recap 共用，避免散落 `json_extract` 漂移。 |
| `src/memall/core/thin_waist.py` / `thin_waist_search.py` | `unified_search/enhanced_search/vector_search/hybrid_search` 支持 `source_pipeline` 过滤与 `source_kind`/`metadata_source_pipeline` 标注；`vector_search`/`hybrid_search` 采用 `sqlite3.Row` 安全访问。 |
| `src/memall/mcp/tools/retrieve.py` | `handle_retrieve`/search/recap/continuity_pack 返回 provenance（`source_pipeline`、`source_kind`、`kb_excluded`、`excluded_source_pipelines`）；KB chunk 不进入 `handle_recap` L4/L5、continuity pack、session 注入。 |
| `src/memall/pipeline/session.py` | `session_start`/continuity 使用 KB 排除策略；session 注入/L3/L4/L5 不得混入 KB 证据。 |
| `src/memall/pipeline/kb_enrich.py`（新增，P2-4） | 非阻塞 KB enrichment hook：`kb_enrich_handler` config-gated（`kb.enrich_async`，默认 true）、daemon thread/best-effort、失败吞掉、`kb_enrich_stats` 可观测；`register_default_kb_enrich_hook` 由 `mcp/tools/__init__` best-effort 注册。`memory_write._fire_kb_enrich` 仅在 KB ingest 成功 capture 后触发。 |
| `src/memall/config.py` | 新增 `kb.enrich_async=true`（P2-4 默认非阻塞、保守）。 |

> **验收**：文档导入产出包含 `category=knowledge`、`source_pipeline=document.import`（或 `kb_ingest`）、`doc_id/doc_hash/chunk_hash`、版本/去重/supersede 行为正确。检索/MCP 可按 `source_pipeline` 过滤并返回 `source_kind`；KB 不出现在 continuity/recap/session 注入；async enrichment 不阻塞 ingest 且异常不影响写入。

---

### P3：效用反馈闭环（让系统越用越好）

**目标**：从“被动存储”转向“主动优化”——记录用户/任务对记忆的采纳/纠错/拒绝信号，按 memory/slot/source/pipeline 归聚效用，自适应调整阈值。

**具体改动**：

| 文件 | 攭动要点 |
|---|---|
| `src/memall/mcp/tools/retrieve.py` | `handle_record_interaction`（或增补）记录：`memory_id`、`task_id`、`interaction_type`（`click|dwell|impression|copy|skip`）、`dwell_ms`、`accepted/rejected/corrected/ignored` 信号。 |
| `src/memall/search/` | 持久化效用账本：按 `slot_key/source_pipeline/scope` 聚合 `accepted/rejected/corrected/ignored` 计数，计算 `utility_score`。 |
| `src/memall/core/thin_waist.py:capture/resolve` | 低 utility / 高 conflict rate 的 slot 触发 `REVIEW` 或阈值收紧；高 utility slot 放宽 resolve 阈值。 |
| `src/memall/config.py` | 新增配置：`quality.utility.tracking_enabled`、`utility.slot_weight_factor`、`utility.min_utility_for_auto_ingest`（默认保守）。 |
| `ADR/0028/...` | 声明效用闭环为 P3，默认**不**自动改写历史，仅供策略参考。 |

> **验收**：在测试里记录 `record_interaction` 信号；`get_config("quality.utility.tracking_enabled")` 为 True 时，`retrieve` 结果里的排序会受 utility 影响（高 utility 靠前）；关闭时无影响。

---

### P4：图谱/连续性/归档自进化（长期治理）

**目标**：定期/异步的维护任务——canonicalization、冲突复核、过期失效、低效用归档、主题/画像刷新、策略自适应。默认不静默物理删除关键证据。

**具体改动**：

| 文件 | 改动要点 |
|---|---|
| `src/memall/pipeline/` | `distill/reflect/ops` 等 pipeline 做为定期任务入口；`source_pipeline=lifecycle.distill`、`source_pipeline=reflect.aggregate`、`source_pipeline=ops.split` 已有，复用或新增 `source_pipeline=cognitive.loop.maintenance`。 |
| `src/memall/search/` | 可选的 `compact`/`archive` 命令，生成归档索引条目（append-only 锚点），旧证据保留可回读。 |
| `src/memall/mcp/tools/` | 新增 `handle_maintenance` 或在 `handle_retrieve` 里 `mode=audit` 时暴露 `conflicts/history/evidence` 以便人工审计。 |
| `ADR/0028/...` | 声明所有自动动作：有快照/回滚可选、可审计日志、默认保留原始条目。 |

> **验收**：定期维护任务跑完后，`metadata.resolve.status`、`memory_status` 已按规则更新；归档产物在 `ADR/archived_sessions/` 里，可在检索时 `include_archived=True` 显式展示；无静默删除关键记忆的行为。

---

## 3. 依赖与风险

| 风险 | 缓解 |
|---|---|
| `CaptureId` int 兼容性（~107 call sites） | 所有改动以增量形式在 `metadata`/`metadata.resolve` 里完成，不改 `int` 形状；`CaptureId(int)` 保持不变。 |
| `Metadata JSON` 向后兼容 | `_row_to_memory` 通过 `row.get("metadata", "{}")` 读取；老库 `metadata` 为 `{}` 时新字段默认空。 |
| 检索/MCP 消费者意外报错 | 新增字段在 JSON 序列化前先 `get(meta_key, default)`；所有模型字段 `Optional` 或有默认值。 |
| resolve 引擎已有 bug / 临时失效 | P0 先在已验证路径上跑（`_find_active_slot_memory`、已有 test）；新增字段默认回 `NOOP/MERGE` 时序，非破坏性。 |
| 测试基线被坏测试拖垮 | 先修 `test_resolve_can_be_disabled`；其他测试不动。 |
| 环境变量/Memory 老库迁移 | 新库默认有 `valid_from/invalid_at`；老库通过 `ALTER TABLE` 或迁移脚本补列（离线进行，不影响在线运行）。 |

---

## 4. 实施路线图（按依赖顺序）

| 阶段 | 主要交付 | 预计工时 |
|---|---|---|
| **P0**（最高优先级） | 修复坏测试；resolve/validity 字段增量暴露到 retrieve/unified_search/metadata；确保老库迁移安全 | 1-2 天 |
| **P1** | Continuity Pack 接口（capture/continuity_pack/retrieve）；测试跨会话有效状态注入与检索 | 2-3 天 |
| **P2** | KB 入库契约（category=knowledge、doc_hash/doc_id）；MCP 检索过滤；MCP 写入 batch capture 兼容 | 2-3 天 |
| **P3** | 效用反馈埋点（record_interaction + utility 聚合）；配置开关默认 off / 保守 | 2-3 天 |
| **P4** | 维护任务/归档/审计模式（可选，在 P0-P3 稳定后展开） | 1-2 周（可延后） |

---

## 5. 开始执行（P0 立即启动）

下面开始 **P0**：修复坏测试 + expose resolve/validity 字段。后续轮次再做 P1、P2...

**（若确认继续，我会在下一轮直接：1) 修复坏测试；2) 在 thin_waist._row_to_memory 里增量读取 metadata.slot_key/metadata.resolve；3) 在 retrieve/unified_search 返回的 JSON 里带上这些字段；4) 重跑测试验收。）**

---

*文档结束。下一步：执行 P0 任务。*
---

## 6. 本地实施状态（未提交，验收后再提交）

> 更新时间：2026-11-09（本地工作区）；执行约束：**先本地改造+验收，合格后再提交**。

### 已落地（本地验证）

- **P2-1/2 检索 provenance 与 KB 标注**：`unified_search/enhanced_search/vector_search/hybrid_search` 支持 `source_pipeline` 过滤；`vector_search`/`hybrid_search`/MCP retrieve 返回 `source_kind`、`metadata_source_pipeline`（`sqlite3.Row` 安全）。`retrieve` 的 `_cognitive_fields` 增量暴露 `source_pipeline`、`source_kind`（兼容旧字段）。
- **P2-2 continuity/recap/session KB 排除**：KB 证据（`document.import`/`kb_ingest`/knowledge+document provenance）不进入 `session_start` 注入、`build_continuity_pack`、`handle_recap` L4/L5；continuity pack 显式返回 `kb_excluded`、`excluded_source_pipelines`、`why_selected`。
- **P2-3 可扩展策略模块（持续收敛）**：新增 `src/memall/core/kb_policy.py`（`KB_SOURCE_PIPELINES`、`source_kind_from_pipeline`、`kb_sql_exclusion_clauses` 等），已接入 `thin_waist_search`、`mcp/tools/retrieve`（含 `handle_recap` KB 排除）、`pipeline/session`（`session_start` 注入过滤、L3 workflow 查询、`build_continuity_pack` why_selected/exclusion）；`thin_waist.py` 当前主要为显式 `source_pipeline` 正向过滤（非 KB 混入排除），暂不强改。
- **P2-4 异步 enrichment 骨架（增强）**：新增 `src/memall/pipeline/kb_enrich.py`（config-gated `kb.enrich_async`，默认 true；daemon thread best-effort；异常吞掉）。增强观测/幂等：`kb_enrich_stats` 返回 `fired/ok/failed/skipped/deduped/last_error/seen_keys`；基于 `doc_id+chunk_hash+doc_version+chunk_index` 的进程内幂等去重（有界 seen set）。`mcp/tools/__init__` best-effort 注册；`memory_write._fire_kb_enrich` 仅在 `handle_kb_ingest` 成功 capture 后触发。后续替换点：持久化队列/后台 worker、重试上限、跨进程幂等。
- **P1 continuity_pack 契约对齐（增量兼容）**：`build_continuity_pack`/`handle_continuity_pack` 返回 ADR 契约字段 `active_state`、`conflicts`、`pending_decisions`、`recent_supersedes`、`why_selected`，并保留 `active_memories`、`key_decisions`、`open_reviews`、`project_state` 等兼容别名。
- **回归修复**：移除 `handle_batch_capture` 中误用的 KB enrich 调用（避免 `ingest_metadata` 未定义）。
- **测试**：补充/更新 `tests/test_kb_ingest_p2_expanded.py`、`tests/test_kb_ingest_p2.py`（含 continuity contract、MCP source_kind 一致性、enrichment worker、batch_capture 回归、KB 排除契约）。

### 本地验收（`.venv` + `PYTHONPATH=.:src`）

```bash
cd /vol1/@appshare/fn-deepseek-harness/MemALL
PYTHONPATH=.:src ./.venv/bin/python -m pytest \
  tests/test_kb_ingest_p2.py tests/test_kb_ingest_p2_expanded.py \
  tests/test_capture_resolve_engine.py \
  tests/test_session.py tests/test_session_sweep.py -q
# 结果：43 passed (expanded)（24 + 15），2026-11-10 本地（P2-3 fallback 对齐 + P1 边界/P3/MCP 契约补充）
```

### 待继续（验收后再提交）

- P2-3：将 `thin_waist.py`/`retrieve.py` recap+continuity 等剩余散落 `json_extract` KB 排除统一到 `kb_policy`；补 provenance 边界契约测试。
- P2-4：将占位 enrich 接到真实 pipeline/后台 worker（幂等 key `doc_id+chunk_hash+doc_version`、重试上限、指标暴露、重处理策略）。
- P1：补 `mode=continuity`/continuity 注入路径的端到端契约测试与 validity/resolve 过滤边界用例。
- P3：效用反馈闭环最小实现（默认 off、保守），`record_interaction` → utility 聚合 → 排序/resolve 策略。
- 更广回归：`*retrieve*`/`*continuity*`/`*session*`/`*batch*`/`*hybrid*`/`*vector*` 子集；确认工作区原有未提交改动不被误纳入提交。


### 2026-11-10 继续推进（P1/P2-4/P3 最小契约，本地未提交）
- **P1**：`handle_continuity_pack` MCP 路径委托 `session.build_continuity_pack` 已 smoke；补充 `test_p1_continuity_pack_mcp_handler_contract`（契约键 + KB 排除标签 + KB 证据不进入包体）。
- **P2-4**：`kb_enrich` 增强为幂等 key（`doc_id+chunk_hash+doc_version+chunk_index`）+ 有界 seen set + 统计字段（`fired/ok/failed/skipped/deduped/last_error/seen_keys`）；仍为进程内非持久化骨架，未阻塞 capture。后续必须替换为可配置持久化 enqueue/后台 worker（跨进程幂等、重试上限、可重处理）。
- **P3（最小，保守）**：确认 `record_interaction` 已存在（`memall_write` 路由 + `search.enhancements.record_interaction` + MCP schema/validator）；补充 `test_p3_record_interaction_mcp_best_effort`，验证 best-effort/参数校验，不改变排序默认行为。

### P0–P2 技术文档/清单/债务（已补齐）
- 总览与索引：[docs/p0-p2/README.md](../docs/p0-p2/README.md)
- 改造技术文档：[docs/p0-p2/TRANSFORMATION_DOC.md](../docs/p0-p2/TRANSFORMATION_DOC.md)
- P0/P1/P2 分项：[docs/p0-p2/p0-resolve-exposure.md](../docs/p0-p2/p0-resolve-exposure.md)、[docs/p0-p2/p1-continuity-contract.md](../docs/p0-p2/p1-continuity-contract.md)、[docs/p0-p2/p2-kb-boundary-hardening.md](../docs/p0-p2/p2-kb-boundary-hardening.md)
- 提交清单：[docs/p0-p2/COMMIT_CHECKLIST.md](../docs/p0-p2/COMMIT_CHECKLIST.md)
- 技术债务：[docs/p0-p2/TECH_DEBT_LEDGER.md](../docs/p0-p2/TECH_DEBT_LEDGER.md)


### 2026-11-10 最新推进（模型切换后继续全自动，本地未提交）

> 以上"待继续/历史验收数字"为早期基线；此处为工作区最新状态，保留上文以保持可追溯性。

**P1（边界契约补齐）**：`tests/test_kb_ingest_p2_expanded.py` 新增 4 个测试，覆盖 temporal validity（`valid_from/invalid_at`）、`include_conflicts`×有效性、`recent_supersedes`、MCP `session_start` KB 排除契约。`build_continuity_pack` 的 `why_selected.filters` 含 `temporal_validity,kb_excluded`。（4 passed）

**P2-4（持久化 worker 加固，真实缺陷修复）**：`kb_enrich._claim_job` 原为 SELECT-then-UPDATE，无跨进程条件保护 → 两进程可重复 claim 同一 queued 行；现改为 CAS 条件 UPDATE（`WHERE id=? AND status='queued' AND next_retry/claimed_at`），dedup 分支同样条件化，竞争失败递归重试。新增 3 个测试（单赢家、running 不可重 claim、budget 有界钳制）。`autostart` 默认仍 False，gateway best-effort hook 不变。（`tests/test_kb_enrich_worker_p2.py` 16 passed）

**P3（默认关闭的效用重排，真实缺陷修复 + 端到端传播）**：
- 修复 `enhanced_search` 增强路径引用未定义 `arguments` 的 NameError；新增显式 `utility_rerank` 参数（此前 MCP `handle_retrieve` 会把该 kwarg 漏传给 `retrieve()`）。
- `unified_search` 全路径（fts5/direct/vector/hybrid）解析 `utility_rerank`、纳入 `wants_enhancements`、转发到 `enhanced_search` 与 `_apply_enhancements_inplace`；内部 dict 的 additive `utility_rerank_signal/score` 透传到 Memory 对象与 JSON payload。
- 保持默认 off：`search.utility_rerank.enabled=False`；保守门控（>=min_interactions 证据，永不丢结果）。
- 新文件 `tests/test_utility_rerank_p3.py`：6 个 E2E 契约（6 passed）。

**最新验收**：扩展 scoped regression（9 文件）-> **70 passed in 65.59s (exit 0)**。详见 `docs/p0-p2/PRECOMMIT_SCOPED_REPORT_2026-11-10.md`。

**仍待（P4 / P3 灰度前）**：canonicalization/conflict review/expiry/low-utility 归档/主题画像/策略自适应/审计；P3 冷启动回退、跨 agent 隔离、遥测/基线、灰度与 kill-switch 标准；真实多进程并发集成测试。以上均登记于 `docs/p0-p2/TECH_DEBT_LEDGER.md`。

### 从检查点继续推进（2026-11-10，基于 `8aa68cc`，本地未提交）

> 检查点的「下一步建议」主干已在 `8aa68cc` 完成；本轮补齐更深一层，**不改任何默认**。

- **P1**：`resolve_status`×temporal validity 的 `pending_decisions` 边界、`include_conflicts=False` 抑制、`session_start auto_inject` 逐桶 KB 排除（`tests/test_kb_ingest_p2_expanded.py` +3）。
- **P2-4**：worker 协作式 stop（`Event`+有界 join+幂等）、`kb_enrich_worker_status()`/`kb_enrich_stats.worker` 观测、MCP `kb_enrich_stop`、validator 登记 3 个 kb action（修既有 unknown 缺口）、**真多进程 spawn CAS 单赢家**、reprocess 幂等（`tests/test_kb_enrich_worker_p2.py` 20 passed）。部署文档 `docs/p0-p2/p2-4-worker-deployment.md`。
- **P3**：冷启动/低证据回退、跨 agent 隔离、权重钳制/永不丢项（`tests/test_utility_rerank_p3.py` 10 passed）；度量基线/灰度/Kill-Switch 文档 `docs/p0-p2/p3-utility-rerank-rollout.md`；`search.utility_rerank.enabled` 仍 False。
- **P4**：设计前置 `docs/p0-p2/p4-deferred-design.md`（8 项，仅登记，保持 deferred）。
- **验收**：scoped 9 文件 **81 passed**；详见 `docs/p0-p2/PRECOMMIT_SCOPED_REPORT_2026-11-10.md`。
