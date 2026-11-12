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
| `kb_sql_exclusion_clauses()` 统一所有 KB 排除 SQL | ✅ unified | `src/memall/core/kb_policy.py`（调用端：session/retrieve/recap/auto_dream/cross_agent/lifecycle/llm_enhance/federation_tools） |
| `source_kind_from_pipeline()` 归一化 | ✅ new | `src/memall/core/kb_policy.py` |
| `vector_search`/`hybrid_search` `source_pipeline` 参数 | ✅ new | `src/memall/core/thin_waist_search.py` |
| `kb_enrich` 非阻塞 hook + 统计 | ✅ new | `src/memall/pipeline/kb_enrich.py` |
| `handle_kb_ingest` after-capture fire enrich | ✅ wired | `src/memall/mcp/tools/memory_write.py` |
| `handle_batch_capture` 无 stray `_fire_kb_enrich` | ✅ clean | `src/memall/mcp/tools/memory_write.py` |
| config `"kb": {"enrich_async": True}` | ✅ added | `src/memall/config.py` |
| P2-3 KB 策略全量收敛（2026-11-12） | ✅ done | `pipeline/*`、`mcp/federation_tools.py`、`mcp/tools/retrieve.py`、`pipeline/session.py` |

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

## 从检查点继续推进（2026-11-10，基于 `8aa68cc`，未提交）

- **P1**：补齐 `resolve_status`×temporal validity 的 `pending_decisions` 边界、`include_conflicts=False` 抑制 CONFLICT/REVIEW 进入 active_state、`session_start`/`auto_inject` 逐桶 KB 排除断言（+3 测试）。
- **P2-4**：worker 增加协作式 stop（`Event` + 有界 join + 幂等）、`kb_enrich_worker_status()`、`kb_enrich_stats.worker` 观测；MCP `kb_enrich_stop` action；**修复既有缺口**（`kb_enrich_stats`/`kb_reprocess` 在 validator 中原本 unknown，新增 `KBEnrichActionInput` 登记 3 action）；**真多进程** spawn CAS 单赢家测试；reprocess 幂等。部署文档 `p2-4-worker-deployment.md`（autostart 默认 off）。
- **P3**（默认 off 不改）：冷启动/低证据回退、跨 agent 隔离、权重钳制/永不丢项测试（+4，共 10 passed）；策略文档 `p3-utility-rerank-rollout.md`（度量基线/灰度/Kill-Switch）。
- **P4**：设计前置文档 `p4-deferred-design.md`（8 项，仅登记不实现）。
- **验收**：scoped 9 文件 **81 passed**；详见 `PRECOMMIT_SCOPED_REPORT_2026-11-10.md`。

## 「按蓝图补齐」轮次（2026-11-10，本地未提交）
- 逐条对照 ADR 0028 §2 字面改动表，把此前「等价替代 / 明确 deferred」的条目真正补齐：**P1** conversation.session_end 注入 + continuity 搜索开关；**P3** 反馈列/维度效用账簿/quality.utility.* 配置/效用驱动 resolve（可审计）/tracking_enabled 验收键；**P4** 从 deferred 改为 opt-in 非破坏性实现（maintenance 模块/audit/归档锚点/快照回滚/include_archived）。
- 全部新增能力 default-off / config-gated / additive / non-destructive；逐条对照见 `blueprint-completion-matrix.md`。
- 验收：`tests/test_blueprint_completion.py` 12 passed；扩展 scoped 15 文件 125 passed；全量见报告最新条目。


## 本轮改造（2026-11-11 本地验收，未提交）：语义去重 v2 配置生效与契约收敛

### 变更摘要
- `src/memall/core/thin_waist.py` 捕获语义去重门控：
  - 显式遗留键优先：`semantic_dedup_threshold`、`semantic_dedup_window` 存在时仍优先生效（兼容既有调用/测试）。
  - 无遗留键时走 v2：`semantic_dedup_threshold_cosine`（TF-IDF/余弦阈值）、`semantic_dedup_window_same_agent`（候选窗口）。
  - 保留键 `semantic_dedup_window_cross_agent`、`semantic_dedup_window_cross_session`、`semantic_dedup_use_embeddings`（默认 0/False）：捕获路径仍为同智能体本地文本相似度，**不启用**跨智能体/跨会话候选选择与嵌入去重。
  - 字符级回退（`_char_level_dup_lookup`）继续按遗留阈值推导保守 Jaccard bar（遗留阈值存在时 `max(0.5, threshold-0.2)`）或专用 `semantic_dedup_threshold_jaccard`（无遗留阈值时）。
  - 安全护栏保留：精确哈希去重始终先跑、语义去重默认关、 `_NEG_RE`/`_QTY_RE` 反极性/量化护栏、CJK 字 3-gram/Jaccard 回退、项目/线程边界。
