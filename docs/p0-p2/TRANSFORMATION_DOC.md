# 改造技术文档（ADR0028 P0-P2 状态概览）

## 版本
基于 commit `92863b6`；2026-11-10 边界审计/P2-4 基础后本地验收 **46 passed**（`test_kb_ingest_p2.py test_kb_ingest_p2_expanded.py test_capture_resolve_engine.py test_session.py test_session_sweep.py`）。

## 已完成 P0-P2 改造内容概览

### P0：稳住测试基线 + 增量暴露合同
| 目标 | 状态 | 关键文件 |
|------|------|----------|
| 修复 `test_capture_resolve_engine.py` 坏导入 | ✅ passed | `tests/test_capture_resolve_engine.py` |
| resolve/validity 字段增量暴露到 retrieve/unified_search/metadata | ✅ exposed | `src/memall/mcp/tools/retrieve.py`、`src/memall/core/thin_waist.py`、`src/memall/core/thin_waist_search.py` |
| `_cognitive_fields` / `_row_meta_dict` 新增助手 | ✅ new | `src/memall/core/thin_waist.py` |
| `source_pipeline` 支持在 vector/hybrid search | ✅ new | `src/memall/core/thin_waist_search.py` |

### P1：跨会话 Continuity Pack
| 目标 | 状态 | 关键文件 |
|------|------|----------|
| `session_start` KB 证据排除（L3/L4/L5） | ✅ aligned | `src/memall/pipeline/session.py` |
| `build_continuity_pack` 契约字段 + `kb_excluded` | ✅ returned | `src/memall/pipeline/session.py`、`src/memall/mcp/tools/retrieve.py` |
| MCP `handle_continuity_pack` 端到端 smoke | ✅ smoke | `src/memall/mcp/tools/retrieve.py` |
| `handle_recap` KB exclusion guard | ✅ guard | `src/memall/mcp/tools/retrieve.py` |
| continuity_pack KB 证据不进入包体 | ✅ assert | `tests/test_kb_ingest_p2_expanded.py` |

### P2：Knowledge Pack 与 KB 入库
| 目标 | 状态 | 关键文件 |
|------|------|----------|
| 新增 `kb_policy.py` 为权威 KB source-pipeline/provenance 总线 | ✅ new | `src/memall/core/kb_policy.py` |
| `kb_sql_exclusion_clauses()` 统一所有 KB 排除 SQL | ✅ unified | `src/memall/core/kb_policy.py`（调用端：session/retrieve/recap） |
| `source_kind_from_pipeline()` 归一化 | ✅ new | `src/memall/core/kb_policy.py` |
| `vector_search`/`hybrid_search` `source_pipeline` 参数 | ✅ new | `src/memall/core/thin_waist_search.py` |
| `kb_enrich` 非阻塞 hook + 统计 | ✅ new | `src/memall/pipeline/kb_enrich.py` |
| `handle_kb_ingest` after-capture fire enrich | ✅ wired | `src/memall/mcp/tools/memory_write.py` |
| `handle_batch_capture` 无 stray `_fire_kb_enrich` | ✅ clean | `src/memall/mcp/tools/memory_write.py` |
| config `"kb": {"enrich_async": True}` | ✅ added | `src/memall/config.py` |

### MCP 契约扩展（additive/back-compat）
| 字段 | 所在 | 说明 |
|------|------|------|
| `metadata.slot_key` | retrieve/ thin_waist | P0 新增，老库默认空串 |
| `metadata.resolve.status/reason/confidence` | retrieve/ thin_waist | P0 新增，老库默认 `{}` / `null` |
| `metadata.valid_from` / `invalid_at` / `memory_status` | retrieve/ thin_waist | P0 新增，老库默认 `""` |
| `active_state` / `conflicts` / `pending_decisions` / `recent_supersedes` / `why_selected` | continuity_pack | P1 新增，含 `kb_excluded` / `excluded_source_pipelines` / `source_kind` |
| `source_pipeline` / `source_kind` | retrieve/_cognitive / search | P2 新增，通过 `kb_policy` 计算/过滤 |

## 本地验收记录（2026-11-10）
```bash
# P0：resolve engine + incremental exposure
cd /vol1/@appshare/fn-deepseek-harness/MemALL
PYTHONPATH=.:src ./.venv/bin/python -m pytest tests/test_capture_resolve_engine.py -q
# 4 passed

# P1 + P2：KB ingest contracts + MCP + enrichment hooks + KB policy
cd /vol1/@appshare/fn-deepseek-harness/MemALL
PYTHONPATH=.:src ./.venv/bin/python -m pytest tests/test_kb_ingest_p2.py tests/test_kb_ingest_p2_expanded.py -q
# 43 passed (expanded)
```
## 后续路线图备注（P2-4 / P3 / P1 边界）

### P2-4（本次提交之外，持续进行）
- [ ] 将 `kb_enrich.py` daemon thread 替换为持久化 enqueue/background worker（跨进程幂等 key + 重试上限 + 可观测 metrics / MCP 暴露）。
- [ ] `config.yaml` / `config.py` 中补充 `kb.enrich_worker.backend` 入口（`sqlite+queue|redis+queue|noqueue`）。
- [ ] P2-3 检查剩余硬编码 KB 字面量（`auto_dream.py`/`cross_agent.py`/`lifecycle.py`/`llm_enhance.py`）。

### P3（最小，保守，默认 off）
- [ ] 确认 `record_interaction` best-effort MCP 路由已存在（已完成）。
- [ ] `test_p3_record_interaction_mcp_best_effort` 通过（已完成）。
- [ ] utility 聚合/排序策略切换的配置 + tests（默认 off，不改变 ranking defaults，待显式 config + 测试后展开）。

