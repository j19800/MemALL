# MemALL

MemALL is a sophisticated memory management system designed for AI agents, providing long-term memory storage, retrieval, and intelligent processing capabilities.

## Features

- **Hierarchical Memory Organization**: Memories are organized into levels (P0-P2, L1-L11) representing different types of knowledge from raw data to distilled wisdom
- **Intelligent Capture**: Automatic quality scoring, subject generation, and level assignment based on content analysis
- **Advanced Search**: 
  - FTS5 full-text search with unicode61 tokenizer for improved Chinese recall
  - Vector search using BGE embeddings
  - Hybrid search combining FTS5 and vector results
  - Unified search entrance that auto-routes between search modes based on query intent
  - **Intent tags**: auto-derived keywords (entities + identifiers + CJK terms) indexed as their own FTS column, so a memory is findable by what it is *about*, not only by words in its text; exact-match `tags` filter for narrowing
  - **Query enhancement layer**: faceted counts, synonym/history expansion, suggestions, topic clustering, score explanations, personalised ranking, context boosting (all opt-in)
  - **Session novelty**: repeat queries within a session demote already-seen results (never exclude) so new material surfaces
- **Configurable Quality Gates**: Adjustable quality thresholds and gate policies (strict/lenient) per memory level
- **Contextual Recall**: Automatically surfaces related high-level memories after capturing new content
- **Batch Operations**: Efficient batch memory capture and search
- **Thread-aware Operations**: Groups related memories into conversation threads for improved contextual understanding
- **Semantic Thread Fallback**: When temporal window yields no thread candidates, uses semantic similarity to find related memories (M2)
- **Provenance Tracking**: Source IDs track which memories contributed to distilled knowledge (M3)
- **Heat-based Level Promotion**: Automatically promotes frequently accessed memories to higher knowledge levels (M4)
- **MCP Server**: Full Model Context Protocol implementation for seamless integration with AI agents
- **Lifecycle Management**: Automated distillation, forgetting, and maintenance operations
- **Security & Access Control**: Agent-based ownership validation and visibility controls
- **Write Deny-list**: `security.disabled_projects` / `disabled_agents` reject captures from test/audit/one-off identities before they pollute the store (reads stay available)
- **Localisable Injection**: session-injection labels follow `prompt.lang` (`zh` / `en`)
- **Extensible Architecture**: Plugin system and lifecycle hooks for customization

## Installation

```bash
# Clone the repository
git clone https://github.com/j19800/MemALL.git
cd MemALL

# Install dependencies
pip install -e .

# Initialize the database
memall init
```

## Configuration

MemALL supports configuration through:
- JSON config file (`config.json`)
- YAML config file (`memall.yaml`) 
- Environment variables (prefix `MEMALL_`)

### Quality Gate Configuration

The quality gate can be configured via `memall/config.py`:

```python
"quality": {
    # Quality thresholds per level (0-10 scale) - memories scoring below these thresholds
    # may be rejected by the quality gate depending on gate_policy
    "thresholds": {
        "P0": 0,      # No quality gate for highest priority
        "P1": 2,      # Very low threshold for P1
        "P2": 3,      # Low threshold for P2
        "L1": 3,      # Low threshold for L1
        "L2": 3,      # Low threshold for L2
        "L3": 4,      # Moderate threshold for L3
        "L4": 5,      # Standard threshold for L4
        "L5": 5,      # Standard threshold for L5 (decisions/tasks need reasoning)
        "L6": 5,      # Standard threshold for L6 (reflections need substance)
        "L7": 4,      # Slightly lower for L7
        "L8": 4,      # Slightly lower for L8
        "L9": 4,      # Lower threshold for distilled knowledge (L9+)
        "L10": 4,     # Lower threshold for distilled knowledge (L10+)
        "L11": 4,     # Lower threshold for distilled knowledge (L11+)
    },
    # Gate policy: "strict" (reject if ANY dimension below threshold) or 
    # "lenient" (reject only if AVERAGE score below threshold)
    "gate_policy": "lenient",
    # Minimum content length: memories shorter than this are rejected regardless of quality score
    "min_length": 15,
},
```

