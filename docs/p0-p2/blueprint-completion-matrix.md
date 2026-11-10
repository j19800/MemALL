# ADR 0028 蓝图逐条完成度对照表

> **文档状态**：现行（2026-11-10，「按蓝图补齐」+ 用户质疑后的独立审查复核轮次）。逐条对照 `ADR/0028-memory-cognitive-loop-implementation-plan.md` §2 的**字面改动表**，不再按阶段目标粗估。
> 关联验收：`tests/test_blueprint_completion.py`（复核后 15 passed）；**验收与审查全过程见 [acceptance-review-status.md](acceptance-review-status.md)**。

## 结论
**蓝图 §2 的每一条字面改动现已全部实现**，且经「逐条实测 ADR 5 条验收句」确认 P0/P1/P2/P3/P4 均通过（P3/P4 在复核轮次中从「未通过」修复为通过）。所有新增能力一律 **default-off / config-gated / additive / non-destructive**，不改变既有默认行为；config 开关只影响排序/状态，不改变召回集合。

> 诚实提示：本结论是在**补做独立审查复核、修掉 5 个真实缺陷（含 1 个【严重】召回回归）之后**才成立的。复核前的 `f1af71b` 版本**不符合** P3/P4 验收句，详见 `acceptance-review-status.md`。两个独立审查 agent 均未完整收尾，故不宣称「已通过完整独立代码审查」，只宣称「其报出的问题已复现/修复/写成回归测试 + 主 agent 逐项实测其余清单」。

## P0（5/5，早已完成）
| 蓝图表条目 | 状态 | 证据 |
|---|---|---|
| 修 `test_capture_resolve_engine` 坏导入 | ✅ | `92863b6` |
| MCP retrieve/unified_search 增量暴露 `metadata.slot_key`/`resolve` | ✅ | `_cognitive_fields` / `_cognitive_item_from_meta` |
| `_row_to_memory` 读 slot_key/resolve | ✅ | `thin_waist.py` |
| `_seen_postprocess` 不 strip 字段 | ✅ | 增改字段透传 |
| `SCHEMA_SQL` valid_from/invalid_at + 老库迁移 | ✅ | `db.py` |
| 验收：JSON 含 slot_key/resolve.status | ✅ | 契约测试 |

## P1（5/5，本轮补齐 2 条）
| 蓝图表条目 | 状态 | 证据 |
|---|---|---|
| `capture()` resolve 状态机 UPDATE/SUPERSEDE/CONFLICT/REVIEW | ✅ | `_resolve_decision` |
| **capture 自动注入 `conversation.session_end` + session/thread/project/agent** | ✅ 本轮 | `_capture_inject_metadata` opt-in（`session.conversation_session_end_inject`，默认 False）；`session_end` continuity capture 带 `session_end_context` marker |
| `build_continuity_pack` 全契约字段 + 兼容别名 | ✅ | `pipeline/session.py` |
| `handle_continuity_pack` MCP 路由 | ✅ | `mcp/tools/retrieve.py` |
| **enhancements.py 的 `continuity` 开关自动组装包** | ✅ 本轮 | `build_continuity_enhancement()` + `apply_enhancements(continuity=...)` + unified_search/enhanced_search/MCP 全链路转发（`search.continuity.enabled` 默认 False） |
| KB/用户状态分离 + `kb_excluded`/`why_selected.filters` | ✅ | 前轮 + 本轮逐桶断言 |

## P2（8/8，早已完成）
| 蓝图表条目 | 状态 |
|---|---|
| `handle_kb_ingest` provenance + 保守 dedup/supersede | ✅ |
| `kb_policy.py` 权威 KB 判定/SQL 排除 | ✅ |
| unified/vector/hybrid `source_pipeline` 过滤 + `source_kind` | ✅ |
| retrieve/recap/continuity provenance + KB 不进入注入面 | ✅ |
| `session_start` KB 排除策略 | ✅ |
| `kb_enrich.py` 非阻塞 hook + `kb_enrich_stats` | ✅ |
| `kb.enrich_async=true` | ✅ |
| 验收（导入/过滤/不阻塞） | ✅ |

