# MemALL v0.1.0 — First Public Release

> **Tag**: v0.1.0 | **License**: MIT | **Python**: ≥3.9
> **PyPI**: `pip install memall-os`
> **Release date**: 2026-06-20

---

## Highlights

- **38 native MCP tools** — memory CRUD, knowledge graph, session management, federation, self-evolution, **hybrid search**
- **10-layer memory lifecycle** (P0 + L1–L10) — from raw fact to system-level insight
- **22-step autonomous pipeline** — enrich → classify → time-slice → link → reflect → distill_l7 → distill → integrate
- **Native knowledge graph** — typed-relation traversal (`refines`, `cites`, `contradicts`, `supersedes`)
- **Multi-agent shared memory** — cross-agent federation queries out of the box
- **L6 self-reflection + L9 distillation + OODA self-evolution**
- **LAN federation** — zero-config peer discovery, no server needed
- **100% local** — SQLite + FTS5 + vector search, zero cloud dependency
- **Discussion convergence engine** — multi-agent debate → consensus → traceable decision
- **Hybrid search (FTS5 + vec0 RRF)** — dual-recall with optional cross-encoder reranking

---

## Quick Start

```bash
pip install memall-os
memall init
memall start
```

Then connect your MCP client:

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
/capture "Decided to go open-source: MIT, GitHub-first"
/retrieve "open-source"
# → finds the memory above with context, timestamp, relations
```

---

## What's Stable

| Area | Status |
|------|--------|
| MCP HTTP server | ✅ Hardened with auto-restart (#8145) |
| Pipeline (2145 memories, 25s) | ✅ Verified, OpenBLAS OOM fixed (#8133) |
| Scheduler → Windows Task | ✅ Pipeline 04:00, Forget 03:00 |
| Database path | ✅ USERPROFILE-based, no SYSTEM profile leak (#8144) |
| Decision arc closed-loop | ✅ L4 → L5 → L6 auto lifecycle |
| Hybrid search (FTS5 + vec0 RRF) | ✅ Production-ready |
| CJK FTS5 query | ✅ CJK characters split into individual tokens |
| Cross-encoder reranking | ✅ Optional (requires `pip install memall-os[rerank]`) |
| Search result metadata | ✅ Includes subject, category, level, owner, agent_name |
| Auto-embed on update | ✅ Embedding refreshed when content changes |
| LAN federation | ✅ Verified |
| Discussion convergence | ✅ Multi-agent auto-consensus |
| PyPI package | ✅ `memall` 0.1.0 published |

---

## Notable Changes (v0.1.0+)

### Search Architecture Upgrade (3 Phases)

| Change | Description |
|--------|-------------|
| **CJK FTS5** | CJK characters are now split into individual tokens for correct FTS5 matching |
| **Hybrid search** | New `memall_hybrid_search` MCP tool — FTS5 keyword + vec0 vector, merged via RRF |
| **Metadata filters** | Filter results by category, level, owner before RRF merge |
| **Cross-encoder reranking** | Optional `rerank=True` enables BAAI/bge-reranker-v2-m3 re-scoring (heavy: ~1.8GB) |
| **Config-driven** | All search parameters configurable via `config.json` / `MEMALL_*` env vars |
| **Auto-embed on update** | Embedding auto-refreshed when memory content changes |
| **Force rebuild cleanup** | `build_index(force=True)` now also cleans orphan vec0 rows |
| **Enriched results** | Each result includes subject, category, level, owner, agent_name |

### Removed

- `rerank` removed from `full` dependency group — core install stays lightweight (~50MB, no AI models)

## Notable Fixes

| Issue | Fix | ID |
|-------|-----|----|
| DB path deadlock | Migrated from SYSTEM profile to USERPROFILE | #7905 / #8144 |
| Convergence unhashable dict | `action_items` str/dict type fix | #8018 / #8133 |
| Scheduler daemon crash | Migrated to Windows Task Scheduler | #7959 |
| OpenBLAS OOM | `tfidf_svd_embed` dimension fix | #8133 |
| MCP HTTP stability | Hardened with health endpoint + restart wrapper | #8145 |
| Discussion dual-path | `_unwrap`/`_unwrap_meta` for cleanup.py format mismatch | #8274 |
| Silent errors | 79 try-except-pass blocks converted to warnings | #7965 |
| YAML module | Missing `__init__.py` in PyYAML restored |

---

## 38 MCP Tools

| Category | Tools |
|----------|-------|
| **Memory CRUD** | `capture`, `retrieve`, `update`, `smart_store`, `store_batch` |
| **Knowledge Graph** | `connect`, `traverse`, `timeline` |
| **Search** | `vector_search`, `hybrid_search` |
| **Session** | `session_start`, `session_end`, `session_summary` |
| **Identity & Persona** | `persona`, `persona_profile`, `identity`, `ask` |
| **Discussion & Decision** | `discussion_create`, `discussion_respond`, `discussion_status`, `trace` |
| **Federation** | `fed_query`, `fed_publish`, `fed_conflicts`, `fed_inject`, `fed_extract` |
| **Hub Sync** | `hub_connect`, `hub_sync` |
| **Pipeline & Evolution** | `run_pipeline`, `reflect_interact`, `forget`, `adaptive`, `index_rebuild` |
| **Security & Ops** | `security`, `ops`, `gateway`, `db` |
| **Onboarding** | `onboarding` |

---

## Known Limitations

- **Single-tenant only** — multi-tenant planned for v0.2.0
- **WAL auto-checkpoint** — not yet implemented (P1-1)
- **18 sleeping AI modules** — 3 to be integrated in v0.2.0
- **Web UI** — minimal, CLI-focused

---

## Credits

**MemALL Team** — Founder & CEO
**Trae** — Chief Tech-Product Architect, Ops & Marketing
**Claude** — Product Manager
**Codex / opencode** — Tech Lead, Architecture
**WorkBuddy** — GTM Engineer & DevRel
**Marvis** — Architect

---

## What's Next (v0.2.0)

- [ ] Multi-tenant user system (user.id, namespace isolation)
- [ ] 3 sleeping AI modules: SemanticDedup / NLQueryEngine / KnowledgeHealth
- [ ] Silent error cleanup (90+ → logger.warning)
- [ ] Web UI redesign with vis-network graph
- [ ] WAL auto-checkpoint + size cap
- [ ] Session_end transaction guard
- [ ] CLI 43-command docstring full coverage

---

## Resources

- [README](./README.md)
- [QUICKSTART](./QUICKSTART.md)
- [Competitive Comparison](./COMPARISON.md)
- [One-pager](./marketing/one-pager.md)
- [Demo video](./marketing/memall-intro-30s.mp4)
