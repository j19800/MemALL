<p align="center">
  <img src="https://img.shields.io/badge/license-MIT-blue" alt="MIT License">
  <img src="https://img.shields.io/badge/python-3.10%2B-blue" alt="Python 3.10+">
  <img src="https://img.shields.io/badge/MCP-2025--03--26-purple" alt="MCP">
  <img src="https://img.shields.io/badge/version-0.1.77-green" alt="Version 0.1.69">
  <img src="https://img.shields.io/github/stars/j19800/MemALL?style=social" alt="GitHub stars">
</p>

<h1 align="center">MemALL</h1>
<p align="center"><strong>Multi-agent Memory OS</strong> — 本地优先的 AI Agent 持久化记忆系统</p>

<p align="center">
  <i>7 MCP tools · 3 priority + 11-layer memory lifecycle · 31-step self-evolving pipeline · Knowledge graph · Multi-agent shared memory · <b>Hook-driven automation</b></i>
</p>

<p align="center">
  <b>English</b> · <a href="README.zh-CN.md">中文</a> · <a href="CHANGELOG.md">Changelog</a>
</p>

---

## ✨ What is MemALL?

MemALL gives AI Agents **memory that persists across sessions, tools, and agents**. Install it, connect it to Claude Desktop / Cursor / Cline / any MCP client, and your agents stop starting from scratch every conversation.

```bash
pip install memall-os          # install (lightweight, no AI models)
pip install memall-os[full]    # install + viz + FAISS
pip install memall-os[rerank]  # install + cross-encoder reranking (HEAVY: ~1.8GB, requires PyTorch)
memall init                    # initialize
memall start                   # start services
memall hook list               # list registered lifecycle hooks
memall hook register post_capture --action log  # register a hook
# → MCP ready at http://127.0.0.1:9876/mcp
# → MCP ready at stdio (add to your mcp.json)
```

Then in any MCP client:

```json
{
  "mcpServers": {
    "memall": {
      "command": "memall",
      "args": ["serve"]
    }
  }
}
```

```python
# /capture "Decided to go open-source: MIT license, GitHub-first distribution"
# /retrieve "pricing decision"
# → finds the memory above, plus its context, timestamp, and relations
```

---

## 🔥 Why MemALL?

### 🧠 14-Level Memory Lifecycle (3 Priority + 11 Lifecycle Layers)

Not just "store and retrieve". Every memory has a semantic level with exclusion-based priority classification, epoch detection, and echo scoring:

| Level | Meaning | Example |
|-------|---------|---------|
| **P0/P1/P2** | **Planning** (priority layers) | "Ship v0.1.0 by June 23" |
| L1 | Raw fact | "User reported login bug" |
| L2 | Convention | "Use exclusion chain for classifier" |
| L3 | Business idea | "MemPort — cross-platform memory migration" |
| L4 | Decision | "Use FastAPI, not Flask" |
| L5 | Discussion | Multi-agent debate with convergence |
| **L6** | **Self-reflection** | "What went well/badly this session" |
| **L7** | **Preference** | "Prefers SMALL response, English-only" |
| **L8** | **Edge-promoted** | Auto-promoted via knowledge graph connectivity |
| **L9** | **Distillation** | Same-agent, same-category knowledge compression ("10k conversations → 200 knowledge nodes") |
| **L10** | **System insight** | **Cross-category** insight — same agent spanning ≥2 distinct categories |
| **L11** | **Domain intelligence** | **Cross-project / cross-agent** domain patterns |

> **Scope note:** "Cross-domain" at L10 means *cross-category within one agent* (produced by `integrate_step`). Truly *cross-agent* insight is produced separately by the `cross_agent` pipeline step, which can also emit L10 (cross-agent L9 aggregation) and L11 (domain patterns recurring across ≥5 agents). The two mechanisms are distinct.

**30 core steps + 5 optional** auto pipeline (see `list_pipeline_steps()`): enriches → classifies → detects epochs → reflects → distills → integrates → observes. No manual CRUD needed.

### 🔗 Knowledge Graph

Memories are connected by typed edges (`refines`, `cites`, `contradicts`, `supersedes`, `extends`). Traverse from "bug #123" to "fix PR #456" to "retrospective L6 reflection" in one hop.

```
/capture "Bug #123: OOM on large datasets"
/connect 123 456 --relation "resolved_by"
/traverse 123
→ finds #456 (fix), #789 (regression test), #912 (L6 reflection)
```

### 🤝 Multi-Agent Shared Memory

Claude, opencode, Codex, WorkBuddy — all reading from and writing to the same memory base. Cross-agent queries, fact extraction, and active push delivery are single MCP calls.

```
/fed_query "architecture decision last week" agent_name="claude"
→ returns cross-agent results with source attribution
```

### 🔄 Self-Evolving Pipeline

MemALL doesn't just store — it **improves itself**:

