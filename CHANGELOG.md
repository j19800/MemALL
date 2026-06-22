# Changelog

## [v0.1.2] - 2026-06-22

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
