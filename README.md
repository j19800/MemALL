<p align="center">
  <img src="https://img.shields.io/badge/license-MIT-blue" alt="MIT License">
  <img src="https://img.shields.io/badge/python-3.9%2B-blue" alt="Python 3.9+">
  <img src="https://img.shields.io/badge/MCP-2025--03--26-purple" alt="MCP">
  <img src="https://img.shields.io/github/stars/j19800/MemALL?style=social" alt="GitHub stars">
</p>

<h1 align="center">MemALL</h1>
<p align="center"><strong>Multi-agent Memory OS</strong> — 本地优先的 AI Agent 持久化记忆系统</p>

<p align="center">
  <i>42 MCP tools · 11-layer memory lifecycle · 24-step self-evolving pipeline · Knowledge graph · Multi-agent shared memory</i>
</p>

<p align="center">
  <b>English</b> · <a href="README.zh-CN.md">中文</a>
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

### 🧠 11-Layer Memory Lifecycle

Not just "store and retrieve". Every memory has a semantic level with exclusion-based priority classification, epoch detection, and echo scoring:

| Level | Meaning | Example |
|-------|---------|---------|
| P0/P1/P2 | Planning | "Ship v0.1.0 by June 23" |
| L1 | Raw fact | "User reported login bug" |
| L2 | Convention | "Use exclusion chain for classifier" |
| L3 | Business idea | "MemPort — cross-platform memory migration" |
| L4 | Decision | "Use FastAPI, not Flask" |
| L5 | Discussion | Multi-agent debate with convergence |
| **L6** | **Self-reflection** | "What went well/badly this session" |
| **L7** | **Preference** | "Prefers SMALL response, English-only" |
| **L8** | **Edge-promoted** | Auto-promoted via knowledge graph connectivity |
| **L9** | **Distillation** | "10k conversations → 200 knowledge nodes" |
| **L10** | **System insight** | Cross-domain pattern detection |
| **L11** | **Domain intelligence** | Cross-project domain patterns |

**24 core steps + 5 optional** auto pipeline: enriches → classifies → detects epochs → reflects → distills → integrates → observes. No manual CRUD needed.

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
3. **L9 Distillation** — compresses raw conversations into structured knowledge
4. **L11 Domain Intelligence** — cross-project pattern detection
5. **Forget & Adaptive** — TTL-based decay, low-value cleanup, automatic re-indexing
6. **OODA Loop** — Observe → Orient → Decide → Act, no human intervention

### 🏠 100% Local

SQLite + FTS5 + vector search. Zero cloud dependency. Your data stays on your machine.

---

## 🛠️ 42 MCP Tools

| Category | Tools |
|----------|-------|
| **Memory CRUD** | `capture`, `retrieve`, `update`, `smart_store`, `store_batch` |
| **Knowledge Graph** | `connect`, `traverse`, `timeline` |
| **Search** | `vector_search`, FTS5 full-text search, `hybrid_search` (FTS5+vec0 RRF, optional cross-encoder[¹]), `memall_search` |
| **Session** | `session_start`, `session_end`, `session_summary` |
| **Identity & Persona** | `persona`, `persona_profile`, `identity`, `ask`, `memall_identity` |
| **Discussion & Decision** | `discussion_create`, `discussion_respond`, `discussion_status`, `trace` |
| **Distillation** | `memall_distill_pending` |
| **Federation** | `fed_query`, `fed_publish`, `fed_deliver`, `fed_conflicts`, `fed_inject`, `fed_extract` |
| **Hub Sync** | `hub_connect`, `hub_sync` |
| **Pipeline & Evolution** | `run_pipeline`, `reflect_interact`, `forget`, `adaptive`, `index_rebuild`, `memall_forget`, `memall_adaptive` |
| **Security & Ops** | `security`, `ops`, `gateway`, `db`, `memall_db` |
| **Onboarding** | `onboarding` |
| **Export/Import** | `export`, `import`, `sync` |

---

## 🚀 Quick Start

```bash
# 1. Install
pip install memall-os

# 2. Initialize
memall init
memall start

# 3. Connect your MCP client
# Add to your mcp.json:
# {
#   "mcpServers": {
#     "memall": { "command": "memall", "args": ["serve"] }
#   }
# }

# 4. Start remembering
/capture "Project X: decided to use FastAPI, reason: async support"
/capture "Fixed OOM bug in NLP pipeline — root cause: vector dimension mismatch"
/retrieve "FastAPI decision"
```

Or clone from source:
```bash
git clone https://github.com/j19800/MemALL
cd memall
pip install -e .
```

---

## 📊 Why Not Competitors?

| Feature | MemALL | Mem0 | Letta | Zep |
|---------|--------|------|-------|-----|
| **Memory model** | 11 layers (P0-L11) | user/session | agent/memory-block | session/summary |
| **Knowledge graph** | ✅ Native + traversal | ❌ | ❌ | ❌ |
| **Self-evolving pipeline** | ✅ 24-step auto + 5 optional | ❌ | ❌ | ❌ |
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
├── mcp/          # MCP adapter (42 tools)
├── pipeline/     # 24-step auto pipeline + 5 optional
│   ├── observe/  # OODA observation step
│   ├── distill/  # L9/L10/L11 distillation, L7 preference, epoch detection
│   ├── classify/ # Exclusion-based priority classification
│   └── forget/   # TTL decay, L5 archive, low-value decay
├── graph/        # Knowledge graph + arc lifecycle management
├── federation/   # Cross-device memory sync + active push delivery
├── plugins/      # Dashboard / guardrails / rate limiter / metrics
└── migrations/   # DB migrations
```

---

## 📝 Roadmap

- [x] **v0.1.0** — Core MCP server, 42 tools, CLI, PyPI package
- [ ] **v0.2.0** — Web dashboard, user system, Pro tier gating
- [ ] **v0.3.0** — Cloud sync, team collaboration, API gateway

---

## 🤝 Contributing

PRs welcome! See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

- Report bugs → [Issues](https://github.com/j19800/MemALL/issues)
- Ask questions → [Discussions](https://github.com/j19800/MemALL/discussions)
- Browse the code → [Repository](https://github.com/j19800/MemALL)

---

## 📄 License

MIT License. See [LICENSE](LICENSE) for details.

> [¹] Cross-encoder reranking requires `pip install memall-os[rerank]` (downloads PyTorch + ~560MB model on first use). Core search works without it using RRF fusion of FTS5 + vec0.
