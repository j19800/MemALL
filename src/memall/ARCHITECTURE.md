# MemALL Architecture

This document describes the internal architecture of MemALL (v0.1.66). It is intended for
contributors and operators who need to understand how data flows through the system.

## 1. High-Level Layering

```
┌─────────────────────────────────────────────────────────────┐
│  Interfaces (CLI / Python SDK / MCP STDIO / HTTP Gateway)   │
├─────────────────────────────────────────────────────────────┤
│  Application Layer: agent_memory, intelligent_capture,      │
│  bridge, gateway, mcp server + tools                          │
├─────────────────────────────────────────────────────────────┤
│  Core Engine: thin_waist (single write entry) / retrieve      │
│  • lifecycle hooks · project_infer · entity_extractor · nlp   │
├─────────────────────────────────────────────────────────────┤
│  Pipeline: enrich · classify · distill · integrate · decay    │
│  · forget · session · echo · link · entity_extraction …       │
├─────────────────────────────────────────────────────────────┤
│  Storage Layer: SQLite (memories, edges, identities, fts5,    │
│  memory_embeddings, mem_vec) + connection pool                │
└─────────────────────────────────────────────────────────────┘
```

## 2. Core Engine ("Thin Waist")

`core/thin_waist.py` is the single write entry point for **all** memory writes. Every code path
(CLI, MCP tools, SDK, bridge) funnels through `capture(MemoryInput)`. Responsibilities:

- **Project inference** — `core/project_infer.py` infers a non-empty `project` from agent name or
  content keywords (fixes the historical ~77% empty-project defect).
- **Level normalization** — `core/levels.normalize_level()` clamps invalid values to `P2`; never
  lets a write fail on the SQLite `CHECK` constraint.
- **Content-hash dedup** — exact-content deduplication always runs.
- **Lifecycle hooks** — `core/lifecycle.py` dispatches pre/post hooks around capture/store/
  retrieve/search/pipeline/step; plugins and MCP hooks subscribe here.
- **Search** — re-exports `vector_search`, `hybrid_search`, `timeline` from `thin_waist_search.py`
  (isolates heavy deps: onnxruntime/tokenizers/sentence-transformers).

Read path: `retrieve()` supports filtering by owner/agent/category/project/level/subject/date
range, FTS5 full-text, and optional level-boosted ranking.

## 3. Storage Layer (`core/db.py`)

Single SQLite file (`~/.memall/data.db`, WAL journal). Key tables:

| Table | Purpose |
|-------|---------|
| `memories` | Core memory records; bitemporal `valid_from` / `invalid_at`; `CHECK(level IN (...))` |
| `edges` | Typed memory-to-memory relations with weight + bitemporal validity |
| `identities` | Agent registry (agent_name, type, profile_json, status, heartbeat) |
| `memories_fts` | FTS5 virtual table over content/subject/summary |
| `memory_embeddings` | 512-dim float32 vectors per memory |
| `mem_vec` | sqlite-vec virtual table for KNN vector search |
| `suggestions`, `narrative_clusters`, `pipeline_events`, `pipeline_cursors`, … | Pipeline and analytics state |
| `shared_memories`, `conflicts` | Federation shared memory + conflict tracking |
| `entities`, `health_metrics`, `intercept_logs`, `discussions` | Extensibility and observability |

32 schema migrations (`migrations/001`–`032`) evolve the schema incrementally. Migration 028 adds
the level `CHECK` constraint; migration 027 fixes invalid levels and agent name casing.

## 4. Search Stack (`search/`)

Pluggable `SearchProvider` interface (`search/base.py`). Built-in providers:

- `Vec0Provider` — KNN over the `mem_vec` sqlite-vec virtual table (index lives in SQLite, no
  external files).
- `FaissProvider` — FAISS-based provider (registered but heavier).

`search/intent_router.py` classifies each query into `DIRECT_LOOKUP` / `FACT_SEARCH` /
`SEMANTIC_SEARCH` / `HYBRID_SEARCH` and routes to FTS5 / vector / hybrid accordingly:

- Direct lookup: pure numeric ID.
- Fact search: `agent:` prefix, `[Lx]` level filter, short non-CJK queries → FTS5.
- Semantic search: long content (≥14 CJK chars or ≥8 tokens) → vector.
- Hybrid: short CJK queries (≥3 CJK chars) → FTS5 + vector + RRF fusion.

Optional ONNX cross-encoder reranker (bge-reranker-base INT8, SSE4.2, no PyTorch/AVX2 required)
with graceful fallback to raw scoring. Search results are level-weighted (P0–P2 = 1.0 down to
L9–L11 = 0.3) so working memory surfaces before distilled content.

Embeddings default to `BAAI/bge-small-zh-v1.5` (512-dim) via sentence-transformers; a TF-IDF +
TruncatedSVD fallback keeps vector search working without torch. Vector models are persisted as
JSON + `.npy` (not pickle) to avoid deserialization RCE risk.

## 5. Pipeline (`pipeline/`)

Background memory processing driven by `pipeline/pipeline.py`. Each step:

- Declares an idempotency contract (`cursor` / `recompute` / `dedup` / `external`).
- Commits independently after each step — re-running after a crash converges safely.
- Is subject to a quality gate (`QUALITY_GATES`: min input/output counts, coverage gain).

