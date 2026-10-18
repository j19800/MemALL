# Changelog

All notable changes to MemALL are documented in this file.

## [Unreleased]

### Added
- `mcp/models.py`: Added BatchCaptureInput and BatchSearchInput models for batch memory operations
- `mcp/validator.py`: Registered batch_capture and batch_search actions in _ACTION_MODELS
- `mcp/tools/memory_write.py`: Added handle_batch_capture function for efficient multiple memory capture
- `mcp/tools/retrieve.py`: Added handle_batch_search function for efficient multiple query search
- `mcp/tools/__init__.py`: Updated memall_read and memall_write tool definitions to include batch operations

### Fixed
- `mcp/models.py`: `CaptureInput.level`, `SmartStoreInput.level`, and `UpdateInput.level` patterns now accept `L11` (previously rejected), matching the CLI choices and SQLite `CHECK` constraint (`P0/P1/P2` + `L1–L11`).
- `config.py`: default `search.provider` changed from `"tfidf"` (not registered in `search/registry.py`, which caused `get_provider()` to return `None`) to `"vec0"` (a registered, working provider).
- `core/db.py`: Changed FTS5 tokenizer to `unicode61` to improve Chinese full-text search recall.


## [0.1.66] — 2026-10-17

### Changed
- Version bumped to 0.1.66.

### Fixed
- `core/levels.py`: centralized single source of truth for level enum (P0/P1/P2 + L1–L11) with alias mapping and clamp-to-default, eliminating divergent level vocabularies across producers.
- `core/project_infer.py`: centralized project inference so every write path (capture/smart_store/store_batch/MCP tools) populates a non-empty `project`, fixing the historical ~77% empty-project defect.
- `mcp/models.py`: `AskInput` explicitly declares `subject` field (Bug-1 fix); `agent_name` retained as backward-compatible alias.
- `core/thin_waist.py`: `_ALLOWED_UPDATE_FIELDS` whitelist prevents column-injection and ownership hijacking via `UPDATE`.
- `mcp/tools/memory_write.py`: cross-agent ownership enforcement on update (default on; supervisor agents exempt via `security.supervisor_agents`).
- `core/db.py`: Windows DB path prefers non-system drives (D–H) to avoid filling `C:`.
- `search/intent_router.py`: query intent classification routes FTS5 / vector / hybrid by CJK token count and agent/level prefixes.
- `graph/retrieve.py`: level-weighted search ranking (P0–P2 = 1.0 → L9–L11 = 0.3).
- `graph/embeddings.py`: sentence-transformers import probed with timeout to avoid hanging on Windows torch init; TF-IDF/SVD fallback.
- `search/vector_model.py`: vector model persisted as JSON + `.npy` instead of pickle (RCE mitigation).
- `pipeline/pipeline.py`: per-step idempotency contracts (`cursor`/`recompute`/`dedup`/`external`) and quality gates; async pipeline queue.
- `pipeline/distill.py`: upsert-based L9 distillation (one per agent+category), fixing unbounded near-identical L9 accumulation.
- `intelligent_capture.py`: intent-based auto capture (decision→L4, lesson→L6, preference→L7, plan→L5, identity→L1, knowledge→L9, fact→P2) with subject generation and quality-gate fallback to P2.
- `migrations/`: 32 schema migrations covering identities, memories confidence/visibility/tags/stream/creator, edges bitemporal fields, embeddings, discussions, entities, level CHECK constraint, temporal edge fields, pipeline run contract.
