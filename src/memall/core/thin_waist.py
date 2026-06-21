import logging
import json
import re
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Optional
from .db import get_pool, content_hash
from .models import Memory, MemoryInput

logger = logging.getLogger(__name__)
logger = logging.getLogger(__name__)


# Valid agent_name pattern: simple identifiers (alphanumeric, underscore, hyphen, dot, @, CJK)
_VALID_AGENT_RE = re.compile(r'^[a-z0-9_@.\u4e00-\u9fff-]+$')
_CJK_RE = re.compile(r'[\u4e00-\u9fff\u3400-\u4dbf\uf900-\ufaff]')
_AGENT_TAG_RE = re.compile(r'(\d{4}-\d{2}-\d{2}|\d{10,})')
_AGENT_BLACKLIST = frozenset({
    "architecture", "brainstorm", "unknown", "session_active",
    "general",
})


# Security: whitelist of column names permitted in UPDATE SET clause
_ALLOWED_UPDATE_FIELDS = frozenset({
    "level", "category", "project", "summary", "subject",
    "confidence", "visibility", "content", "agent_name", "owner",
    "metadata",
})

# Valid L5 status values for lifecycle management
_VALID_L5_STATUSES = frozenset({"active", "done", "archived"})


@contextmanager
def _pool_conn():
    """Context manager wrapping ConnectionPool.get() / .put()."""
    pool = get_pool()
    conn = pool.get()
    try:
        yield conn
    finally:
        pool.put(conn)


# Category-to-prefix mapping for auto-generated subject lines
_SUBJECT_PREFIX = {
    "decision": "[决策]",
    "problem": "[问题]",
    "architecture": "[架构]",
    "implementation": "[实现]",
    "testing": "[测试]",
    "deployment": "[部署]",
    "meeting": "[讨论]",
    "documentation": "[文档]",
    "planning": "[规划]",
    "learning": "[学习]",
    "idea": "[灵感]",
    "reflection": "[复盘]",
    "fix": "[修复]",
    "config": "[配置]",
    "rule": "[规则]",
    "correction": "[修正]",
    "message": "[消息]",
    "marvis_message": "[消息]",
    "heartbeat": "",
}

# Conversation filler starts to strip when generating subject
_FILLER_STARTS = [
    "好的，", "好的 ", "明白了，", "明白了 ", "我知道了，", "我知道了 ",
    "我觉得", "我认为", "我想", "关于",
    "嗯，", "嗯 ", "呃，", "呃 ", "那个", "这个",
    "然后", "所以",
]


def _make_subject(content: str, category: str, agent_name: str, owner: str) -> str:
    """Auto-generate a human-readable subject line.

    Format: [TypePrefix] Who: core_phrase  (≤60 chars)
    Example: [决策] admin: 从 SQLite 迁移到 PostgreSQL
    """
    prefix = _SUBJECT_PREFIX.get(category, "")

    # Extract core phrase: strip filler starts, take first meaningful segment
    core = content.strip()
    for filler in _FILLER_STARTS:
        if core.startswith(filler):
            core = core[len(filler):].strip()
            break

    # Try sentence boundary first (Chinese then English)
    for sep in ("。", "！", "？", "；", "\n", ". ", "! ", "? "):
        idx = core.find(sep)
        if 10 < idx < 60:
            core = core[:idx]
            break
    else:
        # Fallback: first 35 chars
        core = core[:35]

    # Build who label: prefer owner, fall back to agent_name
    who = owner or agent_name or ""
    who_label = f"{who}: " if who else ""

    # Assemble
    label = f"{prefix}{who_label}{core}" if prefix else f"{who_label}{core}"

    # Trim to 60 chars max
    if len(label) > 60:
        label = label[:57] + "..."

    return label.strip()


def _row_to_memory(row) -> Memory:
    return Memory(
        id=row["id"], content=row["content"], content_hash=row["content_hash"],
        level=row["level"], owner=row["owner"], agent_name=row["agent_name"],
        subject=row["subject"], project=row["project"], category=row["category"],
        summary=row["summary"], occurred_at=row["occurred_at"],
        created_at=row["created_at"], updated_at=row["updated_at"],
        supersedes=row["supersedes"], confidence=row["confidence"],
        visibility=row["visibility"],
        access_count=row["access_count"], metadata=row["metadata"],
    )


