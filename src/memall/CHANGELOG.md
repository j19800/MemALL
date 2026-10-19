# Changelog

All notable changes to MemALL are documented in this file.

## [Unreleased]

### Added
- **Query enhancement layer** (`memall/search/enhancements.py`) implementing the six previously-unimplemented retrieval features:
  - **Faceted search** — `compute_facets()` aggregates counts over the unlimited candidate set. Column names validated against a hard whitelist (`level`, `category`, `project`, `agent_name`, `owner`, `stream`, `visibility`, `memory_status`, `confidence`); unknown fields are dropped rather than interpolated into SQL.
  - **Query expansion** — `expand_query()` blends a synonym dictionary (`search.query_expansion.synonyms` config or `~/.memall/synonyms.json`) with recent query-history terms; OR-only so recall can never drop.
  - **Query suggestions** — `suggest_queries()` returns completions from `memories.subject` and query history.
  - **Result clustering** — `cluster_results()` groups results by topic with deterministic (seeded) spherical k-means over TF-IDF + CJK bigrams. Never drops results; singletons fold into an "其他" cluster.
  - **Explainability** — `explain_result()` reports matched terms, per-engine ranks, numeric score contributions and boosts; `highlight_matches()` returns content snippets around matches.
  - **Personalised ranking** — `build_affinity()` / `apply_personalization()` learn a per-memory affinity from the new `search_interactions` table (click/dwell/copy/impression/skip) and blend it into the base score.
  - **Context-aware boosting** — `build_context_profile()` / `apply_context_boost()` infer the agent's active project/category from recent interactions and boost matching results.