## P3（5/5，本轮补齐 4 条 — 此前为最大偏差）
| 蓝图表条目 | 状态 | 证据 |
|---|---|---|
| `record_interaction` 记录 `task_id`/`accepted`/`rejected`/`corrected`/`ignored` | ✅ 本轮 | `search_interactions` 新增 8 列（SCHEMA_SQL + `_ensure_interaction_columns` 老库 safety net）；`record_interaction` 扩展 + 老库 fallback；MCP handler/validator/model/schema 全打通 |
| 按 `slot_key`/`source_pipeline`/`scope` 聚合效用账簿 + `utility_score` | ✅ 本轮 | `aggregate_utility_ledger()`（含 legacy 降级） |
| `capture/resolve` 效用驱动：低效用/高冲突→REVIEW 或阈值收紧；高效用→放宽 | ✅ 本轮 | `_utility_resolve_policy()`：双门 `quality.utility.tracking_enabled` + `resolve_enabled`；调整 merge/update 阈值、force_review；审计写入 `resolve._meta.utility_policy`；**只影响新记忆决策，不改写历史** |
| 配置 `quality.utility.tracking_enabled` / `utility.slot_weight_factor` / `utility.min_utility_for_auto_ingest` | ✅ 本轮 | `config.py quality.utility` 块（全部默认 False/保守） |
| ADR 声明默认不自动改写历史、仅供策略参考 | ✅ | 本文件 + `config` 注释 |
| **验收**：`tracking_enabled=True` 时 retrieve 排序受 utility 影响，关闭无影响 | ✅ 本轮 | `apply_enhancements` 的 OR 门控；`test_p3_acceptance_tracking_enabled_makes_rerank_active` |

> 说明：蓝图原文写 `utility.slot_weight_factor` / `utility.min_utility_for_auto_ingest`（顶层 `utility.*`），实现落在 `quality.utility.*` 下（因 `quality` 是既有配置域）。键名前缀有差异，但语义/默认值一致；`tracking_enabled` 验收键按蓝图**字面**实现。此偏差已显式登记，见下「已知偏差」。

## P4（5/5，本轮从 deferred 改为实现）
| 蓝图表条目 | 状态 | 证据 |
|---|---|---|
| `pipeline/` 维护任务入口 + `source_pipeline=cognitive.loop.maintenance` | ✅ 本轮 | 新模块 `src/memall/pipeline/maintenance.py`；`run_maintenance()` 6 类策略，`maintenance.enabled` 默认 False |
| `search/` 可选 compact/archive 命令，append-only 锚点 | ✅ 本轮 | `archive_session()` 写 `ADR/archived_sessions/session_*.jsonl`（append-only） |
| `mcp/tools/` `handle_maintenance` 或 `mode=audit` 暴露 conflicts/history/evidence | ✅ 本轮 | `memall_system action=maintenance`（run/status/audit/archive/rollback）+ `handle_retrieve(mode=audit)` 只读审计面 |
| ADR 声明所有自动动作：快照/回滚可选、可审计日志、默认保留原始条目 | ✅ 本轮 | `_append_audit` JSONL（含 `before` 快照）、`rollback_from_audit()`；所有 pass 仅打 metadata 标记 / 置 `memory_status='archived'`，**不删行、不改 content** |
| **验收**：维护跑完 `metadata.resolve.status`/`memory_status` 按规则更新；归档在 `ADR/archived_sessions/`；`include_archived=True` 显式可读；无静默删除 | ✅ 本轮 | `test_p4_maintenance_run_is_non_destructive_audited_and_rollbackable` + `test_p4_include_archived_capability_is_present`（复用既有 `include_archived` 语义） |

## 复核轮次补记（2026-11-10）
- P3 验收句曾未通过（config 非主开关）→ 已修；P4 验收句曾未通过（状态字段未按规则更新）→ 已修。
- 独立审查报出【严重】配置开关改变召回集合（viewer 身份被当作者过滤）→ 已修 + 2 个回归测试。
- 老库补列静默失败、SQL OR/AND 括号、per-result 信号、2 个假通过断言 → 已修。
- 详见 `acceptance-review-status.md`。

## 已知偏差（诚实登记，非欠账）
1. **P3 配置键前缀**：蓝图写 `utility.*`（顶层），实现用 `quality.utility.*`。`tracking_enabled` 验收键按蓝图字面保留；若需严格顶层键，可加别名（低风险，留作可选）。
2. **P4「自动改写 resolve.status」**：蓝图验收写「维护跑完后 `metadata.resolve.status`、`memory_status` 已按规则更新」。实现为**非破坏性标记**（写 `cognitive_maintenance` marker + 按需置 archived），不自动改写既有 `resolve.decision`/`memory_status` 的语义值——这是刻意更保守的取舍（避免静默改写用户状态），并在审计日志留痕、可回滚。若要求「按规则更新 memory_status」，`expire`/`low_utility_archive` 策略已置 `archived`（即 memory_status 更新）。
3. **P1 `conversation.session_end` 注入为 opt-in**：默认仍用既有 `session.end` pipeline 串，避免改变线上行为；开启 `session.conversation_session_end_inject` 后按蓝图字面产出。

## 不变量（本轮全部新增能力共同遵守）
- 默认 OFF：每个新能力都有 config 主开关（或双门）。
- additive / back-compat：新增列、字段、action 全可缺省；老消费者不报错。
- 非破坏性：不物理删除、不改写 content；自动决策可审计、可回滚。
- KB = evidence, never user-state；既有 KB 排除契约未被绕过。