- 测试增强：`tests/test_semantic_dedup_config_contract.py` 新增 3 个契约用例：
  1. `test_v2_semantic_dedup_keys_apply_when_legacy_absent`：无遗留键时 v2 cosine 阈值与同智能体窗口生效合并近似重复。
  2. `test_v2_same_agent_window_respected_when_legacy_absent`：`semantic_dedup_window_same_agent=1` 限制候选集，阻隔中间噪声记忆后的合并。
  3. `test_v2_cross_scope_windows_reserved_no_behavior_change`：保留跨智能体/跨会话窗口与嵌入开关**不**在本地文本路径下启用跨智能体合并。

### 验收结果
```bash
cd /vol1/@appshare/fn-deepseek-harness/MemALL
PYTHONPATH=.:src ./.venv/bin/python -m pytest tests/test_semantic_dedup_config_contract.py tests/test_capture_semantic_dedup.py tests/test_semantic_dedup_safety.py -q
# 13 passed
PYTHONPATH=.:src ./.venv/bin/python -m pytest tests/test_auto_capture.py tests/test_auto_inject_corrections.py tests/test_session.py tests/test_session_sweep.py tests/test_improve.py -q
# 28 passed
```

### 仍在账技术债务（本轮未解决，记录于 TECH_DEBT_LEDGER）
- 真跨智能体/跨会话语义去重候选选择与嵌入去重路径：保留键仅为契约占位，未实现。
- 自动入库来源仍仅限 `memall_persona.ask`/`memall_system.reflect`/`memall_discussion.respond` 后置钩子；会话/代码/错误/任务事件源未落地。
- 自进化/智能化：活跃纠偏已注入 session_start（`[EVOLVE]`），但有效性追踪、L4→L6 提取、跨智能体共识演化配置仍缺；无专用 `tests/test_intelligent*`/`tests/test_dream*`。

## 2026-11 本地继续深挖/加固（未提交）
- **语义去重/线程推断**：`_semantic_dedup_lookup` 增加可选 `project` 过滤；`_infer_thread_root` 语义回退传入 `project=data.project`，防止跨项目语义串线（保留时间窗/项目边界语义）。
- **自进化（improve）**：个人与全局 correction 在重复运行时合并唯一 `source_l6_ids`，按合并证据重算/持久化 `hit_count` 与 severity，避免重复膨胀；既有全局规则改为合并更新而非仅首次插入。
- **自动入库可控性**：新增 `capture.auto_capture_enabled`（默认 True，向后兼容），hook 读取失败时 fail-open 保持原行为。
- **契约测试与验收**：新增线程语义回退项目边界、improve 幂等合并、auto-capture 配置开关用例；定向 19 passed；全量 683 passed, 2 skipped（exit 0）。
- **未提交**：保留 v2 跨域/embedding 预留键默认关闭、自动入库来源仍限 ask/reflect/discussion、自进化深度能力与 `tests/test_intelligent*`/`tests/test_dream*` 仍待后续。

## 2026-11 local pass: P0-1/P0-2 自动入库/语义去重/智能化强化（本地验收，未提交）

### P0-1: 语义去重分层决策 + 理由/可观测性
- **精确哈希去重路径现在输出 additive dedup 元数据**（`thin_waist.py`）：`status="dedup_hash"` 的 capture contract 现在包含 `dedup` 字典，含 `layer: "exact_hash"`、`reason: "exact_hash_match"`、`decision: "merge"`、`target_id`、`similarity: 1.0` —— 提供完整可观测性，不破坏兼容性（异常回退路径仍返回 `CaptureId(..., status="dedup_hash")` 保底）。
- **遗留/v2 键优先级保持**：`semantic_dedup_threshold`/`semantic_dedup_window` 存在时优先；无遗留键时走 v2 `semantic_dedup_threshold_cosine`/`semantic_dedup_window_same_agent`。
- **字符级回退**：无遗留阈值时用专用 `semantic_dedup_threshold_jaccard`；有遗留阈值时保守 `max(0.5, legacy-0.2)`。
- **预留键默认关闭**：`semantic_dedup_window_cross_agent`、`semantic_dedup_window_cross_session`、`semantic_dedup_use_embeddings` 契约化存在但非生效（默认 0/False）。
- **所有安全护栏保留**：精确哈希优先、语义去重默认关、`_NEG_RE`/`_QTY_RE` 反极性/量化护栏、CJK 3-gram/Jaccard 回退、项目/线程边界、window=0 禁用语义。
- **契约测试**：`test_legacy_semantic_dedup_keys_take_precedence_in_capture_path` 通过。