### P1 边界测试（P1 续篇，本次提交之外）
- [ ] `test_p1_continuity_pack_validity_filter`（valid_from/invalid_at 过滤边界）。
- [ ] `test_p1_continuity_pack_resolve_filter`（CONFLICT/REVIEW 过滤边界）。
- [ ] `test_p1_session_start_kb_exclusion`（session_start injected memories KB 排除验证）。
- [ ] `test_p1_continuity_pack_include_conflicts_kb_still_excluded`（include_conflicts=True 仍 kb_excluded=True）。

## 向后兼容声明
- **所有新增字段均为 additive**：老代码通过 `.get("key", default)` 读取，无字段时使用默认值，不报错。
- **MCP 响应形状不变**：老消费者看不见新字段时业务逻辑不受影响。
- **KB 是 evidence，never user-state**：`session_start`/`continuity_pack`/`recap` 均已过滤 KB 证据，不再 inject 到 user-state 表面。
- **config gate**：`kb.enrich_async` 默认 true，可 per deployment 关闭；`kb.enrich_worker.backend` 可配持久化 worker backend。

## 2026-11-10 边界审计与 P2-4 基础（未提交工作区）
- **P1/P2 边界硬化**：`federation_tools.auto_inject` 增加 KB SQL/category 排除并返回 additive `kb_excluded`/`excluded_source_pipelines`；`session_start(auto_inject=True)` 对整个 injection 递归做 KB 防御过滤（`source_pipeline`/`metadata_source_pipeline`/parsed `metadata.source_pipeline`/`category=knowledge`+document pipeline），并在结果中标记 `kb_excluded`。
- **Continuity**：`build_continuity_pack` 增加 temporal validity SQL 过滤（`valid_from <= now_ts` when present, `invalid_at > now_ts` when present），`excluded_source_pipelines` 来自 `KB_SOURCE_PIPELINES`；`why_selected.filters` 含 `validity_aware,temporal_validity,kb_excluded,non_archived,...`。
- **P2-4 基础**：`memory_write.handle_kb_ingest` 成功 chunk capture 后 fire `_fire_kb_enrich`（best-effort）；`core/db.py` 新增 `pipeline_jobs` 持久化表 DDL（队列/worker 基础）；`memall_system` 新增 `kb_enrich_stats` 读 action（additive）。
- **契约测试**：`test_p1_continuity_pack_mcp_mode_routing_contract` 改为 portable registry + additive/back-compat handler contract；新增/强化 `auto_inject` KB contract、whole-injection session_start KB exclusion、temporal validity 相关边界断言。

## 验收（2026-11-10，含工作区改动）
```bash
cd /vol1/@appshare/fn-deepseek-harness/MemALL
PYTHONPATH=.:src ./.venv/bin/python -m pytest   tests/test_kb_ingest_p2.py tests/test_kb_ingest_p2_expanded.py   tests/test_capture_resolve_engine.py tests/test_session.py tests/test_session_sweep.py -q
# 46 passed
```

## 后续（P2-4/P1/P3，按债务账本推进）
- P2-4：`kb_enrich` 持久化 enqueue 到 `pipeline_jobs`、独立 worker claim/重试/backoff、跨进程幂等消费、reprocess、队列 metrics/MCP 完整暴露、`kb.enrich_worker.backend` 配置。
- P1：`include_conflicts=True` 仍 KB 排除的更多组合、superseded/REVIEW × temporal validity 交叉用例、continuity/injection 端到端路由。
- P3：`record_interaction` → utility ledger 聚合 → 检索/resolve 策略开关；默认 off，需显式 config+tests，不改 ranking 默认。


## 当前状态更新（2026-11-10，模型切换后继续全自动推进，未提交）

> 上文 "46 passed" 等为 commit `92863b6` 时的历史基线；以下为工作区最新状态。

### P1 边界契约（本轮新增）
- `build_continuity_pack` 增加/验证了 temporal validity（`valid_from/invalid_at`）过滤、`include_conflicts` 与有效性的交叉、`recent_supersedes` 边界、MCP `session_start` KB 排除契约。
- 新增 4 个测试于 `tests/test_kb_ingest_p2_expanded.py`，全部通过。

### P2-4 持久化 worker（本轮加固）
- `kb_enrich._claim_job` CAS 条件 UPDATE，修复跨进程重复 claim；新增 3 个测试（CAS 单赢家/running 不可重 claim/budget 有界）。`tests/test_kb_enrich_worker_p2.py` -> 16 passed。
- `kb.enrich_worker.autostart` 默认仍为 False；gateway 为 best-effort 启动 hook。

### P3 效用重排（本轮真实缺陷修复，仍默认关闭）
- `enhanced_search` 修复增强路径 `arguments` 未定义 NameError；新增显式 `utility_rerank` 参数。
- `unified_search` 全路径（fts5/direct/vector/hybrid）解析并转发 flag；内部 dict 的 `utility_rerank_signal/score` 透传到结果。
- MCP `handle_retrieve`/`handle_unified_search` 端到端验证；`tests/test_utility_rerank_p3.py` -> 6 passed。
- `search.utility_rerank.enabled` 默认 False：不开配置时无信号、顺序不变、不丢结果。

### 最新验收
扩展 scoped regression（9 个测试文件）-> **70 passed in 65.59s (exit 0)**。详见 `PRECOMMIT_SCOPED_REPORT_2026-11-10.md`。
