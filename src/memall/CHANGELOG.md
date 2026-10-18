# Changelog

All notable changes to MemALL are documented in this file.

## [Unreleased]

### Added
- Added `quality.thresholds` and `quality.gate_policy` configuration options to `memall/config.py` to make the thin_waist quality gate fully configurable
- Added `quality.min_length` configuration option to set minimum content length for memories
- Added `unified_search` action to `memall_read` tool that routes between FTS5, vector, and hybrid search based on mode or intent detection
- Added contextual recall hook in thin_waist to surface related high-level memories after capture
- Added `contextual_recall_enabled` configuration option to enable/disable contextual recall feature
- Added semantic similarity fallback to thread ID inference (M2) when temporal window yields no candidate
- Added `source_ids` field to Memory and MemoryInput models for provenance tracking (M3)
- Added heat-based level promotion pipeline step (M4) that promotes frequently accessed memories to higher levels
- Added `promotion` configuration section with access thresholds, minimum age, and reference considerations

### Changed
- Increased level weights for L9-L11 in search ranking from 0.3 to 0.7 to surface distilled knowledge more prominently
- Made quality scoring thresholds configurable instead of hardcoded values
- Added `unified_search` action to the memall_read tool input schema and handler
- Updated thin_waist.py to dispatch the unified_search function for unified search entrance
- Enhanced quality scoring to use configurable thresholds and gate policy
- Added minimum content length check based on quality.min_length setting
- Enhanced thread ID inference to include semantic similarity fallback after temporal window check
- Updated Memory and MemoryInput models to include source_ids field for tracking provenance
- Added promote_step to pipeline execution sequence

### Fixed
- Fixed missing `BatchSearchInput` import in `memall/mcp/validator.py`
- Fixed `AttributeError: module 'memall.mcp.tools.retrieve' has no attribute 'handle_batch_search'` by implementing the handler and wiring it properly




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