### Contextual Recall

Contextual recall can be enabled/disabled via configuration:

```python
"capture": {
    # ... other capture settings ...
    # Contextual recall: after storing a memory, check for related high-level memories in the same project.
    # Enable to surface related L4/L6/L9+ memories.
    "contextual_recall_enabled": True,
},
```

### Thread ID Inference with Semantic Fallback (M2)

Thread inference can be configured to use semantic similarity as a fallback when the temporal window yields no candidates:

```python
"capture": {
    # ... other capture settings ...
    # Thread inference: link new memories to a thread root within N minutes.
    # 0 disables the feature (thread_id left null). Tune per deployment.
    "thread_inference_window_minutes": 60,
    # Thread inference semantic fallback: when temporal window yields no candidate,
    # use semantic similarity to find related memories (M2 enhancement).
    "thread_inference_semantic_window": 50,
    "thread_inference_semantic_threshold": 0.7,
    # ... other capture settings ...
},
```

### Provenance Tracking with Source IDs (M3)

Memories now track their source memories for provenance tracking, especially useful for distilled knowledge:

```python
# In Memory and MemoryInput models:
source_ids: str = "[]"  # JSON array of source memory IDs
```

### Heat-based Level Promotion (M4)

Automatically promote frequently accessed memories to higher knowledge levels:

```python
"promotion": {
    # Enable/disable heat-based level promotion
    "enabled": true,
    # Access count thresholds for promotion between levels
    # Format: "from_level": {"to_level": min_access_count, ...}
    "access_thresholds": {
        "P2": {"L1": 15},      # Promote P2→L1 when accessed 15+ times
        "L1": {"L2": 25},      # Promote L1→L2 when accessed 25+ times
        "L2": {"L3": 35},      # Promote L2→L3 when accessed 35+ times
        "L3": {"L4": 50},      # Promote L3→L4 when accessed 50+ times
        "L4": {"L5": 75},      # Promote L4→L5 when accessed 75+ times
        "L5": {"L6": 100},     # Promote L5→L6 when accessed 100+ times
        "L6": {"L7": 150},     # Promote L6→L7 when accessed 150+ times
        "L7": {"L8": 200},     # Promote L7→L8 when accessed 200+ times
        "L8": {"L9": 300},     # Promote L8→L9 when accessed 300+ times
        "L9": {"L10": 500},    # Promote L9→L10 when accessed 500+ times
        "L10": {"L11": 800},   # Promote L10→L11 when accessed 800+ times
    },
    # Minimum time (in days) a memory must exist before being eligible for promotion
    "min_age_days": 7,
    # Whether to also consider referenced memories (edges pointing to this memory)
    "consider_references": true,
    # Reference count threshold for promotion (in addition to access count)
    "reference_threshold": 5,
},
```

## Usage

### Basic Memory Capture

```python
from memall.core.thin_waist import capture, MemoryInput

memory_id = capture(MemoryInput(
    content="Your memory content here",
    agent_name="your_agent_name",
    category="your_category",
    project="your_project",
    level="P2"  # or L1-L11 for different knowledge levels
))
```

### Capturing Memories with Source IDs (for Provenance Tracking)

```python
from memall.core.thin_waist import capture, MemoryInput

# Create a distilled memory from source memories
memory_id = capture(MemoryInput(
    content="Distilled insight from multiple source memories",
    agent_name="your_agent_name",
    category="your_category",
    project="your_project",
    level="L9",  # Distilled knowledge level
    source_ids='["123", "456", "789"]'  # JSON array of source memory IDs
))
```

### Searching Memories