1. **L6 Reflection** — auto-reviews work quality, identifies patterns, corrects mistakes
2. **L7 Preference Extraction** — learns user preferences from interaction patterns
3. **Hook-Driven Automation** — post-capture auto-triggers lightweight pipeline + discussion convergence; pre-retrieve injects pending work; post-pipeline reports rich summaries
4. **L9 Distillation** — compresses raw conversations into structured knowledge
5. **L11 Domain Intelligence** — cross-project pattern detection
6. **Forget & Adaptive** — TTL-based decay, low-value cleanup, automatic re-indexing
7. **OODA Loop** — Observe → Orient → Decide → Act, no human intervention

### 🏠 100% Local

SQLite + FTS5 + vector search. Zero cloud dependency. Your data stays on your machine.

---

## 🛠️ MCP Tool Surface (7 server endpoints)

> **Count clarification:** The gateway exposes **7 top-level MCP tools** (`memall_write`, `memall_read`, `memall_persona`, `memall_discussion`, `memall_federation`, `memall_system`, `memall_hooks_recent`). Each is a *consolidated* tool with many `action`s — the table below lists the individual actions, which may read like "30+ tools" but are dispatched through those 7 endpoints. This is why older docs said "6 consolidated tools": the count depends on whether `memall_hooks_recent` is counted. Source of truth: `src/memall/mcp/tools/__init__.py`.

| Category | Tools |
|----------|-------|
| **Memory CRUD** | `capture`, `retrieve`, `update`, `smart_store`, `store_batch` |
| **Knowledge Graph** | `connect`, `traverse`, `timeline` |
| **Search** | `vector_search`, FTS5 full-text search, `hybrid_search` (FTS5+vec0 RRF, optional cross-encoder[¹]), `memall_search` |
| **Session** | `session_start`, `session_end`, `session_summary` |
| **Identity & Persona** | `persona`, `persona_profile`, `identity`, `ask`, `memall_persona action=identity` |
| **Discussion & Decision** | `discussion_create`, `discussion_respond`, `discussion_status`, `trace` |
| **Distillation** | `memall_system action=distill` |
| **Federation** | `fed_query`, `fed_publish`, `fed_deliver`, `fed_conflicts`, `fed_inject`, `fed_extract` |
| **Hub Sync** | `hub_connect`, `hub_sync` |
| **Pipeline & Evolution** | `run_pipeline`, `reflect_interact`, `forget`, `adaptive`, `index_rebuild`, `memall_write action=forget`, `memall_system action=adaptive` |
| **Hook Activity** | `memall_hooks_recent` — view async pipeline runs, notifications, and reminder checks |
| **Security & Ops** | `security`, `ops`, `gateway`, `db`, `memall_system action=db`, `memall_system action=security`, `memall_write action=ops` |
| **Onboarding** | `onboarding` |
| **Export/Import** | `export`, `import`, `sync` |

---

## 🚀 Quick Start

```bash
# One-shot install + start (recommended)
pip install memall-os && memall quickstart

# Or from source
git clone https://github.com/j19800/MemALL.git
cd MemALL
python install.py
```

`memall quickstart` initialises the database, starts the gateway, wires up the
MCP config, and opens the browser — nothing else to configure.

Then start recording:

```bash
memall capture "Project X: decided to use FastAPI, reason: async support"
memall capture "Fixed OOM bug in NLP pipeline — root cause: vector dimension mismatch"
memall retrieve "FastAPI decision"
```

---

## 📊 Why Not Competitors?

| Feature | MemALL | Mem0 | Letta | Zep |
|---------|--------|------|-------|-----|
| **Memory model** | 14 levels (P0/P1/P2 + L1–L11) | user/session | agent/memory-block | session/summary |
| **Knowledge graph** | ✅ Native + traversal | ❌ | ❌ | ❌ |
| **Self-evolving pipeline** | ✅ 31-step auto + 5 optional | ❌ | ❌ | ❌ |
| **Multi-agent shared** | ✅ Federation + active push | ❌ | Same agent only | ❌ |
| **Decision tracking** | ✅ Arc lifecycle | ❌ | ❌ | ❌ |
| **Discussion convergence** | ✅ Multi-agent auto | ❌ | ❌ | ❌ |
| **Protocol** | **MCP (native)** | REST API | REST + gRPC | REST API |
| **Local-first** | ✅ SQLite | ❌ Cloud | PostgreSQL | ❌ Cloud |
| **Open source** | ✅ MIT | ⚠️ Partial | ✅ | ⚠️ Partial |

---

## 📁 Project Structure

```
src/memall/
├── cli/          # CLI (40+ commands)
├── core/         # SQLite / NLP / vector search / event processor / echo scoring
├── api/          # FastAPI REST (35 routes)
├── mcp/          # MCP adapter (7 consolidated tool endpoints)
├── pipeline/     # 31-step auto pipeline + 5 optional (see list_pipeline_steps())
│   ├── observe/  # OODA observation step
│   ├── distill/  # L9/L10/L11 distillation, L7 preference, epoch detection
│   ├── classify/ # Exclusion-based priority classification
│   └── forget/   # TTL decay, L5 archive, low-value decay
├── graph/        # Knowledge graph + arc lifecycle management
├── federation/   # Cross-device memory sync + active push delivery
├── plugins/      # Hook/event-driven dashboard, exporter, notifier, scheduler
└── migrations/   # DB migrations
```