### P0-2: 自动入库信息增量 + 持久化短窗口去重
- **ask 答案的信息增量门控**（`auto_capture.py`）：新增 `_looks_low_information()` 启发式（微小确认类如 "可以，完全没问题" 通过 token 集合 + 正则识别），带可观测 `_REASON_SKIPPED_LOW_INFORMATION`；`_information_increment_ratio()` 对比答案与问题；配置 `capture.auto_capture_info_increment_min_ratio`（默认 0.15）与 `capture.auto_capture_info_increment_enabled`（默认 `True`，fail-open）。
- **持久化短窗口去重**：`auto_capture_recent` 表（PK `(fingerprint, agent_name)`）300s 窗口、最小 2 次命中、按智能体作用域键 + 遗留空智能体兼容；契约测试 `test_auto_capture_persistent_dedup_across_restart_and_reports_reason` 通过。
- **进程内有界决策缓存**（`_last_decisions`，最多 512 条）并输出可观测理由：`captured`、`skipped_disabled`、`skipped_empty`、`skipped_min_length`、`skipped_error_payload`、`skipped_dedup_in_process`、`skipped_dedup_persistent`、`skipped_low_information`、`capture_error`。
- **配置感知最小长度**：`_auto_capture_min_len()` 链式 `capture.auto_capture_min_content_len` → `quality_gate.lenient_min_len` → 20（钳制 10–500）。
- **智能体传播修复**：`memall_discussion.respond` extractor 现在输出 `agent_name` hint；hook 从 hints/arguments 解析 agent 再捕获（修复 `test_auto_capture_reflect_and_discussion_extractors`）。
- **分词正则修正**：`_tokenize_for_increment` 使用 `[a-z0-9_]{2,}|[\u4e00-\u9fff]`。

### 验收结果
| 测试套件 | 结果 |
|----------|------|
| 定向 P0 测试（7 文件） | **34 passed, 0 failed** |
| 扩展回归（9 文件） | **54 passed, 0 failed** |
| 全量基线（历史） | **683 passed, 2 skipped, 192 warnings** |
| **全量回归（2026-11-12 本地）** | **689 passed, 2 skipped, 192 warnings (exit 0)** |

## 2026-11-12 第二轮：四项开放债务推进（本地验收，未提交）

### 变更概览
| 面 | 变更 | 默认行为 |
|---|---|---|
| 自动入库来源（TD-AUTO-SRC-01） | `auto_capture.py` 注册/注销/白名单查询函数；config `capture.auto_capture_extractors` 仅可收窄 | 内置 ask/reflect/respond 不变 |
| 持久去重加固 | `_persistent_dedup_settings()`：window/min_hits/retention/max_rows 可配 + 钳制 + rowid 裁剪 | 300s / 2 hits / retention=max(w*12,3600) / 10000 rows，与旧契约一致 |
| 自进化观测（TD-EVOL-TRACK-01） | corrections 加性列（injected/adopted）；`record_corrections_injection/record_correction_adoption/correction_effectiveness_report`；MCP `correction_report` | 写路径默认关（两枚 evolution 门 False），报告只读常开 |
| 语义去重预留（TD-DEDUP-V2-01） | 两个新契约锁定 cross_session=0 与 use_embeddings=True 均为惰性预留 | 默认全关不变 |

### 测试
- 新增 9 个契约测试（auto_capture +3、improve/tracking +4、semantic dedup +2、MCP route 计入 improve）。
- 定向回归 68 passed；全量基线见 AUTO_INTEL_PROGRESS 与 PRECOMMIT 报告。

### 回滚
- 关闭新 config 键（或删除）即完全回到旧行为；corrections 新列为 additive，可留驻。

## 2026-11-12 第三轮：TD-EVOL-TRACK-01 自动采纳判定器（本地验收，未提交）
- `evaluate_session_corrections()`：会话窗口三态信号（adoption 需注入证据且无关键词复发；violation 只阻断不罚；no_signal 不动计数）。
- 双钩子接入：`session_end`（直接评估）+ `session_start` stale 关闭（补 session_end 不可靠缺口）；结果均为加性字段。
- 门控：`correction_auto_adoption_eval`（默认 False）为主门；写计数仍需 adoption 门，否则 dry-run 预览。灰度路径：dry-run 核对 verdict → 开写门。
- 全量验收：**703 passed, 2 skipped (exit 0)**；本轮新增 5 测试；默认行为零变化（session_end 形状在默认配置下不变，有专项测试锁定）。
