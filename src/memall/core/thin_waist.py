import logging
import json
import re
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Optional
from .db import get_pool, content_hash
from .models import Memory, MemoryInput
from .nlp import cosine_sim, compute_tfidf
from memall.graph.embeddings import EMBED_DIM
from memall.mcp.hooks import (dispatch_lifecycle, HOOK_PRE_CAPTURE,
                              HOOK_POST_CAPTURE, HOOK_PRE_STORE,
                              HOOK_POST_STORE, HOOK_PRE_RETRIEVE,
                              HOOK_POST_RETRIEVE, HOOK_PRE_SEARCH,
                              HOOK_POST_SEARCH)

logger = logging.getLogger(__name__)


# Valid agent_name pattern: simple identifiers (alphanumeric, underscore, hyphen, dot, @, CJK)
_VALID_AGENT_RE = re.compile(r'^[a-z0-9_@.\u4e00-\u9fff-]+$')
_CJK_RE = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf\uf900-\ufaff\u2e80-\u2eff\u2f00-\u2fdf]+")
_CJK_STOP: set[str] = {"的", "了", "在", "是", "我", "有", "和", "就", "不", "人", "都", "一", "个", "上", "也", "很", "到", "说", "要", "去", "你", "会", "着", "没有", "看", "好", "自己", "这", "那", "哪", "什么", "怎么", "如何", "为什么"}

_HAS_JIEBA = False
try:
    import jieba
    _HAS_JIEBA = True
except ImportError:
    pass
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

# Level-based subject prefixes (Phase 1: level naming unification)
_LEVEL_SUBJECT_PREFIX = {
    "L1": "[L1 身份]", "L2": "[L2 时间]", "L3": "[L3 流程]",
    "L4": "[L4 会话]", "L5": "[L5 计划]",
    "L6": "[L6 反思]",
    "L6-聚合": "[L6 聚合]", "L6-周反思": "[L6 周反思]", "L6-月反思": "[L6 月反思]",
    "L7": "[L7 教训]",
    "L8": "[L8 关系]",
    "L9": "[L9 蒸馏]", "L9-聚合": "[L9 聚合]",
    "L10": "[L10 整合]", "L11": "[L11 商业]",
    "P0": "[P0 原始]", "P1": "[P1 原始]", "P2": "[P2 原始]",
    "P3": "[P3 原始]", "P4": "[P4 原始]",
}

# Conversation filler starts to strip when generating subject
_FILLER_STARTS = [
    "好的，", "好的 ", "明白了，", "明白了 ", "我知道了，", "我知道了 ",
    "我觉得", "我认为", "我想", "关于",
    "嗯，", "嗯 ", "呃，", "呃 ", "那个", "这个",
    "然后", "所以",
]


def normalize_agent_name(name: str) -> str:
    """Normalize and validate an agent_name. Returns safe fallback "system" if invalid."""
    if not name:
        return "system"
    name = name.strip().lower()
    if (not _VALID_AGENT_RE.match(name)
            or name in _AGENT_BLACKLIST
            or _AGENT_TAG_RE.search(name)):
        return "system"
    # Reject single-character names (template leaks, parse artifacts, stray symbols)
    if len(name) < 2:
        return "system"
    # Reject template variable leaks: system.agent_name, demo.agent_name, etc.
    if name.endswith(".agent_name"):
        return "system"
    # Reject names containing template delimiters
    if "{" in name or "}" in name:
        return "system"
    # Reject single-CJK-character names (会, 全, 本, etc. — always parse artifacts)
    cjk_count = sum(1 for c in name if "一" <= c <= "鿿" or "㐀" <= c <= "䶿")
    if cjk_count >= 1 and len(name) == cjk_count:
        # Pure CJK names: require at least 2 CJK characters
        if cjk_count < 2:
            return "system"
    return name


def _make_subject(content: str, category: str, level: str, agent_name: str, owner: str) -> str:
    """Auto-generate a human-readable subject line.

    Format: [LevelPrefix] Who: core_phrase  (≤60 chars)
    Example: [L4 会话] admin: 从 SQLite 迁移到 PostgreSQL
    Falls back to category-based prefix if level has no mapping.
    """
    # Prefer level-based prefix, fallback to category-based
    prefix = _LEVEL_SUBJECT_PREFIX.get(level, _SUBJECT_PREFIX.get(category, ""))

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
        thread_id=row["thread_id"], agent_name_locked=bool(row["agent_name_locked"]),
    )


