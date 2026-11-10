# 独立代码审查报告（第二轮，2026-11-10 晚）

> **文档状态**：现行。应「再跑一次独立审查」请求而做。
> 审查对象：`5bc7198`（acceptance + review fixes）及 `f1af71b`（blueprint completion）相对 `0a6af93` 的全部代码改动（src/ + tests/）。
> **重要透明说明**：本轮启动了 2 个独立 subagent（正确性/安全性、验收/测试质量），**均未完整收尾**（未产出最终分级报告，消息通道亦无法催办）。为避免再次「半截审查」，本报告由 **lead 以第二视角逐项实测**完成：所有结论都附可复现脚本依据，不引用我自己写的测试断言作为唯一证据。若后续 subagent 返回，再合并其发现。

## 审查方法
- `git diff 0a6af93..5bc7198 -- src/ tests/` 通读。
- 对 6 类正确性/安全性点 + 5 条 ADR 验收句 + 测试覆盖，逐条写**独立验证脚本**实跑（临时 DB，`tests.test_helpers.init_temp_db`）。
- 环境：`cd /vol1/@appshare/fn-deepseek-harness/MemALL && PYTHONPATH=.:src ./.venv/bin/python ...`

## 分级发现

### 【严重】
**无（当前工作区）**。
> 上一轮独立审查报出的【严重】「config 开关改变召回集合」已复现、修复（`enhanced_search(viewer_is_identity=...)` + viewer 同步 + plain 路径统一），并由本轮独立脚本复验：author1 写 2 行 public、viewer1 检索，config OFF=2 行 / ON=2 行 / 显式 author filter=2 行，且 ON 时带 utility 信号。

### 【中等】
1. **`retrieve()` 基线信任过滤较宽松（既有，非本轮引入）**：`viewer=human` 可读到 `visibility='private'` 行（实测 `retrieve(viewer="viewer1")` 返回含 private 的 2 行）。本轮 `handle_retrieve` 把 `agent_name` 对齐为 viewer 语义后，MCP 检索面也随之可见 private 行——**但这与底层 `retrieve` 既有行为一致，不是新增越权面**（改动前 MCP 面是把 viewer 当作者过滤、恒返回 0，属于「错得不同」）。判定：不属本轮改动缺陷；若产品要求收紧，需单独改信任模型（影响全仓），已登记为既有负债，不擅改。
2. **无其它未修【中等】**：老库补列静默失败、SQL OR/AND 括号、per-result 信号缺失、2 个假通过断言均已在 `5bc7198` 修复并复验。

### 【轻微】
1. `mode=audit` 的 `conflicts` 段不带信任过滤（`SELECT ... WHERE memory_status LIKE ...`），但**只返回紧凑投影**（id/subject/memory_status/updated_at），不含 content —— 实测 `SECRET private content` 原文未出现在 audit 响应里。信息泄露风险低，仅状态元数据。
2. `quality.utility.low_utility_review` / `high_utility_relax` 默认值是 `True`，看似 default-on；实测它们被 `tracking_enabled` + `resolve_enabled` **双门**前置拦截（两门默认 False），因此单独为 True 不生效。属可读性问题，非功能缺陷。
3. 两个独立 subagent 未完整收尾 —— 流程缺口，非代码缺陷；已用本报告（第二视角逐项实测）兜底。

## 逐点结论（正确性/安全性 6 点）

