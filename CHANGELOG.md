## [v0.1.15] - 2026-06-26

### Chores

- **Dead imports**: Removed 13 unused imports across 9 MCP Python files (`hooks.py`, `hooks_builtin.py`, `http_transport.py`, `hub_client.py`, `registry.py`, `server.py`, `shared.py`, `tools/capture.py`, `tools/distill.py`).

### Fixed

- **S1-CLI-03 CLI 与 MCP 重复消除**: 创建 `memall.cli.handle_call.mcp_call()` 包装器，所有 CRUD 和 pipeline 命令改走 `adapter.handle_call()` 而非直调 thin_waist；MCP 成为唯一业务入口，CLI 退化为纯视图层；保留基础设施命令 CLI-only（init/start/stop/doctor/serve 等 19 个）。 (`cli/handle_call.py`, `cli/commands/base.py`, `cli/commands/pipeline_commands.py`, `cli/commands/management_commands.py`, `mcp/models.py`)

### Note

- **S1 全部清零 33/33 (100%)** 🎉 46 项（13 S0 + 33 S1）技术负债全部修复完毕。剩余 S2(24)/S3(13) 按需/迭代处理。

- **S1-CLI-02 init_temp_db 重复隔离逻辑**: conftest.py 已有 autouse fixture（monkeypatch+tmp_path）做每测试隔离，init_temp_db() 额外做 tempfile+patch+init_db 造成重复开销 → 改为返回 (None,None) 空操作桩，26 个测试文件无需修改。 (`tests/test_helpers.py`)

- **S1-BRG-01 bridge 错误处理**: lark_client.py Popen 加 try/except 捕获异常并 return、stdout 遍历加 try/finally 确保 proc.wait() 即使 handler 崩溃也执行；main.py stop() 加 try/finally 保护两个 watcher 都执行、MCP capture 失败日志从 DEBUG 升级到 WARNING、mentions 加 isinstance(m, dict) 防止非字典元素 AttributeError、两个 "silent error" 日志替换为具体描述。 (`bridge/lark_client.py`, `bridge/main.py`)

- **S1-SRH-01 CJK tokenization**: 3 处 TfidfVectorizer 从 token_pattern=r'(?u)\b\w+\b' 改为 tokenizer=tokenize（nlp.tokenize 已支持 CJK [\w\u4e00-\u9fff]+）。 (`core/nlp.py`, `pipeline/cluster.py`)

- **S1-SRH-02 faiss_provider 错误日志**: _encode() 两个 "silent error" 日志替换为描述性消息 + exc_info=True。 (`search/faiss_provider.py`)

- **S1-MCP-04 gateway 输入验证**: 添加 _validate() 静态方法复用 mcp/models.py Pydantic 模型，校验 5 个 POST handler（capture/retrieve/traverse/timeline/profile），消除手动 data.get() 式验证。 (`gateway.py`)

### Note

- **S1 进度 32/33 (97.0%)**: 唯一剩余 CLI-03（CLI/MCP 重复，约 1 周重构量）。git push 因端口 443 不可达暂缓。

### Chores