Steps include: `event_processor`, `enrich`, `cleanup`, `classify`, `procedure`, `time_slice`,
`arc_status`, `echo`, `epoch`, `convergence`, `link`, `decay`, `backup`, `session`, `extract`,
`entity_extraction`, `embed_index`, `reflect`, `distill_l7`, `distill`, `integrate`.

Distillation (`pipeline/distill.py`) groups memories by (agent, category) and produces upserted
L9 knowledge rows (one per group), fixing the old unbounded near-identical L9 accumulation.

An async queue (`enqueue_pipeline`) runs pipelines on a daemon thread without blocking callers.

## 6. MCP Layer (`mcp/`)

Model Context Protocol server exposing tools to AI agent hosts.

- `mcp/server.py` — STDIO JSON-RPC server (initialize → tools/list → tools/call), supports
  protocol versions 2024-11-05 through 2025-11-25.
- `mcp/adapter.py` — Tool dispatch with pre/post hooks, metrics, tracing, session-context
  injection, intercept-log injection.
- `mcp/registry.py` — `ToolRegistry` of `ToolDef` entries (name, description, inputSchema,
  handler).
- `mcp/models.py` — Pydantic input models with per-action validation (e.g. `CaptureInput`,
  `RetrieveInput`, `ConnectInput`, `SmartStoreInput`).
- `mcp/validator.py` — Maps `(tool_name, action)` → Pydantic model for strict input validation.
- `mcp/tools/` — Individual tool handlers: capture, retrieve, memory_write, discuss, distill,
  federation, gateway, graph, hub, index, manage, onboarding, persona, pipeline, reflect,
  search, security, session, smart_store, trace, trust, update.
- `mcp/http_transport.py` — MCP over HTTP (SSE + POST).
- `mcp/shared.py` — Session auto-start, pending session notes, intercept logging.

## 7. Federation (`federation/`)

Cross-instance memory sharing and conflict handling.

- `federation/family.py` — Shared "family" database path.
- `federation/conflict.py` — Contradiction detection using keyword pairs (e.g. 采用/放弃,
  支持/反对) + TF-IDF similarity; auto-resolution (newer-wins / older-wins / undecided).
- `federation/health.py` — Health metrics: totals, agent distribution, conflict status, duplicate
  / orphan detection, 7-day trend snapshots.
- `federation/visualize.py` — Federation visualization.

Gateway-layer federation (`gateway_federation.py`, `gateway_peers.py`, `gateway_sync.py`) provides
peer discovery, pairing, federated queries, and export/import bundles.

## 8. Strategy Layer (`strategy/`)

`MemoryStrategy` abstract base (`strategy/base.py`) wraps capture/retrieve with agent-specific
processing: buffer, cross_agent, entity, kg, sharing, summary. Strategies sit on top of the core
and share the same quality gates, dedup, and identity checks.

## 9. Plugins (`plugins/`)

Whitelist-based plugin loader (`plugins/loader.py`). Built-in plugins: dashboard, exporter,
notifier, scheduler. Plugins implement `register()` and optional hook functions
(`on_capture`, `on_retrieve`, `on_pipeline`, …). Hooks are dispatched through
`core/lifecycle.HookRegistry`.

## 10. Lark / Feishu Integration (`lark/`)

Multi-bot bidirectional IM: credentials, client (tenant_access_token + send/reply), webhook
event receiver for @mentions, and `BotPoller` consumer threads started automatically by the MCP
server for every configured agent.

## 11. Scheduler (`scheduler/`)

Background daemon (`scheduler/scheduler.py`, `agent_round.py`) that runs periodic maintenance:
forget, audit, heartbeat, pipeline, doctor, and optional lifecycle/dream passes.

## 12. CLI (`cli/`)

`cli/main.py` builds ~50 argparse subcommands. Command handlers live in `cli/commands/`:
base (init/capture/search/get/connect/traverse/timeline/update/knowledge/insights),
pipeline_commands, federation_commands, gateway_commands, management_commands
(start/stop/status/doctor/migrate/trust/identity/graph-visualize/index/retrieve/onboarding/
serve/db/setup/register/uninstall/backup/restore/export/import/sync/mcp-connect/arcs/hook-list/
hook-register).

Legacy aliases (`add`, `store`, `link`, `history`, `show`, `delete`) redirect to modern commands.

## 13. Security Model

- Cross-agent ownership enforcement (`security.enforce_agent_ownership`, default on): an agent
  may only update memories it owns; supervisors listed in `security.supervisor_agents` are exempt.
- `security.audit_sensitive`, `set_permission`, `check_access`, `security_score` for access
  control auditing.
- Gateway: unauthenticated SPA endpoints are loopback-only; other origins need a bearer token.
- CSRF tokens (`X-MemAll-CSRF`) required for state-changing routes (`/pipeline/run`,
  `/migrations/run`).
- CORS allow-list and rate-limit exemptions for static/health paths.

## 14. Data Flow Summary

1. **Write** → `capture()` → project inference → level normalization → content-hash dedup →
   lifecycle hooks → SQLite `memories` insert → `mem_vec` / FTS5 index update.
2. **Read** → `retrieve()` → filter + FTS5 / vector / hybrid → level-weighted ranking → optional
   ONNX rerank → results.
3. **Pipeline** → async queue → per-step commit → idempotent re-entry → derived tables
   (embeddings, clusters, L9 distillations).
4. **Federation** → publish shared memories → conflict detection → auto-resolution → health
   snapshots.