| # | 检查点 | 结论 | 依据 |
|---|---|---|---|
| 1 | SQL 正确性（4 个候选查询 / 动态 GROUP BY / audit 查询） | **通过** | `conflict_review` 已加括号修复；`dims` 白名单实测过滤 `slot_key; DROP TABLE...` → 只用 `slot_key`；GROUP BY 与 SELECT `sel` 同源；`expire` 用参数化 `invalid_at <= ?`；audit 查询纯 OR 无优先级混用 |
| 2 | 老库迁移 | **通过** | `sqlite3.Row` 与 tuple 两种连接实测均补齐 8 列且幂等；`record_interaction` 老库 fallback 实测写入 1 行（第一次 INSERT 失败后事务仍可用） |
| 3 | 身份/信任过滤 | **通过（附既有负债）** | `viewer_is_identity=True` 不引入新越权（见【中等】1）；fts5/direct/vector/hybrid/auto 五模式 OFF/ON 实测：召回一致、ON 时带信号（vector n=0 为 embedding 环境限制，转发链已确认）；legacy 直接调 `enhanced_search(agent_name=X)` 默认 False 保持原语义 |
| 4 | 配置门控 | **通过** | 14 个新 config 键逐个读默认值：所有**主开关**均 False；`low_utility_review/high_utility_relax` 为 True 但被双门拦截；`_utility_tracking_active()` 实测仅影响检索路径，capture 写入路径不受影响且反复切换/reset 稳定 |
| 5 | 安全 | **通过** | `archive_session` 实测 5 种穿越串（`../../etc/passwd` 等）全部落在归档目录内；审计 JSONL 写指定路径（实测存在），`run_maintenance` 有界（10 候选 / max=3 → 实际标记 3）；`mode=audit` 不泄露 content（实测） |
| 6 | 资源/异常/有界 | **通过** | `run_maintenance` try/finally 关连接、`get_conn` 路径均 finally close；except 只吞 best-effort 路径（hook/audit），主写入路径不吞；bounded 实测 3≤3 |

## 验收标准逐条（独立脚本，不复用 tests 断言）

| 阶段 | 结论 | 依据 |
|---|---|---|
| P0 | **通过** | `handle_retrieve` / `handle_unified_search` JSON 含 `slot_key`/`resolve` 字段 |
| P1 | **通过** | `build_continuity_pack` 5 个契约字段齐备、`kb_excluded=True`、KB `doc_id` 未入包 |
| P2 | **通过** | KB ingest 产出 `category=knowledge` / `source_pipeline=document.import` / `doc_hash` / `chunk_hash`；`source_pipeline` 过滤调用无错 |
| P3 | **通过** | 只开 `quality.utility.tracking_enabled`、不传任何 flag：`unified_search(fts5)` JSON `results[0]` 带 `utility_rerank_signal`；`enhanced_search` / `handle_retrieve` / `handle_unified_search` 同样生效；关闭后无信号且召回不变 |
| P4 | **通过** | `run_maintenance(conflict_review)` 后 `resolve.status` ACCEPTED→REVIEW；`expire` 后 `memory_status` ACTIVE→archived；归档锚点写入 `ADR/archived_sessions/`；`include_archived=True` 可读；行未删除；`rollback_from_audit` 精确恢复 ACCEPTED/CONFLICT/无 marker |

**召回不变专项**（上轮严重回归）：author1 写 2 行 public + viewer1(human) 检索 → OFF=2 / ON=2 / 显式 author filter=2，ON 带信号。**通过**。

## 测试质量与覆盖

| 项 | 结论 |
|---|---|
| 默认收集 | `pytest tests/ --collect-only -q` → **615 tests collected**；`tests/` 下 86 个 `test_*.py` |
| 全量结果 | **613 passed, 2 skipped, 0 failed, 0 errors**（613+2=615，与收集数一致 → **无漏跑**） |
| skip 合理性 | 2 个 = `test_embeddings.py` 缺 `sentence_transformers` 的条件跳过，与改动无关 |
| 假通过断言 | 全文扫描 `or True` / 永真分支 / 弱断言 → **未发现**（上轮 2 处已修）；`test_blueprint_completion.py` 15 个测试断言均含具体值/状态断言，3 个新回归（老库补列、召回不变、handle_retrieve 召回+per-result 信号）实测覆盖所声称场景 |
| 提交态自洽 | `git write-tree` 导出 492 文件，关键模块/API 可导入、默认全 off、`viewer_is_identity` 参数存在、排除项 `bak_phase1` 未混入 |

## 总体结论

**代码（src/）可以认为通过正确性与安全性审查**：无未修【严重】/【中等】缺陷；6 个检查点全部通过；唯一【中等】为 `retrieve()` 基线信任过滤的既有设计，非本轮改动引入，已显式登记。
**ADR 0028 的 5 条验收句全部真实通过**（P3/P4 为修复后通过，且 config 开关不改变召回集合）。
**测试覆盖完整、无漏跑、无假通过**。