_QUALITY_DIMS = [
    "completeness",
    "clarity",
    "relevance",
    "specificity",
    "persistence",
    "source_traceability",
    "context_stability",
    "sensitivity",
]


def _score_quality(data: MemoryInput, content_hash_val: str) -> dict:
    text = data.content or ""
    text_len = len(text.strip())
    scores: dict = {}

    scores["completeness"] = min(10, max(0, (text_len - 10) // 20))
    filler = ["嗯", "那个", "然后", "所以", "呃", "啊", "好的，", "明白了，"]
    scores["clarity"] = 10 if text_len > 40 else max(0, 10 - sum(text.count(f) for f in filler) * 2)
    scores["relevance"] = 7
    scores["specificity"] = min(10, len(re.findall(r'\d{4}|v\d+\.\d+|[A-Z]{2,}\d*|#\d+', text)) * 2)
    scores["persistence"] = 8 if data.level in ("P0", "P1") else 6
    scores["source_traceability"] = 8 if data.agent_name and data.owner else 4
    scores["context_stability"] = 8 if "临时" not in text and "暂时" not in text else 3
    scores["sensitivity"] = 10 if not re.search(r'(password|token|secret|apikey|api_key|sk-)\s*[:=]', text, re.I) else 2

    for k in _QUALITY_DIMS:
        scores[k] = max(0, min(10, scores[k]))

    avg = sum(scores.values()) / len(scores) if scores else 0
    min_dim = min(scores.values()) if scores else 0
    threshold_map = {"P0": 5, "P1": 6, "P2": 5}
    required = threshold_map.get(data.level or "P2", 5)
    passed = avg >= required and min_dim >= 3
    gate = "accepted" if passed else ("review" if avg >= required - 1 else "rejected")
    result = {"dimensions": scores, "avg": round(avg, 2), "min": min_dim, "gate": gate, "level": data.level}
    return result


def capture(data: MemoryInput | dict | str, **overrides) -> int:
    if isinstance(data, str):
        data = MemoryInput(content=data)
    elif isinstance(data, dict):
        data = MemoryInput(**data)
    for k, v in overrides.items():
        if hasattr(data, k):
            setattr(data, k, v)

    if not data.content.strip():
        raise ValueError("content cannot be empty")

    # Ensure every memory has an agent_name — fallback to "system"
    if not data.agent_name:
        data.agent_name = "system"

    # Agent name normalization and validation
    data.agent_name = data.agent_name.strip().lower()
    if (not _VALID_AGENT_RE.match(data.agent_name)
            or data.agent_name in _AGENT_BLACKLIST
            or _AGENT_TAG_RE.search(data.agent_name)):
        data.agent_name = "system"

    # Ensure owner has a sensible default for display
    if not data.owner:
        data.owner = data.agent_name

    # Auto-generate subject if not provided by caller
    if not data.subject:
        data.subject = _make_subject(data.content, data.category, data.agent_name, data.owner)

    now = datetime.now(timezone.utc).isoformat()
    h = content_hash(data.content)

    quality_result = _score_quality(data, h)
    quality_entry = {
        "value": quality_result,
        "_meta": {"version": 1, "written_at": now},
    }
    if isinstance(data.metadata, dict):
        data.metadata["quality"] = quality_entry
    else:
        try:
            existing_meta = json.loads(data.metadata or "{}")
        except Exception:
            existing_meta = {}
        existing_meta["quality"] = quality_entry
        data.metadata = existing_meta

    with _pool_conn() as conn:
        cur = conn.execute(
            "SELECT id FROM memories WHERE content_hash = ?", (h,)
        )
        existing = cur.fetchone()
        if existing:
            conn.execute(
                "UPDATE memories SET access_count = access_count + 1, updated_at = ? WHERE id = ?",
                (now, existing["id"]),
            )
            conn.commit()
            return existing["id"]

        # L5 duplicate check: same agent + subject → merge metadata, don't duplicate
        if data.level == "L5" and data.agent_name and data.subject:
            dup = conn.execute(
                "SELECT id, metadata FROM memories WHERE level = 'L5' AND agent_name = ? AND subject = ? LIMIT 1",
                (data.agent_name, data.subject),
            ).fetchone()
            if dup:
                existing_meta = {}
                try:
                    raw = dup["metadata"]
                    existing_meta = json.loads(raw) if isinstance(raw, str) and raw.strip() else {}
                except Exception:
                    existing_meta = {}
                incoming = data.metadata or {}
                if isinstance(incoming, str):
                    try:
                        incoming = json.loads(incoming)
                    except Exception:
                        incoming = {}
                if isinstance(existing_meta, dict) and isinstance(incoming, dict):
                    merged = {**existing_meta, **incoming}
                    conn.execute(
                        "UPDATE memories SET metadata = ?, updated_at = ? WHERE id = ?",
                        (json.dumps(merged, ensure_ascii=False), now, dup["id"]),
                    )
                    conn.commit()
                    return dup["id"]

        if data.agent_name:
            data.visibility = _get_allowed_write_visibility(conn, data.agent_name, data.visibility)
            if data.owner and data.owner != data.agent_name:
                ident = conn.execute(
                    "SELECT agent_type, trusted_by FROM identities WHERE agent_name = ?",
                    (data.agent_name,),
                ).fetchone()
                if ident and ident["agent_type"] != "human":
                    trusted = json.loads(ident["trusted_by"]) if ident["trusted_by"] else []
                    if data.owner not in trusted:
                        owners = [data.agent_name] + trusted[:3]
                        data.owner = owners[0] if owners else data.agent_name

        occurred = data.occurred_at or now
        cur = conn.execute(
            """INSERT INTO memories
               (content, content_hash, level, owner, agent_name, subject,
                project, category, summary, occurred_at, created_at, updated_at,
                supersedes, confidence, visibility, metadata, thread_id)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                data.content, h, data.level, data.owner, data.agent_name,
                data.subject, data.project, data.category, data.summary,
                occurred, now, now,
                data.supersedes, data.confidence, data.visibility,
                json.dumps(data.metadata) if isinstance(data.metadata, dict) else data.metadata,
                data.thread_id,
            ),
        )
        mem_id = cur.lastrowid

        # Decision Arc: L4 memories start as 'open'
        if data.level == "L4":
            conn.execute("UPDATE memories SET arc_status = 'open' WHERE id = ?", (mem_id,))
            conn.commit()

        # Auto-persist embedding for new memory (best-effort, never blocks capture)
        try:
            from memall.graph.embeddings import _auto_embed
            _auto_embed(conn, mem_id, data.content, h)
            conn.commit()
        except Exception:
            logger.warning("thin_waist.py: silent error", exc_info=True)
        return mem_id


def update(memory_id: int, **fields) -> bool:
    """Update fields of an existing memory.

    Only fields in ``_ALLOWED_UPDATE_FIELDS`` are accepted — all others
    are silently ignored.  This whitelist prevents SQL injection even
    though the SET clause is assembled via string formatting, because
    column names are provenance-checked against the whitelist before
    any f-string interpolation.
    """
    with _pool_conn() as conn:
        existing = conn.execute("SELECT id, level, metadata FROM memories WHERE id = ?", (memory_id,)).fetchone()
        if not existing:
            return False
        sets = []
        params = []

        # ── L5 status validation ──
        # status is stored inside metadata JSON, not as a top-level field
        if existing["level"] == "L5" and "metadata" in fields:
            raw_meta = fields["metadata"]
            if isinstance(raw_meta, str):
                try:
                    incoming = json.loads(raw_meta)
                except (json.JSONDecodeError, TypeError):
                    incoming = {}
            elif isinstance(raw_meta, dict):
                incoming = raw_meta
            else:
                incoming = {}
            if isinstance(incoming, dict) and "status" in incoming:
                raw_status = incoming["status"]
                if isinstance(raw_status, str) and raw_status not in _VALID_L5_STATUSES:
                    raise ValueError(
                        f"invalid L5 status '{raw_status}': "
                        f"must be one of {', '.join(sorted(_VALID_L5_STATUSES))}"
                    )

        # ── metadata merge (not replace) ──
        if "metadata" in fields:
            existing_meta = {}
            raw_existing = existing["metadata"]
            if raw_existing:
                try:
                    existing_meta = json.loads(raw_existing) if isinstance(raw_existing, str) else raw_existing
                except (json.JSONDecodeError, TypeError):
                    existing_meta = {}
            incoming = fields["metadata"]
            if isinstance(incoming, str):
                try:
                    incoming = json.loads(incoming)
                except (json.JSONDecodeError, TypeError):
                    incoming = {}
            if isinstance(incoming, dict):
                merged = {**existing_meta, **incoming}
                fields["metadata"] = json.dumps(merged, ensure_ascii=False)

        for k, v in fields.items():
            if k in _ALLOWED_UPDATE_FIELDS:
                sets.append(f"{k} = ?")  # safe: k is whitelisted
                params.append(v)
        if not sets:
            return False
        now = datetime.now(timezone.utc).isoformat()
        sets.append("updated_at = ?")
        params.append(now)
        params.append(memory_id)
        conn.execute(f"UPDATE memories SET {', '.join(sets)} WHERE id = ?", params)

        # Decision Arc: if memory is now L4 with NULL arc_status, set to 'open'
        after = conn.execute("SELECT level, arc_status FROM memories WHERE id = ?", (memory_id,)).fetchone()
        if after and after["level"] == "L4" and after["arc_status"] is None:
            conn.execute("UPDATE memories SET arc_status = 'open' WHERE id = ?", (memory_id,))

        conn.commit()
        return True


def retrieve(query=None, viewer=None, **filters) -> list | Memory | None:
    with _pool_conn() as conn:
        if isinstance(query, int) or (isinstance(query, str) and query.isdigit()):
            rid = int(query)
            conn.execute("UPDATE memories SET access_count = access_count + 1 WHERE id = ?", (rid,))
            conn.commit()
            cur = conn.execute("SELECT * FROM memories WHERE id = ?", (rid,))
            row = cur.fetchone()
            return _row_to_memory(row) if row else None

        where = ["1=1"]
        params = []

        for key in ("owner", "agent_name", "category", "project", "level"):
            val = filters.get(key)
            if val:
                where.append(f"memories.{key} = ?")
                params.append(val)

        # Agent isolation: when a viewer identifies itself but no explicit
        # agent_name filter is set, default to viewing only its own memories.
        # This ensures agents see their own data by default.
        if viewer and not filters.get("agent_name"):
            where.append("memories.agent_name = ?")
            params.append(viewer)

        subject = filters.get("subject")
        if subject:
            where.append("memories.subject LIKE ?")
            params.append(f"%{subject}%")

        date_start = filters.get("date_start")
        if date_start:
            where.append("memories.occurred_at >= ?")
            params.append(date_start)
        date_end = filters.get("date_end")
        if date_end:
            where.append("memories.occurred_at <= ?")
            params.append(date_end)

        limit = filters.get("limit", 20)

        if query and isinstance(query, str):
            q = fts_query(query)
            if q:
                where.append("memories.id IN (SELECT rowid FROM memories_fts WHERE memories_fts MATCH ?)")
                params.append(q)

        sql = f"SELECT * FROM memories WHERE {' AND '.join(where)} ORDER BY occurred_at DESC LIMIT ?"
        params.append(limit)
        cur = conn.execute(sql, params)
        rows = cur.fetchall()
        results = [_row_to_memory(r) for r in rows]

        if viewer:
            results = _filter_by_trust(conn, results, viewer)

        return results


VISIBILITY_ORDER = ["public", "shared", "family", "trusted", "private"]
VISIBILITY_RANK = {v: i for i, v in enumerate(VISIBILITY_ORDER)}


def _get_agent_read_level(conn, agent_name: str) -> str:
    row = conn.execute(
        "SELECT agent_type, trusted_by FROM identities WHERE agent_name = ?",
        (agent_name,),
    ).fetchone()
    if not row:
        return "public"
    agent_type = row["agent_type"]
    if agent_type == "human":
        return "private"
    trusted_by = json.loads(row["trusted_by"]) if row["trusted_by"] else []
    if any(t in trusted_by for t in ["*", agent_name]):
        return "trusted"
    if trusted_by:
        return "family"
    return "shared"


def _get_allowed_write_visibility(conn, agent_name: str, desired: str) -> str:
    row = conn.execute(
        "SELECT agent_type FROM identities WHERE agent_name = ?",
        (agent_name,),
    ).fetchone()
    if row and row["agent_type"] == "human":
        return desired
    read_level = _get_agent_read_level(conn, agent_name)
    read_rank = VISIBILITY_RANK.get(read_level, 2)
    write_rank = min(read_rank + 1, len(VISIBILITY_ORDER) - 1)  # one stricter
    allowed = VISIBILITY_ORDER[write_rank]
    if VISIBILITY_RANK.get(desired, 0) >= VISIBILITY_RANK.get(allowed, 0):
        return desired  # desired is at least as restrictive as allowed
    return allowed  # clamp up to stricter level


def _filter_by_trust(conn, memories: list, viewer: str) -> list:
    viewer_level = _get_agent_read_level(conn, viewer)
    viewer_rank = VISIBILITY_RANK.get(viewer_level, 0)
    filtered = []
    for m in memories:
        v = m.visibility or "private"
        mem_rank = VISIBILITY_RANK.get(v, 4)
        # Include if viewer is the owner, the creating agent, or has sufficient permission
        if m.owner == viewer or m.agent_name == viewer:
            filtered.append(m)
        elif mem_rank <= viewer_rank:
            filtered.append(m)
    return filtered


def fts_query(raw: str) -> str:
    """Build an FTS5 MATCH query string from a raw user query.

    FTS5''s default ``unicode61`` tokenizer keeps contiguous CJK characters
    as single tokens (e.g. ``"南海归墟"`` is ONE token, not four), so the
    standard phrase-match syntax works correctly for Chinese text.

    Each whitespace-separated term is quoted as a phrase match::

        "胡八一 南海归墟 1983"
        -> ''"胡八一" AND "南海归墟" AND "1983"''
    """
    terms = raw.strip().split()
    if not terms:
        return ""
    return " AND ".join(f'"{t}"' for t in terms)


VALID_RELATIONS = ["extends", "contradicts", "refines", "cites", "supersedes", "related"]


def connect(source_id: int, target_id: int, relation_type: str = "refines", weight: float = 1.0, metadata: str = "{}") -> int:
    if source_id == target_id:
        raise ValueError("self-connection is not allowed")
    if relation_type not in VALID_RELATIONS:
        raise ValueError(f"invalid relation type: {relation_type}")

    with _pool_conn() as conn:
        for rid in (source_id, target_id):
            if not conn.execute("SELECT 1 FROM memories WHERE id = ?", (rid,)).fetchone():
                raise ValueError(f"memory {rid} does not exist")

        cur = conn.execute(
            "SELECT id FROM edges WHERE source_id = ? AND target_id = ? AND relation_type = ?",
            (source_id, target_id, relation_type),
        )
        existing = cur.fetchone()
        if existing:
            return existing["id"]

        now = datetime.now(timezone.utc).isoformat()
        cur = conn.execute(
            "INSERT INTO edges (source_id, target_id, relation_type, weight, created_at, metadata) VALUES (?,?,?,?,?,?)",
            (source_id, target_id, relation_type, weight, now, metadata),
        )
        eid = cur.lastrowid
        conn.commit()
        return eid


def traverse(node_id: int, depth: int = 1, relation_filter: Optional[str] = None) -> dict:
    with _pool_conn() as conn:
        seen = {node_id}
        seen_edges = set()
        nodes = {}
        edges_out = []
        current = [node_id]

        for _ in range(depth):
            if not current:
                break
            placeholders = ",".join("?" for _ in current)
            edge_sql = f"""
                SELECT e.id, e.source_id, e.target_id, e.relation_type, e.weight, e.metadata
                FROM edges e
                WHERE (e.source_id IN ({placeholders}) OR e.target_id IN ({placeholders}))
            """
            edge_params = current + current
            if relation_filter:
                edge_sql += " AND e.relation_type = ?"
                edge_params.append(relation_filter)

            edges_rows = conn.execute(edge_sql, edge_params).fetchall()
            next_level = set()
            for er in edges_rows:
                src, tgt = er["source_id"], er["target_id"]
                ekey = (src, tgt, er["relation_type"])
                if ekey in seen_edges:
                    continue
                seen_edges.add(ekey)
                edges_out.append({
                    "id": er["id"], "source_id": src, "target_id": tgt,
                    "relation_type": er["relation_type"], "weight": er["weight"],
                })
                if src not in seen:
                    seen.add(src)
                    next_level.add(src)
                if tgt not in seen:
                    seen.add(tgt)
                    next_level.add(tgt)

            if next_level:
                ids = ",".join(str(x) for x in seen)
                node_rows = conn.execute(
                    f"SELECT id, content, subject, category, level, summary, confidence FROM memories WHERE id IN ({ids})"
                ).fetchall()
                for nr in node_rows:
                    nodes[nr["id"]] = {
                        "id": nr["id"], "content": nr["content"],
                        "subject": nr["subject"], "category": nr["category"],
                        "level": nr["level"], "confidence": nr["confidence"],
                    }
            current = list(next_level)

        root = conn.execute(
            "SELECT id, content, subject, category, level, summary, confidence FROM memories WHERE id = ?",
            (node_id,),
        ).fetchone()
        if root:
            nodes[root["id"]] = {
                "id": root["id"], "content": root["content"][:200],
                "subject": root["subject"], "category": root["category"],
                "level": root["level"], "confidence": root["confidence"],
            }

        return {"root": node_id, "nodes": list(nodes.values()), "edges": edges_out}


def smart_store(content: str, owner: str = "", agent_name: str = "",
                subject: str = "", project: str = "", category: str = "general",
                level: str = "P2", dedup_threshold: float = 0.85) -> dict:
    """Store memory with content_hash dedup + optional semantic similarity check.

    Returns {"id": memory_id, "status": "new"|"duplicate"}."""
    from memall.core.nlp import tfidf_svd_embed, cosine_sim, compute_tfidf
    from memall.graph.embeddings import EMBED_DIM

    # Check exact hash first
    h = content_hash(content)
    with _pool_conn() as conn:
        existing = conn.execute("SELECT id FROM memories WHERE content_hash = ?", (h,)).fetchone()
        if existing:
            return {"id": existing["id"], "status": "duplicate", "reason": "exact_hash"}

        # Semantic dedup: compare against recent memories
        recent = conn.execute(
            "SELECT id, content FROM memories WHERE agent_name = ? AND LENGTH(TRIM(content)) > 10 ORDER BY created_at DESC LIMIT 20",
            (agent_name,),
        ).fetchall()
        if recent and dedup_threshold > 0:
            texts = [content[:1000]] + [r["content"][:1000] for r in recent]
            vecs = tfidf_svd_embed(texts, dims=EMBED_DIM)
            if vecs is not None and len(vecs) > 1:
                tfidf_docs = compute_tfidf(texts)
                sim = cosine_sim(tfidf_docs[0], tfidf_docs[1])
                for i, r in enumerate(recent):
                    if i + 1 < len(tfidf_docs):
                        sim_i = cosine_sim(tfidf_docs[0], tfidf_docs[i + 1])
                        if sim_i > sim:
                            sim = sim_i
                if sim >= dedup_threshold:
                    return {"id": recent[0]["id"], "status": "duplicate", "reason": f"semantic_similarity_{sim:.2f}"}

        mid = capture(MemoryInput(
            content=content, owner=owner, agent_name=agent_name,
            subject=subject, project=project, category=category, level=level,
        ))
        return {"id": mid, "status": "new"}


def store_batch(items: list) -> dict:
    """Batch insert multiple memories. Each item is a dict with keys:
    content (required), owner, agent_name, subject, project, category, level.

    Returns {"ids": [...], "count": N}."""
    ids = []
    for item in items:
        mid = capture(MemoryInput(
            content=item.get("content", ""),
            owner=item.get("owner", ""),
            agent_name=item.get("agent_name", ""),
            subject=item.get("subject", ""),
            project=item.get("project", ""),
            category=item.get("category", "general"),
            level=item.get("level", "P2"),
        ))
        ids.append(mid)
    return {"ids": ids, "count": len(ids)}


def vector_search(query: str, top_k: int = 10, provider: Optional[str] = None) -> dict:
    """Semantic vector search.

    Uses the configured search provider (default TF-IDF+SVD).
    Set ``provider="faiss"`` to use FAISS (Phase 2).
    """
    from memall.config import get_config
    active = provider or get_config("search.provider", "tfidf")
    if active == "faiss":
        from memall.search import get_provider
        p = get_provider("faiss")
        if p is not None:
            return p.search(query, top_k=top_k)
    from memall.graph.retrieve import retrieve as graph_retrieve
    return graph_retrieve(query, mode="vector", top_k=top_k)


def hybrid_search(query: str, top_k: int = 10, rrf_k: int = 60) -> dict:
    """RRF (Reciprocal Rank Fusion) hybrid search combining FTS5 + vec0.

    1. FTS5 keyword search → ranked results
    2. vec0 KNN vector search → ranked results
    3. RRF merge: score = 1/(rrf_k + rank_fts) + 1/(rrf_k + rank_vec)

    Returns dict with ``results``, ``total``, and per-source hit counts.
    """
    from memall.graph.retrieve import _query_embed, _vec0_knn
    import struct

    with _pool_conn() as conn:
        # FTS5 results
        fts_q = fts_query(query)
        fts_rows = []
        if fts_q:
            fts_rowids = conn.execute(
                "SELECT rowid FROM memories_fts WHERE memories_fts MATCH ? "
                "ORDER BY rank LIMIT ?",
                (fts_q, top_k * 2),
            ).fetchall()
            if fts_rowids:
                ids = [r["rowid"] for r in fts_rowids]
                placeholders = ",".join("?" * len(ids))
                fts_rows = conn.execute(
                    f"SELECT id, content, subject, category FROM memories WHERE id IN ({placeholders})",
                    ids,
                ).fetchall()

        # vec0 KNN results
        query_vec = _query_embed(query)
        vec_rows = []
        if query_vec is not None:
            vec_results = _vec0_knn(conn, query_vec, top_k * 2)
            for vr in vec_results:
                row = conn.execute(
                    "SELECT id, content, subject, category FROM memories WHERE id = ?",
                    (vr["memory_id"],),
                ).fetchone()
                if row:
                    vec_rows.append(row)

        if not fts_rows and not vec_rows:
            # Fallback: pure FTS5
            return {
                "query": query, "mode": "hybrid_rrf",
                "results": [{"memory_id": r["id"], "content": r["content"][:200], "source": "fts"} for r in fts_rows],
                "total": len(fts_rows),
            }

        # RRF merge
        scores: dict[int, dict] = {}
        for rank, r in enumerate(fts_rows):
            scores[r["id"]] = {
                "memory_id": r["id"],
                "content": r["content"][:200],
                "rrf_score": 1.0 / (rrf_k + rank + 1),
                "fts_rank": rank + 1,
                "vec_rank": None,
            }
        for rank, r in enumerate(vec_rows):
            if r["id"] in scores:
                scores[r["id"]]["rrf_score"] += 1.0 / (rrf_k + rank + 1)
                scores[r["id"]]["vec_rank"] = rank + 1
            else:
                scores[r["id"]] = {
                    "memory_id": r["id"],
                    "content": r["content"][:200],
                    "rrf_score": 1.0 / (rrf_k + rank + 1),
                    "fts_rank": None,
                    "vec_rank": rank + 1,
                }

        sorted_results = sorted(scores.values(), key=lambda x: -x["rrf_score"])[:top_k]
        return {
            "query": query,
            "mode": "hybrid_rrf",
            "results": sorted_results,
            "total": len(scores),
            "fts_hits": len(fts_rows),
            "vec_hits": len(vec_rows),
        }


def timeline(query: Optional[str] = None, hours: int = 24, category: Optional[str] = None,
             project: Optional[str] = None, limit: int = 50,
             start: Optional[str] = None, end: Optional[str] = None,
             days: Optional[int] = None) -> list:
    with _pool_conn() as conn:
        from datetime import timedelta

        cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()

        where = []
        params: list = []

        if start:
            where.append("occurred_at >= ?")
            params.append(start)
        if end:
            where.append("occurred_at <= ?")
            params.append(end)
        if not start and not end:
            if days:
                cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
            else:
                cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
            where.append("occurred_at >= ?")
            params.append(cutoff)

        if category:
            where.append("category = ?")
            params.append(category)
        if project:
            where.append("project = ?")
            params.append(project)
        if query:
            q = fts_query(query)
            if q:
                where.append("id IN (SELECT rowid FROM memories_fts WHERE memories_fts MATCH ?)")
                params.append(q)

        params.append(limit)
        sql = f"SELECT * FROM memories WHERE {' AND '.join(where)} ORDER BY occurred_at DESC LIMIT ?"
        rows = conn.execute(sql, params).fetchall()
        return [_row_to_memory(r) for r in rows]