```python
from memall.mcp.adapter import handle_call
import json

# Unified search (auto-routes between search modes)
result = handle_call('memall_read', {
    'action': 'unified_search',
    'query': 'your search query',
    'limit': 10
})
search_results = json.loads(result)

# Specific search modes
result = handle_call('memall_read', {
    'action': 'search',  # or 'vector_search', 'hybrid_search', etc.
    'query': 'your search query',
    'mode': 'hybrid',    # auto, direct, fts5, vector, hybrid
    'limit': 10
})
```

### Query Enhancement Layer

Six retrieval-quality features sit between the candidate set and the caller.
Every one is **off by default** and gated by its own `search.*` config flag,
so the plain search path is unchanged until a deployment opts in:

```python
# Faceted search — aggregate counts over the *unlimited* candidate set
result = handle_call('memall_read', {
    'action': 'search', 'query': '检索', 'mode': 'fts5',
    'facet_fields': ['level', 'category', 'project'],   # or ['auto']
})
# -> {"results": [...], "facets": {"level": {"L4": 3, "P2": 5}, ...}}

# Explainability — why did this result rank here?
result = handle_call('memall_read', {
    'action': 'search', 'query': '检索',
    'explain': True,     # matched_terms, ranks, score contributions
})

# Query expansion + suggestions
result = handle_call('memall_read', {
    'action': 'search', 'query': '检索',
    'expand_query': True,   # synonym + query-history expansion
    'suggest': True,        # corpus-vocabulary completions
})

# Result clustering — topic grouping
result = handle_call('memall_read', {
    'action': 'search', 'query': '检索',
    'cluster': True,        # -> {"clusters": [{"label": ..., "size": ..., "results": [...]}]}
})

# Personalised ranking + context boosting — learn from interactions
result = handle_call('memall_read', {
    'action': 'search', 'query': '检索', 'agent_name': 'workbuddy',
    'personalize': True,    # per-agent affinity from search_interactions
    'context_boost': True,  # boost results in the active project/category
})

# Feed the learning signal (clicks / dwells / impressions)
result = handle_call('memall_write', {
    'action': 'record_interaction',
    'query': '检索',
    'memory_id': 42,
    'interaction_type': 'click',   # click | dwell | impression | copy | skip
    'dwell_ms': 45000,
    'rank_position': 1,
    'agent_name': 'workbuddy',
})
```

Config defaults (all disabled):

```json
{
  "search": {
    "query_expansion": {"enabled": false, "synonyms": {}, "history_expansion": 3},
    "facets": {"default_fields": ["level", "category", "project"], "max_fields": 8},
    "explain": {"enabled": false},
    "clustering": {"enabled": false, "k": 0, "min_cluster_size": 2},
    "personalization": {"enabled": false, "weight": 0.25, "window_days": 30},
    "context_boost": {"enabled": false, "weight": 0.15, "window_days": 7}
  }
}
```

Enable a feature by setting its flag, e.g. `search.explain.enabled = true` in
`config.json` / `memall.yaml` (or `MEMALL_SEARCH_EXPLAIN__ENABLED=true`).

### Batch Operations

```python
# Batch capture
result = handle_call('memall_write', {
    'action': 'batch_capture',
    'items': [
        {
            'content': 'First memory content',
            'agent_name': 'agent1',
            'level': 'P2'
        },
        {
            'content': 'Second memory content', 
            'agent_name': 'agent2',
            'level': 'L6'
        }
    ]
})

# Batch search
result = handle_call('memall_read', {
    'action': 'batch_search',
    'queries': ['query1', 'query2', 'query3'],
    'limit': 5
})
```

### Sub-action arguments

Each action that needs a sub-action has its **own** field, so a value can
never be valid for one action and rejected by another:

```python
handle_call('memall_write', {'action': 'forget', 'forget_sub_action': 'review'})
handle_call('memall_write', {'action': 'ops', 'ops_sub_action': 'dedup', 'dry_run': True})
handle_call('memall_system', {'action': 'db', 'db_sub_action': 'stats'})
handle_call('memall_system', {'action': 'gateway', 'gateway_sub_action': 'status'})
handle_call('memall_system', {'action': 'security', 'security_sub_action': 'score'})
handle_call('memall_system', {'action': 'adaptive', 'adaptive_sub_action': 'report'})
handle_call('memall_system', {'action': 'onboarding', 'onboarding_sub_action': 'status'})
handle_call('memall_system', {'action': 'reflect', 'reflect_sub_action': 'probe',
                              'memory_id': 42})
handle_call('memall_system', {'action': 'distill', 'distill_sub_action': 'list'})
```

`sub_action` is still accepted as a **deprecated alias** for all of the above.

Destructive operations (delete / merge / split / dedup / archive / VACUUM)
require an explicit confirmation pass: inspect first (`dry_run=true` or the
`review`/`stats` variant), then re-call with `confirm=true`.

### Identity parameters

`agent_name` is optional everywhere it appears. When omitted or empty it
falls back to the configured human owner (`identity.human_owner`, default
`老陈`) — the same invariant the write path uses, where owner is always a
human. `fed_inject` is the deliberate exception: it needs to know *which*
agent's profile to inject, so it requires an explicit name.

Validation errors are phrased as one actionable sentence, e.g.

```
ops_sub_action: must be one of: merge, split, tag, batch_tag, archive, restore, dedup, undo (got 'expired')
```

## Proactive Memory

MemALL surfaces memories without being asked, through three independent paths.

### 1. Session injection (`session_start`)

`auto_inject()` assembles 16 sections for the calling agent in one pass —
persona, L1/L7 identity traits, L3 workflow skills, L4 decision arcs, L5 active
tasks, L6 reflections, L7 lessons, L10 panoramic overview, L11 domain knowledge,
pending discussions, and behaviour patterns. Two shapes come back:

- `injection` — the structured dict, for programmatic consumers
- `injection_formatted` — a ~800-character compact block that the model reads
  directly at the top of the session:

```
[CONTEXT] 会话上下文（首段先概括以下信息）：
[NARRATIVE] 新会话开始。
[PROFILE] L1=… L7=…
[TODO] task one | task two
[CONTINUITY] 距上次约 6h +4mem 2dec
[CORRECTIONS] …
[LESSONS] …
[WORKFLOW] …
[DECISIONS] 待决(2) 进行中(1)
[OVERVIEW] …
[DOMAIN] …
[DISTILL] 2 组待写摘要，调 memall_system action=distill 查看
```

Results are cached for 300s per agent.

### 2. Zero-cost delivery on every read

Agents do not have to call `session_start` themselves. The first non-read
tool call from an agent auto-starts a session, and the formatted block is
attached to the next `memall_read` / `memall_persona` response under
`_meta.session_context`. Async hook activity arrives the same way under
`_meta.hook_activity`.

### 3. Background rounds (agents have no runtime)

An agent never "wakes up" on its own, so the scheduler daemon runs
`agent_round()` every `scheduler.proxy_session_interval` seconds (default 300).
For each active agent it finds pending discussions and active L5 tasks and
writes a P2 reminder memory, which the agent then encounters in its next real
retrieval. Reminders are de-duplicated (1h for discussions, 6h for tasks).

### Auto-capture

Conclusions reached *through* the tools are captured without an explicit
`capture()` call. A `post_tool_use` hook extracts from three sources:

| Source action | Captured as | Content |
|---|---|---|
| `memall_persona ask` | `L4` / `decision` | question + reasoned answer |
| `memall_system reflect` | `L6` / `reflection` | stance + reasoning on a lesson |
| `memall_discussion respond` | `L4` / `discussion` | stance + argument |

Guardrails, so the system's own output cannot become its own evidence:

- Auto-captured rows are tagged `metadata.source`, and **`ask` excludes them
  from its evidence pool** — otherwise each stance would reinforce itself
  across later questions on the same topic.
- **`distill` excludes them too** — otherwise speculation would be promoted
  into `L9` "knowledge" and outrank the observations it came from.