_REASONING_MARKERS = [
    "因为", "所以", "根因", "原因是", "取决于", "比较", "权衡",
    "方案", "选", "采用", "决定", "结论",
    "数据", "从.*看", "分析", "调研", "实测",
    "问题", "瓶颈", "不足", "改进",
    "用户说", "你的意思是", "确认",
]

_QUALITY_DIMS = [
    "completeness",
    "clarity",
    "relevance",
    "specificity",
    "reasoning",       # replaces persistence — measures "有理有据"
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

    # "reasoning" — measures whether content contains evidence/analysis language
    reasoning_hits = sum(1 for p in _REASONING_MARKERS if re.search(p, text, re.I))
    scores["reasoning"] = min(10, max(0, reasoning_hits * 2))

    scores["source_traceability"] = 8 if data.agent_name and data.owner else 4
    scores["context_stability"] = 8 if "临时" not in text and "暂时" not in text else 3
    scores["sensitivity"] = 10 if not re.search(r'(password|token|secret|apikey|api_key|sk-)\s*[:=]', text, re.I) else 2

    for k in _QUALITY_DIMS:
        scores[k] = max(0, min(10, scores[k]))

    avg = sum(scores.values()) / len(scores) if scores else 0
    min_dim = min(scores.values()) if scores else 0

    # Level-specific thresholds
    threshold_map = {
        "P0": 5, "P1": 6, "P2": 5,
        "L4": 6, "L5": 6,           # decisions + tasks need reasoning
        "L6": 6,                     # reflections need substance
        "L7": 5, "L9": 5, "L10": 5, "L11": 5,
    }
    required = threshold_map.get(data.level or "P2", 5)

    # Level-specific gate: L6 reflections MUST have reasoning >= 2
    if data.level == "L6" and scores["reasoning"] < 2:
        passed = False
        gate = "rejected"
    elif data.level in ("L4", "L5") and not data.subject:
        passed = False
        gate = "rejected"
    else:
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

    content_len = len(data.content.strip())
    if content_len < 50:
        logger.warning(f"capture: very short memory ({content_len} chars) agent={data.agent_name} cat={data.category}: {data.content[:60]}")
    elif content_len < 80:
        logger.info(f"capture: short memory ({content_len} chars) agent={data.agent_name} cat={data.category}: {data.content[:60]}")

    # Agent name normalization and validation (handles empty -> system)
    data.agent_name = normalize_agent_name(data.agent_name)

    # Ensure owner has a sensible default for display
    if not data.owner:
        data.owner = data.agent_name

    # Pre-capture lifecycle hook (blocking — can abort capture)
    dispatch_lifecycle(HOOK_PRE_CAPTURE, blocking=True, data=data)

    # Auto-generate subject if not provided by caller
    if not data.subject:
        data.subject = _make_subject(data.content, data.category, data.level, data.agent_name, data.owner)

    now = datetime.now(timezone.utc).isoformat()
    h = content_hash(data.content)

    quality_result = _score_quality(data, h)
    quality_gate = quality_result.get("gate", "accepted")

    # Quality gate: reject/subject/empty content checks
    #   "rejected" → warn and skip (too thin to be useful)
    #   "review"   → warn but store (marginal, caller should improve)
    if quality_gate == "rejected":
        # UX1: soft-degrade instead of raising ValueError — log warning and return None
        logger.warning(
            "capture: quality gate rejected (avg=%.2f, min=%d, len=%d) — stored anyway",
            quality_result.get("avg", 0), quality_result.get("min", 0),
            len(data.content or ""),
        )
    if quality_gate == "review":
        logger.warning(
            "capture: quality gate review (avg=%.2f, min=%d) agent=%s cat=%s",
            quality_result.get("avg", 0), quality_result.get("min", 0),
            data.agent_name, data.category,
        )

    # Enforce: subject must be non-empty for L4+
    if data.level in ("L4", "L5", "L6", "L7", "L9", "L10", "L11") and not data.subject:
        logger.warning("capture: %s memory missing subject, content=%.60s", data.level, data.content or "")
        data.subject = _make_subject(data.content, data.category, data.level, data.agent_name, data.owner)

    # Auto-inject provenance if caller didn't provide it
    if isinstance(data.metadata, dict):
        if "source" not in data.metadata:
            data.metadata["source"] = "capture_api"
    else:
        try:
            existing_meta = json.loads(data.metadata or "{}")
        except Exception:
            existing_meta = {}
        if "source" not in existing_meta:
            existing_meta["source"] = "capture_api"
        data.metadata = existing_meta

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

        # API 层校验：agent_name 必须在 identities 表中存在
        if data.agent_name:
            # 查询 identities 表验证
            ident = conn.execute(
                "SELECT id FROM identities WHERE agent_name = ?",
                (data.agent_name,)
            ).fetchone()

            if not ident:
                # Auto-register unknown agent — capture() is the write path,
                # not a validation gate. Identity records are created lazily.
                conn.execute(
                    "INSERT OR IGNORE INTO identities (agent_name, agent_type) "
                    "VALUES (?, 'ai')",
                    (data.agent_name,),
                )
                logger.info(
                    "capture: auto-registered agent '%s' in identities table",
                    data.agent_name,
                )

            # 如果 owner 存在且不是 agent_name 本身，需要验证是否为 trusted
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

            data.visibility = _get_allowed_write_visibility(conn, data.agent_name, data.visibility)

        occurred = data.occurred_at or now
        supersedes = data.supersedes if data.supersedes and data.supersedes != "[]" else None
        try:
            cur = conn.execute(
                """INSERT INTO memories
                   (content, content_hash, level, owner, agent_name, subject,
                    project, category, summary, occurred_at, created_at, updated_at,
                    supersedes, confidence, visibility, metadata, thread_id, agent_name_locked)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    data.content, h, data.level, data.owner, data.agent_name,
                    data.subject, data.project, data.category, data.summary,
                    occurred, now, now,
                    supersedes, data.confidence, data.visibility,
                    json.dumps(data.metadata) if isinstance(data.metadata, dict) else data.metadata,
                    data.thread_id,
                    0,  # 默认 0 = 未锁定
                ),
            )
            mem_id = cur.lastrowid
        except Exception:
            # Race: concurrent insert with same content_hash — fetch existing
            conn.rollback()
            existing = conn.execute(
                "SELECT id FROM memories WHERE content_hash = ?", (h,)
            ).fetchone()
            if existing:
                return existing["id"]
            raise

        # Publish pipeline event for new memory
        try:
            conn.execute(
                "INSERT INTO pipeline_events (memory_id, event_type, created_at) VALUES (?, 'new_memory', datetime('now'))",
                (mem_id,),
            )
            conn.commit()
        except Exception:
            logger.warning("pipeline_events insert failed", exc_info=True)

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
            logger.warning("embedding auto-embed failed (install sentence-transformers for vector search)", exc_info=True)

        # Dynamic Dream: active contradiction detection (best-effort, never blocks)
        try:
            from memall.config import get_config as _get_dream_config
            if _get_dream_config("dream.enabled", True):
                from memall.pipeline.dream import dream_scan
                _dreams = dream_scan(
                    conn,
                    new_mem_id=mem_id,
                    agent_name=data.agent_name,
                    content=data.content,
                    category=data.category,
                    scan_window=_get_dream_config("dream.scan_window", 50),
                    threshold=_get_dream_config("dream.threshold", 0.4),
                )
                if _dreams:
                    conn.commit()
                    logger.info("capture: dream found %d conflict(s) for memory #%d", len(_dreams), mem_id)
        except Exception:
            logger.debug("capture: dream scan skipped (non-fatal)", exc_info=True)

        dispatch_lifecycle(HOOK_POST_CAPTURE, data=data, memory_id=mem_id)
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
                # Normalize agent_name on update (log warning if changed)
                if k == "agent_name":
                    normalized = normalize_agent_name(v)
                    if normalized != v:
                        logger.warning(
                            f"update({memory_id}): agent_name %r normalized to %r",
                            v, normalized,
                        )
                    v = normalized
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

        # Auto-refresh embedding when content changes
        if "content" in fields:
            new_content = fields["content"]
            h = content_hash(new_content)
            try:
                from memall.graph.embeddings import _auto_embed
                _auto_embed(conn, memory_id, new_content, h)
                conn.commit()
            except Exception:
                logger.warning("embedding re-embed failed (install sentence-transformers for vector search)", exc_info=True)

        return True


def retrieve(query=None, viewer=None, **filters) -> list | Memory | None:
    dispatch_lifecycle(HOOK_PRE_RETRIEVE, query=query, viewer=viewer, filters=filters)
    with _pool_conn() as conn:
        if isinstance(query, int) or (isinstance(query, str) and query.isdigit()):
            rid = int(query)
            conn.execute("UPDATE memories SET access_count = access_count + 1 WHERE id = ?", (rid,))
            conn.commit()
            cur = conn.execute("SELECT * FROM memories WHERE id = ?", (rid,))
            row = cur.fetchone()
            if row:
                result = _row_to_memory(row)
                dispatch_lifecycle(HOOK_POST_RETRIEVE, query=query, result=result)
                return result
            dispatch_lifecycle(HOOK_POST_RETRIEVE, query=query, result=None)
            return None

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

        dispatch_lifecycle(HOOK_POST_RETRIEVE, query=query, results=results)
        return results


VISIBILITY_ORDER = ["public", "shared", "family", "trusted", "private"]
VISIBILITY_RANK = {v: i for i, v in enumerate(VISIBILITY_ORDER)}


def _get_agent_read_level(conn, agent_name: str) -> str:
    row = conn.execute(
        "SELECT agent_type, trusted_by FROM identities WHERE agent_name = ?",
        (agent_name,),
    ).fetchone()
    if not row:
        return "private"  # unknown agents default to most restrictive
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


def _filter_by_trust_dict(results: list[dict], viewer: str) -> dict[int, bool]:
    """Dict-compatible visibility filter for hybrid_search results.

    Returns a dict mapping memory_id → allowed (True/False).
    Uses the same VISIBILITY_RANK logic as _filter_by_trust.
    """
    with _pool_conn() as conn:
        viewer_level = _get_agent_read_level(conn, viewer)
    viewer_rank = VISIBILITY_RANK.get(viewer_level, 0)

    allowed: dict[int, bool] = {}
    for r in results:
        mid = r["memory_id"]
        owner = r.get("owner") or ""
        agent = r.get("agent_name") or ""
        # Include if viewer is the owner or creator
        if owner == viewer or agent == viewer:
            allowed[mid] = True
            continue
        # Need visibility — lazy fetch from DB
        _visibility = _fetch_visibility(mid, viewer)
        mem_rank = VISIBILITY_RANK.get(_visibility, 4)
        allowed[mid] = mem_rank <= viewer_rank
    return allowed


def _fetch_visibility(memory_id: int, viewer: str) -> str:
    """Fetch a single memory's visibility with cache (module-level dict)."""
    if memory_id in _VIS_CACHE:
        return _VIS_CACHE[memory_id]
    with _pool_conn() as conn:
        row = conn.execute(
            "SELECT visibility FROM memories WHERE id = ?", (memory_id,)
        ).fetchone()
        vis = row["visibility"] if row else "private"
    # Cache up to 1000 entries
    if len(_VIS_CACHE) > 1000:
        _VIS_CACHE.clear()
    _VIS_CACHE[memory_id] = vis
    return vis


_VIS_CACHE: dict[int, str] = {}


def _split_cjk(text: str) -> str:
    """Insert spaces between CJK characters so FTS5's unicode61 tokenizer
    treats each CJK character as a separate token.

    Non-CJK sequences (ASCII, digits) are left untouched.
    """
    parts: list[str] = []
    pos = 0
    for m in _CJK_RE.finditer(text):
        if m.start() > pos:
            parts.append(text[pos:m.start()])
        parts.append(" ".join(m.group()))
        pos = m.end()
    if pos < len(text):
        parts.append(text[pos:])
    return "".join(parts)


def fts_query(raw: str) -> str:
    """Build an FTS5 MATCH query string from a raw user query.

    FTS5 unicode61 tokenizer treats 2+ consecutive CJK characters as tokens.
    Single CJK characters are dropped. jieba sub-tokens are added as OR
    alternatives for broader recall. Non-CJK words use exact match.
    """
    terms = raw.strip().split()
    if not terms:
        return ""
    tokenized: list[str] = []
    for t in terms:
        if _CJK_RE.search(t):
            # Base: the raw CJK run (a valid unicode61 token)
            cjk_options = [f'"{t}"']
            if _HAS_JIEBA:
                words = [w for w in jieba.cut(t, cut_all=False)
                         if len(w.strip()) >= 2 and w not in _CJK_STOP]
                for w in words:
                    if len(w) >= 2 and w != t:
                        cjk_options.append(f'"{w}"')
            # Fallback: for 4+ char CJK not split by jieba, try 2-char sub-tokens
            if len(cjk_options) == 1 and len(t) >= 4:
                for i in range(0, len(t) - 1, 2):
                    sub = t[i:i+2]
                    if len(sub) >= 2 and sub != t:
                        cjk_options.append(f'"{sub}"')
            if len(cjk_options) > 1:
                tokenized.append(f'({" OR ".join(cjk_options)})')
            else:
                tokenized.append(cjk_options[0])
        else:
            tokenized.append(f'"{t}"')
    if len(tokenized) > 1:
        return " AND ".join(tokenized)
    return tokenized[0] if tokenized else ""


VALID_RELATIONS = [
    "extends", "contradicts", "refines", "cites", "supersedes", "related",
    "updates", "derives",
]

# Ontology hierarchy: broader → narrower.
# Used for traversing up/down the relation type hierarchy.
# Example: "updates" implies "supersedes" — querying for updates also returns supersedes.
ONTOLOGY_HIERARCHY = {
    "updates": ["supersedes"],          # updating something → superseding the old
    "derives": ["refines"],             # deriving from → refining
    "extends": ["cites"],               # extending → citing the source
    "refines": [],                       # leaf in ontology (no further narrowing)
    "cites": [],
    "supersedes": [],
    "contradicts": [],
    "related": [],
    "concerns": [],
    "context": [],
    "integrates": [],
    "delegates": [],
    "derived_from": ["refines"],
}

# Inverse: narrower → broader (for downward expansion queries)
_ONTOLOGY_PARENTS: dict[str, list[str]] = {}
for _parent, _children in ONTOLOGY_HIERARCHY.items():
    for _c in _children:
        _ONTOLOGY_PARENTS.setdefault(_c, []).append(_parent)
ONTOLOGY_PARENTS = _ONTOLOGY_PARENTS


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


def traverse(node_id: int, depth: int = 1, relation_filter: Optional[str] = None,
             thread_aware: bool = False) -> dict:
    with _pool_conn() as conn:
        seen = {node_id}
        seen_edges = set()
        nodes = {}
        edges_out = []
        current = [node_id]

        # ── Thread-aware expansion: include thread-linked memories ──
        if thread_aware:
            root_thread = conn.execute(
                "SELECT thread_id FROM memories WHERE id = ?", (node_id,)
            ).fetchone()
            related = set()
            if root_thread and root_thread["thread_id"]:
                # This node has a thread_id → find siblings (same thread_id) + parent
                parent_id = root_thread["thread_id"]
                if parent_id not in seen:
                    related.add(parent_id)
                    seen.add(parent_id)
                sibling_rows = conn.execute(
                    "SELECT id FROM memories WHERE thread_id = ? AND id != ?",
                    (parent_id, node_id),
                ).fetchall()
                for sr in sibling_rows:
                    if sr["id"] not in seen:
                        related.add(sr["id"])
                        seen.add(sr["id"])
            else:
                # This node is a root → find children (memories referencing it as thread_id)
                child_rows = conn.execute(
                    "SELECT id FROM memories WHERE thread_id = ? AND id != ?",
                    (node_id, node_id),
                ).fetchall()
                for cr in child_rows:
                    if cr["id"] not in seen:
                        related.add(cr["id"])
                        seen.add(cr["id"])
            if related:
                ph = ",".join("?" * len(related))
                thread_node_rows = conn.execute(
                    f"SELECT id, content, subject, category, level, summary, confidence FROM memories WHERE id IN ({ph})",
                    tuple(related),
                ).fetchall()
                for nr in thread_node_rows:
                    nodes[nr["id"]] = {
                        "id": nr["id"], "content": nr["content"],
                        "subject": nr["subject"], "category": nr["category"],
                        "level": nr["level"], "confidence": nr["confidence"],
                    }
                # Add same_thread edges from root to all thread-related nodes
                for rid in related:
                    ekey = (node_id, rid, "same_thread")
                    if ekey not in seen_edges:
                        seen_edges.add(ekey)
                        edges_out.append({
                            "id": None, "source_id": node_id, "target_id": rid,
                            "relation_type": "same_thread", "weight": 0.5,
                        })
                # Include thread-related nodes in BFS frontier
                current = list(set(current) | related)

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
                # Ontology expansion: include child types in the hierarchy
                expanded_types = [relation_filter]
                children = ONTOLOGY_HIERARCHY.get(relation_filter, [])
                expanded_types.extend(children)
                placeholders_in = ",".join("?" * len(expanded_types))
                edge_sql += f" AND e.relation_type IN ({placeholders_in})"
                edge_params.extend(expanded_types)

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

    # Pre-store lifecycle hook
    dispatch_lifecycle(HOOK_PRE_STORE, content=content, owner=owner, agent_name=agent_name)

    # Check exact hash first
    h = content_hash(content)
    with _pool_conn() as conn:
        existing = conn.execute("SELECT id FROM memories WHERE content_hash = ?", (h,)).fetchone()
        if existing:
            result = {"id": existing["id"], "status": "duplicate", "reason": "exact_hash"}
            dispatch_lifecycle(HOOK_POST_STORE, result=result)
            return result

        # Semantic dedup: compare against recent memories
        recent = conn.execute(
            "SELECT id, content FROM memories WHERE agent_name = ? AND LENGTH(TRIM(content)) > 10 ORDER BY created_at DESC LIMIT 20",
            (agent_name,),
        ).fetchall()
        if recent and dedup_threshold > 0:
            texts = [content[:1000]] + [r["content"][:1000] for r in recent]
            if len(texts) > 1:
                tfidf_docs = compute_tfidf(texts)
                sim = cosine_sim(tfidf_docs[0], tfidf_docs[1])
                for i, r in enumerate(recent):
                    if i + 1 < len(tfidf_docs):
                        sim_i = cosine_sim(tfidf_docs[0], tfidf_docs[i + 1])
                        if sim_i > sim:
                            sim = sim_i
                if sim >= dedup_threshold:
                    result = {"id": recent[0]["id"], "status": "duplicate", "reason": f"semantic_similarity_{sim:.2f}"}
                    dispatch_lifecycle(HOOK_POST_STORE, result=result)
                    return result

        mid = capture(MemoryInput(
            content=content, owner=owner, agent_name=agent_name,
            subject=subject, project=project, category=category, level=level,
        ))
        result = {"id": mid, "status": "new"}
        dispatch_lifecycle(HOOK_POST_STORE, result=result)
        return result


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


# ── Cross-encoder reranker (Phase 2) ──

_reranker = None          # cached CrossEncoder instance
_reranker_model_name = None  # track which model is loaded


def _rerank(results: list[dict], query: str, top_k: int) -> list[dict]:
    """Re-rank RRF results with a cross-encoder model.

    Lazy-loads ``CrossEncoder`` on first call (cached thereafter).  Falls
    back to the original RRF ordering if ``sentence-transformers`` is not
    installed or the model fails to load / infer.
    """
    global _reranker, _reranker_model_name

    if not results:
        return results

    from memall.config import get_config

    model_name = get_config("search.reranker_model", "BAAI/bge-reranker-v2-m3")
    rerank_top_k = get_config("search.rerank_top_k", 30)

    # (Re)load the model if first call or model changed
    if _reranker is None or _reranker_model_name != model_name:
        _reranker = None
        _reranker_model_name = None
        try:
            from sentence_transformers import CrossEncoder
            _reranker = CrossEncoder(model_name, device="cpu")
            _reranker_model_name = model_name
            logger.info("reranker loaded: %s", model_name)
        except ImportError:
            logger.warning("sentence-transformers not installed; cross-encoder reranking disabled")
            return results[:top_k]
        except Exception:
            logger.warning("failed to load reranker %s; using RRF results", model_name, exc_info=True)
            _reranker = None
            return results[:top_k]

    # Score candidates
    try:
        candidates = results[:rerank_top_k]
        pairs = [(query, r.get("content", "")[:512]) for r in candidates]
        scores = _reranker.predict(pairs, show_progress_bar=False)
        for r, s in zip(candidates, scores):
            r["rerank_score"] = float(s)
        candidates.sort(key=lambda x: -x.get("rerank_score", 0))
        return candidates[:top_k]
    except Exception:
        logger.warning("reranker inference failed; using RRF results", exc_info=True)
        return results[:top_k]


def _context_rerank(results: list[dict], query: str, top_k: int,
                     viewer: str | None = None) -> list[dict]:
    """Context-aware re-ranking: micro-adjust cross-encoder scores based on
    the caller's recent interaction patterns.

    Three signals (configurable weights):
      1. **Domain affinity** — if the viewer has recently searched a category,
         boost results in that category by ``affinity_boost``.
      2. **Agent affinity** — if the viewer is ``workbuddy``, boost results
         authored by workbuddy.
      3. **Freshness boost** — results created/updated within 24h get a small
         ``freshness_boost``, but cannot overtake the top 3 from reranker.

    This function is a no-op (returns results unchanged) when:
      - ``viewer`` is None/empty
      - ``search.context_rerank.enabled`` is False (config)
      - ``sentence-transformers`` cross-encoder isn't loaded (no rerank_score)

    Args:
        results: Reranked results list (each has ``rerank_score``).
        query: Original search query (for logging, unused in scoring).
        top_k: Number of results to return after re-ranking.
        viewer: Name of the calling agent/user.

    Returns:
        Re-ranked results list (sorted desc by adjusted score).
    """
    if not viewer or not results:
        return results[:top_k]

    from memall.config import get_config

    if not get_config("search.context_rerank.enabled", False):
        return results[:top_k]

    # Only adjust if cross-encoder scores exist (reranker ran)
    if "rerank_score" not in results[0]:
        return results[:top_k]

    weight = get_config("search.context_rerank.weight", 0.15)
    freshness_boost = get_config("search.context_rerank.freshness_boost", 1.1)
    affinity_boost = get_config("search.context_rerank.affinity_boost", 1.2)

    # 1. Domain affinity: find viewer's recent category distribution
    viewer_categories: dict[str, int] = {}
    try:
        with _pool_conn() as conn:
            recent_searches = conn.execute(
                "SELECT category, COUNT(*) as cnt FROM memories "
                "WHERE LOWER(agent_name) = LOWER(?) AND category != '' "
                "AND created_at > datetime('now', '-7 days') "
                "GROUP BY category ORDER BY cnt DESC LIMIT 5",
                (viewer,),
            ).fetchall()
            viewer_categories = {r["category"]: r["cnt"] for r in recent_searches}
    except Exception:
        viewer_categories = {}

    viewer_lower = viewer.lower()

    # 2. Apply per-result boost
    from datetime import timedelta as _td
    _freshness_cutoff = (datetime.now(timezone.utc) - _td(days=7)).date().isoformat()
    for r in results:
        boost = 1.0
        cat = (r.get("category") or "").lower()

        # Domain affinity: viewer's frequent categories
        if cat in viewer_categories:
            boost += (affinity_boost - 1.0) * min(1.0, viewer_categories[cat] / 5)

        # Agent affinity: same author
        agent = (r.get("agent_name") or "").lower()
        if agent == viewer_lower:
            boost += (affinity_boost - 1.0) * 0.5

        # Freshness boost: recent memories (~7 day window)
        created = r.get("created_at") or r.get("occurred_at") or ""
        if created and created[:10] > _freshness_cutoff:
            boost += (freshness_boost - 1.0)

        r["context_score"] = (r.get("rerank_score", 0) or 0) * (1 + weight * (boost - 1.0))
        r["context_boost"] = round(boost - 1.0, 3)

    # Sort by adjusted context_score
    results.sort(key=lambda x: -(x.get("context_score", 0) or 0))

    # Enforce: top 3 from cross-encoder stay in top 3 (freshness can't jump the queue)
    # Re-sort: first, pin the top 3 by rerank_score at positions 0-2
    top3_ids = {r["memory_id"] for r in sorted(
        results, key=lambda x: -(x.get("rerank_score", 0) or 0)
    )[:3]}

    pinned = [r for r in results if r["memory_id"] in top3_ids]
    unpinned = [r for r in results if r["memory_id"] not in top3_ids]
    pinned.sort(key=lambda x: -(x.get("rerank_score", 0) or 0))
    unpinned.sort(key=lambda x: -(x.get("context_score", 0) or 0))

    combined = (pinned + unpinned)[:top_k]
    return combined


def vector_search(query: str, top_k: int = 10, provider: Optional[str] = None) -> dict:
    """Semantic vector search.

    Uses the configured search provider (default vec0 KNN via bge-small-zh-v1.5).
    Set ``provider="faiss"`` to use FAISS.
    """
    from memall.config import get_config
    active = provider or get_config("search.provider", "faiss")
    if active == "faiss":
        from memall.search import get_provider
        p = get_provider("faiss")
        if p is not None:
            return p.search(query, top_k=top_k)
    from memall.graph.retrieve import retrieve as graph_retrieve
    return graph_retrieve(query, mode="vector", top_k=top_k)


def hybrid_search(query: str, top_k: int = 10, rrf_k: Optional[int] = None,
                  category: Optional[str] = None, level: Optional[str] = None,
                  owner: Optional[str] = None, rerank: bool = False,
                  viewer: Optional[str] = None) -> dict:
    """RRF (Reciprocal Rank Fusion) hybrid search combining FTS5 + vec0.

    1. FTS5 keyword search → ranked results
    2. vec0 KNN vector search → ranked results
    3. RRF merge: score = 1/(rrf_k + rank_fts) + 1/(rrf_k + rank_vec)
    4. (optional) Cross-encoder reranking of top candidates
    5. (optional) Context-aware re-ranking using viewer profile

    Optional metadata filters (``category``, ``level``, ``owner``) are applied
    before the RRF merge, reducing candidate pool size.

    When ``rerank=True`` the top ``search.rerank_top_k`` candidates
    are re-scored by a cross-encoder model for improved relevance ordering.
    If ``viewer`` is also provided, a final context-aware micro-adjustment
    is applied based on the viewer's recent category preferences.

    Requires ``pip install memall-db[rerank]`` (heavy: ~1.8GB with PyTorch).
    Falls back to RRF-only ordering if the model is unavailable.

    Returns dict with ``results`` (each includes memory_id, content, subject,
    category, level, owner, agent_name, rrf_score, fts_rank, vec_rank),
    ``total``, and per-source hit counts.
    """
    from memall.graph.retrieve import _query_embed, _vec0_knn
    from memall.config import get_config

    if rrf_k is None:
        rrf_k = get_config("search.rrf_k", 60)

    dispatch_lifecycle(HOOK_PRE_SEARCH, query=query, top_k=top_k, rrf_k=rrf_k,
                       category=category, level=level, owner=owner)

    def _apply_meta_filters(rows: list) -> list:
        filtered = rows
        if category:
            filtered = [r for r in filtered if r.get("category") == category]
        if level:
            filtered = [r for r in filtered if r.get("level") == level]
        if owner:
            filtered = [r for r in filtered if r.get("owner") == owner]
        return filtered

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
                    f"SELECT id, content, subject, category, level, owner, agent_name, created_at FROM memories WHERE id IN ({placeholders})",
                    ids,
                ).fetchall()
        fts_rows = _apply_meta_filters(fts_rows)

        # vec0 KNN results
        query_vec = _query_embed(query)
        vec_rows = []
        if query_vec is not None:
            vec_results = _vec0_knn(conn, query_vec, top_k * 2)
            for vr in vec_results:
                row = conn.execute(
                    "SELECT id, content, subject, category, level, owner, agent_name, created_at FROM memories WHERE id = ?",
                    (vr["memory_id"],),
                ).fetchone()
                if row:
                    vec_rows.append(row)
        vec_rows = _apply_meta_filters(vec_rows)

        if not fts_rows and not vec_rows:
            return {
                "query": query, "mode": "hybrid_rrf",
                "results": [],
                "total": 0,
            }

        # RRF merge
        scores: dict[int, dict] = {}
        for rank, r in enumerate(fts_rows):
            scores[r["id"]] = {
                "memory_id": r["id"],
                "content": r["content"][:200],
                "subject": r["subject"] or "",
                "category": r["category"] or "",
                "level": r["level"] or "",
                "owner": r["owner"] or "",
                "agent_name": r["agent_name"] or "",
                "created_at": r["created_at"] or "",
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
                    "subject": r["subject"] or "",
                    "category": r["category"] or "",
                    "level": r["level"] or "",
                    "owner": r["owner"] or "",
                    "agent_name": r["agent_name"] or "",
                    "created_at": r["created_at"] or "",
                    "rrf_score": 1.0 / (rrf_k + rank + 1),
                    "fts_rank": None,
                    "vec_rank": rank + 1,
                }

        sorted_results = sorted(scores.values(), key=lambda x: -x["rrf_score"])

        # Visibility filter: apply before returning results
        if viewer and sorted_results:
            visibility_scores = _filter_by_trust_dict(sorted_results, viewer)
            sorted_results = [r for r in sorted_results if visibility_scores.get(r["memory_id"], True)]

        if rerank:
            from memall.config import get_config
            if get_config("search.rerank_enabled", True):
                sorted_results = _rerank(sorted_results, query, top_k)
                # Context-aware re-ranking (micro-adjustment after cross-encoder)
                sorted_results = _context_rerank(sorted_results, query, top_k, viewer=viewer)
            else:
                sorted_results = sorted_results[:top_k]
        else:
            sorted_results = sorted_results[:top_k]

        dispatch_lifecycle(HOOK_POST_SEARCH, query=query, results=sorted_results,
                           total=len(scores), fts_hits=len(fts_rows), vec_hits=len(vec_rows))
        return {
            "query": query,
            "mode": "hybrid_rerank" if rerank else "hybrid_rrf",
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
