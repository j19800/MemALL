# Changelog

All notable changes to MemALL are documented in this file.

## [Unreleased]

### Fixed — MCP flow audit: 16 reported issues + 4 found by the acceptance run
An external review of the MCP layer (`docs/MemALL-MCP流程审查报告-20261020.md`) listed 16 defects. All were addressed; four more surfaced only when the acceptance checklist was actually executed.

- **P1-16 (the only defect actively destroying data).** The keyword classifiers rewrote caller-chosen `category`/`level`: every task written through `capture()` became `fix`/`testing` within 60s, so `notify_pending_tasks()` (which selects `category='task'`) returned zero and the reminder chain died silently; L10 reports were demoted to L6. New `memall/core/pins.py`: `capture()` records the caller's explicit fields in `metadata.pinned_fields`, contract categories (`task`, `discussion`, `discussion_response`, `decision`, `identity`, `rule`) are pinned by definition, and BOTH classifier paths honour it. The report's advice that "a backfill is enough" is wrong — a batch re-scan re-flips corrected rows (observed).
- **P0-1 — the self-evolution steps never ran in a serve-only deployment.** `run_lightweight_pipeline` skips distill/integrate; the only periodic full run lived in the daemon. New `memall/pipeline/fallback.py`: a watchdog in the MCP process that every 5 min checks for work (unconsumed events, distillable L4/L5/L9) and runs distill+integrate only then, refusing when another run is live or recent. The backlog is visible in `action=digest` (`pipeline_backlog`, warns above 500).
- **P0-3 — bare threads per capture.** 100 writes meant 100+ threads each loading the ONNX model and contending for the write lock. `core/bg_registry` is now a bounded pool (3 workers); measured 20 queued tasks → peak 4 threads.
- **`database is locked` under load (found by acceptance #3).** Two causes: the 5s `busy_timeout` was too short (now configurable, 30s), and — the real one — pipeline steps opened DEFERRED transactions (`BEGIN`), whose read→write lock **upgrade** returns `SQLITE_BUSY` immediately and **ignores `busy_timeout` entirely**. All 8 deferred `BEGIN` sites now use `BEGIN IMMEDIATE`. Verified: 200 captures + background pipeline, zero lock errors.
- **The task chain was broken at its last step (found by acceptance #10).** `notify_pending_tasks()` inserted its reminder with `supersedes=None` against a `NOT NULL` column, so the INSERT raised and no reminder was ever created — even with the category correct. Also, tasks created through `capture()` carried no `metadata.status` while the query requires `status='active'`; `capture()` now sets it for L5/task rows. Verified end to end.
- **P1-4 — `access_count` conflated reads with duplicate writes.** A hash hit incremented it (inflating the hot-topic ranking, the decay score and the zero-access health stat) while retrieval never did (only ~21% of the store had ever been read). Retrieval now batches +1; duplicate writes count in the new `memories.dedup_hits` (migration 032).
- **P1-5/P1-6 — search filters were applied in memory and `project` did not exist.** Hybrid cut the pool to 2×top_k and filtered afterwards (a category filter usually left zero rows); `vector_search` could not isolate by project/category/level/agent. Filters are now pushed into SQL, both functions accept `project`/`agent_name`, and the MCP read models declare them — previously the validator stripped them before the handler ran, so the filters never worked at the MCP surface at all.
- **P1-7** `batch_search` called `retrieve()` (plain FTS5) while claiming "unified search"; it now goes through `unified_search(mode='hybrid')`.
- **P1-8** the intent router checked `cjk >= 3 → HYBRID` before `cjk >= 14 → SEMANTIC`, so every Chinese query returned HYBRID and the vector path was dead code. The long-content check now comes first.
- **P0-2** did NOT reproduce as described (the validator strips undeclared fields, so `action=capture` with `dedup_threshold` returns 200, not 500) — but the fields were **silently dropped**. `capture()` now takes only `MemoryInput` fields and reports the rest as `ignored_fields` with a hint.
- **P2-9** the capture-audit hook's matcher was `"capture"` (the core function name) while dispatch passes the MCP tool name, so the audit trail never fired. **P2-10** removed the unreferenced `_READ_ONLY_ACTIONS`. **P2-11** the `hot` branch closes its connection in `finally`. **P2-12** a session note is no longer consumed on read (it waits for an ACK after delivery). **P2-13** the auto-start cooldown persists in `session_cooldowns` (a restart used to reset it, piling duplicate sessions). **P2-14** migration 031 removes test identities using the write deny-list as the criterion (production 106 → 74). **P2-15** `process_events` claims its events before processing, so daemon and MCP cannot double-consume.
- New guards (12; total 38): `caller_pins_classification`, `capture_audit_hook_fires`, `background_threads_bounded`, `fallback_produces_self_evolution`, `busy_timeout_survives_concurrency`, `search_filters_pushed_to_sql`, `intent_reaches_semantic`, `access_count_means_reads`, `batch_search_is_semantic`, `capture_ignores_foreign_fields`, `session_cooldown_persisted`, `session_note_acknowledged`.

### Added — human-readable titles enforced at the single write funnel
An audit of the production store (3804 live rows) found **469 memories whose subject was machine scaffolding** — `[NOVEL:..][FILE:..][CHUNK:..]` writer markers (171), embedded newlines (194), fixed English templates `Auto-summary:`/`Global:`/`Domain pattern:`/`lesson:` (142) — plus 115 rows whose whole body was a raw `json.dumps` report and 6 legacy rows titled with a truncated tool response. Titles are what humans and agents re-read from every search result, so enforcement went to `capture()`, the single write funnel, and the existing store was repaired.

- `sanitize_subject()` (new): strips template marker spans, collapses newlines/tabs, drops markdown headers, keeps our canonical bracket labels (`[L6 反思]`, `[L10 全局知识]` — matched by L/P pattern, not an exact set), removes unbalanced stray brackets, caps length with an ellipsis. Edge trim is deliberately asymmetric: leading brackets/punctuation strip, trailing only sentential punctuation (a closing `）` may legitimately close a pair).
- `capture()` now **sanitises caller-supplied subjects** — every audited violation arrived as an external subject that the generation path never saw — and falls back to `_make_subject`.
- `_make_subject` adopts a canonical label already present in the content as the prefix instead of double-prefixing with the inferred level (the label in the text is the more truthful type when levels drift on reclassification); cuts long first lines at their line end so newlines never leak; renders a JSON/array **body** into a human line (`来自 marvis 的消息：topic（致 opencode）`, `身份：name（role）`, the inner text of MCP tool dumps) while leaving the body untouched for consumers that parse it.
- Quality gate: clarity ≤5 when the body carries template markers, ≤4 for a whole-JSON body, ≤3 when the raw caller subject is machine-looking — scored on the **pre-sanitisation** value (sanitising first would hide the violation; `capture` passes `caller_subject` through).
- Producers fixed: the **observation step's** L6 memory (largest machine-content source) is now prose with the machine report in `metadata.report`; cross-agent L10/L11 titles became `[L10 整合] 跨 Agent 全局模式：X（N 条教训聚合）` / `[L11 领域] …`; `Auto-summary:` → `[L9 摘要] …`; `lesson:` → `教训：…`.
- Existing store repaired (`repair_readable_titles.py`, verified after: 0 marker / 0 newline / 0 English-template subjects, 0 raw-JSON observation bodies; backup `data_pre_titles.db` taken first).
- New guard `subjects_human_readable`; **26/26 guards + 4 suites**, three consecutive clean rounds.
- Fixed a long-standing harness flake family while there: `get_conn`/`ConnectionPool` gated init on a process-global flag, so the first connection opened after `DB_PATH` moved to a fresh file could skip initialisation ("no such table"); init is now checked per actual file. Background threads spawned by capture are registered in the new `core/bg_registry` — suites disable spawning (their transactions raced assertions: the "discussion stance auto-captured 0→0" flake), guard harnesses drain them before switching databases.

### Fixed — deep live testing of every MCP feature (turn 74/75)
All of the following were found by **driving the real MCP protocol** (client tool calls through the long-lived server), which the in-process suites structurally cannot reach.

- **`convergence` step crashed** with `'sqlite3.Row' object has no attribute 'get'` — three sites: `_converge_single`, `plugins/scheduler` capture-triggered convergence (the path every capture actually exercises), and a Row list reaching `converge_discussion` despite its dict contract. Rows are now materialised at every fetch, and `converge_discussion` normalises at its boundary so no caller can trip it.
- **`distill`/`integrate`/`convergence` aborted with `UNIQUE constraint failed: memories.content_hash`** — upsert UPDATEs rewrote the UNIQUE hash without checking it was already held by another row; `integrate` used a plain INSERT after a separate existence check (race window); and `convergence`'s plain INSERTs aborted the transaction that *included* the `status=converged` update, so discussions stayed active and collided again every run, forever. All inserts are now idempotent (`_insert_or_reuse_id`, `INSERT OR IGNORE`, pre-checked UPDATE targets).
- **Archived (soft-deleted) rows were re-processed by 14 pipeline steps** — content-scanning queries lacked `memory_status != 'archived'`, so deleted memories were distilled/promoted/enriched/linked, and denied-identity writes derived from them failed whole steps. Canonical predicate lives in `pipeline/sql_fragments.py`; `dormant`/`superseded`/`conflict`/`epoch` remain eligible (live states).
- **`memall serve --watch`** added — the stdio server is long-lived, so code changes did not take effect until the client respawned it, and fixes *appeared* broken. The watcher restarts the server child on any mtime change in the `memall` package.
- **FTS retrieve() ranked by recency, not relevance.** Two layers: the original `ORDER BY occurred_at DESC` ignored match quality entirely; and switching to `bm25()` was a silent no-op with the `id IN (SELECT rowid FROM memories_fts WHERE MATCH ?)` form — bm25() evaluates to constant 0 there (verified). The predicate is rewritten to its direct `FROM memories_fts JOIN memories ... WHERE memories_fts MATCH ?` form. The first results of a phrase query are now the rows containing the phrase, not recent noise containing one bigram.
- **Facet counts described the wrong population.** `enhanced_search` applied `agent_name_filter` to results but built the facet WHERE from only `viewer` — a 4-result page came back with "L9: 259" (the whole store). Both paths now share one resolved filter set.
- **Write deny-list was bypassable by its target identities.** `normalize_agent_name()` redirects 10+-digit names (`probe123456789012`) to `system`, and the gate ran *after* normalisation, so `probe*` never matched. One production row leaked (archived). The gate now checks the raw identity first.
- **`hot` action was dead code**: the cutoff variable was named `week_ago` but set to `now()`, so `created_at > now` matched nothing — `hot_topics` always returned `[]` in production. Fixed and now excludes archived rows.
- **digest included archived rows** — same class as the pipeline bug; planning surfaces must not show deleted history.
- **fts5 enhancement inertness**: `enhanced_search` carried `rrf_score=None` into the enhancement dicts, so personalisation/context-boost computed `0.0 × anything = 0.0` on that path. Position is now carried as an RRF-form score.
- A deny-list/quality-gate refusal in `distill_l7` is now a per-item skip, not a step failure.

### Fixed — performance & recall evaluation (bench_latency.py / bench_recall.py)
The two committed benchmarks drive the real `handle_call` MCP path against production and surfaced four more defects:

- **`fts_query()` raised `sqlite3.OperationalError` on ~5% of real content slices** — pasted code/JSON (quotes, braces) parses as FTS5 MATCH syntax. Emitted strings now escape inner quotes (FTS5 doubling) and map braces to spaces *inside* the outer phrase, preserving adjacency semantics (the first fix attempt degraded phrases to implicit-AND: 394 vs 284 candidates — caught by the benchmark, fixed, and now guarded).
- **Archived rows leaked into live retrieval** — the shared WHERE builder had no status predicate, so plain fts5/direct returned soft-deleted rows while hybrid/vector hid them (an archived-test-row sweep left "Promotion test" answering with three archived rows as its top-3). Archived excluded by default; `include_archived=True` for deliberate history reads.
- **The ANN index ignored soft-delete entirely** — 910/4447 vectors (20%) described archived rows; vec0 cannot see `memory_status`. Three layers: `batch_archive` purges vectors of archived rows, `_vec0_knn` (the single funnel all four vector readers share) filters archived rowids with 4× over-fetch, `batch_restore` re-embeds on restore. Production purged (4447 → 3537, all live-backed).
- **Two new guards (25 total):** `fts_query_survives_syntax`, `retrieve_excludes_archived`.

Measured (3802 live memories): latency fts5 7ms warm / hybrid 102ms / unified 109ms p95 (cold first-call 563ms = model load). Recall@10 (72 stratified rows × 3 query kinds × 3 modes): exact-phrase fts5 58–76%, subject-head 51–53%, short-paraphrase 18–19%; vector mode strictly weaker than FTS on every query kind (backend verified `bge-onnx-sse42`); hybrid lands between. The FTS candidate-rate probe separates index loss from ranking loss: at 64–82% candidate rate the residual recall gap is tokenisation (unicode61 whole-word ASCII tokens vs the CJK single-char scheme — long ASCII runs like "memall serve" in subjects are not phrase-reachable), not ranking.

### Added
- Four new regression guards (23 total): `denylist_before_normalize`, `search_ranks_by_bm25`, `facets_match_population`, `hot_topics_functional` (drives the real `handle_call` MCP path), plus `digest_excludes_archived`.
- `check_fixes.py` closes the previous connection pool before switching databases — dropping the reference left open handles on the production file and made the next guard fail with "database is locked".

### Added — proactive-memory improvements ported from dsh-meow-memory
A deep review of [dsh-meow-memory](https://github.com/Phant0Meow/dsh-meow-memory) (a cross-session memory plugin for DeepSeek Harness) produced a prioritised list of design ideas; the P0/P1/P2 items were implemented here, adapted to MemALL's MCP-server architecture (the code itself is TypeScript/DSH-plugin and not reusable — these are ported *principles*).

- **Tags activated (P0.1).** `memories.tags` existed in the schema and was writable via `ops:tag`, but was never FTS-indexed and never read — 0 of ~4.6k production rows carried a tag. Now a memory is findable by what it is *about*, not only by words that happen to appear in its text:
  - `memall/core/tags.py` — `normalize_tags`, `tags_to_text` (FTS flattening), `extract_auto_tags` (entities + identifiers + jieba/bigram CJK terms), `merge_tags`, `tag_filter_sql` (exact `json_each` match).
  - `memories_fts` gains a `tags` column; sync triggers + backfill/rebuild index `memall_split_cjk(memall_tags_text(tags))`. Migration `030_add_fts_tags_column.py` recreates the index for existing DBs.
  - `capture()` auto-derives tags when the caller supplied none (`capture.auto_tags_enabled`); explicit tags always win.
  - `retrieve()`/`enhanced_search()` gained `tags=[...]` + `tags_all` exact filters (in the shared WHERE builder, so facets honour them); exposed on `memall_read`.
  - `db:backfill_tags` ops action retro-fits tags onto historical untagged rows (applied to production: 4572 rows tagged).
  - Fixed a latent conflict: migration 029 re-created a stale 3-column trigger set that clobbered 030's 4-column triggers on fresh DBs; 029 is now a point-in-time repair that no-ops when the `tags` column already exists.
- **Write deny-list (P0.2).** `security.disabled_projects` / `disabled_agents`: `capture()` rejects writes from disabled identities (mirrors meow-memory's `disabledWorkspaces`); `agent_round` skips them. Reads stay available — hiding stored history is a different concern from refusing to store it.
- **Session seen-tracking (P0.3).** `memall/search/seen.py`: repeat queries within a session demote already-returned results below a protected top slice so novelty surfaces. The critical rule is inherited from meow-memory: seen items are **demoted, never excluded** — hard exclusion makes search drift further off with every query. `unified_search` over-fetches so the reorder has material. In-process bounded LRU with TTL; off by default; needs `session_id`.
- **Config hot-reload (P0.4).** The MCP server is long-lived and silently served stale config until restart. `get_config()` re-stats the `~/.memall` config files at most every 2s and rebuilds transparently on change; `reload_config()` for immediate effect. Also fixed `load_json_config`/`load_yaml_config` docstrings that promised CWD resolution they never did (behaviour kept home-only; the reload tracker mirrors exactly what the loaders read, or it would reload-loop).
- **KV-cache section order (P1.5).** `injection_formatted` now emits stable sections (PROFILE/CORRECTIONS/LESSONS/WORKFLOW/BEHAVIOR/OVERVIEW/DOMAIN) before volatile ones (DECISIONS/TODO/DISTILL/CONTINUITY/NARRATIVE). Previously the per-session `[NARRATIVE]` greeting sat at position 1 and cache-busted every stable section behind it.
- **Pipeline failures are no longer silent (P1.6).** `run_pipeline` returned `status:"ok"` even with failed steps (meow-memory #41 lesson). A run with failed steps is now `status:"partial"` with `failed_steps` listed; quality-gated steps reported separately as `gated_steps`. `_finalize_pipeline_run` persists `partial`; the notifier and lightweight-pipeline consumer surface it.
- **Idle-session sweep (P1.7).** `idle_session_sweep()` ends + harvests sessions whose newest memory is older than `scheduler.session_idle_minutes` (default 120), so an abandoned session stops re-harvesting an ever-growing window. Runs on the `agent_round` cadence.
- **Injection-label localisation (P1.8).** `memall/i18n.py` (zh source-of-truth + en packs, `L(key, **fmt)`); the session-injection labels and greeting phrases — the text an agent re-reads every session — now follow `prompt.lang` (default zh). Scoped to labels; full prompt bodies intentionally left in place.
- **Post-compaction recap (P1.10).** `memall_read action=recap`: a MemALL server cannot see the host's compaction event, so this is the pull surface — one cheap read returns the last session summary + recent L4/L5 decisions/tasks.
- **Per-bug regression guards (P2.11).** `check_fixes.py` — one named guard per bug that was ever fixed silently (FTS5 CJK, dead auto-capture, classify demotion, English-L6 rejection, feedback loops, hash-hit promotion, MCP contract fields, config hot-reload, seen-never-drops, pipeline-partial, deny-list), runnable in one command. 14 guards + 4 suites.
- **First-create notice (P2.13).** `session_start` on an empty store returns `first_create_notice` with the DB path, an unrecoverable-deletion warning, and the backup/doctor safety nets.

### Changed
- **Identity redirects are now logged (P2.12).** `normalize_agent_name()` maps any name containing a date or 10+ consecutive digits to `"system"` (a guard against parse artifacts). That redirect was silent — memories landed under the wrong agent and every later agent-scoped query missed them. Now warns once per distinct input, naming the reason.
- P2.14 timer audit: all 16 `threading.Thread` sites in MemALL are `daemon=True`, so meow-memory's #60 class (a non-unref'd timer keeping the host process alive) cannot occur — documented, no change.

### Added
- **Auto-capture is now wired up and functional.** `memall/auto_capture.py` registered its `post_tool_use` hook at module import, but no module ever imported it — the feature had never run in production. `mcp/adapter.py` now imports it.
- **Auto-capture rewritten around explicit extractors.** The old condition read `arguments["content"]`, which only exists on capture-family calls — the same calls it then skipped, so it could never fire. Extraction now keys off `(tool, action)` with one extractor per source, reading `arguments` and `result` as appropriate:
  - `memall_persona/ask` → `L4/decision` (question + reasoned answer)
  - `memall_system/reflect` → `L6/reflection` (stance + reasoning on a lesson)
  - `memall_discussion/respond` → `L4/discussion` (stance + argument)
  Pure reads and the capture family are deliberately excluded.
- Auto-capture de-duplication: a per-process LRU keyed on `(action, target id, content prefix)` stops the same conclusion being re-stored on every repeat call. `capture()`'s hash dedup cannot cover this because auto-captured text varies in wording.
- `scheduler.proxy_session_interval` config key (default 300s). `scheduler.py` had always read this key, but it was absent from `_DEFAULT_CONFIG`, so the `agent_round` cadence could only be changed by editing code.
- `test_search_features.py` coverage extended; `test_auto_capture.py` (19 assertions) covers the auto-capture sources, the de-duplication, the feedback-loop guards, and the hash-hit promotion semantics.

### Changed
- **Hash-hit re-capture now promotes instead of discarding intent.** `_capture_dedup_check` used to only bump `access_count` on a content-hash hit, silently swallowing the caller's level: re-capturing the same text at a higher level left the row unchanged with no signal. It now applies a level change using `_should_promote_level()`, which treats a band change (priority `P0/P1/P2` ↔ lifecycle `L1..L11`) as a deliberate re-classification and otherwise only moves toward the more significant end. It also fills `category`/`subject`/`project`/`summary` when they were empty.
- `_PENDING_SESSION_NOTE` (a single module-level string) became `_PENDING_SESSION_NOTES`, a per-agent dict, and `consume_session_note()` takes the agent name. A second agent starting a session in the same process used to overwrite the first agent's pending context, which could deliver one agent's injected memories to another.
- `auto_inject()` de-duplicated two pairs of redundant queries: the L5 `pending_tasks` and `l5_active_global` sections now derive from one query, as do the L4 `decision_arcs` and `l4_recent_global` sections. 16 serial queries per `session_start` became 14 with identical output shapes.
- `mcp/tools/retrieve.py` pre-checks `query`/`content` by name for the high-frequency search handlers.
- `ReflectInteractMCPInput` now declares `agent_name`. The validator's `model_dump(exclude_unset=True)` was dropping it, so every reflect-derived memory was attributed to `system` regardless of the caller.

### Fixed
- **Pipeline: `convergence` step crashed** with `'sqlite3.Row' object has no attribute 'get'`. `_converge_single()` read response rows and called `.get()` on them, but `sqlite3.Row` has no `.get()` — the rows were only converted to dicts further down, on the path that was never reached. The failure only surfaced when a discussion actually converged, which is why it survived in production.
- **Pipeline: `distill` / `integrate` aborted** with `UNIQUE constraint failed: memories.content_hash`. Three separate causes:
  1. The upsert `UPDATE` rewrites `content_hash` (UNIQUE across the table) and aborted when another row already held that hash. The target hash is now checked first and the code falls through to the insert path instead of raising.
  2. `integrate` used a plain `INSERT INTO` for the L10 row while the "does this hash exist?" check was a separate statement — a concurrent writer could take the hash in between. Now `INSERT OR IGNORE` + reuse of the existing row id.
  3. `convergence` inserted its L4 decision, L5 tasks and response memories with a plain `INSERT INTO`; a repeat (same discussion converged twice, same agent restating a position) aborted the transaction, which rolled back the `status = converged` update — so the discussion stayed active and the next pipeline run collided again, forever. All inserts are now idempotent via a shared `_insert_or_reuse_id()` helper, and the edge inserts use `INSERT OR IGNORE`.
- **Pipeline steps re-processed archived (soft-deleted) memories.** `memory_status='archived'` means deleted, but the content-scanning queries of 14 steps did not filter it — so archived rows were distilled, promoted, enriched, linked and classified as if live, and when such a row belonged to a denied identity the resulting write was refused, failing the step (`distill_l7: capture: agent 'autocap...' is disabled`). The scanning steps (distill, distill_l7, integrate, reflect, promote, enrich, classify, link, arc_status, observe, cross_agent, entity_extraction, identity) now exclude archived rows; the canonical predicate lives in `pipeline/sql_fragments.py`. Note that `dormant` / `superseded` / `conflict` / `epoch` are *live* states and remain eligible.
- `plugins/scheduler.py` called `.get()` on two `sqlite3.Row` values (the discussion row and the supersedes row) in the discussion-convergence path — the same crash class as the convergence step, on a path that only runs when a discussion converges.
- A write refused by the deny-list or the quality gate is now a per-item outcome, not a step failure: `distill_l7` catches the `ValueError` and skips that lesson instead of aborting the step.
- **`classify` silently demoted caller-assigned levels.** `_detect_layers()` accepted a `current_level` argument and never used it, and its fallback for unrecognised text was `P2`. Any explicitly-written `L4`/`L5`/`L6`/`L7`/`L8`/`L11` memory whose text matched no marker pattern was rewritten to `P2` by the capture-triggered lightweight pipeline. The fallback now preserves a non-default `current_level`; only `L9`/`L10` remain hard-protected as before.
- **`_inline_classify` overwrote category with `"general"`.** `best_cat` defaulted to `"general"` and was written unconditionally, so an unmatched memory lost its caller-assigned category (e.g. `reflection` → `general`) on every pass. It is now `None` until a rule matches, and only changed columns are written.
- **English L6 reflections were always rejected.** `_REASONING_MARKERS` held only Chinese terms and the L6 gate hard-rejects `reasoning < 2`, so any English reflection scored 0 and was refused by the quality gate. Added 27 English equivalents; verified the Chinese side is unchanged (good content still accepted, junk still rejected).
- **`ask` reinforced its own output.** Every answer is auto-captured as `L4`, and `_search_memories()` had no source filter — so each stance became "evidence" for the next question on the same topic, drifting toward its own earlier answer. The evidence pool now excludes `metadata.source = "ask"`.
- **`distill` could promote the system's own speculation into `L9`.** Auto-captured `ask`/`reflect`/`discussion` rows sit at `L4`/`L5`, which the distiller's level filter admits. The distill source query now excludes all three markers, so only genuinely authored memories are distilled.

### Notes
- `normalize_agent_name()` maps any name containing a date or 10+ consecutive digits to `system` (a guard against parse artifacts like `claude-2026-01-01`). This is pre-existing and intentional, but it means a timestamp-suffixed agent name is silently redirected — worth knowing when writing tests or naming agents.

### Added
- `pipeline/sql_fragments.py` — the canonical "not archived" SQL predicate, so the many pipeline queries share one definition instead of each spelling it slightly differently.
- Two new regression guards: `no_row_get_hazards` (static scan of the pipeline package for `.get()` on a fetched row, scoped per function) and `pipeline_ignores_archived` (asserts every content-scanning step filters archived rows).

### Changed
- `check_fixes.py` now closes the previous connection pool before switching databases, so a guard cannot leave an open handle on the production file (which made the *next* guard fail with "database is locked" — a cross-guard artifact that looked like a product bug).

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
