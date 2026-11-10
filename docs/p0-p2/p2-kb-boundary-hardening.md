# P2：Knowledge Pack 与 KB 入库（知识库事实受控入库）

## 目标
1. KB 证据（`source_pipeline IN ('document.import','kb_ingest')` 或 `category='knowledge' + document.* provenance）受控入库，不泄漏到 user-state 表面（continuity/recap/session injection）。
2. 新增 `kb_policy.py` 为权威总线：`KB_SOURCE_PIPELINES`、`is_kb_source_pipeline`、`source_kind_from_pipeline`、`kb_sql_exclusion_clauses()`。
3. 统一 `thin_waist`/`retrieve`/`session`/`search` 中剩余的硬编码 `NOT IN ('document.import','kb_ingest')` 为 `kb_policy` 等价子句。
4. `vector_search`/`hybrid_search` 支持 `source_pipeline` 参数 + 返回 `source_kind` / `metadata_source_pipeline`（`sqlite3.Row` 安全）。
5. `kb_enrich` 非阻塞增强 hook（config-gated `kb.enrich_async`，默认 true；daemon thread best-effort；幂等 key + 有界 seen set + 统计）。
6. `handle_kb_ingest` after-capture fire enrich hook；`handle_batch_capture` 无 stray `_fire_kb_enrich`。

## 已完成改动（均已纳入 commit `92863b6` / 本地验收 46 passed (含边界审计，2026-11-10)）

### 1. 新增 `src/memall/core/kb_policy.py`
- `KB_SOURCE_PIPELINES = ("document.import", "kb_ingest")`——已知 KB source pipeline 集合。
- `is_kb_source_pipeline(sp)` → `bool`：判断 pipeline 是否为 KB 来源。
- `source_kind_from_pipeline(sp, meta)` → `"document_import" | "kb_ingest" | "user_state"`：归一化 source_pipeline → source_kind（`"document.import"`→`"document_import"`，`"kb_ingest"`→`"kb_ingest"`，空/heuristic `kb_kind/doc_id`→`"user_state"`，其它 `.`→`_`）。
- `kb_sql_exclusion_clauses(column="metadata", table_alias="")` → `list[str]`：权威 SQL 片段（参数-free）：
  1. `COALESCE(json_extract(column,'$.source_pipeline'),'') NOT IN ('document.import','kb_ingest')`
  2. `NOT (COALESCE(category,'')='knowledge' AND COALESCE(json_extract(column,'$.source_pipeline'),'') LIKE 'document.%')`
  可组合使用 `AND` 拼接，精确排除 KB 证据、保留对话/user-state。

### 2. `src/memall/pipeline/session.py`
- L3 工作流查询 `except` fallback 改为 `kb_sql_exclusion_clauses()` 生成（已与 retrieve/recap 对齐）。
- L4/L5 injection KB guard：使用 `_kb_exclude()` helper 过滤 `source_pipeline in KB_SOURCE_PIPELINES` 且 `category='knowledge' AND source_pipeline LIKE 'document.%'`。
- `build_continuity_pack` `except` fallback 改为 `kb_sql_exclusion_clauses()` 等价生成。
- L3/L4/L5 KB 排除已在 `handle_recap`（retrieve.py:209）与 `session_start` 中统一。

### 3. `src/memall/mcp/tools/retrieve.py`
- `handle_recap`：导入 `kb_sql_exclusion_clauses()`，在 `except Exception` 分支使用完整子句：
  `"COALESCE(json_extract(metadata,'$.source_pipeline'),'') NOT IN ('document.import','kb_ingest') AND NOT (category='knowledge' AND COALESCE(json_extract(metadata,'$.source_pipeline'),'') LIKE 'document.%')"`。
- `_cognitive_fields`：新增 `source_pipeline`、`source_kind`（通过 `source_kind_from_pipeline` 计算），兼容旧字段。
- `handle_continuity_pack`：已委托 `session.build_continuity_pack`。

### 4. `src/memall/core/thin_waist.py` / `thin_waist_search.py`
- `_row_to_memory` / `_cognitive_item_from_meta`：增量暴露 `slot_key`、`resolve` / `valid_from`/`invalid_at`/`memory_status` 到 metadata 层（P0）。
- `_row_meta_dict` / `_slot_key_for_memory` / `_find_active_slot_memory` / `_resolve_decision` / `_apply_resolve_metadata`：resolve 引擎完整路径（slot_key 推断、决策规则、元数据注入）。
- `vector_search` / `hybrid_search`：`source_pipeline` 参数支持，meta 列 `json_extract` 过滤，返回 `metadata_source_pipeline`。

### 5. `src/memall/pipeline/kb_enrich.py`（新增，P2-4）
- `kb_enrich_handler(meta)`：进程内 enrichment 处理函数。
- `kb_enrich_stats`：`fired/ok/failed/skipped/deduped/last_error/seen_keys`——幂等 key `doc_id+chunk_hash+doc_version+chunk_index`，有界 seen set (`_MAX_SEEN=4096`)。
- `set_kb_enrich_hook(fn)` / `_fire_kb_enrich(meta)`：daemon thread，failure swallowed，仅在 `handle_kb_ingest` 成功 capture 后触发。
- `register_default_kb_enrich_hook()`：`mcp/tools/__init__` best-effort 导入（try/except），防止 import fail 拖垮主系统。

### 6. `src/memall/mcp/tools/memory_write.py`
- KB ingest hook wiring：`handle_kb_ingest` 在 successful capture 后 fire `_fire_kb_enrich`；`handle_batch_capture` 无 stray fire。

### 7. `src/memall/config.py`
- `"kb": {"enrich_async": True}`——P2-4 config gate（保守默认开，可 per deployment 关闭）。

## 验收
```bash
cd /vol1/@appshare/fn-deepseek-harness/MemALL
PYTHONPATH=.:src ./.venv/bin/python -m pytest tests/test_kb_ingest_p2.py tests/test_kb_ingest_p2_expanded.py -q
# 46 passed (含边界审计，2026-11-10)（24 + 15）

