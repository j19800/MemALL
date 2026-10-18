# MemALL — Multi-Agent Collaborative Memory OS

**Version:** 0.1.68  
**Language:** Python 3.11+  
**Storage:** SQLite (single file, bitemporal) + optional vector backends (vec0 / FAISS)

MemALL is a memory operating system for multi-agent collaboration. It gives every AI agent a
structured, queryable, long-term memory with level-based lifecycle management (P0–P2 priority +
L1–L11 lifecycle), semantic search, knowledge graphs, agent profiles, federation across instances,
and an automated processing pipeline.

## Core Concepts

| Concept | Description |
|---------|-------------|
| **Memory** | A structured record: content, level, owner, agent_name, project, category, subject, summary, timestamps, tags, confidence, visibility. |
| **Level** | Canonical enum `P0/P1/P2` (priority) + `L1…L11` (lifecycle). Enforced by a SQLite `CHECK` constraint; invalid values are normalized to `P2`. |
| **Edge** | Typed relation between two memories (`refines`, `contradicts`, `extends`, `cites`, `supersedes`, `related`) with bitemporal validity windows. |
| **Pipeline** | Background processing steps (enrich, classify, distill, integrate, decay, forget, …) that are reentrant and commit per step. |
| **Federation** | Cross-instance memory sharing, conflict detection, and automatic conflict resolution. |
| **MCP** | Model Context Protocol server (STDIO + HTTP) exposing 7 aggregated tools (`memall_write`, `memall_read`, `memall_persona`, `memall_discussion`, `memall_federation`, `memall_system`, `memall_hooks_recent`), each dispatching on `action` (some also on `sub_action`). Every action is schema-validated; unknown actions are rejected with a list of valid ones. Discussion stances accept both `agree/disagree/pass` (MCP surface) and `confirm/reject/abstain` (convergence layer), normalized automatically. |
| **Batch Operations** | Efficiently capture multiple memories or search multiple queries in single operations via `memall_write action=batch_capture` and `memall_read action=batch_search`. |

## Quick Start

```bash
# 1. Initialize the database
python -m memall init

# 2. Capture a memory (via CLI)
python -m memall capture "决定采用 SQLite 作为主存储" --agent claude --project memall --category decision --level L4

# 3. Search memories
python -m memall search "SQLite 存储" --limit 10

# 4. Start the HTTP gateway (port 9919) and open the SPA
python -m memall serve

# 5. Run the processing pipeline
python -m memall pipeline
```

### Python SDK

```python
from memall.agent_memory import add, search

mid = add(
    "完成了架构重构，性能提升 30%",
    agent="claude",
    project="memall",
    category="implementation",
    level="P1",
)
results = search("架构重构", agent="claude", limit=5)
```

## Interfaces

| Interface | Entry Point | Notes |
|-----------|-------------|-------|
| CLI | `python -m memall <command>` | ~50 subcommands covering init/capture/search/get/connect/traverse/timeline/update/pipeline/forget/persona/suggest/bridge/ask/adaptive/security/ops/gateway/db/backup/restore/export/import/sync/mcp-connect |
| Python SDK | `from memall.agent_memory import add, search` | High-level wrapper with auto project inference and level normalization |
| MCP STDIO | `python -m memall.mcp.server` | JSON-RPC over stdin/stdout; tools registered via `mcp/tools/` |
| HTTP Gateway | `MemAllGateway` on `127.0.0.1:9919` | REST API (`/api/*`, `/v30api/*`) + SPA frontend (`/dashboard`, `/graph`, `/artifact`, …) |

## Configuration

Configuration is layered (last wins): built-in defaults → `config.json` → `memall.yaml` → `MEMALL_*`
environment variables. Keys use dot-path access, e.g. `get_config("db.path")`.

Key sections: `db`, `gateway`, `discovery`, `identity`, `search`, `dream`, `persona`, `forget`,
`capture`, `security`, `distill`, `lifecycle`, `nlp`, `plugins`, `scheduler`, `strategy`, `logging`.

The database defaults to `~/.memall/data.db`. On Windows, non-system drives (D–H) are preferred to
avoid filling `C:`.

## Project Structure

```
memall/
├── __main__.py          # CLI entry (python -m memall)
├── config.py            # Configuration manager
├── agent_memory.py      # Agent SDK (add/search)
├── intelligent_capture.py # Intent-based auto capture
├── bridge.py            # stdin JSON bridge for external processes
├── gateway.py           # Local HTTP gateway (aiohttp, port 9919)
├── core/                # Core engine: db, thin_waist, levels, nlp, lifecycle, project_infer, entity_extractor
├── cli/                 # Command-line interface (~50 subcommands)
├── pipeline/            # Background memory processing pipeline
├── graph/               # Knowledge graph + vector search (bge-small-zh-v1.5, sqlite-vec)
├── search/              # Pluggable search providers (vec0, faiss) + intent router
├── mcp/                 # Model Context Protocol server + tools
├── federation/          # Cross-instance federation, conflict detection, health
├── strategy/            # Memory strategy layer (buffer, entity, kg, sharing, summary)
├── lark/                # Lark/Feishu multi-bot IM integration
├── plugins/             # Plugin system (dashboard, exporter, notifier, scheduler)
├── scheduler/           # Background daemon scheduler
├── migrations/          # SQLite schema migrations (001–032)
└── api/                 # Legacy REST API (retained for reference)
```

## Contributing

- All writes funnel through `core/thin_waist.capture()` (inserts) and `core/thin_waist.update()`
  (updates) — keep them the single write entry points; MCP tool handlers must not issue raw
  `UPDATE memories ...` statements.
- Levels must be normalized via `core/levels.normalize_level()` before writing; never bypass the
  SQLite `CHECK` constraint.
- Pipeline steps must declare an idempotency contract (`cursor` / `recompute` / `dedup` /
  `external`) so re-runs after a crash converge safely.
- After any change, run full import/parse + dry-run validation, then update `CHANGELOG.md` and
  commit with message format `type: description` (fix/feat/refactor/docs/chore).

## License

Internal project — see repository for licensing details.
