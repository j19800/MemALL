# 独立工作线：Capture 质量漏斗路由 + Readability 归一化

> **文档状态**：现行（2026-11-10）。**与 ADR 0028 正交**，单独提交、单独验收。
> 关联：`tests/test_capture_quality_workline.py`、`tests/test_capture_quality_contract.py`；基线依赖已在仓库（`CaptureId`、`_is_human_readable_content`、`_normalize_content_for_human_readability`、`capture()` 的 quality/readability 门禁）。

## 1. 这条线是什么

把「管线蒸馏产物」和「同步/拆分写入」统一收敛到 `capture()` 这个**单一写入入口**，让它们也过质量/可读性门禁；任何异常都回退到原手动 `INSERT`，保证不阻塞、不丢数据。配套把 `capture` 的质量契约通过 MCP handler 暴露出来。

| 文件 | 改动 |
|---|---|
| `pipeline/auto_dream.py` | L9 模式 / L10 知识：改用 `capture(MemoryInput(...))`，metadata 带 `l9_source`/`l10_source`/`source_pipeline`/`ingest_mode`；异常回退原 INSERT |
| `pipeline/cross_agent.py` | L10 整合 / L11 领域：同上，保留 `derived_from` 边（用 `CaptureId` 当 source_id） |
| `pipeline/lifecycle.py` | `_distill_clusters` 的 L9 提炼：同上，`layer_source` 保留 |
| `pipeline/llm_enhance.py` | L9 摘要：同上，`refines` 边保留 |
| `pipeline/observe.py` | L6 观察 / 周月反思 / growth_log：同上，fallback 非致命 |
| `pipeline/reflect.py` | `_aggregate_l6` L6 聚合：同上 |
| `gateway_sync.py` | `_import_memories`：同步内容做可读性归一化（**不拒绝**），metadata 保留 `readability` 保真标记、补 `ingest_mode=sync`、重算 hash 后再去重 |
| `mcp/tools/capture.py` | handler 从 `CaptureId.to_dict()` 暴露 additive `quality`/`readability`/`ingest_mode`/`source_pipeline`/`decision_hint`；`ensure_ascii=False` |
| `pipeline/ops.py` | `split_memory` 分段做可读性归一化，metadata 补 `source_pipeline=ops.split`/`ingest_mode=auto` |

## 2. 本次「处理」时修掉的 2 个真实 bug

### 2.1 `MemoryInput` 没有 `weight` 字段（静默回退）
`auto_dream.py` / `cross_agent.py` 的 capture 分支传了 `weight=float(len(members))`，但 `MemoryInput` 是 dataclass、无 `weight` 字段 → 构造即抛 `TypeError`，被 `try/except` 吞掉后**静默回退到 legacy INSERT**，整条「走质量漏斗」的意图落空，且 fallback 的 metadata 不带 `ingest_mode`/`source_pipeline`。
- 修复：去掉 3 处 `weight=`，把数值记进 `metadata["member_count"]`（信息不丢，契约合法）。
- 守卫：`test_memoryinput_has_no_weight_kwarg` + `test_pipeline_capture_paths_do_not_pass_weight`（静态扫描）。

### 2.2 `split_memory` 的 `supersedes=NULL` 触发 NOT NULL（基线既有）
`split_memory` 的 INSERT 显式给 `supersedes` 传 `None`，而该列是 `NOT NULL DEFAULT '[]'` → 整条 split 路在当前 HEAD 上直接 `IntegrityError` 不可用（本工作线 diff 未碰此行，属基线遗留）。
- 修复：改为字面量 `"[]"`（split 出的新记忆本就不继承原记忆的 supersedes 关系）。
- 守卫：`test_ops_split_segments_normalized_and_tagged`。

## 3. 验收（`tests/test_capture_quality_workline.py`，6 passed）

1. `test_memoryinput_has_no_weight_kwarg` — 契约守卫
2. `test_pipeline_capture_paths_do_not_pass_weight` — 静态回归
3. `test_auto_dream_distill_uses_capture_and_persists_metadata` — 证明 L10 真走 capture（metadata 含 `ingest_mode=auto`/`source_pipeline=auto_dream.distill`/`member_count`），非静默回退
4. `test_capture_handler_exposes_quality_contract` — MCP `{id,status}` + additive 字段
5. `test_gateway_sync_normalizes_unreadable_content_preserves_fidelity` — 归一化不拒绝 + `readability` 保真
6. `test_ops_split_segments_normalized_and_tagged` — split 可用 + metadata 标记

`tests/test_capture_quality_contract.py`（3 passed）为基线既有配套，本轮一并纳入提交。

## 4. 与 ADR 0028 的边界（重要）

- ADR 0028 主线 = KB evidence / continuity / utility rerank / worker；**本线完全不碰**这些模块（`thin_waist.py`、`kb_enrich.py`、`session.py` 等均未改）。
- 本线依赖的 `CaptureId`/quality/readability 基础设施**已在基线**（`92863b6` 及更早），不是本轮新增。
- 两个提交分开：ADR0028 见 `docs/p0-p2/*`；本线见本文档。这样任何一条线都可独立回滚/审查。

## 5. 不变量 / 已知限制

- **不阻塞**：所有 capture 分支都包在 `try/except` 内，失败回退原 INSERT。
- **不丢数据**：fallback 保留原有去重与列。
- **provenance**：新路径的 metadata 统一带 `ingest_mode`/`source_pipeline`；fallback 路径部分也补了（observe/reflect/lifecycle/ops），但 **auto_dream/cross_agent/llm_enhance 的 fallback 未补 source_pipeline**（保持与基线一致，避免扩大 diff）。
- **分类漂移风险**：`capture()` 会做 project 推断与分类（可能改写 level/category）。本线通过显式传 `level`/`category` + `_capture_normalize_and_validate` 的 pinned 机制缓解，但未做全管线端到端断言——若后续发现蒸馏产物 level 被改，需补 pinned 契约测试。
- `thin_waist.py.bak_phase1` 备份文件**不纳入**任何提交（待决策删除/归档）。