- **Dead imports batch (S2-16~24)**: 批量清理 40+ 处死 import，涉及 30 个文件 — agent_memory, api/server, bridge/main+config, core/context_assembler+db+nlp, federation/conflict+family+health, gateway, graph/embeddings+retrieve, lark/consumer, lark_notify, mcp/hooks+hooks_builtin+http_transport+hub_client+registry+server+shared+tools/*, migrations/004, pipeline/ask+behavior+bridge+cleanup+cluster+distill_l7+dream+improve+observe+session+stream+time_slice, scheduler, search/faiss_provider. 测试全绿。

- **S2-12**: adaptive.py 移除 `_get_adaptive_snapshot()` 中重复的 distill_history CREATE TABLE（由 adaptive_distill() 先创建）。

### Fixed

- **S3-08**: 命名规范统一 — ① `reflect.py` 修复 `focus_tag` 嵌套方括号问题（`[L6 反思 [工程实践]]` → `[L6 反思 工程实践]`）；② `thin_waist.py` `_LEVEL_SUBJECT_PREFIX` 补充 L6/L9 子类型变体（`L6-聚合/周反思/月反思`、`L9-聚合`）；③ `federation_tools.py` 修复 `startswith('[L7')` → `startswith('[L7 ')` 防止误匹配；④ `mcp/tools/distill.py` 修复 `startswith("[L9")` 缺少闭合括号 + 操作符优先级 bug（`i > 0 ... or ...` 无括号时 i=0 也会触发 L10 检查）。 (`pipeline/reflect.py`, `core/thin_waist.py`, `mcp/federation_tools.py`, `mcp/tools/distill.py`)

- **S3-10**: 会话 overhead 优化 — 3 项优化：① `session.py` L6 harvest 去重键从 content_hash 改为 `session_id`（JSON metadata），避免每轮 pipeline 创建重复 L6；② `reflect.py` 添加每日每 agent 最多 15 条 L6 升级的频率上限（`_MAX_L6_PER_AGENT_PER_DAY`），防止 reflect 步骤 L6 爆涨；③ `session.py` L6 summary/subject 去重（原两字段相同值），summary 改为空字符串。 (`pipeline/session.py`, `pipeline/reflect.py`)

- **S3-09**: 嵌入依赖声明化 — embeddings.py 模块级检测 `sentence_transformers` 存在性（`_HAS_ST` 标志），缺失时 log 明确提示安装命令；`_get_model()` 提前 raise ImportError 给出清晰错误；thin_waist.py 两处 embedding 失败日志从 `"silent error"` 改为描述性消息。 (`graph/embeddings.py`, `core/thin_waist.py`)

### Chores

- **S3-13**: git/CHANGELOG 自动化 — 新增 `scripts/post_commit_hook.py`（自动检测 CHANGELOG 版本号创建 git tag + 警告未更新 CHANGELOG）；`.git/hooks/post-commit.bat` 作为 hook 入口。 (`scripts/post_commit_hook.py`, `.git/hooks/post-commit.bat`)

### Docs

- **技术负债看板审计修复**: 基于逐文件行数统计校准 cli/ (6,800→4,338) 和 tests/ (3,000→11,476) 行数；验证 13 项 S0 代码级存在性（S0-003/S0-006 本轮修复，其余 11 项已核实）；Kanban 合并为单列"13/13 全部已修复"；Sprint 表替换为 S1 批量计划（5 项 ~45m）；饼图移除 S0 段重算（S1 47%/S2 34%/S3 19%）；热力图 85 项计数不一致修复。 (`frontend/index.html`, `src/memall/api/frontend/index.html`, `debt/INVENTORY.md`, `debt/DASHBOARD.md`)

### Note

- S0-004/S0-005 经审计确认当前代码已不存在裸漏洞（token leak 不在 handler 中，int() 已用 _safe_int/except 保护），标注"已核实"而非"已修复"。
- 缺失模块（lark/api/federation/scheduler/plugins/migrations 约 5,800 行）尚未纳入负债扫描，需后续 scan.py 规则收敛后补充。

## [v0.1.13] - 2026-06-26

### Fixed

- **S0-007 UUID 截断碰撞风险**: `str(uuid.uuid4())[:8]` 截断到 32 位 → 使用完整 UUID 字符串，消除 10 万次操作 50% 碰撞风险。 (`pipeline/session.py`)

- **S0-009 N+1 边缘计数**: `classify_step()` 每行执行独立 `COUNT(*) FROM edges` WHERE source_id=? → 预聚合 `GROUP BY source_id` 一次性查完，消除每 batch 500 次额外查询。 (`pipeline/classify.py`)

- **S0-011 O(n²) 自适应去重**: `adaptive.py` compression 模式 `SELECT id, content FROM memories ORDER BY id` 无 LIMIT → 添加 `LIMIT 5000`，防止大库时 O(n²) 性能爆炸。 (`pipeline/adaptive.py`)

- **S0-012 Memory dataclass 字段缺失**: `Memory` dataclass 缺 `thread_id` 和 `agent_name_locked` → 补全字段；`_row_to_memory()` 同步添加 `.get()` 安全读取；下游代码可通过 Memory 对象直接访问所有 DB 字段。 (`core/models.py`, `core/thin_waist.py`)

- **S0 清零确认**: 全部 13 项 S0 Critical 负债已修复（v0.1.11~v0.1.13）。
  - 安全类：S0-003~006（auth bypass、token leak、int crash、MCP auth）
  - 数据类：S0-002（PRAGMA FK）、S0-007（UUID）、S0-012（dataclass）
  - 性能类：S0-008（link O(n²)）、S0-009（N+1）、S0-010（enrich LIMIT）、S0-011（adaptive O(n²)
  - 静默失败：S0-013（embedding）
  - 运行时：S0-001（NameError）

## [v0.1.12] - 2026-06-26

### Fixed

- **Embedding 静默失败**: `_vec0_upsert()` 和 `_auto_embed()` 不再吞没异常，异常正确传播给调用方；`build_index()` 中 `DELETE FROM mem_vec` 失败时记录 warning 而非 bare `pass`。 (`graph/embeddings.py`)

- **NLP CJK 单字过滤**: `nlp.py:41` `len(t) > 1` 原过滤所有单字 token（含 CJK），改为保留单字 CJK 字符（如"猫""狗"）同时仍过滤单英文字母，修复中文搜索无结果问题。 (`core/nlp.py`)

- **link.py O(n²) 无边界**: `SELECT ... ORDER BY id` 无 LIMIT，2000+ 记忆时 O(n²) 全表比较 → 添加 `LIMIT 2000`，防止 pipeline 长时间阻塞。 (`pipeline/link.py`)

- **Pipeline 各步缺失 LIMIT**: `enrich.py`、`distill.py`(x2)、`integrate.py` 均无 LIMIT，大库时全表扫描 — 统一添加 `LIMIT 2000`~`5000`；`observe.py` 合并 3 次冗余 L6 metadata 全表扫描为 1 次；`reflect.py` 已自带 LIMIT 500。 (`pipeline/enrich.py`, `pipeline/distill.py`, `pipeline/integrate.py`, `pipeline/observe.py`)

## [v0.1.11] - 2026-06-26

### Security

- **L7 自助化闭环**: `handle_session_start()` 从 `auto_inject()` 结果中提取 L7 lessons/preferences 和 L6 reflections，格式化为显式行为指导文本返回，Claude 在 session 启动时即可读取并遵循。 (`mcp/tools/session.py`)

- **/pair 端点泄漏 auth_token**: 移除配对响应中的 `token` 字段，防止未授权用户通过 `/pair` 获取凭据。 (`gateway.py`)

