# P1：跨会话 Continuity Pack（最直接的感知价值）

## 目标
下次会话启动或任务开始时，系统能生成并暴露“有效的跨会话上下文”：已确认的偏好/决策/未决项/冲突提醒，而不是空白或整段历史。保持向后兼容：MCP `continuity_pack` 返回的字段在老消费者看不见时不报错。

## 已完成改动（均已纳入 commit `92863b6` / 本地验收 39 passed）

### 1. `src/memall/pipeline/session.py`
- `session_start(agent_name, auto_inject=True)`：在 L3/L4/L5 注入 KB 证据排除守卫：
  - L3 `except` fallback 改为 `kb_sql_exclusion_clauses()` 等价子句（`COALESCE(json_extract(metadata,'$.source_pipeline'),'') NOT IN ('document.import','kb_ingest') AND NOT (category='knowledge' AND ... LIKE 'document.%')`）。
  - L4/L5 injection `l4_recent_global` / `l5_active_global` 新增 KB `source_pipeline` guard（`KB_SOURCE_PIPELINES` set + `category='knowledge' AND source_pipeline LIKE 'document.%'` 双重检查）。
- `build_continuity_pack(agent_name, project, limit, include_conflicts)`：在 `except Exception` fallback 改为从 `kb_sql_exclusion_clauses()` 生成，使其与 `kb_policy` 权威定义一致。保留 `_excluded_sps` 后备。
- KB 证据（`source_pipeline IN ('document.import','kb_ingest')` 或 `category='knowledge' + document.* provenance`) 不进入 continuity/session injection/recap。

### 2. `src/memall/mcp/tools/retrieve.py`
- `handle_continuity_pack`：委托 `session.build_continuity_pack`；返回 ADR 契约字段：
  - `active_state`、`conflicts`、`pending_decisions`、`recent_supersedes`、`why_selected`（含 `kb_excluded`、`excluded_source_pipelines`、`source_kind`）。
  - 兼容别名：`active_memories`、`key_decisions`、`open_reviews`、`project_state`、`viewer/agent_name/project/build_ts/total_*`。
- `handle_recap`：KB exclusion guard 完整子句（`kb_sql_exclusion_clauses()`），返回 `excluded_source_pipelines=list(KB_SOURCE_PIPELINES)`、`kb_excluded=True`。

### 3. 新增测试（`tests/test_kb_ingest_p2_expanded.py`）
- `test_p1_continuity_pack_contract_and_kb_exclusion`：验证 continuity_pack 契约键 + `kb_excluded=True` + KB 证据不进入包体。
- `test_p1_continuity_pack_mcp_handler_contract`：MCP 路由→`retrieve.handle_continuity_pack`→`session.build_continuity_pack` 端到端。
- `test_p1_continuity_pack_include_conflicts_boundary`：`include_conflicts=False/True` 均保持 `kb_excluded=True`，KB 证据不进入包体。

## 验收
```bash
cd /vol1/@appshare/fn-deepseek-harness/MemALL
PYTHONPATH=.:src ./.venv/bin/python -m pytest tests/test_kb_ingest_p2_expanded.py -q --tb=short
# 15 passed（含 P1 边界 + MCP 契约）
```


## 2026-11-10 边界审计补充（未提交）
- `session_start(auto_inject=True)`：对整个 injection 递归防御过滤 KB（含 `metadata_source_pipeline`/parsed metadata），`injection.kb_excluded`/`excluded_source_pipelines` 与顶层 `result` 同步标记；L3/L4/L5 SQL 仍保留 KB 排除。
- `auto_inject`（`federation_tools`）：L1/L3/L4/L5/L7/L10/L11 路径应用 KB 排除（SQL + metadata/category 防御），返回 additive `kb_excluded`/`excluded_source_pipelines`。
- `build_continuity_pack`：temporal validity SQL 过滤（`valid_from <= now` when present, `invalid_at > now` when present）；`excluded_source_pipelines=list(KB_SOURCE_PIPELINES)`；`why_selected.filters` 含 `temporal_validity`。
- 契约测试：portable MCP registry/handler contract；whole-injection KB exclusion；`auto_inject` KB contract；validity/resolve 边界断言增强。

## 本轮补充（2026-11-10，未提交）
新增 P1 边界契约测试（均在 `tests/test_kb_ingest_p2_expanded.py`，4 passed）：
- `test_p1_continuity_pack_validity_filter_excludes_expired`：`valid_from/invalid_at` 窗外记录不进入 active/conflict/pending。
- `test_p1_continuity_pack_include_conflicts_respects_validity`：`include_conflicts=True` 仅暴露时间有效的 REVIEW/CONFLICT；KB 证据始终排除。
- `test_p1_continuity_pack_recent_supersedes_boundary`：supersession 链与 KB 排除边界。
- `test_p1_session_start_mcp_kb_exclusion_contract`：MCP `handle_session_start` 返回 `kb_excluded` 且注入面不含 KB 证据。
`build_continuity_pack` 的 `why_selected.filters` 含 `temporal_validity,kb_excluded` 等。

## 从检查点继续（2026-11-10，未提交）
新增 3 个剩余边界组合测试：`resolve_status`（REVIEW/PENDING/CONFLICT）× temporal validity 的 `pending_decisions` 门控；`include_conflicts=False` 抑制 CONFLICT/REVIEW `memory_status` 行；`session_start auto_inject` 逐注入桶 KB 排除快照。注意 `conflicts` 桶是紧凑投影（`id/subject/memory_status/slot_key/created_at`），不含 `content`，断言需按其契约。