---

## 📝 Roadmap

- [x] **v0.1.0** — Core MCP server, 7 consolidated tool endpoints, CLI, PyPI package
- [x] **v0.1.69** — Repo made clonable again (`task_identity` + migrations 038/040 were live but uncommitted), goal `triage` gate now emits an action, skills layer transactions fixed for concurrency, policy refusals return 403 instead of 500
- [x] **v0.1.70** — Every remaining cross-agent read path closed: 
esources/read, ed_publish laundering/attribution forgery, and 	raverse/	imeline/	race now apply the trust filter; amily.db follows MEMALL_DB_PATH. Five red/blue duels now guard these.
- [x] **v0.1.71** — The four remaining P1s: security permit is now authorised (no self-escalation), contract level/category can no longer be rewritten through update(), destructive gateway routes require a credential even from loopback, and the two identity normalisers are unified. Seven red/blue duels guard them. Also fixes a convergence regression my own identity change introduced.
- [x] **v0.1.72** — Task-card transitions are now a compare-and-set on the observed status instead of a whole-metadata overwrite: a concurrent claim can no longer erase a completion (measured 10/40 lost before, 0/80 after), and a finished card can no longer be re-opened by a claim. Also removed the last known flaky guard.
- [x] **v0.1.80** — B-tier handover items landed and guarded: B1 convergence stance veto (`convergence_honours_stance` — unanimous with any reject, or majority with rejecting ≥ agreeing, does not converge), and B3 dependency notification (a resolved task now notifies the owners of its `depends_on` dependents via a stable dedup key). CHANGELOG v0.1.80 entry pushed to origin with these changes.
- [ ] **v0.2.0** — Web dashboard, user system, Pro tier gating
- [ ] **v0.3.0** — Cloud sync, team collaboration, API gateway

### Known gaps (stated, not hidden)

- **Task transitions use an optimistic compare-and-set, not a row lock.** `core/task_cas.py` writes only if the stored status still equals the one the caller read, so a lost update becomes a `conflict` the caller retries. It is not a true pessimistic lock: two writers can still both pass the read and one simply loses the write, which is safe for status but means callers must handle `conflict`.
- **Goal-level block reasons are not observable.** Cards already carry a two-value `last_block_reason` enum; goals still `return None` when they are stuck, so "why this goal is not moving" is invisible.
- **The guard suite is slow by design.** Every guard replays all migrations via `init_db()`, so a full run takes ~29 minutes (measured: 92 guards + 4 bundled suites in 1748s). That is now short enough to run as a gate — see `tools/verify.py` — but the per-guard migration replay is still the reason it cannot be a pre-push hook.
- **`check_fixes.py` scratch directories are reclaimed.** Every run still creates dozens of ~2.5 MB database copies, but they now live under one PID-tagged run root that is deleted at the end of the run and swept at the start of the next one (a live sibling run is never touched). Measured before the fix: 4,384 leftover directories totalling 10.7 GB. Set `MEMALL_GUARD_KEEP_SCRATCH=1` to keep the directory for debugging.
- **The 2026-10-31 red/blue P1s are closed, with two exceptions.** `security permit` now requires authorisation, contract `level`/`category` are immutable through `update()`, destructive gateway routes need a credential even from loopback, and the two identity normalisers are unified into `core/agent_names.py`. Still open from that report: the gateway's loopback carve-out is narrower but not gone (SPA reads remain tokenless by design), and `claim_task` is still not atomic across processes.
- **Write-side attribution is not refused.** By the chosen policy ("writes pass, reads are locked") an explicit `agent_name` can still create work on another agent's behalf; impersonation is made observable via `wake_audit`/`ops_log` rather than rejected.

---

## ✅ Before you commit: the acceptance gate

There is one command that answers "is this tree fit to commit / release?":

```bash
python tools/verify.py --plan fast    # ~4 min: imports + 9 duels + fast pytest subset
python tools/verify.py --plan guards  # includes the full 92-guard suite (~40 min)
python tools/verify.py --plan full    # everything, including the full pytest suite
python tools/verify.py --list         # show plans and stages
```

It runs each stage, records its own verdict (a failing stage does not abort the
rest), prints every failure line with the full log path, and exits non-zero if
anything is red. It also points `MEMALL_DB_PATH` at a throwaway directory, so a
gate run can never touch your database.

Why it exists: the two real gates — the guard suite and pytest — both existed
before, but the guard run took ~40 minutes so it was only run "when someone
remembered", and pytest hung outright. That is how a bare-`BEGIN` transaction
defect reached the mainline.

## 🤝 Contributing

PRs welcome! See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

- Report bugs → [Issues](https://github.com/j19800/MemALL/issues)
- Ask questions → [Discussions](https://github.com/j19800/MemALL/discussions)
- Browse the code → [Repository](https://github.com/j19800/MemALL)

---

## 📄 License

MIT License. See [LICENSE](LICENSE) for details.

> [¹] Cross-encoder reranking requires `pip install memall-os[rerank]` (downloads PyTorch + ~560MB model on first use). Core search works without it using RRF fusion of FTS5 + vec0.