# KB policy 权威检查
python3 -c "
from memall.core.kb_policy import KB_SOURCE_PIPELINES, kb_sql_exclusion_clauses, source_kind_from_pipeline
assert KB_SOURCE_PIPELINES == ('document.import', 'kb_ingest')
clauses = kb_sql_exclusion_clauses()
assert len(clauses) == 2
assert clauses[0].__contains__(\"NOT IN ('document.import','kb_ingest')\")
print('KB policy authority: OK')
"
# KB source kind normalization: OK
```


## 2026-11-10 P2-4 基础推进（未提交）
- `handle_kb_ingest` 成功 chunk capture 后调用 `_fire_kb_enrich`（best-effort, non-blocking），携带 `doc_id/chunk_hash/doc_version/chunk_index/source_pipeline/memory_id/project`。
- `core/db.py` 新增 `pipeline_jobs` 持久化表 DDL（P2-4 durable queue/worker 基础）：`kind/doc_id/chunk_hash/doc_version/chunk_index/status/attempts/max_attempts/next_retry_at/result/error_msg/handled_by/completed_at` + 索引。
- `memall_system` 新增 `kb_enrich_stats` action（additive read）：返回 `kb_enrich_stats()`，便于可观测/排障；schema enum 已扩展。
- 待完成（按 TECH_DEBT_LEDGER）：`kb_enrich` 写入/claim `pipeline_jobs`、独立 worker、跨进程幂等、重试/backoff、reprocess、队列深度 metrics、`kb.enrich_worker.backend` 配置。

## 本轮补充（2026-11-10，未提交）
- `kb_enrich._claim_job` 改为 CAS 条件 UPDATE，消除跨进程重复 claim 竞争；dedup 分支同样条件化。
- 新增测试：CAS 单赢家、running 行不可重 claim、`process_budget` 有界钳制（[1,500]，`trigger_mode=bounded_once`）。
- `tests/test_kb_enrich_worker_p2.py` -> 16 passed。
