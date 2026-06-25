## [v0.1.9] - 2026-06-25

### Changed

- **Phase 1: [GRAPH] 段从 L8 关键词查询改为 edges 实时聚合**: `auto_inject` 中 4 条 edges 查询替换了旧 L8 memories 查询——时间窗口计数(24h/7d/total)、类型分布(GROUP BY)、最近 5 条边(ID 无 JOIN)、活跃节点 TOP5 含 subject。`session_start` 中 `[GRAPH]` 从单行 subjects 升级为 4 行结构化输出。95 tests pass。 (`mcp/federation_tools.py`, `pipeline/session.py`)

### Fixed

- **supersedes FK constraint — schema + all INSERT paths**: `db.py` still had `supersedes TEXT NOT NULL DEFAULT '[]'` but models use `Optional[str] = None`. Fresh DBs rejected all INSERTs with `None` for supersedes. Fixed schema to `INTEGER REFERENCES memories(id)` (no NOT NULL), changed all 4 INSERT paths in `convergence.py` + guard in `thin_waist.py`. (`core/db.py`, `core/models.py`, `core/thin_waist.py`, `pipeline/convergence.py`)

- **converge_discussion string action_items missing assignee**: When action_items are plain strings (not dicts), the loop left `assigned_to=""`. Now extracts `participants` from discussion metadata and rotates through them as fallback assignees. Also adds `"assignee"` to task_meta dict for proper task attribution. (`pipeline/convergence.py`)

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