- Repeated identical conclusions are de-duplicated per process.
- Pure reads and the capture family never trigger auto-capture.

### Post-compaction recap

MemALL cannot see the host's context-compaction event, so `recap` is the pull
surface for it — one cheap read-only call that restores what must not be lost:

```python
handle_call('memall_read', {'action': 'recap', 'agent_name': 'claude', 'limit': 5})
# -> {"session": {...last summary...}, "recent": [L4/L5 decisions & tasks], "note": ...}
```

### Tags (intent keywords)

Every capture without explicit tags gets a small derived keyword set (entities,
identifiers like `FTS5`/`memory_update`, CJK words via jieba or bigrams). Tags
are indexed in FTS5 as their own column, so `retrieve("Python")` finds memories
*about* Python even when the body never says the word:

```python
# auto-derived on capture; explicit tags always win
handle_call('memall_write', {'action': 'capture', 'content': '...', 'tags': ["检索", "FTS5"]})

# exact-match filter (any / all)
handle_call('memall_read', {'action': 'retrieve', 'tags': ["检索"], "tags_all": False})

# retro-fit tags onto historical untagged rows (one-off maintenance)
handle_call('memall_system', {'action': 'db', 'db_sub_action': 'backfill_tags',
                              'dry_run': True})
```

## Architecture

MemALL follows a modular architecture with clearly separated concerns:

- **Core**: Database models, memory capture, quality scoring
  - `core/tags.py` — intent-keyword normalisation, extraction, SQL filtering
  - `i18n.py` — session-injection label packs (`zh`/`en`)
- **Search**: FTS5, vector, and hybrid search implementations
  - `search/enhancements.py` — the query enhancement layer (facets, expansion,
    suggestions, clustering, explainability, personalisation, context boosting)
  - `search/seen.py` — per-session novelty demotion (demote, never exclude)
- **MCP Layer**: Model Context Protocol implementation for agent integration
- **Proactive**: `mcp/federation_tools.auto_inject` (session injection),
  `mcp/shared.ensure_session_started` (zero-cost delivery),
  `scheduler/agent_round` (background rounds), `auto_capture` (auto-capture),
  `pipeline.session.idle_session_sweep` (idle consolidation)
- **Pipeline**: Intelligent capture, distillation, and maintenance workflows
- **Pipeline Steps**: 
  - Standard steps: event_processor, enrich, cleanup, classify, procedure, time_slice, arc_status, echo, epoch, convergence, link, decay, backup, session, extract, entity_extraction, embed_index, reflect, distill_l7, distill, integrate, improve, observation, identity, reasoning, auto_summarize, discover_assoc, cross_agent, adaptive_ttl, archive
  - **New in M4**: promote (heat-based level promotion)
  - Optional steps: persona, narrative, cluster, suggest, bridge
- **Utilities**: Configuration, logging, and helper functions

## Development

### Running Tests

The regression suite is plain scripts at the repo root (no pytest required):

```bash
python check_fixes.py            # 14 per-bug guards + the 4 suites, one command
python check_fixes.py --list     # the guard manifest
python check_fixes.py fts_cjk    # run selected guards

python test_search_features.py   # query enhancement layer (34 assertions)
python test_auto_capture.py      # auto-capture + feedback-loop guards (19)
python test_match_consistency.py # multi-value match semantics + recap (8)
python test_m2_m3_m4.py          # thread inference / source_ids / promotion
```

Each guard in `check_fixes.py` pins a bug that was once fixed *silently* (no
error, wrong results). Add a guard whenever you fix such a bug — that is the
practice ported from dsh-meow-memory's `check-fixes.mjs`.

### Adding New Features

1. Follow the existing code patterns and conventions
2. Add appropriate tests for new functionality
3. Update documentation as needed
4. Ensure backward compatibility is maintained
5. Config-gate anything that changes retrieval behaviour; default off

## License

MIT License

## Acknowledgments

Thanks to all contributors and the open-source community for their support.