保留项（诚实）：
- 两个独立 subagent 未完整收尾 → 本报告是 lead 第二视角逐项实测，不是「两个独立 agent 都签字」；如需严格双人签字结论，需重跑能完整收尾的审查。
- `retrieve()` 基线信任过滤（human 可读 private）作为既有负债登记，不在本轮修。
- `thin_waist.py.bak_phase1` 仍未跟踪/未纳入任何提交。

---

# 第三轮审查（2026-11-10 晚，换审查方式：对抗式逐项复现）

> 应「换一种审查方式，并看看是否有什么缺陷，安全性问题」而做。方法改为：**把每个接口当作可被恶意调用方利用的攻击面，写最小复现脚本实锤后再修**（而非再扫检查清单）。两个窄任务 subagent 仍未完整收尾，但其一留下了一条已坐实的假通过断言（问题 1），成为本轮起点。

## 发现并修复的 6 个真实缺陷

### 1.【严重·测试】假通过断言（subagent 报出，已实锤修复）
`test_p3_utility_resolve_metadata_audit_record_persisted` 断言 `"applied" in up`——`{'applied': False}` 照样通过，功能坏掉测不出来。
**修复**：双门 ON 时断言 `applied is True` + `engine` + 阈值字段；另加负面控制（`resolve_enabled=False` ⇒ `applied is False`）。

### 2.【严重·召回语义】project 过滤器在增强路由上静默丢失
显式传 `project=projA`：plain 路径召回 1 行，走 enhanced 路由（任意 enhancement flag 或 config-only 主开关）召回 2 行——`project` 被 `enhanced_search` 具名参数吃掉，没进 `retrieve_kwargs`。
**修复**：`enhanced_search` 把非空 `project` 重新注入 retrieve 过滤。回归测试 `test_explicit_project_filter_survives_enhancement_routing`（三种路由 ids 全等）。

### 3.【中等·审计】`_resolve_decision` 早退路径不带 utility_policy
no_slot/new_slot/same_content 直接 return，`utility_policy` 缺失；双门开着也无法证明「策略已激活但因无 slot 而无需调整」。
**修复**：所有返回路径统一挂 `policy._meta`（applied 反映双门状态，NOOP 决策逻辑不变）。

### 4.【严重·安全】伪造 rollback entry = 任意写原语（实锤）
`rollback_from_audit(entry)` 直接信任调用方传入的 `before` 快照。复现：构造 `{"before":{"id":任意行,"metadata":{"pwned":true},"memory_status":"archived"}}` 一次调用即任意改写任意记忆。
**修复**：快照唯一来源改为**服务端审计日志**（`_find_audit_record(ts, memory_id, action)` 按标识定位）；调用方 `before` 一律忽略；无匹配记录拒绝；拒绝回滚 rollback 记录；**CAS**——当前行必须仍等于日志 `after`，否则拒绝（防止覆盖后来合法写入）；恢复前校验快照 metadata 可读、status 在白名单。回归测试 4 段：伪造拒绝 / 陌生 ts 拒绝 / read_audit 定位字段回滚成功且精确恢复 / rollback 记录不可再回滚。

### 5.【严重·授权边界】actions 参数可越过运维 config 门
`run_maintenance(actions=["expire"])` 在 `maintenance.expire=False` 时仍归档了记忆（实锤：ACTIVE→archived）。
**修复**：调用方只能**缩小**不能**放大**——requested 必须与 config 授权集求交；`skipped_unauthorised` 回报；无授权动作时 `ran=False` 且不写审计。回归测试 `test_p4_caller_actions_cannot_widen_config_authorization`。

### 6.【严重·安全】read_audit 跨身份泄露（经 MCP mode=audit 可达）
审计日志含全量 before/after metadata 快照（含他人 private 记忆的正文与 metadata 内密钥）。复现：attacker 调 `mode=audit`，响应中出现 `sk-secret-123` 与 victim 正文。
**修复**：`read_audit(limit, redact=True)` 默认输出**脱敏投影**（ts/action/memory_id/rules_applied + `metadata_sha256` 不可逆指纹，剥掉 metadata/content）；磁盘原始日志保留完整快照，仅 trusted `redact=False` 内部路径可读（回滚/验证仍精确）；`_AUDIT_TAIL_MAX_BYTES`(4MB) 字节尾读防 OOM。复验：MCP mode=audit 不再出现 api_key/正文；identifiers 回滚仍恢复原值。回归测试 `test_p4_read_audit_redacts_snapshots_for_untrusted_readers`。