- `search_interactions` table in `core/db.py` for interaction telemetry (clicks, dwells, impressions, copies, skips) feeding personalisation and context profiles.
- `enhanced_search()` in `core/thin_waist.py` — retrieval entry point that applies the enhancement layer while preserving `retrieve()`'s exact filter semantics (shared `_build_retrieve_where()`).
- `record_interaction` action on the **`memall_write`** tool (it writes a row, so it must not sit behind `memall_read`'s `readOnlyHint`), with `RecordInteractionInput` validator model.
- Enhancement arguments on `UnifiedSearchInput` / `RetrieveInput` and the `memall_read` input schema: `facet_fields`, `facet_fields_str`, `expand_query`, `suggest`, `explain`, `cluster`, `personalize`, `context_boost`, `agent_name`, `viewer`.
- `memall_split_cjk()` SQL function (registered by `core/db.py`) that inserts spaces between CJK characters so FTS5's `unicode61` tokenizer emits one token per character.
- Migration `029_reindex_fts_cjk.py` — rewrites the FTS5 sync triggers to call `memall_split_cjk()` and reindexes existing rows.
- Config sections: `search.query_expansion`, `search.facets`, `search.explain`, `search.clustering`, `search.personalization`, `search.context_boost` — all **off by default**.
- `test_search_features.py` — 34 assertions covering all six features, backward compatibility, SQL-injection resistance of facet fields, and the MCP surface.
- **Per-action sub-action fields** on `memall_system` and `memall_write`. A single shared `sub_action` enum used to mix seven different vocabularies, so almost every declared value was invalid for almost every action. Each action now has its own field (`distill_sub_action`, `gateway_sub_action`, `db_sub_action`, `security_sub_action`, `adaptive_sub_action`, `onboarding_sub_action`, `reflect_sub_action`, `forget_sub_action`, `ops_sub_action`), and `sub_action` remains as a deprecated alias.
- `gateway status` sub-action — read-only view of the in-process gateway (running flag, port, peer count) so callers no longer have to guess before start/stop.
- `_humanize_validation_error()` in `mcp/validator.py` — rewrites Pydantic's raw output into one actionable sentence (`field: what's wrong (got 'x') [expected: ...]`).
- Unknown-tool detection in `mcp/adapter.py` — lists the valid tool names instead of the validator's misleading "unknown action … Valid actions: (empty)".

### Changed
- `unified_search()` routes `direct`/`fts5` requests through `enhanced_search()` when any enhancement flag is set, and applies post-retrieval enhancements to `vector`/`hybrid` results via `_apply_enhancements_inplace()`. Plain (no-flag) calls keep the previous behaviour byte-for-byte.
- `handle_retrieve()` in `mcp/tools/retrieve.py` routes to `enhanced_search()` when enhancement arguments are present; `facet_fields_str` accepts comma-separated fields for clients that cannot send arrays.
- `fts_query()` now emits CJK queries as space-separated single-character phrases (`"检索"` → `"检 索"`) to match the new index tokenisation, with adjacent 2-character sub-phrases OR-ed in for recall on runs of 3+ characters.
- `hybrid_search()` now forwards `viewer` to trust filtering (previously the argument was accepted but unused by `unified_search`).
- `_backfill_fts_if_empty()` and `rebuild_fts()` tokenise through `memall_split_cjk()` instead of copying columns verbatim.
- **Empty-identity policy is now uniform** across the persona family: `agent_name` is optional and falls back to `identity.human_owner` (the same invariant the write path uses). Previously `persona`/`persona_profile`/`identity` raised a validation error on an empty name while `ask`/`foresight`/`fed_query` silently queried "all unowned memories" — callers could not predict which would happen.
- `handle_forget` / `handle_ops` / `handle_db` / `handle_security` / `handle_adaptive` / `handle_gateway` / `handle_onboarding` / `handle_reflect` now read their canonical `<action>_sub_action` field first, then fall back to the deprecated `sub_action` alias.

### Fixed
- **Root cause: FTS5 CJK search returned zero results on freshly-created databases.** The sync triggers inserted `new.content` verbatim; `unicode61` treats a contiguous CJK run as one token, so a query for `检索` matched nothing inside longer prose. Production databases happened to work (built by an older SQLite with a CJK tokenizer) which masked the defect. Fixed by tokenising through `memall_split_cjk()` on both the write and query side, plus migration 029 for existing databases.
- FTS5 sync triggers were silently missing after the table-rebuild migrations (021/028 `DROP TABLE memories`), leaving `memories_fts` empty on fresh databases. `init_db()` now re-asserts the triggers *after* migrating and backfills the index.
- `_register_sql_functions()` is now called on every connection (`get_conn`, `ConnectionPool._new_conn`) and before each migration script, so `memall_split_cjk` is always available where the triggers need it.
- `explain_result()` crashed with `TypeError` when slicing `dict.fromkeys(...)`; now converts to a list first.
- `RecordInteractionInput.action` rejected the MCP routing value (`"record_interaction"`) because it was validated against the interaction-type vocabulary; the interaction kind now lives in `interaction_type`, with `action` accepted as a legacy alias.
- **`memall_read` was annotated `readOnlyHint: true` but wrote to the database** via `record_interaction`. MCP clients use that hint to decide on parallelism, caching and confirmation, so the annotation was actively wrong. The action moved to `memall_write`.
- **Dead schema contracts:** `memall_write`'s `sub_action` enum advertised `stats` for `ops`, but `OpsInput`'s pattern rejected it — an LLM reading the schema could never make a valid call. The enum and the pattern are now derived from the same per-action vocabulary.
- `handle_ops`' "missing sub_action" error named the wrong field (`sub_action` instead of `ops_sub_action`); same class of fix for `handle_db`, `handle_security`, `handle_adaptive`, `handle_gateway`, `handle_onboarding`, `handle_reflect`.
- `PersonaInput` / `PersonaProfileInput` / `IdentityInput` required `min_length=1` on `agent_name` while the sibling actions accepted an empty value; all now share the documented fallback.
- `handle_identity` returned a bare `{"error": "not found"}`; it now includes a hint about how the identity record gets created.
- `handle_vector_search` / `handle_hybrid_search` / `handle_unified_search` / `handle_smart_store` accepted whitespace-only `query`/`content`; they now pre-check and report the field by name.

### Removed
- `_split_cjk` in `core/thin_waist.py` was dead code (defined, never called). Its logic now lives in `core/db.py:memall_split_cjk` where it is actually wired into the index.
- The flat 36-value `sub_action` enum on `memall_system` (replaced by per-action fields). It mixed seven vocabularies, so every value was invalid for six of the seven actions.






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