- **所有 /api/* 绕过认证**: 改为仅 GET/HEAD /api/* 免认证（只读公开），POST/PUT/DELETE 需要 Bearer token，修复 `POST /api/discussions/create` 和 `/respond` 无认证问题。 (`gateway.py`)

- **MCP HTTP 零认证**: `handle_mcp_post` 新增可选的 Bearer token 检查（`MEMALL_MCP_TOKEN` 环境变量），作为 127.0.0.1 绑定之外的纵深防御。 (`mcp/http_transport.py`)

### Fixed

- **int(query_param) 非数字入参崩溃**: 4 处 `int(request.query.get(...))` 改为 `_safe_int()`，非数字入参返回默认值而非 500。 (`gateway.py`)

- **PRAGMA foreign_keys=OFF 无恢复**: `distill_step()` 在 try 前保存 `PRAGMA foreign_keys` 状态，finally 中恢复，防止连接池复用后外键永久失效。 (`pipeline/distill.py`)

- **discover_peers socket fd 泄漏**: 二次 bind 失败时关闭 socket 再 raise，防止 fd 泄漏。 (`gateway.py`)

- **Hub 消息未做清理**: 从数据库拼接 agent_name/subject/content 到消息体时过滤非打印字符，限制长度。 (`mcp/federation_tools.py`)

- **ThreadPoolExecutor 永不 shutdown**: `_on_shutdown` 中调用 `executor.shutdown(wait=False)`，确保平滑退出。 (`mcp/http_transport.py`)

- **session_end 重复 if count>3 块**: 移除第 2 个重复的 `if count > 3:` 块（lines 683-781），该块与第一个块（line 109）功能重复，会创建重复的 L4 会话记忆和 L6 反思。 (`pipeline/session.py`)

## [v0.1.10] - 2026-06-26

### Fixed

- **agent_name_locked 列缺少迁移**: 新建 `020_add_memories_agent_name_locked.py`，对已有数据库执行 `ALTER TABLE ADD COLUMN`。防止 `capture()` 因缺失列而崩溃。 (`migrations/020_add_memories_agent_name_locked.py`)

- **"system" 身份未在 identities 表注册**: `init_db()` 中 seed "system" agent；`capture()` 改为自动注册未知 agent_name 而非 raise ValueError，修复 gateway HTTP API 对新 agent 请求返回 500 的问题。 (`core/db.py`, `core/thin_waist.py`)

- **confirm_discussion 硬编码 [??] 前缀未随 Phase 1 更新**: 主题剥离改用 regex 同时兼容 `[??]` 和 `[讨论]` 前缀；L4 decision subject 和 content 改为 `[L4 会话]` 标准格式。 (`pipeline/convergence.py`)

- **update() 静默规范化 agent_name**: 当 agent_name 被 normalize 改变时（如小写化、黑名单命中→"system"）添加 logger.warning 告警。 (`core/thin_waist.py`)

## [v0.1.9] - 2026-06-25

### Changed

- **Phase 3: 废弃 _L8_WORDS 关键词正则**: `_L8_WORDS` 正则替换为废弃注释，`_LAYER_RULE_LIST` 移除 L8 条目（不再通过关键词匹配标记 L8）。L8 升级仅保留 edges 检测路径（edges 表 JOIN + module_refs）。L8 加入 `_TERMINAL_LAYERS`——一旦通过边提升到 L8即不可变。`_LAYER_RANK` 保留 L8 用于排名兼容。95 tests pass。 (`pipeline/classify.py`)

- **Phase 2: Gateway 图谱页面 `/graph` + JSON API `/api/graph`**: 新增 gateway 图谱可视化页面——整体统计（记忆数、关系数、图密度）、关系类型分布表（14 种类型带占比）、活跃节点 TOP 20（可点击跳转节点详情）、节点详情页（`?node_id=N` 显示该节点的 50 条最近边）。`/api/graph` 返回 JSON 格式的 totals/types/hubs。95 tests pass。 (`gateway.py`)

- **Phase 1: [GRAPH] 段从 L8 关键词查询改为 edges 实时聚合**: `auto_inject` 中 4 条 edges 查询替换了旧 L8 memories 查询——时间窗口计数(24h/7d/total)、类型分布(GROUP BY)、最近 5 条边(ID 无 JOIN)、活跃节点 TOP5 含 subject。`session_start` 中 `[GRAPH]` 从单行 subjects 升级为 4 行结构化输出。95 tests pass。 (`mcp/federation_tools.py`, `pipeline/session.py`)

### Fixed

- **supersedes FK constraint — schema + all INSERT paths**: `db.py` still had `supersedes TEXT NOT NULL DEFAULT '[]'` but models use `Optional[str] = None`. Fresh DBs rejected all INSERTs with `None` for supersedes. Fixed schema to `INTEGER REFERENCES memories(id)` (no NOT NULL), changed all 4 INSERT paths in `convergence.py` + guard in `thin_waist.py`. (`core/db.py`, `core/models.py`, `core/thin_waist.py`, `pipeline/convergence.py`)

- **converge_discussion string action_items missing assignee**: When action_items are plain strings (not dicts), the loop left `assigned_to=""`. Now extracts `participants` from discussion metadata and rotates through them as fallback assignees. Also adds `"assignee"` to task_meta dict for proper task attribution. (`pipeline/convergence.py`)

- **agent_name 规范化管理 (方案 C)**: 从 `capture()` 中提取 `normalize_agent_name()` 独立函数至 `core/thin_waist.py`，应用于 `update()`、`convergence.py` 中 4 处直接 INSERT（`create_discussion`、`confirm_discussion`、`converge_discussion` L5 task、`check_pending_discussions`）以及 `gateway.py` 中 `_import_identity` 和 `_import_memories` 路径。数据清理：40 条空 agent_name → "system"。代理名称统一经过 strip+lower+regex+黑名单校验。 (`core/thin_waist.py`, `pipeline/convergence.py`, `gateway.py`)

- **Phase 1: 层级命名规范统一 — subject 前缀**: 新增 `_LEVEL_SUBJECT_PREFIX` 映射表（level → `[Lx 标签]`），`_make_subject()` 签名增加 `level` 参数，优先使用 level prefix 再 fallback 到 category prefix。distill.py L9 subject 追加 `[L9 蒸馏]` 前缀，integrate.py L10 subject 从 `"L10:{agent}跨领域洞察({})"` 改为 `"[L10 整合] {agent} 跨领域洞察({})"`。85 tests pass（无新增失败）。 (`core/thin_waist.py`, `pipeline/distill.py`, `pipeline/integrate.py`)

- **Phase 2: 遗留 subject 数据清理**: 975 条 L9 旧数据追加 `[L9 蒸馏]` 前缀，4 条 L4 `[??]` 编码残损修复为 `[L4 会话]`。不改新生成逻辑，只清理存量。 (`一次性数据迁移`)

- **Artifact 页面 `/artifact`**: 新增 gateway 公共路由，展示 session 成果清单（commits、讨论收敛、Agent 评估矩阵），添加到导航栏和 auth 白名单。 (`gateway.py`)

## [v0.1.8] - 2026-06-25

### Fixed

- **classify_step LIMIT 500 无声丢失**: SQL 查询缺少 ORDER BY，每次仅重复扫描旧 500 条，830 条非 terminal 记忆（含 48 条有 edges 候选）从未处理。改为游标分页 — `pipeline_cursors` 表追踪 `last_classify_id`，每次跑 500 条，渐进覆盖全部 1330 条，跑完自动重置循环。L8 边缘检测（module_refs + edges 表）现在能覆盖全部记忆。 (`pipeline/classify.py`)

## [v0.1.7] - 2026-06-25

### Changed

- **Lightweight session_start**: Added TTL cache (300s) to `auto_inject()` — after first call per agent, all subsequent calls return cached data with 0 SQL queries. Moved L4 summaries, L5 todos, and BEHAVIOR annotations into the cache. session_start SQL reduced from ~23 queries to ~3 (stale check + session create + cache miss). (`mcp/federation_tools.py`, `pipeline/session.py`)

## [v0.1.6] - 2026-06-25

### Added

- **Phase 0: Composite index idx_level_agent**: New index `idx_memories_level_agent ON memories(level, agent_name)` accelerates all level+agent queries in session_start (used by 28-40 SQL queries). Zero data dependency, immediate effect. (`core/db.py`)

- **L3 scope field**: New `metadata.scope` field (values: `agent`/`family`/`shared`, default=`agent`) controls L3 workflow visibility across agents. Backward compatible — NULL defaults to `agent`. (`mcp/federation_tools.py`, `pipeline/session.py`)

- **Phase 1: Behavioral stage annotation**: New `pipeline/behavior.py` module with regex-based OODA loop detection (observe→model→predict→deviate→correct). Integrated into `enrich_step()` — 222 memories annotated on first run. `session_start()` now includes `[BEHAVIOR]` section with stage distribution and common sequences. (`pipeline/behavior.py`, `pipeline/enrich.py`, `pipeline/session.py`)

### Changed

- **L3 scope-aware queries**: `auto_inject()` workflow_skills and `session_start()` category matching now filter L3 by scope — agent-scoped workflows only visible to their creator, family/scoped visible to all agents. (`mcp/federation_tools.py`, `pipeline/session.py`)

- **Existing L3 memories scoped**: Discussion participation workflow (#10395) → `scope=family` (跨agent通用), codex research (#10396) → `scope=agent` (私有调研报告). (`core/thin_waist.py`)

### Fixed

- **confirm_discussion auto-converge**: Function was inserting a P2 response but not converging the discussion — docstring said "immediately converges" but code returned "responded". Now properly calls `converge_discussion()`. (`pipeline/convergence.py`)

- **converge_discussion supersedes=None (3 more)**: L4 decision and L5 task INSERTs still had `None` for `supersedes` column — missed in the v0.1.5 fix. L5 task INSERT also missing `project` value (latent bug masked by L4 supersedes error). (`pipeline/convergence.py`)

## [v0.1.5] - 2026-06-25

### Added

- **L11 Domain Knowledge Layer**: New terminal layer (rank 95) for business/strategy/domain knowledge, distinct from L3 workflow templates. 89 existing L3 memories bulk-reclassified to L11. (`pipeline/classify.py`)
- **L11 classify rules**: `_L11_WORDS` regex (weight 70) captures business, domain, strategy signals — automatically classifies new captures. (`pipeline/classify.py`)
- **L11 in auto_inject + session injection**: `auto_inject()` now returns `domain_knowledge` (L11 memories). `session_start()` formats `[DOMAIN]` section in context injection. (`mcp/federation_tools.py`, `pipeline/session.py`)
- **L11 infrastructure**: forget TTL (730d), thin_waist validation, search boost (0.3x), frontend color (#14b8a6), CLI --level choices, pipeline level checks, terminal exclusions in reflect/distill/identity. (11 files)

### Changed

- **L3 clarified purpose**: Layer 3 reserved for reusable multi-stage workflow templates (roles/stages/transitions). Existing non-workflow L3 content moved to L11. (`pipeline/classify.py`)

## [v0.1.4] - 2026-06-23

### Added

- **SDK Layer — `agent_memory.py`**: New `add()` / `search()` high-level API with automatic project inference. Every memory stored via `add()` gets a non-empty `project` field — inferred from `agent_name` (workbuddy→memall, douyin-daily→douyin-daily) or content keywords, with `"memall"` as default fallback. (`agent_memory.py`)
- **Project field fallback in all capture paths**: MCP `capture` tool, MCP `smart_store` tool now auto-fill project via `infer_project()` when the caller omits it. (`mcp/tools/capture.py`, `mcp/tools/memory_write.py`)

### Fixed

- **Pipeline INSERTs missing project column**: All 6 pipeline files (`session.py`, `distill.py`, `integrate.py`, `reflect.py`, `observe.py`, `convergence.py`) — INSERT INTO memories now includes `project`, derived from source memories via majority vote. (`pipeline/*.py`)
- **Scripts INSERTs missing project**: `daily_checkin.py`, `daily_explore.py`, `self_task.py`, `weekly_checkin.py`, `scheduler/agent_round.py` — all raw INSERTs updated to include `project` column. (`scripts/*.py`, `scheduler/agent_round.py`)
- **Backfill migration**: `_backfill_project.py` scanned 1982 empty-project memories and backfilled 1629 (82%) via agent mapping, group majority, and content heuristics. Empty rate: 78% → 13.9%. (`pipeline/_backfill_project.py`)
- **logger-in-docstring bugs (5 more)**: `faiss_provider.py`, `adaptive.py`, `forget.py`, `register.py`, `cleanup.py` — same pattern as the original `federation_tools.py` bug. Zero instances remain across `src/memall/`. (`search/faiss_provider.py`, `pipeline/adaptive.py`, `pipeline/forget.py`, `cli/register.py`, `pipeline/cleanup.py`)

### Security

- **hybrid_search() visibility filtering**: Results now pass through `_filter_by_trust_dict()` before returning; unknown agents default to `read_level="private"` (was `"public"`). (`core/thin_waist.py`)
- **4 shell=True subprocess calls removed**: All changed to list-arg style — eliminates command injection risk from user-controlled `text[:1500]`. (`lark_notify.py`, `lark/consumer.py`, `bridge/lark_client.py`)
- **API server Bearer token auth**: 57 routes protected via middleware; token auto-generated on first start and persisted to config. CORS `"file://"` origin removed. (`api/server.py`)
- **Federation peer token enforcement**: `_remote_retrieve` and `_remote_retrieve_async` now require peer token — skip peer with warning if unconfigured. (`gateway.py`)

## [v0.1.3] - 2026-06-23

### Added

- **E2E Test Suite**: 25-test end-to-end test covering capture, retrieve, timeline, connect, traverse, session lifecycle, smart store, vector search, DB ops, identity, persona, onboarding, pipeline, index rebuild, dedup, and error handling — all calling `handle_call` directly (no HTTP server) with retry-on-BUSY pattern. (`tests/test_e2e.py`, `tests/test_helpers.py`)
- **Memory Health System**: New `memall.core.health` module with `collect()` for actionable memory diagnostics. Integrated into `memall doctor --deep` for deep health checks and `session_start` as `[HEALTH]` section. Reports graph coverage, reflection rate, isolated memories, stale discussions, pipeline freshness, and DB size with issue/recommendation hints. (`core/health.py`, `cli/commands/management_commands.py`, `pipeline/session.py`)
- **Export/Import/Sync System**: JSONL export format with content_hash dedup, `--since` time filter, `memall import <file>` for JSON/JSONL import, and `memall sync --from <file>` for incremental sync with state tracking in `~/.memall/sync_state.json`. (`cli/export.py`, `cli/main.py`, `cli/commands/management_commands.py`)

### Fixed

- **Category Taxonomy Normalization**: Eliminated all 122 composite categories and consolidated 100+ labels → 25 clean categories. Fixed root cause in `integrate.py` (L10 merge no longer concatenates categories with `、`; picks majority category instead). Applied DB cleanup via migration script to standardize synonyms (`bugfix→fix`, `business_idea→business`, `discussion_response→discussion`, `daily_summary→report`, etc.). (`pipeline/integrate.py`)
- **ops.py SyntaxError**: Moved `import logging` to module level to fix `expected 'except' or 'finally' block` crash introduced in earlier commit. (`pipeline/ops.py`)

### Changed

- **Lazy Auto-Init**: `get_conn()` and `ConnectionPool._new_conn()` now call `init_db()` on their first invocation, so no explicit `memall init` is required for new users or agents that clone the repo. (`core/db.py`)

### Fixed

- **Gateway import global content_hash dedup**: Dedup check was scoped by `agent_name`, but the `UNIQUE` constraint is global — switched to a global lookup. (`gateway.py`)
- **Connection Pool Write Lock**: `pool_conn()` returned connections with uncommitted implicit write transactions, causing "database is locked" on reused connections. Added `conn.commit()` in pool_conn context manager's finally block. (`core/db.py`)
- **vec0 Dimension Mismatch**: `build_index()` passed raw k-dim SVD vectors (k ≪ 256 for small datasets) to vec0 expecting 256-dim vectors. Added padding to `EMBED_DIM=256` before `tobytes()`. (`graph/embeddings.py`)
- **Pipeline Hook TypeError**: `_hook_pipeline_stop` assumed all step results were `int`, but `classify_step()` returns `dict`. Added `_count()` helper to extract integer from dict. (`mcp/hooks_builtin.py`)
- **OpsInput None Defaults**: Pydantic model had `Optional[int] = None` which `model_dump()` preserved as `None`, causing `TypeError` in dedup operator. Changed to explicit `Field(...)` defaults. (`mcp/models.py`)
- **`_auto_embed` Missing Table**: Called `SELECT` on `memory_embeddings` before table existed on fresh DB. Added `_ensure_embeddings_table()` guard. (`graph/embeddings.py`)
- **`_load_embeddings_matrix` Missing Table**: Queried `memory_embeddings` without creating it first. Added `_ensure_embeddings_table()` call. (`graph/embeddings.py`)
- **`_query_embed` Dimension Mismatch**: SVD produced k-dim query vectors (k < `EMBED_DIM`) causing matmul shape error. Added padding to `EMBED_DIM=256`. (`graph/retrieve.py`)
- **Migration 015/017/018 Silent Errors**: `logger = logging.getLogger(__name__)` placed inside docstrings, never executed — migrations silently caught all exceptions. Extracted logger assignment above docstring. (`migrations/015_*.py`, `migrations/017_*.py`, `migrations/018_*.py`)
- **Missing `identity_profile` Column**: Column referenced in code but missing from base schema DDL. Added to `CREATE TABLE identities`. (`core/db.py`)
- **Thread-Safe Connection Close**: `ConnectionPool.get()` tried to close connections owned by another thread, causing `ProgrammingError`. Added specific catch for `sqlite3.ProgrammingError`. (`core/db.py`)
- **SyntaxWarning `\\w`**: Invalid escape sequence `\w` in docstring triggered Python 3.12 warning. Escaped backslash. (`graph/embeddings.py`)
- **`doctor --deep` UnboundLocalError**: Redundant `import json` inside `cmd_doctor()` shadowed the module-level import, causing `UnboundLocalError` on all non-`--fix` runs. Removed the local import. (`cli/commands/management_commands.py`)
- **MCP stdout GBK crash**: `_respond()` wrote JSON with `ensure_ascii=False` to `sys.stdout`, which crashes on Windows GBK consoles when Unicode chars (✅) appear. Added `sys.stdout.reconfigure(encoding='utf-8')` at `serve()` entry + `PYTHONIOENCODING=utf-8` env var in MCP config. (`mcp/server.py`, `.claude/settings.json`)
- **DB default on C: drive**: `_resolve_db_path()` now prefers first available non-system drive (D:, E:, …) on Windows instead of always dropping in `C:\Users\...\.memall`. Backups and `memall doctor` path checks follow the same logic. (`core/db.py`, `cli/backup_restore.py`, `cli/commands/management_commands.py`)

### Publishing

- **PyPI `memall-os` 0.1.2 published** under account `j19800-dev` (new account created after the old `j19800` account got locked out by 2FA). Package renamed from `memall-db` → `memall-os` since `memall` is too similar to the existing `memall-db` project (PyPI rejects similar names). Install: `pip install memall-os`.

## [v0.1.1] - 2026-06-21

### Fixed

- **HTTP Transport Crash**: Root cause fixed — sync `handle_call()` blocked aiohttp event loop. Offloaded to `ThreadPoolExecutor` (12 fast + 2 heavy workers) with `asyncio.wait_for()`. Auto-restart on crash/port conflict. (`http_transport.py`, `shared.py`)
- **DB Connection Deadlock**: `ConnectionPool.get()` had no timeout on `Queue.get()` — added 30s barrier. 21 raw `sqlite3.connect()` calls missing `timeout=10` — all backfilled across federation, lark, cli, pipeline modules. (`core/db.py`, 8 federation/cli/api files)

### Added

- **L7 Lifecycle Closure**: `auto_inject` defaults to True across all entry points (5 files) — new sessions automatically inject `[L7约束]` behavioral rules. L6→L7 auto-distillation via `distill_l7.py` regex-based lesson extraction, registered in pipeline after `reflect_step()`. (`pipeline/distill_l7.py`, `mcp/models.py`, `mcp/tools/__init__.py`, `mcp/tools/session.py`, `pipeline/session.py`, `api/server.py`, `pipeline/pipeline.py`)

### Changed

- **CLAUDE.md**: Added "自动提交" rule — each independent change auto-updates ALL relevant .md (not just CHANGELOG) + commit + push + notify user.

### Changed

- **Lazy Auto-Init**: `get_conn()` and `ConnectionPool._new_conn()` now call `init_db()` on their first invocation, so no explicit `memall init` is required for new users or agents that clone the repo. (`core/db.py`)

## [v0.1.0] - 2026-06-19

### Added

- **Memory Lifecycle**: 10-layer memory architecture (P0/L1-L10) with automatic pipeline
- **Decision Arcs**: Full L4→L5→L6 lifecycle with convergence engine for multi-agent discussions
- **Timeline System**: Pre-aggregated time_slices (day/week/month) + epoch detection (gaps, topic drift, reflection inflection points)
- **Self-Reflection (L6)**: Automatic quality review, pattern recognition, error correction
- **Knowledge Distillation (L9)**: Compress raw memories into structured knowledge graph
- **Multi-Agent Federation**: Cross-agent memory publish/query/conflict resolution with trust hierarchy
- **LAN Discovery**: Auto-detect nearby peers via mDNS, bidirectional sync
- **Hybrid Search**: FTS5 exact match + sqlite-vec (256-dim) semantic similarity
- **Session Management**: session_start with auto-inject, session_end with summary, session_summary
- **Agent Identity**: L1 identity traits + L7 preferences profiling
- **Onboarding System**: 5-step guided setup for new users
- **OODA Self-Improvement**: Observe-Orient-Decide-Act loop without human intervention
- **Quality Gates**: 8-dimension scoring in pipeline (relevance, coherence, novelty, actionability, etc.)
- **Auto-Forget**: TTL expiration + low-value decay with review mechanism
- **Memory Ops**: merge, split, tag, archive, restore, dedup tools
- **Security Governance**: audit, permit, check, score subsystem
- **Gateway Server**: HTTP export/import, LAN discovery, federated queries

### Changed

- **Architecture Redesign**: From legacy 62-action surface to Thin Waist 5-method (capture/recall/connect/traverse/timeline)
- **MCP Tool Consolidation**: 19 independent tools → 4 tool sets (core, AI, graph, system) → 37 unified MCP tools
- **Pipeline v3**: 21-step automatic pipeline (enrich → classify → time_slice → arc_status → echo → epoch → reflect → distill → integrate → ...)
- **Configuration**: All config stored in SQLite `config` table, env overridable
- **Identity evolved**: Agent identities table with L1/L7 portrait generation
- **Discipline Migration**: Legacy daemon → Windows Scheduled Tasks (04:00 pipeline, 03:00 forget)
- **MCP Server**: Unified STDIO + HTTP transport via config-based routing
- **Pricing positioning**: Freemium model (Free: 5k memory limit, Pro: $9.99/mo) defined

### Fixed

- **DB Path Resolution**: Config-based path respecting overrides (#7905, #8144)
- **OpenBLAS OOM**: Pipeline crash on 2000+ memories (#8133)
- **Scheduler Restored**: After 12-day downtime, migrated to Windows Tasks (#7895)
- **Discussion Dual-Path**: _meta/value duplicate entries (#8177)
- **Discussion Metadata Migration**: Legacy table drop without re-wrap cycle (#7958, #7963)
- **Silent Errors**: 79 blocks across 33 files migrated from bare pass to logger.warning (#7965)
- **Database Copy Bug**: Fixed concurrent write corruption (#5558)
- **Classify Level Loss**: layer field not persisted in classify step (#4894)
- **Bridge N+1**: Per-edge queries converted to batch IN (#5306)
- **Migration Cleanup**: Double migration system removed (#5305)
- **Test Isolation**: conftest.py + production DB protection (#6292)
- **10-Layer Health Skew**: Resolved architecture imbalance (#4975, #4977)
- **Consumer Recovery**: Message consumption restored after refactor (#4979-#4981)
- **FTS5 Repair**: memory-doctor.py for database integrity checks
- **Discussion Status**: Removed bare status after cleanup (#7963)
- **Backup Restoration**: Added memall backup/restore/check commands

### Removed

- Legacy daemon process (replaced by Windows Scheduled Tasks)
- _run_migrations + 7 migration files (dual migration system)
- Legacy SmartMemoryInjector (integrated into pipeline)
- Kronvex from comparison table (blocking marketplace listing)
- FTS5 as standalone MCP tool (SQLite built-in, not a tool)

### Security

- **3-Layer Safety Net**: Permission + circuit-breaker + recovery
- **QR Pairing**: LAN device authentication without network exposure
- **PII Redaction**: Optional content sanitization in scrape/parse pipelines
- **API Key Auth**: MCP Server authentication module

## [v0.0.2] - 2026-06-06

### Added

- Phase 2 compression and decay mechanisms (DreamGenerator, MemoryLifecycle)
- Timeline dimension: time_slices, epochs, session summary injection
- Decision Arc: full L4→L5→L6 lifecycle
- Discussion convergence engine
- MCP Marketplace listing draft
- LAN discovery and federation prototype

### Changed

- Legacy -> Thin Waist architecture migration completed
- 19 MCP tools -> 4 tool sets
- Tag normalization: 352 unique tags → 33 (91% reduction), 5-dimension standard set
- Database path: sandbox (~/.MemALL) → workspace

### Fixed

- Dead code and script cleanup
- L9 decay pipeline timeout
- FTS5 + vector search hybrid
- BOM illegal characters (8 files)
- Indentation/syntax errors (5 files)

## [v0.0.1] - 2026-05-25

### Added

- Initial MemALL prototype with SQLite-backed memory storage
- MCP server with HTTP + STDIO transport
- CLI with 40+ subcommands
- Agent SDK Python client
- Basic capture/retrieve/timeline/search operations
- 10-layer architecture: P0/L1-L10
- FTS5 full-text search
- Identity and Agent Registry
- Self-improvement framework (HOT memory injection)