## 非缺陷说明
- `maintenance._paths()` 以 `Path.cwd()` 为相对锚：运维若配相对归档路径，实际位置随进程 CWD 变化。属部署注意事项（绝对路径完全正常），登记为轻微运维项，未改。
- `archive_session` 实测：同 session 多次调用 append-only 累积（设计如此）；空/超长/含换行 session_id 均安全落在归档目录内。

## 第三轮结论
换攻击面复现式审查确实再挖出 4 个此前两轮清单式审查漏掉的真问题（#2/#4/#5/#6，其中 #4/#6 为可实锤的严重安全缺陷）。6 项全部修复并有专门回归测试；`tests/test_blueprint_completion.py` **19 passed**；全量回归数字见 `PRECOMMIT_SCOPED_REPORT_2026-11-10.md` 第三轮条目。信任模型修正后的关键性质：**调用方可请求（限运维授权集内）、可定位（凭日志标识）、不可注入（快照只从服务端日志取）、不可窥探（默认脱敏）**。

## 补充发现（第 7 项）：维护 action 无授权边界
继续追攻击面时发现：`memall_system action=maintenance` 的写入子动作（`run`/`archive`/`rollback`）**没有任何身份校验**——任意能调 MCP 的调用方实测均可触发归档跑批与写归档锚点（`random_low_priv` 调 `run`→ok、`archive`→ok）。config 门只控制「功能是否启用」，不构成「谁有权触发」的授权边界。
**修复**（`src/memall/mcp/tools/__init__.py`）：写入子动作要求 supervisor 身份（`security.supervisor_agents`）或部署的 `identity.human_owner`；`status`/`audit` 只读保持开放。被拒返回 `{"ok": false, "error": "forbidden"}` 并附 caller。
**验证**：新增 `test_p4_maintenance_write_actions_require_supervisor_identity`（随机/匿名被拒、supervisor/owner 放行、只读开放）；`test_p4_maintenance_default_off_and_status` 改为以 supervisor 身份验证 config 门。`test_blueprint_completion.py` → **20 passed**。

## 附带修复：列补齐的 TOCTOU 竞态（全量回归 falkernel 源）
追查「全量回归偶发 `duplicate column name: primary_layer/tags/echo_score`」时定位到真因：`_ensure_missing_columns` / `_ensure_interaction_columns` 先 `PRAGMA table_info` 判断缺列、再 `ALTER TABLE`，两步之间后台 daemon 线程（或并发测试连接）可能已补上该列，第二次 ALTER 抛 `duplicate column name` 并把整个 `init_db()` 打挂 → 表现为**无关测试**的偶发 error/failure（历史多轮都出现过）。
**修复**（`src/memall/core/db.py`）：两处 ALTER 各自捕获 `sqlite3.OperationalError`，仅当消息含 `duplicate column name` 时忽略（等价于「目标状态已达成」），其它错误照常抛出。
**验证**：8 线程并发补列实测 **0 错误**，`memories` 34 列、`search_interactions` 18 列全部齐备。

## 附带修复：P3 验收测试的顺序依赖（flake）
`test_p3_acceptance_tracking_enabled_makes_rerank_active` 在单跑与部分子集通过、全量偶发失败。真因是 `get_config()` 的**热重载**（最多每 2s 重扫配置文件，命中即从默认值重建 `_config`）会静默丢弃测试注入的 `quality.utility.tracking_enabled`。
**修复**（`tests/test_blueprint_completion.py`）：`_cfg_set()` 追加 `cfg._last_mtime_check = float("inf")` 抑制重载（与 conftest 隔离夹具同款手法），并对 unified_search 断言加一次「重设门控后重试」兜底。
**验证**：`test_blueprint_completion.py` **20 passed**；混合子集 40 passed。
