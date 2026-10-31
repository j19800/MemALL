import logging
import json
import random
import re
import threading
import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
from typing import Optional
from .db import get_pool, content_hash
from .levels import normalize_level
from .models import Memory, MemoryInput
from .nlp import cosine_sim, compute_tfidf
from .project_infer import infer_project
from .status import TASK_STATUSES, task_status_of
from memall.config import get_config
from memall.core.lifecycle import (
    dispatch_lifecycle,
    HOOK_PRE_CAPTURE,
    HOOK_POST_CAPTURE,
    HOOK_PRE_STORE,
    HOOK_POST_STORE,
    HOOK_PRE_RETRIEVE,
    HOOK_POST_RETRIEVE,
    HOOK_PRE_SEARCH,
    HOOK_POST_SEARCH,
)

logger = logging.getLogger(__name__)


# Valid agent_name pattern: simple identifiers (alphanumeric, underscore, hyphen, dot, @, CJK)
_VALID_AGENT_RE = re.compile(r"^[a-z0-9_@.\u4e00-\u9fff-]+$")
_CJK_RE = re.compile(
    r"[\u4e00-\u9fff\u3400-\u4dbf\uf900-\ufaff\u2e80-\u2eff\u2f00-\u2fdf]+"
)
_CJK_STOP: set[str] = {
    "的",
    "了",
    "在",
    "是",
    "我",
    "有",
    "和",
    "就",
    "不",
    "人",
    "都",
    "一",
    "个",
    "上",
    "也",
    "很",
    "到",
    "说",
    "要",
    "去",
    "你",
    "会",
    "着",
    "没有",
    "看",
    "好",
    "自己",
    "这",
    "那",
    "哪",
    "什么",
    "怎么",
    "如何",
    "为什么",
}

_HAS_JIEBA = False
try:
    import jieba

    _HAS_JIEBA = True
except ImportError:
    pass
_AGENT_TAG_RE = re.compile(r"(\d{4}-\d{2}-\d{2}|\d{10,})")
_AGENT_BLACKLIST = frozenset(
    {
        "architecture",
        "brainstorm",
        "unknown",
        "session_active",
        "general",
    }
)


# Security: whitelist of column names permitted in UPDATE SET clause
_ALLOWED_UPDATE_FIELDS = frozenset(
    {
        "level",
        "category",
        "project",
        "summary",
        "subject",
        "confidence",
        "visibility",
        "content",
        "agent_name",
        "owner",
        "metadata",
    }
)

# Valid L5 workflow statuses — the single track lives in core.status.
_VALID_L5_STATUSES = TASK_STATUSES


@contextmanager
def _pool_conn():
    """Context manager wrapping ConnectionPool.get() / .put().

    Commits on success, rolls back on exception — mirrors db.pool_conn.
    """
    import sys as _sys

    pool = get_pool()
    conn = pool.get()
    try:
        yield conn
    except Exception:
        try:
            conn.rollback()
        except sqlite3.Error as e:
            logger.warning("Rollback failed during exception path: %s", e)
        raise
    finally:
        if not _sys.exc_info()[0]:
            try:
                conn.commit()
            except sqlite3.Error as e:
                logger.warning("Commit failed on context exit: %s", e)
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
    "L1": "[L1 身份]",
    "L2": "[L2 时间]",
    "L3": "[L3 流程]",
    "L4": "[L4 会话]",
    "L5": "[L5 计划]",
    "L6": "[L6 反思]",
    "L6-聚合": "[L6 聚合]",
    "L6-周反思": "[L6 周反思]",
    "L6-月反思": "[L6 月反思]",
    "L7": "[L7 教训]",
    "L8": "[L8 关系]",
    "L9": "[L9 蒸馏]",
    "L9-聚合": "[L9 聚合]",
    "L10": "[L10 整合]",
    "L11": "[L11 商业]",
    "P0": "[P0 原始]",
    "P1": "[P1 原始]",
    "P2": "[P2 原始]",
    "P3": "[P3 原始]",
    "P4": "[P4 原始]",
}

# Conversation filler starts to strip when generating subject
_FILLER_STARTS = [
    "好的，",
    "好的 ",
    "明白了，",
    "明白了 ",
    "我知道了，",
    "我知道了 ",
    "我觉得",
    "我认为",
    "我想",
    "关于",
    "嗯，",
    "嗯 ",
    "呃，",
    "呃 ",
    "那个",
    "这个",
    "然后",
    "所以",
]

# Machine/template markers embedded in content by external writers, e.g.
# "[NOVEL:她从神殿来][FILE:庆余年文风][CHUNK:7/9]" or "[MODULE:layer/mcp]".
# These must never reach a human-facing subject line.
_TEMPLATE_MARKERS_RE = re.compile(
    r"\[(?:NOVEL|FILE|CHUNK|MODULE|PART|VOL|CHAPTER|SECTION)\s*:[^\]\n]{0,80}\]",
    re.IGNORECASE,
)

# The set of bracket labels a subject line MAY start with (level + category
# prefixes produced by our own naming systems). Anything else in brackets is
# noise.  Content-side variants ([L10 全局知识], [L6-周反思], [L7 教训] ...)
# are matched by pattern, not membership: a label is OURS when it starts
# with an L/P code.
_CANONICAL_SUBJECT_PREFIXES = (
    {p for p in _LEVEL_SUBJECT_PREFIX.values() if p}
    | {p for p in _SUBJECT_PREFIX.values() if p}
)
_OUR_LABEL_RE = re.compile(
    r"^\[([LP]\d{1,2}(?:-[^\]\n]{1,12})?[^\]\n]{0,12})\]\s*"
)


def _split_own_label(text: str) -> tuple[str, str]:
    """Split a leading OURS-style bracket label; return (label+space, rest)."""
    m = _OUR_LABEL_RE.match(text)
    if m:
        return f"[{m.group(1)}] ", text[m.end():]
    m = re.match(r"^(\[[^\]\n]{1,20}\])\s*", text)
    if m and m.group(1) in _CANONICAL_SUBJECT_PREFIXES:
        return m.group(1) + " ", text[m.end():]
    return "", text


def sanitize_subject(value: str, max_len: int = 120) -> str:
    """Force any subject (caller-provided or generated) to human-readable form.

    Rules:
      - drop [NOVEL:...] [MODULE:...] style template marker spans
      - collapse newlines/tabs/whitespace runs to single spaces
      - keep a canonical [L6 反思] / [决策] style prefix intact when present
      - remove stray unbalanced brackets, trim edge punctuation, cap length

    Returns "" when nothing readable survives — callers then auto-generate.

    Every write path funnels through capture(); sanitising caller-supplied
    subjects here is the single place that can keep the store's titles
    readable (the audit found 194 live rows whose subject contained raw
    newlines, 171 carrying [NOVEL:/[MODULE: markers — all pasted through
    untouched because externally supplied subjects were never validated).
    """
    if not value:
        return ""
    s = _TEMPLATE_MARKERS_RE.sub(" ", value)
    s = re.sub(r"[\r\n\t]+", " ", s)
    s = re.sub(r"\s{2,}", " ", s).strip()

    prefix, s = _split_own_label(s)
    s = re.sub(r"^#{1,6}\s*", "", s)             # markdown header after the label
    # unbalanced stray brackets are reader noise; balanced ones (e.g.
    # "(EP09《七日》)", "[正文]") keep their content but drop the marks
    # only when they no longer pair up
    if s.count("[") != s.count("]"):
        s = s.replace("[", "").replace("]", "")

    # Edge trim: leading brackets/punctuation are scaffolding noise, but a
    # TRAILING closing bracket may legitimately close a pair inside the
    # string ("…（历史截断）") — stripping it by character-set broke exactly
    # that (six repair outputs lost their "）"). Only whitespace and
    # sentential punctuation (which never opens a pair) trim off the tail.
    s = s.lstrip("{}（）() 　,，.。;；:：、")
    s = s.rstrip(" 　,，.。;；:：、")
    if len(prefix) + len(s) > max_len:
        room = max(0, max_len - len(prefix) - 1)
        s = s[:room].rstrip(" ,，.。;；:：、") + "…"
    return (prefix + s).strip()


def normalize_agent_name(name: str) -> str:
    """Normalize and validate an agent_name. Returns safe fallback "system" if invalid.

    The redirect to "system" is logged (once per distinct input): silently
    renaming an identity means memories land attributed to the wrong agent and
    every later agent-scoped query misses them.  A visible warning costs
    nothing and turns a silent data-attribution failure into a diagnosable
    one (this exact redirect cost a debugging cycle when a timestamp-suffixed
    test agent was quietly renamed).
    """
    if not name:
        return "system"
    original = name
    name = name.strip().lower()
    if (
        not _VALID_AGENT_RE.match(name)
        or name in _AGENT_BLACKLIST
        or _AGENT_TAG_RE.search(name)
    ):
        _warn_identity_redirect(original, "invalid characters or embedded timestamp")
        return "system"
    # Reject single-character names (template leaks, parse artifacts, stray symbols)
    if len(name) < 2:
        _warn_identity_redirect(original, "single character")
        return "system"
    # Reject template variable leaks: system.agent_name, demo.agent_name, etc.
    if name.endswith(".agent_name"):
        _warn_identity_redirect(original, "template variable leak")
        return "system"
    # Reject names containing template delimiters
    if "{" in name or "}" in name:
        _warn_identity_redirect(original, "template delimiter")
        return "system"
    # Reject single-CJK-character names (会, 全, 本, etc. — always parse artifacts)
    cjk_count = sum(1 for c in name if "一" <= c <= "鿿" or "㐀" <= c <= "䶿")
    if cjk_count >= 1 and len(name) == cjk_count:
        # Pure CJK names: require at least 2 CJK characters
        if cjk_count < 2:
            _warn_identity_redirect(original, "single CJK character")
            return "system"
    return name


# Distinct inputs warned about, bounded so a spammy producer cannot grow it.
_IDENTITY_WARNED: set = set()


def _warn_identity_redirect(original: str, why: str) -> None:
    key = (original or "").strip()[:80]
    if key in _IDENTITY_WARNED or len(_IDENTITY_WARNED) > 200:
        return
    _IDENTITY_WARNED.add(key)
    logger.warning(
        "agent_name %r redirected to 'system' (%s) — memories will be attributed "
        "to 'system', not this identity. Fix the producer if this is unexpected.",
        original, why,
    )


def _make_subject(
    content: str, category: str, level: str, agent_name: str, owner: str
) -> str:
    """Auto-generate a human-readable subject line.

    Format: [LevelPrefix] Who: core_phrase  (≤60 chars)
    Example: [L4 会话] admin: 从 SQLite 迁移到 PostgreSQL
    Falls back to category-based prefix if level has no mapping.
    """
    # Prefer level-based prefix, fallback to category-based. BUT when the
    # content itself opens with one of OUR canonical labels ("[L7 教训] …"),
    # that label is the more truthful type — adopt it and drop the inferred
    # one, or the title reads "[L11 商业]… [L7 教训] …" double-prefixed
    # (the preview pass showed exactly this collision on distilled rows
    # whose level drifted during re-classification).
    prefix = _LEVEL_SUBJECT_PREFIX.get(level, _SUBJECT_PREFIX.get(category, ""))

    # Extract core phrase: strip filler starts, take first meaningful segment
    core = content.strip()
    own_label, core = _split_own_label(core)
    if own_label:
        prefix = own_label.strip()  # the content's own label wins
    # Template markers ([NOVEL:..][FILE:..][CHUNK:..], [MODULE:..]) are
    # writer scaffolding; a title must read like a sentence, not a diff
    # header. (Audit: 171 production subjects carried these verbatim.)
    core = _TEMPLATE_MARKERS_RE.sub(" ", core).strip()

    # Structured payloads (identity cards, agent-to-agent bridge messages)
    # legitimately keep JSON bodies — a consumer parses them. But a title
    # of '{\n  "timestamp"…' is not a title: render the JSON as a human
    # line over its informative fields instead.
    if (core.startswith("{") and core.endswith("}")) or (
        core.startswith("[") and core.endswith("]")
    ):
        try:
            doc = json.loads(core)
        except (json.JSONDecodeError, TypeError):
            doc = None
        if isinstance(doc, list):
            # MCP-style tool-response dump: [{"type":"text","text":…}, …]
            texts = [d.get("text", "") for d in doc
                     if isinstance(d, dict) and isinstance(d.get("text"), str)]
            joined = " ".join(t for t in texts if t).strip()
            core = joined[:80] if joined else "工具返回内容"
        if isinstance(doc, dict):
            def _txt(k):
                v = doc.get(k)
                return v.strip() if isinstance(v, str) and v.strip() else ""

            topic = _txt("topic") or _txt("title") or _txt("subject")
            frm, to, name = _txt("from"), _txt("to"), _txt("name")
            role, body = _txt("role"), _txt("message") or _txt("body")
            if frm and topic:
                core = f"来自 {frm} 的消息：{topic}"
                if to:
                    core += f"（致 {to}）"
            elif topic:
                core = topic
            elif name:
                core = f"身份：{name}（{role}）" if role else f"身份：{name}"
            else:
                picks = [f"{k}={str(v)[:40]}" for k, v in list(doc.items())[:4]
                         if k not in ("timestamp", "ts", "version") and v]
                core = " ".join(picks) if picks else "结构化数据"
            core += "" if not body else f"：{body[:60]}"
            core = core.strip()
    for filler in _FILLER_STARTS:
        if core.startswith(filler):
            core = core[len(filler) :].strip()
            break

    # Try sentence boundary first (Chinese then English)
    for sep in ("。", "！", "？", "；", "\n", ". ", "! ", "? "):
        idx = core.find(sep)
        if 10 < idx < 60:
            core = core[:idx]
            break
    else:
        # A first line longer than the boundary window is still cut at its
        # line end when one exists — otherwise the newline leaks into the
        # subject (audit: 194 subjects with embedded \n). A SHORT first
        # line (<=10, e.g. a "# 标题" header) is skipped in favour of the
        # line below it, which carries the actual phrase.
        nl = core.find("\n")
        if nl > 10:
            core = core[:nl]
        elif nl != -1 and core[nl + 1:].strip():
            core = core[nl + 1:].strip()[:35]
        else:
            core = core[:35]

    # Collapse any surviving whitespace/marker noise in the phrase itself
    # (sanitise the CORE only — the assembled label's [L4 会话] style prefix
    # is intentional and must not go through bracket-edge trimming).
    core = sanitize_subject(core, max_len=60)

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
        id=row["id"],
        content=row["content"],
        content_hash=row["content_hash"],
        level=row["level"],
        owner=row["owner"],
        agent_name=row["agent_name"],
        # creator = the agent that triggered the write (v0.1.61 owner/creator
        # semantics); guarded for pre-migration-025 databases.
        creator=row["creator"] if "creator" in row.keys() else "",
        subject=row["subject"],
        project=row["project"],
        category=row["category"],
        summary=row["summary"],
        occurred_at=row["occurred_at"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        supersedes=row["supersedes"],
        confidence=row["confidence"],
        visibility=row["visibility"],
        access_count=row["access_count"],
        metadata=row["metadata"],
        thread_id=row["thread_id"],
        agent_name_locked=bool(row["agent_name_locked"]),
        tags=row["tags"] if "tags" in row.keys() else "[]",
    )


# Pre-compiled regex for specificity scoring in _score_quality
_SPECIFICITY_RE = re.compile(r"\d{4}|v\d+\.\d+|[A-Z]{2,}\d*|#\d+")
# Reasoning markers (pre-compiled)
_REASONING_COMPILED: list[re.Pattern] = []
# Both languages: ``_score_quality`` uses this to score the "reasoning"
# dimension, and level L6 hard-rejects anything scoring < 2.  When the list
# was Chinese-only, every English reflection scored 0 and was rejected — a
# silent language bias that made L6 unusable for English content.
_REASONING_MARKERS = [
    # ── Chinese ──
    "因为",
    "所以",
    "根因",
    "原因是",
    "取决于",
    "比较",
    "权衡",
    "方案",
    "选",
    "采用",
    "决定",
    "结论",
    "数据",
    "从.*看",
    "分析",
    "调研",
    "实测",
    "问题",
    "瓶颈",
    "不足",
    "改进",
    "用户说",
    "你的意思是",
    "确认",
    # ── English equivalents ──
    r"\bbecause\b",
    r"\btherefore\b",
    r"\bhence\b",
    r"\broot\s+cause\b",
    r"\bdepends?\s+on\b",
    r"\bcompar(e|ed|ing|ison)\b",
    r"\btrade[\s-]?off\b",
    r"\bapproach\b",
    r"\boption\b",
    r"\bdecided?\b",
    r"\bdecision\b",
    r"\bconclusion\b",
    r"\bdata\b",
    r"\bevidence\b",
    r"\banalys[ei]s\b",
    r"\banalyz(e|ed|ing)\b",
    r"\bresearch\b",
    r"\bbenchmark\b",
    r"\bmeasured?\b",
    r"\bproblem\b",
    r"\bbottleneck\b",
    r"\bbug\b",
    r"\blimitation\b",
    r"\bimprove[ment]?\b",
    r"\buser\s+said\b",
    r"\bconfirm(ed)?\b",
]
_REASONING_COMPILED = [re.compile(p, re.IGNORECASE) for p in _REASONING_MARKERS]

_QUALITY_DIMS = [
    "completeness",
    "clarity",
    "relevance",
    "specificity",
    "reasoning",  # replaces persistence — measures "有理有据"
    "source_traceability",
    "context_stability",
    "sensitivity",
]


def _score_quality(data: MemoryInput, content_hash_val: str,
                   caller_subject: str | None = None) -> dict:
    """Score a memory pre-write. ``caller_subject`` is the raw subject the
    caller supplied (before sanitising/auto-generation); machine-looking
    titles are penalised here so the gate sees the violation the
    sanitiser would otherwise hide."""
    text = data.content or ""
    text_len = len(text.strip())
    scores: dict = {}

    scores["completeness"] = min(10, max(0, (text_len - 10) // 20))
    filler = ["嗯", "那个", "然后", "所以", "呃", "啊", "好的，", "明白了，"]
    scores["clarity"] = (
        10 if text_len > 40 else max(0, 10 - sum(text.count(f) for f in filler) * 2)
    )
    # Readability penalty applied even to long content: template-marker text
    # ("[NOVEL:..][FILE:..][CHUNK:..]" / raw JSON dumps) reads as machine
    # output no matter its length, and titles are the human-facing surface.
    # Markers in the SUBJECT are worse than in content: content may
    # legitimately embed snippets, a subject may never embed scaffolding
    # (audit: 171 marker / 194 newline subjects — the quality gate scored
    # them 10/10 clarity because it only ever looked at the body).
    if _TEMPLATE_MARKERS_RE.search(text):
        scores["clarity"] = min(scores["clarity"], 5)
    subj_probe = caller_subject if caller_subject is not None else (data.subject or "")
    if subj_probe and (subj_probe != sanitize_subject(subj_probe)
                       or _TEMPLATE_MARKERS_RE.search(subj_probe)):
        scores["clarity"] = min(scores["clarity"], 3)
    # Raw JSON as the whole memory: machine telemetry dressed as a memory.
    stripped = text.lstrip()
    if stripped.startswith("{") and stripped.endswith("}"):
        scores["clarity"] = min(scores["clarity"], 4)
    scores["relevance"] = 7
    scores["specificity"] = max(1, min(10, len(_SPECIFICITY_RE.findall(text)) * 2))

    # "reasoning" — measures whether content contains evidence/analysis language
    reasoning_hits = sum(1 for p in _REASONING_COMPILED if p.search(text))
    scores["reasoning"] = min(10, max(0, reasoning_hits * 2))

    scores["source_traceability"] = 8 if data.agent_name and data.owner else 4
    scores["context_stability"] = 8 if "临时" not in text and "暂时" not in text else 3
    scores["sensitivity"] = (
        10
        if not re.search(
            r"(password|token|secret|apikey|api_key|sk-)\s*[:=]", text, re.I
        )
        else 2
    )

    for k in _QUALITY_DIMS:
        scores[k] = max(0, min(10, scores[k]))

    avg = sum(scores.values()) / len(scores) if scores else 0
    min_dim = min(scores.values()) if scores else 0

    # Get quality thresholds from config, with fallback to hardcoded values
    quality_config = get_config("quality.thresholds", {})
    default_threshold_map = {
        "P0": 4,
        "P1": 5,
        "P2": 4,
        "P3": 4,
        "P4": 3,
        "L4": 5,
        "L5": 5,  # decisions + tasks need reasoning
        "L6": 5,  # reflections need substance
        "L7": 4,
        "L9": 4,
        "L10": 4,
        "L11": 4,
    }
    # Merge config with defaults (config overrides defaults)
    threshold_map = {**default_threshold_map, **quality_config}
    required = threshold_map.get(data.level or "P2", 5)

    # Get quality gate policy from config
    gate_policy = get_config("quality.gate_policy", "strict")  # strict or lenient
    
    # Get minimum content length from config, with fallback to 15
    min_length = get_config("quality.min_length", 15)
    # Minimum content length: content under min_length chars is too short to be meaningful
    if text_len < min_length:
        gate = "rejected"
    # Level-specific gate: L6 reflections MUST have reasoning >= 2
    elif data.level == "L6" and scores["reasoning"] < 2:
        passed = False
        gate = "rejected"
    elif data.level in ("L4", "L5") and not data.subject:
        passed = False
        gate = "rejected"
    else:
        passed = avg >= required
        if gate_policy == "strict":
            gate = (
                "accepted" if passed else ("review" if avg >= required - 1 else "rejected")
            )
        else:  # lenient policy
            # Only reject if significantly below threshold (more than 2 points below)
            gate = (
                "accepted" if passed else ("review" if avg >= required - 2 else "rejected")
            )

    result = {
        "dimensions": scores,
        "avg": round(avg, 2),
        "min": min_dim,
        "gate": gate,
        "level": data.level,
    }
    return result


def _category_validity(cat: str, min_len: int) -> tuple[bool, str]:
    """Structural check on a category value.

    Guards against the class of pollution that once filled the DB with values
    like ``决``/``策``/``[``/`` `` — produced when a distill step sliced a
    two-char domain name into single characters.  Legitimate short tags
    (``AI``, ``QA``, ``决策``) must still pass, so the bar is structural only:
    at least ``min_len`` chars, no whitespace, printable, and containing at
    least one alphanumeric character.
    """
    c = (cat or "").strip()
    if not c:
        return False, "empty"
    if len(c) < min_len:
        return False, f"too short ({len(c)} < {min_len})"
    if any(ch.isspace() for ch in c):
        return False, "contains whitespace"
    if not c.isprintable():
        return False, "contains control character"
    if not any(ch.isalnum() for ch in c):
        return False, "no alphanumeric characters"
    return True, ""


def _validate_category(data: MemoryInput) -> None:
    """Reject or coerce structurally invalid categories at the write entry.

    The enum (``capture.allowed_categories``) is opt-in: leaving it empty keeps
    the system open to new domain names while still blocking garbage.  Populate
    it where a closed vocabulary is known up front.
    """
    if not get_config("capture.category_validation_enabled", True):
        return
    min_len = int(get_config("capture.category_min_length", 2) or 2)
    allowed = get_config("capture.allowed_categories", None) or []
    action = (
        get_config("capture.category_invalid_action", "coerce") or "coerce"
    ).lower()

    cat = (data.category or "").strip()
    ok, why = _category_validity(cat, min_len)
    if ok and allowed:
        ok = cat.lower() in {str(a).strip().lower() for a in allowed}
        why = "outside allowed_categories" if not ok else ""

    if ok:
        data.category = cat
        return

    msg = f"invalid category {cat!r} ({why})"
    if action == "reject":
        raise ValueError(f"capture: {msg}")
    logger.warning(
        "capture: %s -> coercing to 'general' (agent=%s)", msg, data.agent_name
    )
    data.category = "general"


def classify_stream(category: str, source: str = "") -> str:
    """Separate system bookkeeping (``ledger``) from genuine knowledge.

    Design principle #1 (memory #2833): 旁路捕捉，Agent 感觉不到 MemALL 存在。
    But the implementation drifted into recording its OWN actions as memories
    (915 "本次会话记录了 N 条记忆").  The cure is a two-stream split:

    * ``knowledge`` — real captured content (decisions, discussions, rules,
      genuine reflections).  Eligible for distillation and retrieval.  This is
      the DEFAULT for every user capture.
    * ``ledger`` — pipeline-generated operational bookkeeping (session-harvest
      L4/L6, distillation L9/L10/L11).  Tagged at the producer, never
      re-distilled, low retrieval priority.

    ``ledger_categories`` is a deployment hook for operational noise that
    arrives through the normal capture() path (heartbeat/testing).  It is empty
    by default so real session/meeting/reflection captures stay ``knowledge``.
    """
    ledger = get_config("capture.ledger_categories", [])
    if category and category in ledger:
        return "ledger"
    return "knowledge"


def _capture_normalize_and_validate(
    data: MemoryInput | dict | str, **overrides
) -> MemoryInput:
    """Normalize input type, apply overrides, validate content, set agent/owner defaults."""
    # Which fields the CALLER explicitly provided (dict keys + overrides).
    # The classifier must not overwrite explicitly-chosen category/level:
    # rule-based reclassification silently breaks consumer contracts —
    # notify_pending_tasks() only sees category='task', and a task written
    # as such was flipped to fix/testing by a keyword rule, so the whole
    # reminder chain returned zero (audit P1-16, reproduced live).
    explicit = set(overrides)
    if isinstance(data, dict):
        explicit |= {k for k, v in data.items() if v not in (None, "")}
    elif isinstance(data, MemoryInput):
        # A constructed MemoryInput carries no provenance of "explicit vs
        # default"; fall back to value comparison — a non-default
        # category/level was set deliberately by someone.
        if data.category and data.category != "general":
            explicit |= {"category"}
        if data.level and data.level != "P2":
            explicit |= {"level"}

    if isinstance(data, str):
        data = MemoryInput(content=data)
    elif isinstance(data, dict):
        data = MemoryInput(**data)
    for k, v in overrides.items():
        if hasattr(data, k):
            setattr(data, k, v)

    # Carry the explicit-field provenance to the metadata injector (kept on
    # the dataclass so it survives without a second parameter everywhere).
    try:
        data._pinned = ",".join(sorted(f for f in explicit if f in ("category", "level")))
    except (AttributeError, TypeError):
        pass

    if not data.content.strip():
        raise ValueError("content cannot be empty")

    content_len = len(data.content.strip())
    if content_len < 50:
        logger.warning(
            f"capture: very short memory ({content_len} chars) agent={data.agent_name} cat={data.category}: {data.content[:60]}"
        )
    elif content_len < 80:
        logger.info(
            f"capture: short memory ({content_len} chars) agent={data.agent_name} cat={data.category}: {data.content[:60]}"
        )

    # Deny-list on the RAW identity, before normalisation.  A name like
    # "probe123456789012" is redirected to "system" by normalize_agent_name
    # (10+ consecutive digits), and "system" is not on the deny-list — so a
    # test identity could smuggle writes into the store simply by carrying a
    # timestamp suffix, bypassing exactly the gate meant to stop it.
    _check_disabled_identity_raw(data)

    data.agent_name = normalize_agent_name(data.agent_name)
    _validate_category(data)
    # level must land inside the canonical enum before it reaches the INSERT:
    # the DB CHECK rejects anything else with IntegrityError, and this is the
    # single write entry every path funnels through (MCP / smart_store /
    # store_batch / agent_memory.add / raw calls).
    data.level = normalize_level(data.level)
    # creator = the agent that triggered the write; default to the writing agent.
    if not data.creator:
        data.creator = data.agent_name
    # owner must ALWAYS be a human (design invariant #1 from memory #823).  A
    # missing owner falls back to the configured human owner — never to the
    # writing agent, which would silently turn a human-owned memory into an
    # agent-owned one.
    if not data.owner:
        data.owner = get_config("identity.human_owner", "老陈")
    # stream: default every user capture to 'knowledge' (see classify_stream).
    # Pipeline artifacts are tagged 'ledger' at their producer instead.
    data.stream = data.stream or classify_stream(data.category)
    return data


def _check_disabled_identity_raw(data: MemoryInput) -> None:
    """Deny-list check against the *raw* identity, before normalisation.

    normalize_agent_name() redirects names that fail validation (10+
    consecutive digits, template leaks, single chars) to "system".  Running
    the deny-list after that lets a test identity like
    "probe123456789012" — which matches the "probe*" ban — slip through as
    "system".  Checking the raw value first closes that bypass; the
    post-normalisation check in capture() still covers the inferred project.
    """
    from memall.config import get_config
    from memall.core.denylist import matches_any

    raw = (data.agent_name or "").strip()
    if not raw:
        return
    patterns = get_config("security.disabled_agents", []) or []
    if matches_any(raw, patterns):
        raise ValueError(
            f"capture: agent {raw!r} is disabled "
            f"(security.disabled_agents) — memory not stored"
        )


def _capture_check_disabled(data: MemoryInput) -> None:
    """Reject writes from/into disabled identities (write deny-list).

    ``security.disabled_projects`` / ``security.disabled_agents`` mirror
    dsh-meow-memory's ``disabledWorkspaces``: one-off task agents and test
    identities otherwise pollute the long-term store.  Patterns support globs
    (``autocap*``) — per-run suffixed identities cannot be enumerated by hand,
    which is exactly the case this gate exists for.  Only the *write* path is
    gated; reads stay available so already-stored history remains inspectable.

    Raises ValueError naming the offending value and the config key to edit.
    """
    from memall.core.denylist import is_disabled_agent, is_disabled_project

    if data.project and is_disabled_project(data.project):
        raise ValueError(
            f"capture: project {data.project!r} is disabled "
            f"(security.disabled_projects) — memory not stored"
        )
    if data.agent_name and is_disabled_agent(data.agent_name):
        raise ValueError(
            f"capture: agent {data.agent_name!r} is disabled "
            f"(security.disabled_agents) — memory not stored"
        )


def _capture_assign_tags(data: MemoryInput) -> None:
    """Fill ``data.tags`` with derived intent keywords when the caller left it empty.

    Caller-supplied tags are always respected; this only fills the empty case.
    Auto-derivation is gated by ``capture.auto_tags_enabled``.  Tags are indexed
    as their own FTS5 column (see ``db.memall_tags_text``), so a memory becomes
    findable by what it is *about* rather than only by words that happen to
    appear in its text — the gap that left ``memories.tags`` written-but-unread.
    """
    from memall.core.tags import normalize_tags, extract_auto_tags

    existing = normalize_tags(data.tags)
    if existing:
        # Normalise the caller's list even when we do not derive more, so the
        # stored JSON is always canonical (deduped, length-bounded).
        data.tags = json.dumps(existing, ensure_ascii=False)
        return
    if not get_config("capture.auto_tags_enabled", True):
        data.tags = "[]"
        return
    max_tags = int(get_config("capture.auto_tags_max", 8) or 8)
    derived = extract_auto_tags(
        data.content, subject=data.subject, agent_name=data.agent_name,
        max_tags=max_tags,
    )
    data.tags = json.dumps(derived, ensure_ascii=False)


def _capture_inject_metadata(
    data: MemoryInput, accumulate_key: str | None = None,
    explicit_fields: set | None = None,
) -> MemoryInput:
    """Inject provenance source and optional accumulate_key into metadata."""
    from memall.core.pins import CONTRACT_CATEGORIES

    if isinstance(data.metadata, dict):
        if "source" not in data.metadata:
            data.metadata["source"] = "capture_api"
    else:
        try:
            existing_meta = json.loads(data.metadata or "{}")
        except json.JSONDecodeError:
            existing_meta = {}
        if "source" not in existing_meta:
            existing_meta["source"] = "capture_api"
        data.metadata = existing_meta

    # Record which classification fields the caller set deliberately so the
    # rule-based classifiers (event_processor._inline_classify on every
    # new-memory event, and classify_step on batch runs) never rewrite them.
    # Values the caller chose are the caller's contract; keyword rules are a
    # fallback for fields nobody chose, not an authority over chosen ones.
    if isinstance(data.metadata, dict):
        pinned = sorted(
            {f for f in (explicit_fields or ()) if f in ("category", "level")}
            | {f for f in (getattr(data, "_pinned", "") or "").split(",") if f}
        )
        # Contract categories are pinned by definition even when provenance
        # was unreadable (a post-construction mutation of a default
        # MemoryInput): anything in CONTRACT_CATEGORIES is chosen, never a
        # default.
        if data.category in CONTRACT_CATEGORIES and "category" not in pinned:
            pinned.append("category")
        if pinned:
            data.metadata["pinned_fields"] = pinned
        else:
            data.metadata.pop("pinned_fields", None)

    if accumulate_key:
        if isinstance(data.metadata, dict):
            data.metadata["accumulate_key"] = accumulate_key
        else:
            try:
                existing_meta = json.loads(data.metadata or "{}")
            except json.JSONDecodeError:
                existing_meta = {}
            existing_meta["accumulate_key"] = accumulate_key
            data.metadata = existing_meta

    # An L5 task is ACTIVE until someone marks it done.  Without this the
    # task chain could not see tasks created through the generic capture
    # path: notify_pending_tasks() selects
    # json_extract(metadata,'$.status')='active', and a capture(level='L5',
    # category='task') never set it — the reminder chain returned zero for
    # every task written that way (audit P1-16, second half).
    #
    # metadata.status is the only workflow track now (memory_status no longer
    # mirrors it), so an out-of-vocabulary value here would create a card no
    # reader can see.  Reject it at the funnel instead.
    if data.level == "L5" and data.category in ("task", "discussion"):
        supplied = None
        if isinstance(data.metadata, dict):
            supplied = data.metadata.get("status")
            if data.category == "task" and not supplied:
                data.metadata["status"] = "active"
                supplied = None
            # Make the dispatch target explicit (design #1000007): a task card
            # is addressed to one agent and routing should not have to infer
            # it from the owner field.  agent_name stays the fallback for
            # every card written before this field existed.
            if (data.category == "task" and not data.metadata.get("assignee")
                    and data.agent_name):
                data.metadata["assignee"] = data.agent_name
        elif isinstance(data.metadata, str):
            try:
                parsed = json.loads(data.metadata or "{}")
            except (json.JSONDecodeError, TypeError):
                parsed = None
            if isinstance(parsed, dict):
                supplied = parsed.get("status")
        if supplied is not None and task_status_of(supplied) is None:
            raise ValueError(
                f"invalid L5 status {supplied!r} for {data.category}: must be "
                f"one of {', '.join(sorted(_VALID_L5_STATUSES))}"
            )
    return data


def _capture_inject_quality(
    data: MemoryInput, quality_result: dict, now: str
) -> MemoryInput:
    """Inject quality scoring result into metadata."""
    quality_entry = {
        "value": quality_result,
        "_meta": {"version": 1, "written_at": now},
    }
    if isinstance(data.metadata, dict):
        data.metadata["quality"] = quality_entry
    else:
        try:
            existing_meta = json.loads(data.metadata or "{}")
        except json.JSONDecodeError:
            existing_meta = {}
        existing_meta["quality"] = quality_entry
        data.metadata = existing_meta
    return data


def _should_promote_level(
    new: str, old: str,
    priority: dict, lifecycle: dict,
) -> bool:
    """Decide whether a hash-hit re-capture may change the stored ``level``.

    ``level`` has two incomparable bands (priority P0/P1/P2 vs lifecycle
    L1..L11).  Crossing bands is a deliberate re-classification by the caller,
    so the new value always wins.  Within a band we only move toward the more
    significant end, so a stray lower value cannot demote a distilled memory.
    """
    if new == old:
        return False
    new_p, old_p = new in priority, old in priority
    if new_p != old_p:
        return True  # band change = re-classification
    if new_p and old_p:
        return priority[new] > priority[old]
    return lifecycle.get(new, 0) > lifecycle.get(old, 0)


def _capture_dedup_check(
    conn, data: MemoryInput, h: str, now: str, accumulate_key: str | None = None
) -> int | None:
    """Check for existing memories: content_hash, L7 accumulate, L5 dedup.
    Returns existing memory id on hit, None to proceed with insert."""
    cur = conn.execute(
        "SELECT id, level, category, subject, project, summary, confidence "
        "FROM memories WHERE content_hash = ? "
        # A CLOSED workflow card is history, not a duplicate write.  Without
        # this, re-filing an identical L5 card after it was closed returned
        # the dead row's id: the caller saw "captured", the reminder chain saw
        # a terminal card, and the work never re-entered anyone's queue
        # (measured on goal re-creation, P2-2).
        "AND NOT (level = 'L5' AND COALESCE("
        "  CASE WHEN json_valid(metadata) "
        "       THEN json_extract(metadata, '$.status') ELSE 'active' END, "
        "  'active') IN ('done', 'resolved', 'archived', 'converged', "
        "                'verified', 'closed'))",
        (h,),
    )
    existing = cur.fetchone()
    if existing:
        # ── Promote, don't just re-count ──
        # A hash hit used to only bump access_count.  That silently swallowed
        # the caller's intent when the same content is re-captured at a higher
        # level (e.g. first noted as P2, later recognised as a decision worth
        # L4): the row kept the old level and no signal was returned.
        #
        # ``level`` has TWO bands that are not comparable on one scale:
        #   * priority  P0/P1/P2  — how urgent (P0 highest)
        #   * lifecycle L1..L11   — what kind / how distilled (L11 highest)
        # Re-capturing across bands is a *re-classification* (the caller has
        # decided this content is really a decision, not a note), so the new
        # value wins.  Within a band we only move toward the more significant
        # end, so an accidental lower value cannot demote a distilled memory.
        _PRIORITY = {"P0": 3, "P1": 2, "P2": 1}
        _LIFECYCLE = {f"L{i}": i for i in range(1, 12)}
        promotions: list[str] = []
        params: list = []
        if _should_promote_level(data.level, existing["level"], _PRIORITY, _LIFECYCLE):
            promotions.append("level = ?")
            params.append(data.level)
        if data.category and data.category != existing["category"]:
            promotions.append("category = ?")
            params.append(data.category)
        for field in ("subject", "project", "summary"):
            if getattr(data, field, "") and not existing[field]:
                promotions.append(f"{field} = ?")
                params.append(getattr(data, field))
        if promotions:
            params.extend([now, existing["id"]])
            conn.execute(
                f"UPDATE memories SET {', '.join(promotions)}, "
                f"updated_at = ? WHERE id = ?",
                params,
            )
            logger.info(
                "capture: hash hit on #%d promoted (%s)", existing["id"],
                ", ".join(p.split(" =")[0] for p in promotions),
            )
        # Audit P1-4: a hash hit (re-writing the same content) is a WRITE,
        # not a READ — counting it as an access inflated the hot-topic
        # ranking and the decay score with duplicate writes.  Re-writes are
        # counted in their own column; access_count now means "times this
        # memory was retrieved".
        conn.execute(
            "UPDATE memories SET dedup_hits = COALESCE(dedup_hits, 0) + 1 "
            "WHERE id = ?",
            (existing["id"],),
        )
        conn.commit()
        return existing["id"]

    # ── Defect fix: semantic de-duplication (config-gated) ──
    # Exact-hash matching above misses near-duplicate memories that differ in
    # wording but mean the same thing — the source of the ~166 semantic
    # duplicates. We run a TF-IDF cosine check over recent same-agent memories
    # here, at the single write entry, so every caller benefits. DISABLED by
    # default (see config) to avoid falsely merging legitimately distinct
    # memories; enable where the trade-off is acceptable.
    if get_config("capture.semantic_dedup_enabled", False):
        threshold = get_config("capture.semantic_dedup_threshold", 0.92)
        window = get_config("capture.semantic_dedup_window", 30)
        if threshold > 0 and window > 0:
            dup_id = _semantic_dedup_lookup(conn, data, threshold, window)
            if dup_id is not None:
                # Audit P1-4: a semantic dedup hit is a duplicate WRITE,
                # not a read — count it in dedup_hits, not access_count.
                conn.execute(
                    "UPDATE memories SET dedup_hits = COALESCE(dedup_hits, 0) + 1, "
                    "updated_at = ? WHERE id = ?",
                    (now, dup_id),
                )
                conn.commit()
                logger.info(
                    "capture: semantic dedup -> reuse mem_id=%d (agent=%s, threshold=%.2f)",
                    dup_id,
                    data.agent_name,
                    threshold,
                )
                return dup_id

    # L7 accumulate_key: same pattern corrected again → weight++
    if accumulate_key and data.level == "L7":
        dup = conn.execute(
            "SELECT id, weight FROM memories WHERE level = 'L7' AND accumulate_key = ? LIMIT 1",
            (accumulate_key,),
        ).fetchone()
        if dup:
            new_weight = (dup["weight"] or 1) + 1
            conn.execute(
                "UPDATE memories SET weight = ?, content = ?, content_hash = ?, updated_at = ?, "
                "dedup_hits = COALESCE(dedup_hits, 0) + 1 WHERE id = ?",
                (new_weight, data.content, h, now, dup["id"]),
            )
            conn.commit()
            logger.info(
                "capture: L7 accumulate_key '%s' → weight=%d (mem_id=%d)",
                accumulate_key,
                new_weight,
                dup["id"],
            )
            return dup["id"]

    # L5 duplicate: same agent + subject → merge metadata, don't duplicate.
    # TERMINAL cards are excluded: a closed/cancelled card is history, not a
    # duplicate.  Measured (P2-2): re-creating a goal with the same objective
    # after it closed returned the CLOSED row's id, so the caller believed a
    # fresh goal existed while every subsequent write merged into a done card —
    # the new goal silently never existed.
    if data.level == "L5" and data.agent_name and data.subject:
        dup = conn.execute(
            "SELECT id, metadata FROM memories "
            "WHERE level = 'L5' AND agent_name = ? AND subject = ? "
            "AND COALESCE(CASE WHEN json_valid(metadata) "
            "                  THEN json_extract(metadata, '$.status') "
            "                  ELSE 'active' END, 'active') "
            "    NOT IN ('done', 'resolved', 'archived', 'converged', "
            "            'verified', 'closed') "
            "AND COALESCE(memory_status, '') != 'archived' "
            "ORDER BY id DESC LIMIT 1",
            (data.agent_name, data.subject),
        ).fetchone()
        if dup:
            existing_meta = {}
            try:
                raw = dup["metadata"]
                existing_meta = (
                    json.loads(raw) if isinstance(raw, str) and raw.strip() else {}
                )
            except json.JSONDecodeError:
                existing_meta = {}
            incoming = data.metadata or {}
            if isinstance(incoming, str):
                try:
                    incoming = json.loads(incoming)
                except json.JSONDecodeError:
                    incoming = {}
            if isinstance(existing_meta, dict) and isinstance(incoming, dict):
                merged = {**existing_meta, **incoming}
                conn.execute(
                    "UPDATE memories SET metadata = ?, updated_at = ? WHERE id = ?",
                    (json.dumps(merged, ensure_ascii=False), now, dup["id"]),
                )
                conn.commit()
                return dup["id"]
    return None


def _capture_prepare_identity(conn, data: MemoryInput) -> None:
    """Verify agent identity, auto-register if missing, clamp visibility."""
    if not data.agent_name:
        return
    ident = conn.execute(
        "SELECT id FROM identities WHERE agent_name = ?", (data.agent_name,)
    ).fetchone()
    if not ident:
        conn.execute(
            "INSERT OR IGNORE INTO identities (agent_name, agent_type) "
            "VALUES (?, 'ai')",
            (data.agent_name,),
        )
        logger.info(
            "capture: auto-registered agent '%s' in identities table", data.agent_name
        )

    # NOTE: previously this block silently rewrote ``owner`` to the writing agent
    # whenever the agent had no ``trusted_by`` entry for it — violating invariant
    # #1 ("owner is always a human; creator is the writing agent").  That
    # degradation has been removed.  ``owner`` is now validated-only: an illegal
    # owner is never silently changed, so a caller-supplied human owner (e.g.
    # owner='老陈', agent_name='claude') survives the write intact.

    data.visibility = _get_allowed_write_visibility(
        conn, data.agent_name, data.visibility
    )


def _infer_thread_root(conn, data: MemoryInput, now: str) -> Optional[int]:
    """Return a stable thread root id for ``data`` inside a recent time window.
    
    First tries temporal window approach. If that fails to find a candidate,
    falls back to semantic similarity search (M2 enhancement).

    A "thread" groups memories of the same agent and (inferred) project that
    arrive close together in time. The root is the *earliest* memory in the
    window, so every subsequent memory in the thread shares the same root and
    ``traverse(..., thread_aware=True)`` can surface all siblings.

    Returns None when the feature is disabled (window <= 0) or no recent
    same-agent + same-project memory exists yet.
    """
    # First try: temporal window approach (existing logic)
    window_min = get_config("capture.thread_inference_window_minutes", 0)
    if not window_min or window_min <= 0:
        # Temporal feature disabled, go straight to semantic fallback
        pass
    else:
        proj = data.project or ""
        try:
            cutoff = (
                datetime.fromisoformat(now) - timedelta(minutes=int(window_min))
            ).isoformat()
        except (ValueError, OverflowError):
            cutoff = None
        
        if cutoff is not None:
            try:
                row = conn.execute(
                    "SELECT id FROM memories "
                    "WHERE agent_name = ? AND project = ? AND created_at >= ? "
                    "ORDER BY created_at ASC LIMIT 1",
                    (data.agent_name, proj, cutoff),
                ).fetchone()
            except sqlite3.Error:
                row = None
            
            if row is not None:
                return row["id"] if row else None
    
    # Second try: semantic similarity fallback (M2 enhancement)
    # Only enable semantic fallback if we have content to compare
    if not data.content or len(data.content.strip()) < 10:
        return None
        
    semantic_window = get_config("capture.thread_inference_semantic_window", 50)
    semantic_threshold = get_config("capture.thread_inference_semantic_threshold", 0.7)
    
    if semantic_window <= 0:
        return None
        
    try:
        # Get recent memories from the same agent for semantic comparison
        recent = conn.execute(
            "SELECT id, content FROM memories "
            "WHERE agent_name = ? AND LENGTH(TRIM(content)) >= 10 "
            "ORDER BY created_at DESC LIMIT ?",
            (data.agent_name, int(semantic_window)),
        ).fetchall()
    except sqlite3.Error:
        recent = []
    
    if not recent:
        return None
    
    # Use existing semantic dedup function to find the most similar memory
    try:
        best_id = _semantic_dedup_lookup(
            conn, 
            data, 
            threshold=semantic_threshold, 
            window=semantic_window
        )
        return best_id
    except Exception:
        # If semantic search fails, return None
        return None


def _semantic_dedup_lookup(
    conn, data: MemoryInput, threshold: float, window: int
) -> Optional[int]:
    """Find the most similar recent same-agent memory exceeding ``threshold``.

    Uses TF-IDF cosine similarity over the ``window`` most recent memories of the
    same agent. Returns the best match id, or None if nothing clears the bar.
    """
    try:
        recent = conn.execute(
            "SELECT id, content FROM memories "
            "WHERE agent_name = ? AND LENGTH(TRIM(content)) > 10 "
            "ORDER BY created_at DESC LIMIT ?",
            (data.agent_name, int(window)),
        ).fetchall()
    except sqlite3.Error:
        return None
    if not recent:
        return None
    texts = [data.content[:1000]] + [r["content"][:1000] for r in recent]
    try:
        tfidf_docs = compute_tfidf(texts)
    except Exception:
        return None
    base = tfidf_docs[0]
    best_id, best_sim = None, 0.0
    for i, r in enumerate(recent):
        sim = cosine_sim(base, tfidf_docs[i + 1])
        if sim >= threshold and sim > best_sim:
            best_sim, best_id = sim, r["id"]
    return best_id


def _capture_insert_row(conn, data: MemoryInput, h: str, now: str) -> int:
    """Insert the memory row with race-condition fallback on content_hash collision."""
    occurred = data.occurred_at or now
    supersedes = (
        data.supersedes if data.supersedes and data.supersedes != "[]" else "[]"
    )

    # ``memory_status`` is the storage-lifecycle track only (owned by the
    # pipeline); an L5 card's workflow status lives in ``metadata.status``.
    # Mirroring the latter into the former — which this used to do — put task
    # vocabulary into the lifecycle track, so ``retrieve()``'s not-archived
    # filter and the pipeline's ``memory_status IS NULL`` preconditions
    # disagreed with the card's real state.  Migration 034 cleared the legacy
    # mirrored values.
    meta = data.metadata
    meta_dict = json.loads(meta) if isinstance(meta, str) else (meta or {})
    memory_status = None
    accumulate_key_val = meta_dict.get("accumulate_key")

    try:
        cur = conn.execute(
            """INSERT INTO memories
               (content, content_hash, level, owner, agent_name, creator, subject,
                project, category, summary, occurred_at, created_at, updated_at,
                supersedes, confidence, visibility, metadata, tags, thread_id,
                agent_name_locked, memory_status, accumulate_key, stream)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                data.content,
                h,
                data.level,
                data.owner,
                data.agent_name,
                data.creator,
                data.subject,
                data.project,
                data.category,
                data.summary,
                occurred,
                now,
                now,
                supersedes,
                # ``confidence`` is NOT NULL. Passing None explicitly bypasses
                # the column DEFAULT 0.5 and hits IntegrityError *inside a
                # background thread*, so the write is lost while the caller
                # still reports success. Coalesce here.
                data.confidence if data.confidence is not None else 0.5,
                data.visibility,
                json.dumps(data.metadata)
                if isinstance(data.metadata, dict)
                else data.metadata,
                data.tags,
                data.thread_id,
                0,
                memory_status,
                accumulate_key_val,
                data.stream,
            ),
        )
        return cur.lastrowid
    except Exception:
        conn.rollback()
        existing = conn.execute(
            "SELECT id FROM memories WHERE content_hash = ?", (h,)
        ).fetchone()
        if existing:
            return existing["id"]
        raise


def _capture_post_insert(conn, mem_id: int, data: MemoryInput, h: str) -> None:
    """Post-insert side effects: pipeline event, L4 arc, auto-embedding, dream scan."""
    try:
        conn.execute(
            "INSERT INTO pipeline_events (memory_id, event_type, created_at) VALUES (?, 'new_memory', datetime('now'))",
            (mem_id,),
        )
        conn.commit()
    except sqlite3.Error:
        logger.warning("pipeline_events insert failed", exc_info=True)

    if data.level == "L4":
        conn.execute("UPDATE memories SET arc_status = 'open' WHERE id = ?", (mem_id,))
        conn.commit()

    # Offload heavy post-processing (embedding + dream_scan) to a background thread
    # so the capture() caller does not block on model inference or pattern matching.
    _bg_content = data.content
    _bg_agent = data.agent_name
    _bg_category = data.category
    # Bind the thread to the DB this capture wrote to. Reading the module-global
    # DB_PATH *inside* the thread lets it target a different database if the path
    # is repointed between spawn and run (test fixtures do exactly this), which
    # surfaces as a "database is locked" race on the freshly created file.
    from memall.core.db import DB_PATH as _bg_db_path

    def _background_post_process():
        """Run embedding and dream_scan in a daemon thread."""
        from memall.core.db import get_conn as _bg_get_conn

        _bg_conn = _bg_get_conn(_bg_db_path)
        try:
            try:
                from memall.graph.embeddings import _auto_embed

                # 不再用 _check_st_available() 门禁：本机走 ONNX bge 路径时
                # ST 不可用，原条件恒 False 会导致新记忆漏建向量，只能靠全量
                # rebuild。_auto_embed 内部经 _embed_texts_named 自动按
                # ST → ONNX → TF-IDF 顺序降级，无需此处前置门槛。
                _auto_embed(_bg_conn, mem_id, _bg_content, h)
                _bg_conn.commit()
            except Exception:
                logger.debug("bg embedding failed", exc_info=True)

            try:
                from memall.config import get_config as _get_dream_config

                if _get_dream_config("dream.enabled", True):
                    from memall.pipeline.dream import dream_scan

                    _dreams = dream_scan(
                        _bg_conn,
                        new_mem_id=mem_id,
                        agent_name=_bg_agent,
                        content=_bg_content,
                        category=_bg_category,
                        scan_window=_get_dream_config("dream.scan_window", 50),
                        threshold=_get_dream_config("dream.threshold", 0.4),
                    )
                    _bg_conn.commit()
            except Exception:
                logger.debug("bg dream_scan failed")
        finally:
            try:
                _bg_conn.close()
            except Exception:
                logger.debug("capture bg: connection close failed", exc_info=True)

    from memall.core.bg_registry import spawn as _bg_spawn
    _bg_spawn(_background_post_process)

    # Note: dream_scan results are now processed in the background thread above.
    # The commit and logging are handled inside _background_post_process.

    dispatch_lifecycle(HOOK_POST_CAPTURE, data=data, memory_id=mem_id)


def _capture_write_once(conn, data: MemoryInput, h: str, now: str,
                        accumulate_key) -> int:
    """One attempt at the write half of capture: dedup → identity → insert."""
    existing_id = _capture_dedup_check(conn, data, h, now, accumulate_key)
    if existing_id is not None:
        return existing_id

    _capture_prepare_identity(conn, data)

    # ── Defect fix: populate thread_id so thread-aware retrieve/traverse works ──
    # thread_id is a back-pointer to the *root* memory of the current
    # conversation thread. It was never assigned on the write path, leaving
    # traverse(..., thread_aware=True) permanently dead. We link the new memory
    # to the earliest same-agent + same-project memory inside a recent window,
    # which establishes a stable thread root all siblings share.
    if data.thread_id is None:
        data.thread_id = _infer_thread_root(conn, data, now)

    mem_id = _capture_insert_row(conn, data, h, now)
    _capture_post_insert(conn, mem_id, data, h)

    # Contextual recall: after storing a memory, check for related high-level
    # memories in the same project.
    if get_config("contextual_recall_enabled", True):
        try:
            _contextual_recall(conn, mem_id, data)
        except Exception as e:
            logger.warning(f"Contextual recall failed: {e}", exc_info=True)

    return mem_id


def _is_lock_error(exc: BaseException) -> bool:
    msg = str(exc).lower()
    return "locked" in msg or "busy" in msg


def capture(
    data: MemoryInput | dict | str, accumulate_key: str | None = None, **overrides
) -> int:
    data = _capture_normalize_and_validate(data, **overrides)

    # ── Defect fix: project must never be empty ──
    # capture() is the single write entry for every path (MCP capture / smart_store /
    # store_batch / agent_memory.add / raw calls). Inferring the project HERE — instead
    # of only in a few outer callers — guarantees the field is populated no matter who
    # invoked us. This is the root-cause fix for the ~77% empty-project data-quality issue.
    if not data.project and get_config("capture.project_inference_enabled", True):
        data.project = infer_project(
            agent_name=data.agent_name,
            category=data.category,
            content=data.content,
        )

    # Write deny-list (security.disabled_projects / disabled_agents).  Runs
    # AFTER project inference so an inferred project can also be blocked.
    _capture_check_disabled(data)

    now = datetime.now(timezone.utc).isoformat()
    h = content_hash(data.content)

    dispatch_lifecycle(HOOK_PRE_CAPTURE, blocking=True, data=data)

    # Remember what the CALLER supplied before anything is rewritten: the
    # quality gate penalises machine-looking titles (markers/newlines) on
    # this raw value; sanitising first would hide the violation.
    caller_subject = data.subject

    # Auto-generate subject if not provided by caller; when the caller DID
    # provide one, sanitise it (markers, newlines, edge junk). Titles are
    # what humans and agents re-read from search results — the audit found
    # 366+ live rows whose subject was raw writer scaffolding ("[NOVEL:...]
    # ...\n..." pasted verbatim), and every one of them arrived as an
    # externally-supplied subject that the generation path never saw.
    if data.subject:
        cleaned = sanitize_subject(data.subject)
        if cleaned != data.subject:
            logger.debug("capture: subject sanitised %r -> %r",
                         data.subject[:40], cleaned[:40])
        data.subject = cleaned
    if not data.subject:
        data.subject = _make_subject(
            data.content, data.category, data.level, data.agent_name, data.owner
        )

    # Enforce subject for L4+
    if data.level in ("L4", "L5", "L6", "L7", "L9", "L10", "L11") and not data.subject:
        logger.warning(
            "capture: %s memory missing subject, content=%.60s",
            data.level,
            data.content or "",
        )
        data.subject = _make_subject(
            data.content, data.category, data.level, data.agent_name, data.owner
        )

    quality_result = _score_quality(data, h, caller_subject=caller_subject)
    quality_gate = quality_result.get("gate", "accepted")

    if quality_gate == "rejected":
        raise ValueError(
            f"quality gate rejected (avg={quality_result.get('avg', 0):.2f}, "
            f"min={quality_result.get('min', 0)}, "
            f"len={len(data.content or '')}) — content too short or low quality"
        )
    elif quality_gate == "review":
        logger.debug(
            "capture: quality gate review (avg=%.2f, min=%d) agent=%s cat=%s",
            quality_result.get("avg", 0),
            quality_result.get("min", 0),
            data.agent_name,
            data.category,
        )

    data = _capture_inject_metadata(data, accumulate_key)
    data = _capture_inject_quality(data, quality_result, now)
    # Derive intent keywords when the caller supplied none (subject is now
    # finalised, so the auto-tagger can use it as the highest-signal source).
    _capture_assign_tags(data)

    # SQLite in WAL still refuses an upgrade from a read snapshot to a writer
    # when another connection committed in between: ``BEGIN ... SELECT ... INSERT``
    # raises "database is locked" IMMEDIATELY and ``busy_timeout`` does not apply
    # to that case.  capture() always reads first (hash dedup, identity, thread
    # root), so under concurrent writes — a background pipeline thread plus an
    # agent tool call — the INSERT is exactly that upgrade.  Measured on the live
    # MCP server: a capture burst lost a write to "database is locked" while the
    # caller was told the tool failed.  A retry is safe because the read half is
    # idempotent: if a competing writer won, its row is found by the hash dedup.
    attempts = max(1, int(get_config("capture.lock_retries", 5) or 5))
    for attempt in range(attempts):
        try:
            with _pool_conn() as conn:
                return _capture_write_once(conn, data, h, now, accumulate_key)
        except sqlite3.OperationalError as exc:
            if not _is_lock_error(exc) or attempt == attempts - 1:
                raise
            wait = min(0.05 * (2 ** attempt), 1.0) + random.uniform(0, 0.03)
            logger.warning(
                "capture: %s (attempt %d/%d), retrying in %.2fs",
                exc, attempt + 1, attempts, wait,
            )
            time.sleep(wait)
    raise RuntimeError("capture: retry loop exited without result")  # pragma: no cover


def _contextual_recall(conn, mem_id: int, data: MemoryInput) -> None:
    """After storing a memory, surface related high-level memories in the same project.
    
    This implements M1: post-capture hook to surface related L4/L6/L9+ memories 
    based on current content.
    """
    # Only run contextual recall for knowledge stream (not ledger)
    if classify_stream(data.category or "") != "knowledge":
        return
        
    # Only run for memories that have a project
    project = (data.project or "").strip()
    if not project:
        return
        
    # Only surface memories of level L4, L6, L9, L10, L11 (high-level knowledge)
    target_levels = {"L4", "L6", "L9", "L10", "L11"}
    if data.level not in target_levels:
        # We're storing a lower-level memory, look for related high-level memories
        pass
    else:
        # We're storing a high-level memory, still look for related high-level memories
        # (could be useful for connecting related insights)
        pass
        
    # Use FTS5 to find related memories in the same project with high levels
    # We'll search for key terms from the current content
    text = data.content or ""
    if len(text.strip()) < 10:  # Too short to extract meaningful terms
        return
        
    # Extract key terms (simple approach: look for specific patterns)
    import re
    # Look for technical terms, numbers, quoted content, etc.
    terms = []
    
    # Extract quoted content
    quoted_matches = re.findall(r'["“]([^"”]+)["”]', text)
    terms.extend(quoted_matches)
    
    # Extract technical patterns (version numbers, etc.)
    tech_matches = re.findall(r'\b[vV]\d+\.\d+\b|\b\d{4}\b|\b[A-Z]{2,}\d*\b', text)
    terms.extend(tech_matches)
    
    # Extract potential domain-specific terms (capitalized phrases)
    cap_matches = re.findall(r'\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\b', text)
    terms.extend(cap_matches[:5])  # Limit to avoid too many terms
    
    # If we didn't find enough specific terms, use some general content
    if len(terms) < 2:
        # Split by common punctuation and take meaningful words
        words = re.findall(r'\b[^\W\d_]{2,}\b', text.lower())
        # Filter out very common words
        stopwords = {'的', '了', '在', '是', '我', '有', '和', '就', '不', '人', '都', '一', '一个', '上', '也', '很', '到', '说', '要', '去', '你', '会', '着', '没有', '看', '好', '自己', '这'}
        terms = [w for w in words if w not in stopwords and len(w) >= 2][:10]
    
    if not terms:
        return
        
    # Build search query from terms (OR them together for broader recall)
    query_terms = []
    for term in terms[:8]:  # Limit number of terms
        if len(term.strip()) >= 2:
            query_terms.append(f'"{term}"')
    
    if not query_terms:
        return
        
    # Search for memories in the same project with high levels
    # We'll use a simplified approach: search for each term and combine results
    try:
        from memall.core.db import SCHEMA_SQL
        import sqlite3
        
        # Create a temporary connection for the search (to avoid interfering with current transaction)
        # But we'll use the same connection since we're already in a transaction
        # We need to be careful not to interfere with the current transaction
        
        # For now, we'll implement a simple version that searches for memories
        # with similar content in the same project and high levels
        
        # Get the current memory's content for similarity comparison
        current_content = (data.content or "").lower()
        
        # Search for recent memories in the same project with high levels
        # We'll look back at recent memories (last 100) to find related ones
        cursor = conn.execute("""
            SELECT id, content, level, subject, created_at
            FROM memories 
            WHERE project = ? 
              AND level IN ('L4', 'L6', 'L9', 'L10', 'L11')
              AND id != ?
              AND LENGTH(TRIM(content)) >= 10
            ORDER BY created_at DESC 
            LIMIT 100
        """, (project, mem_id))
        
        related_memories = []
        for row in cursor.fetchall():
            mem_id_db, content, level, subject, created_at = row
            if not content:
                continue
                
            # Simple similarity check: look for common terms
            content_lower = content.lower()
            common_terms = 0
            for term in terms[:5]:  # Check against top 5 terms
                if term.lower() in content_lower:
                    common_terms += 1
            
            # If we found at least 2 common terms, consider it related
            if common_terms >= 2:
                related_memories.append({
                    'id': mem_id_db,
                    'content': content[:100] + ('...' if len(content) > 100 else ''),
                    'subject': subject,
                    'level': level,
                    'common_terms': common_terms
                })
        
        # Log the contextual recall results (in a real implementation, this would trigger notifications)
        if related_memories:
            logger.info(f"Contextual recall for memory {mem_id} found {len(related_memories)} related high-level memories in project '{project}':")
            for mem in related_memories[:3]:  # Log top 3
                logger.info(f"  - [{mem['level']}] {mem['subject'] or mem['content'][:50]}...")
                
    except Exception as e:
        logger.warning(f"Contextual recall search failed: {e}")
        # Don't let contextual recall failures break the main capture flow


def unified_search(arguments: dict) -> str:
    """Unified search entrance that routes to FTS5, vector, or hybrid search based on mode or intent.
    Returns a JSON string with the search results.

    Enhancement arguments (all opt-in, additionally gated by config):
      - ``facet_fields``: list/"auto" — aggregate counts over candidates.
      - ``expand_query``: bool — synonym/history expansion before matching.
      - ``suggest``: bool — query completions from the corpus vocabulary.
      - ``explain``: bool — per-result score breakdown.
      - ``cluster``: bool — topic grouping of results.
      - ``personalize``: bool — per-agent affinity ranking.
      - ``context_boost``: bool — boost active project/category matches.
      - ``agent_name``: viewer identity for personalisation/context/trust.
      - ``session_id``: enables per-session novelty demotion (see
        ``memall.search.seen``; requires ``search.seen_tracking.enabled``).
    """
    import json
    query = arguments.get("query", "").strip()
    top_k = arguments.get("top_k", 10)
    mode = arguments.get("mode", "auto")

    if not query:
        return json.dumps({"error": "query is required"})

    # Enhancement flags — honoured by the fts5 / direct paths (which go
    # through enhanced_search) and by hybrid/vector via apply_enhancements.
    facet_fields = arguments.get("facet_fields")
    expand_query = bool(arguments.get("expand_query", False))
    suggest = bool(arguments.get("suggest", False))
    explain = bool(arguments.get("explain", False))
    cluster = bool(arguments.get("cluster", False))
    personalize = bool(arguments.get("personalize", False))
    context_boost = bool(arguments.get("context_boost", False))
    wants_enhancements = any(
        (facet_fields, expand_query, suggest, explain, cluster, personalize, context_boost)
    )

    if mode == "auto":
        from memall.search.intent_router import classify, resolve_mode
        intent = classify(query)
        mode = resolve_mode(intent)

    if mode in ("direct", "fts5") and wants_enhancements:
        result = enhanced_search(
            query=query,
            viewer=arguments.get("viewer"),
            agent_name=arguments.get("agent_name"),
            limit=_seen_fetch_limit(arguments, top_k),
            facet_fields=facet_fields,
            expand_query=expand_query,
            suggest=suggest,
            explain=explain,
            cluster=cluster,
            personalize=personalize,
            context_boost=context_boost,
            owner=arguments.get("owner"),
            agent_name_filter=arguments.get("agent_name"),
            category=arguments.get("category"),
            project=arguments.get("project"),
            level=arguments.get("level"),
            occurred_at=arguments.get("occurred_at"),
            from_occurred_at=arguments.get("from_occurred_at"),
            to_occurred_at=arguments.get("to_occurred_at"),
        )
        # enhanced_search returns Memory objects; serialise to the same
        # shape the plain paths use.
        items = [
            {
                "memory_id": m.id,
                "content": m.content[:200],
                "subject": m.subject,
                "category": m.category,
                "level": m.level,
                "owner": m.owner,
                "agent_name": m.agent_name,
            }
            for m in result["results"]
        ]
        items = _seen_postprocess(items, arguments, top_k=top_k)
        payload = {
            "query": query,
            "mode": mode,
            "results": items,
            "total": result.get("total", len(items)),
        }
        for key in ("facets", "suggestions", "clusters", "explanations", "expanded_query"):
            if key in result:
                payload[key] = result[key]
        return json.dumps(payload, ensure_ascii=False)

    if mode == "direct":
        # Direct lookup: pure numeric ID.
        # We use the same retrieve function (FTS5 based) but note that direct lookup in the current
        # handle_unified_search in retrieve.py uses the retrieve function and then formats the result.
        # We will do the same.
        retrieve_args = {
            "query": query,
            "limit": _seen_fetch_limit(arguments, top_k),
            "owner": arguments.get("owner"),
            "agent_name": arguments.get("agent_name"),
            "category": arguments.get("category"),
            "project": arguments.get("project"),
            "level": arguments.get("level"),
            "occurred_at": arguments.get("occurred_at"),
            "from_occurred_at": arguments.get("from_occurred_at"),
            "to_occurred_at": arguments.get("to_occurred_at"),
            "include_content": True,
        }
        retrieve_args = {k: v for k, v in retrieve_args.items() if v is not None}
        viewer = arguments.get("viewer") or arguments.get("agent_name") or ""
        result = retrieve(viewer=viewer, **retrieve_args)
        if result is None:
            return json.dumps({"query": query, "mode": "direct", "results": [], "total": 0})
        if isinstance(result, list):
            items = [{"memory_id": r.id, "content": r.content[:200], "subject": r.subject,
                      "category": r.category, "level": r.level, "owner": r.owner, "agent_name": r.agent_name}
                     for r in result]
        else:
            items = [{"memory_id": result.id, "content": result.content[:200], "subject": result.subject,
                      "category": result.category, "level": result.level, "owner": result.owner, "agent_name": result.agent_name}]
        items = _seen_postprocess(items, arguments, top_k=top_k)
        return json.dumps({"query": query, "mode": "direct", "results": items, "total": len(items)},
                           ensure_ascii=False)

    if mode == "fts5":
        retrieve_args = {
            "query": query,
            "limit": _seen_fetch_limit(arguments, top_k),
            "owner": arguments.get("owner"),
            "agent_name": arguments.get("agent_name"),
            "category": arguments.get("category"),
            "project": arguments.get("project"),
            "level": arguments.get("level"),
            "occurred_at": arguments.get("occurred_at"),
            "from_occurred_at": arguments.get("from_occurred_at"),
            "to_occurred_at": arguments.get("to_occurred_at"),
            "include_content": True,
        }
        retrieve_args = {k: v for k, v in retrieve_args.items() if v is not None}
        viewer = arguments.get("viewer") or arguments.get("agent_name") or ""
        result = retrieve(viewer=viewer, **retrieve_args)
        if result is None:
            return json.dumps({"query": query, "mode": "fts5", "results": [], "total": 0})
        if isinstance(result, list):
            items = [{"memory_id": r.id, "content": r.content[:200], "subject": r.subject,
                      "category": r.category, "level": r.level, "owner": r.owner, "agent_name": r.agent_name}
                     for r in result]
        else:
            items = [{"memory_id": result.id, "content": result.content[:200], "subject": result.subject,
                      "category": result.category, "level": result.level, "owner": result.owner, "agent_name": result.agent_name}]
        items = _seen_postprocess(items, arguments, top_k=top_k)
        return json.dumps({"query": query, "mode": "fts5", "results": items, "total": len(items)},
                           ensure_ascii=False)

    if mode == "vector":
        # Vector search: filters are applied on the returned candidates
        # (audit P1-6: the function had no way to isolate by project /
        # category / level / agent).
        result = vector_search(
            query=query,
            top_k=_seen_fetch_limit(arguments, top_k),
            category=arguments.get("category"),
            level=arguments.get("level"),
            owner=arguments.get("owner"),
            project=arguments.get("project"),
            agent_name=arguments.get("agent_name"),
            viewer=arguments.get("viewer") or arguments.get("agent_name"),
        )
        if wants_enhancements:
            _apply_enhancements_inplace(
                result, arguments,
                expand_query=expand_query, suggest=suggest, explain=explain,
                cluster=cluster, personalize=personalize, context_boost=context_boost,
            )
        if isinstance(result, dict) and isinstance(result.get("results"), list):
            result["results"] = _seen_postprocess(result["results"], arguments, top_k=top_k)
        return json.dumps(result, ensure_ascii=False, default=str)

    # Default to hybrid search
    result = hybrid_search(
        query=query,
        top_k=_seen_fetch_limit(arguments, top_k),
        rrf_k=arguments.get("rrf_k", 60),
        category=arguments.get("category"),
        level=arguments.get("level"),
        owner=arguments.get("owner"),
        project=arguments.get("project"),
        agent_name=arguments.get("agent_name"),
        rerank=arguments.get("rerank", False),
        viewer=arguments.get("viewer") or arguments.get("agent_name"),
    )
    if wants_enhancements:
        _apply_enhancements_inplace(
            result, arguments,
            expand_query=expand_query, suggest=suggest, explain=explain,
            cluster=cluster, personalize=personalize, context_boost=context_boost,
        )
    if isinstance(result, dict) and isinstance(result.get("results"), list):
        result["results"] = _seen_postprocess(result["results"], arguments, top_k=top_k)
    result["mode"] = mode if mode != "auto" else result.get("mode", "hybrid_rrf")
    return json.dumps(result, ensure_ascii=False, default=str)


def _seen_state(arguments: dict) -> tuple:
    """(enabled, agent_name, session_id) for seen tracking, or ("", "", "")."""
    session_id = (arguments.get("session_id") or "").strip()
    if not session_id:
        return False, "", ""
    try:
        from memall.config import get_config
        if not get_config("search.seen_tracking.enabled", False):
            return False, "", ""
        agent_name = arguments.get("agent_name") or arguments.get("viewer") or ""
        return True, agent_name, session_id
    except Exception:
        return False, "", ""


def _seen_fetch_limit(arguments: dict, base: int) -> int:
    """Widen the candidate window when seen tracking is active.

    Without over-fetching, demotion is cosmetic: the engine returns exactly
    ``base`` rows, all of which were seen on the previous call, so there is no
    novel item left to promote.  Asking for ``base + seen + protect_top``
    (capped) lets the reorder actually surface *new* results.
    """
    enabled, agent_name, session_id = _seen_state(arguments)
    if not enabled:
        return base
    try:
        from memall.search import seen as _seen_mod
        from memall.search.seen import _protect_top

        n_seen = len(_seen_mod.seen_ids(agent_name, session_id))
        return min(base + n_seen + _protect_top(), 500)
    except Exception:
        return base


def _seen_postprocess(items: list, arguments: dict, top_k: int | None = None) -> list:
    """Demote already-returned results below the protected top slice, then
    record this call's ids as seen for the session.

    No-op unless ``search.seen_tracking.enabled`` and a ``session_id`` are
    present.  Relevance is never sacrificed: the first ``protect_top`` results
    keep their position (see ``memall.search.seen`` for the anti-drift rule).
    When *top_k* is given the (over-fetched) list is truncated after reordering.
    """
    enabled, agent_name, session_id = _seen_state(arguments)
    if not enabled or not items:
        return items[:top_k] if top_k and len(items) > top_k else items
    try:
        from memall.search import seen as _seen_mod

        items = _seen_mod.reorder_novelty(items, agent_name, session_id)
        if top_k is not None:
            items = items[:top_k]
        _seen_mod.record(
            agent_name, session_id,
            [it.get("memory_id") for it in items if it.get("memory_id") is not None],
        )
    except Exception:
        logger.debug("seen postprocess failed (non-fatal)", exc_info=True)
    return items


def _apply_enhancements_inplace(result: dict, arguments: dict, **flags) -> None:
    """Apply post-retrieval enhancements to a dict-shaped search result.

    Used by the vector / hybrid paths of :func:`unified_search`, where the
    results are already dicts.  Facets and query suggestions are computed
    against the ``memories`` table with the same WHERE semantics as
    ``retrieve()`` so they describe the same candidate population.
    """
    from memall.search.enhancements import (
        apply_enhancements,
        compute_facets,
        suggest_queries,
        _default_facet_fields,
        _norm_facet_fields,
    )
    from memall.config import get_config

    query = arguments.get("query", "")
    agent_name = arguments.get("agent_name", "") or arguments.get("viewer", "") or ""
    results = result.get("results") or []

    with _pool_conn() as conn:
        fields = []
        facet_fields = arguments.get("facet_fields")
        if facet_fields:
            if isinstance(facet_fields, str) and facet_fields.strip().lower() == "auto":
                fields = _default_facet_fields()
            else:
                fields = _norm_facet_fields(
                    facet_fields, max_fields=get_config("search.facets.max_fields", 8)
                )
        if fields:
            filters = {
                k: arguments.get(k)
                for k in ("owner", "category", "project", "level")
                if arguments.get(k)
            }
            where = ["1=1"]
            params: list = []
            for key, val in filters.items():
                where.append(f"memories.{key} = ?")
                params.append(val)
            if agent_name:
                where.append("memories.agent_name = ?")
                params.append(agent_name)
            if query and isinstance(query, str):
                q = fts_query(query)
                if q:
                    where.append(
                        "memories.id IN (SELECT rowid FROM memories_fts WHERE memories_fts MATCH ?)"
                    )
                    params.append(q)
            result["facets"] = compute_facets(conn, " AND ".join(where), params, fields)

        if flags.get("suggest"):
            result["suggestions"] = suggest_queries(conn, query, limit=10)

        enhanced = apply_enhancements(
            results,
            query=query,
            conn=conn,
            agent_name=agent_name,
            explain=flags.get("explain", False),
            cluster=flags.get("cluster", False),
            personalize=flags.get("personalize", False),
            context_boost=flags.get("context_boost", False),
        )
    result["results"] = enhanced.get("results") or results
    if enhanced.get("clusters"):
        result["clusters"] = enhanced["clusters"]
    if enhanced.get("explained"):
        result["explained"] = True
    if flags.get("expand_query") and get_config("search.query_expansion.enabled", False):
        from memall.search.enhancements import expand_query as _expand, load_synonyms

        expanded = _expand(query, synonyms=load_synonyms())
        if expanded != query:
            result["expanded_query"] = expanded


def update(memory_id: int, **fields) -> bool:
    """Update fields of an existing memory.

    Only fields in ``_ALLOWED_UPDATE_FIELDS`` are accepted — all others
    are silently ignored.  This whitelist prevents SQL injection even
    though the SET clause is assembled via string formatting, because
    column names are provenance-checked against the whitelist before
    any f-string interpolation.
    """
    # The adapter hands every handler the ORIGINAL arguments under
    # __extra_args__ (so a handler can report fields the validator dropped).
    # It is bookkeeping, not a column — leaving it in `fields` made the
    # whitelist check see only that key, so a legitimate metadata update
    # was reported as "updated: false, fields: ['__extra_args__']" and the
    # L5 status never changed (the task card stayed in pending_tasks).
    fields.pop("__extra_args__", None)
    with _pool_conn() as conn:
        existing = conn.execute(
            "SELECT id, level, metadata FROM memories WHERE id = ?", (memory_id,)
        ).fetchone()
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
                scalar = task_status_of(incoming["status"])
                if scalar is None:
                    raise ValueError(
                        f"invalid L5 status {incoming['status']!r}: "
                        f"must be one of {', '.join(sorted(_VALID_L5_STATUSES))}"
                    )
                if scalar != incoming["status"]:
                    # An {value, _meta} envelope here is invisible to every
                    # json_extract reader, so flatten it before storing.
                    incoming["status"] = scalar
                    fields["metadata"] = json.dumps(incoming, ensure_ascii=False)

        # ── metadata merge (not replace) ──
        if "metadata" in fields:
            existing_meta = {}
            raw_existing = existing["metadata"]
            if raw_existing:
                try:
                    existing_meta = (
                        json.loads(raw_existing)
                        if isinstance(raw_existing, str)
                        else raw_existing
                    )
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
                            v,
                            normalized,
                        )
                    v = normalized
                elif k == "level":
                    # Keep updates inside the canonical enum too — the CHECK
                    # constraint would otherwise raise IntegrityError.
                    v = normalize_level(v)
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
        after = conn.execute(
            "SELECT level, arc_status FROM memories WHERE id = ?", (memory_id,)
        ).fetchone()
        if after and after["level"] == "L4" and after["arc_status"] is None:
            conn.execute(
                "UPDATE memories SET arc_status = 'open' WHERE id = ?", (memory_id,)
            )

        # Generic update is a second way to reach a terminal state
        # (update(status='resolved'/'done'/'archived')).  Leaving the
        # reminder behind tells agent_round the task was already notified
        # FOREVER - the (task, agent) pair is permanently muted, so a card
        # that is later re-opened can never be reminded again.  Close it here
        # as well; the lifecycle helpers do the same for their own paths.
        if existing["level"] == "L5":
            try:
                final = conn.execute(
                    "SELECT metadata FROM memories WHERE id = ?", (memory_id,)
                ).fetchone()
                fmeta = json.loads(final["metadata"] or "{}") if final else {}
                if (isinstance(fmeta, dict)
                        and task_status_of(fmeta.get("status")) in
                        ("resolved", "done", "archived", "converged")):
                    conn.execute(
                        "DELETE FROM memories WHERE category = 'task_pending' "
                        "AND json_extract(metadata, '$.task_id') = ?",
                        (memory_id,))
            except Exception:
                logger.debug("terminal reminder cleanup failed", exc_info=True)

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
                logger.warning(
                    "embedding re-embed failed (install sentence-transformers for vector search)",
                    exc_info=True,
                )

        return True


def _build_retrieve_where(conn, query, viewer, filters) -> tuple[list, list, bool]:
    """Assemble the shared WHERE clause + params for ``retrieve``.

    Returns ``(where_parts, params, is_direct)``.  Factored out so that
    :func:`enhanced_search` can reuse the *identical* filter semantics for
    its facet aggregates (facets must count the same population the results
    come from, just without LIMIT).

    Archived rows are excluded by default: ``memory_status='archived'`` is
    the soft-delete marker (ops.batch_archive / the dedup archivers), and
    the hybrid/vector paths already honour it — the plain retrieve path
    leaking archived rows into results was an inconsistency between modes
    (verified: an fts5 query returned three archived test rows in its
    top-3).  Pass ``include_archived=True`` in filters to deliberately
    inspect deleted history (a history read, not a live recall).
    """
    where = ["1=1"]
    params = []

    # Soft-delete marker. Archived rows are excluded unless the caller
    # deliberately asks to inspect deleted history. This lives here (not only
    # in the scanning pipeline steps) so every mode of retrieve() /
    # enhanced_search() / unified_search shares one definition.
    if not filters.get("include_archived"):
        where.append("COALESCE(memories.memory_status, '') != 'archived'")

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

    # Explicit tag filter: exact match against the JSON array (json_each),
    # NOT a text search — "检" must not match the tag "检索".  Complements
    # the FTS tags column: FTS gives recall by topic, this gives precise
    # "tagged exactly with X" narrowing.
    tag_filter = filters.get("tags")
    if tag_filter:
        from memall.core.tags import tag_filter_sql

        tsql, tparams = tag_filter_sql(tag_filter, match_all=bool(filters.get("tags_all")))
        where.append(tsql)
        params.extend(tparams)

    date_start = filters.get("date_start")
    if date_start:
        where.append("memories.occurred_at >= ?")
        params.append(date_start)
    date_end = filters.get("date_end")
    if date_end:
        where.append("memories.occurred_at <= ?")
        params.append(date_end)

    if query and isinstance(query, str):
        q = fts_query(query)
        if q:
            where.append(
                "memories.id IN (SELECT rowid FROM memories_fts WHERE memories_fts MATCH ?)"
            )
            params.append(q)

    return where, params, False


def retrieve(query=None, viewer=None, **filters) -> list | Memory | None:
    dispatch_lifecycle(HOOK_PRE_RETRIEVE, query=query, viewer=viewer, filters=filters)
    with _pool_conn() as conn:
        if isinstance(query, int) or (isinstance(query, str) and query.isdigit()):
            rid = int(query)
            conn.execute(
                "UPDATE memories SET access_count = access_count + 1 WHERE id = ?",
                (rid,),
            )
            conn.commit()
            cur = conn.execute("SELECT * FROM memories WHERE id = ?", (rid,))
            row = cur.fetchone()
            if row:
                result = _row_to_memory(row)
                if viewer is not None:
                    result = (_filter_by_trust(conn, [result], viewer) or [None])[0]
                dispatch_lifecycle(HOOK_POST_RETRIEVE, query=query, result=result)
                return result
            dispatch_lifecycle(HOOK_POST_RETRIEVE, query=query, result=None)
            return None

        where, params, _ = _build_retrieve_where(conn, query, viewer, filters)
        limit = filters.get("limit", 20)

        # Relevance ordering.  When the query goes through FTS5, rank by
        # bm25() (lower = better).  The previous ORDER BY occurred_at DESC
        # returned the *newest* matches regardless of how well they matched:
        # a 6-character query whose CJK bigram ("测试") appears in 725 rows
        # surfaced recent noise above the rows containing the full phrase.
        # Filter-only retrievals (no query text) keep the recency order.
        fts_ranked = False
        if query and isinstance(query, str):
            fts_ranked = bool(fts_query(query))

        if fts_ranked:
            # bm25() is only meaningful when the MATCH constraint is applied
            # *directly* to the FTS table in the outer query — with the
            # `id IN (SELECT rowid FROM memories_fts WHERE MATCH ?)` form the
            # join's bm25() evaluates to a constant 0 for every row, so the
            # ORDER BY silently degenerates to recency again (verified: all
            # -0.0 from the IN form vs real scores from the direct form).
            # Rewrite the FTS predicate to its direct equivalent; the bound
            # query string sits at the same index in `params`, so ordering
            # and binding stay aligned.
            fts_in_clause = (
                "memories.id IN (SELECT rowid FROM memories_fts "
                "WHERE memories_fts MATCH ?)"
            )
            sql_where = list(where)
            for idx, clause in enumerate(sql_where):
                if clause == fts_in_clause:
                    sql_where[idx] = "memories_fts MATCH ?"
                    break
            sql = (
                "SELECT memories.* FROM memories_fts "
                "JOIN memories ON memories.id = memories_fts.rowid "
                f"WHERE {' AND '.join(sql_where)} "
                "ORDER BY bm25(memories_fts) ASC, memories.occurred_at DESC LIMIT ?"
            )
        else:
            sql = f"SELECT * FROM memories WHERE {' AND '.join(where)} ORDER BY occurred_at DESC LIMIT ?"
        params.append(limit)
        cur = conn.execute(sql, params)
        rows = cur.fetchall()
        results = [_row_to_memory(r) for r in rows]

        # Audit P1-4: retrieval is a READ, so the returned rows must be
        # counted.  Previously only a direct lookup by id incremented
        # access_count, so the hot-topic ranking, the decay score and the
        # "zero_access" health statistic saw a store where only 21% of the
        # memories had ever been read.  One batched UPDATE per retrieval.
        if rows:
            try:
                ids = [r["id"] for r in rows]
                ph = ",".join("?" * len(ids))
                conn.execute(
                    f"UPDATE memories SET access_count = access_count + 1 "
                    f"WHERE id IN ({ph})", ids)
                conn.commit()
            except sqlite3.Error:
                logger.debug("retrieve: access_count update failed", exc_info=True)

        if viewer is not None:
            results = _filter_by_trust(conn, results, viewer)

        dispatch_lifecycle(HOOK_POST_RETRIEVE, query=query, results=results)
        return results


VISIBILITY_ORDER = ["public", "shared", "family", "trusted", "private"]
VISIBILITY_RANK = {v: i for i, v in enumerate(VISIBILITY_ORDER)}


def enhanced_search(
    query: str = "",
    viewer: str = "",
    agent_name: str = "",
    limit: int = 20,
    facet_fields=None,
    expand_query: bool = False,
    suggest: bool = False,
    explain: bool = False,
    cluster: bool = False,
    personalize: bool = False,
    context_boost: bool = False,
    agent_name_filter: Optional[str] = None,
    **filters,
) -> dict:
    """Retrieval with the query-enhancement layer applied.

    Wraps :func:`retrieve` and adds, all independently optional:

    * ``facet_fields`` — aggregate counts over the *unlimited* candidate set
      (``["level", "category", "project"]``, or ``"auto"`` for config
      defaults).  Unknown fields are dropped against a whitelist.
    * ``expand_query`` — synonym / history expansion before matching.
    * ``suggest`` — query completions from the corpus vocabulary.
    * ``explain`` — per-result score breakdown + matched terms.
    * ``cluster`` — topic grouping of the result set.
    * ``personalize`` — per-agent affinity from interaction history.
    * ``context_boost`` — boost results matching the active project/category.

    Every enhancement is additionally gated by its ``search.*`` config flag,
    so passing an argument alone cannot enable a feature a deployment left
    disabled.  Returns a dict; ``results`` always mirrors ``retrieve()``'s
    list so existing consumers keep working.
    """
    from memall.search.enhancements import (
        compute_facets,
        expand_query as _expand,
        load_synonyms,
        suggest_queries,
        apply_enhancements,
        _default_facet_fields,
    )
    from memall.config import get_config

    out: dict = {"query": query, "results": [], "total": 0}

    # ── Facets: normalise + validate the requested fields first ──
    max_fields = get_config("search.facets.max_fields", 8)
    fields: list[str] = []
    if facet_fields:
        if isinstance(facet_fields, str) and facet_fields.strip().lower() == "auto":
            fields = _default_facet_fields()
        else:
            from memall.search.enhancements import _norm_facet_fields

            fields = _norm_facet_fields(facet_fields, max_fields=max_fields)

    # ── Query expansion (synonyms + history) ──
    effective_query = query
    if expand_query and get_config("search.query_expansion.enabled", False):
        synonyms = load_synonyms()
        with _pool_conn() as conn:
            history = []
            if agent_name or viewer:
                from memall.search.enhancements import _recent_query_terms

                history = _recent_query_terms(
                    conn, agent_name or viewer,
                    limit=get_config("search.query_expansion.history_expansion", 3),
                )
        if synonyms or history:
            effective_query = _expand(query, synonyms=synonyms, history_terms=history)
            out["expanded_query"] = effective_query

    # ── Base retrieval (same filter semantics as retrieve) ──
    # Identity resolution: `agent_name_filter` is the explicit retrieval
    # filter (what unified_search passes); plain `agent_name` is the caller's
    # own identity (personalisation) and doubles as the retrieval filter when
    # no separate filter was given — so a direct enhanced_search(agent_name=X)
    # returns X's memories and its facets count the same population.
    effective_agent_filter = (
        agent_name_filter if agent_name_filter is not None else (agent_name or None)
    )
    retrieve_kwargs = dict(filters)
    retrieve_kwargs.setdefault("limit", limit)
    if effective_agent_filter is not None:
        retrieve_kwargs["agent_name"] = effective_agent_filter
    results = retrieve(effective_query or None, viewer=viewer, **retrieve_kwargs)

    if results is None:
        results = []
    if not isinstance(results, list):
        results = [results]
    out["results"] = results
    out["total"] = len(results)

    with _pool_conn() as conn:
        # ── Facet aggregates over the unlimited candidate set ──
        if fields:
            # The facet population must be the SAME set the results come from:
            # same query match, same identity filter, same field filters.
            # Building it by re-running the WHERE builder with the exact same
            # arguments the retrieval above used is what guarantees that; an
            # earlier version disagreed on the identity filter and printed
            # "L9: 259" next to a 4-result page.
            facet_filters = {k: v for k, v in retrieve_kwargs.items()
                             if k != "limit"}
            where, params, _ = _build_retrieve_where(
                conn, effective_query, viewer, facet_filters,
            )
            out["facets"] = compute_facets(conn, " AND ".join(where), params, fields)

        # ── Query suggestions ──
        if suggest:
            out["suggestions"] = suggest_queries(conn, query, limit=10)

        # ── Post-retrieval enhancements (need dict-shaped results) ──
        if explain or cluster or personalize or context_boost:
            # Carry the retrieval rank into the dicts: retrieve() already
            # ordered by bm25, so position i *is* the relevance signal.
            # Without a score the enhancements were inert on this path —
            # personalisation computed 0.0*(1+w*affinity)=0.0 for every row
            # and context boosting likewise, and explain reported all-null
            # ranks.  RRF-form score 1/(k+i) keeps the same scale as the
            # hybrid path so boosts compose consistently across modes.
            _RRF_K = 60
            as_dicts = [
                {
                    "memory_id": m.id,
                    "content": m.content,
                    "subject": m.subject,
                    "category": m.category,
                    "level": m.level,
                    "project": m.project,
                    "owner": m.owner,
                    "agent_name": m.agent_name,
                    "created_at": m.created_at,
                    "occurred_at": m.occurred_at,
                    "rrf_score": round(1.0 / (_RRF_K + i), 6),
                    "fts_rank": i,
                    "vec_rank": None,
                }
                for i, m in enumerate(results, start=1)
            ]
            enhanced = apply_enhancements(
                as_dicts,
                query=query,
                conn=conn,
                agent_name=agent_name or viewer,
                explain=explain,
                cluster=cluster,
                personalize=personalize,
                context_boost=context_boost,
            )
            out["clusters"] = enhanced.get("clusters") or []
            out["explained"] = bool(enhanced.get("explained"))
            # Re-order the Memory objects to match the enhanced ordering.
            order = [d.get("memory_id") for d in enhanced.get("results") or []]
            if order:
                by_id = {m.id: m for m in results}
                out["results"] = [by_id[i] for i in order if i in by_id]
            # Attach explanations alongside the memories for JSON callers.
            expl_by_id = {
                d.get("memory_id"): d
                for d in (enhanced.get("results") or [])
                if d.get("explanation")
            }
            if expl_by_id:
                out["explanations"] = {
                    str(mid): d.get("explanation") for mid, d in expl_by_id.items()
                }

    dispatch_lifecycle(
        HOOK_POST_RETRIEVE,
        query=query,
        results=out["results"],
        facets=out.get("facets"),
    )
    return out


def _get_agent_read_level(conn, agent_name: str) -> str:
    row = conn.execute(
        "SELECT agent_type, trusted_by FROM identities WHERE agent_name = ?",
        (agent_name,),
    ).fetchone()
    if not row:
        # Least privilege, not "private".  ``VISIBILITY_RANK`` is ordered
        # ["public"(0) … "private"(4)] and the filter keeps a row when
        # ``mem_rank <= viewer_rank`` — so returning the *most restricted*
        # visibility name for an unknown agent granted it EVERY row instead.
        # An unregistered identity may see public rows and nothing else.
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

    The FTS5 index is built with the same transformation (see
    ``memall.core.db.memall_split_cjk``), so a query built here matches the
    indexed tokens one-to-one.
    """
    parts: list[str] = []
    pos = 0
    for m in _CJK_RE.finditer(text):
        if m.start() > pos:
            parts.append(text[pos : m.start()])
        parts.append(" ".join(m.group()))
        pos = m.end()
    if pos < len(text):
        parts.append(text[pos:])
    return "".join(parts)


def fts_query(raw: str) -> str:
    """Build an FTS5 MATCH query string from a raw user query.

    The FTS5 index stores each CJK character as its own token (the sync
    triggers run ``memall_split_cjk`` on the way in), so a CJK run in the
    query is likewise split and emitted as a *phrase* of single-char tokens:
    ``"检索"`` → ``"检 索"``.  A phrase requires the characters to be
    adjacent in the document, which keeps precision while making single
    characters searchable on their own.

    For CJK runs of 3+ characters we additionally OR in the adjacent
    2-character sub-phrases ("检索库" → ("检 索 库" OR "检 索" OR "索 库"))
    so a document containing only part of the run is still recalled.

    Non-CJK words use exact match.  Multiple whitespace-separated terms are
    AND-ed together.

    Every emitted string neutralises FTS5 query syntax: an inner double
    quote is doubled (the FTS5 escape rule) and braces map to spaces.
    Without this, pasted code or JSON — e.g. the literal substring
    ``tools/call: {"meth`` found in production content — is parsed as MATCH
    syntax and raises ``sqlite3.OperationalError: unterminated string``;
    verified against production slices, ~5% of real substring queries hit
    this.  Escaping happens *inside* the phrase quotes so adjacency
    semantics are unchanged (measured: per-char unquoted terms silently
    degrade a phrase to implicit-AND — 394 candidates vs 284).
    """
    terms = raw.strip().split()
    if not terms:
        return ""

    def _esc(inner: str) -> str:
        return inner.replace('"', '""').replace("{", " ").replace("}", " ")

    def _phrase(inner: str) -> str:
        # One outer-quoted FTS5 phrase whose content is already split into
        # single-char CJK tokens; quoting the whole phrase keeps adjacency.
        return '"' + _esc(inner) + '"'

    tokenized: list[str] = []
    for t in terms:
        if _CJK_RE.search(t):
            # Phrase of individually-indexed CJK characters.
            cjk_options = [_phrase(_split_cjk(t))]
            # Adjacent 2-char sub-phrases for broader recall on long runs.
            if len(t) >= 3:
                for i in range(len(t) - 1):
                    sub = t[i : i + 2]
                    if _CJK_RE.search(sub):
                        tok = _phrase(_split_cjk(sub))
                        if tok not in cjk_options:
                            cjk_options.append(tok)
            if len(cjk_options) > 1:
                tokenized.append(f"({' OR '.join(cjk_options)})")
            else:
                tokenized.append(cjk_options[0])
        else:
            tokenized.append(_phrase(t))
    if len(tokenized) > 1:
        return " AND ".join(tokenized)
    return tokenized[0] if tokenized else ""


VALID_RELATIONS = [
    "extends",
    "contradicts",
    "refines",
    "cites",
    "supersedes",
    "related",
    "updates",
    "derives",
]

# Ontology hierarchy: broader → narrower.
# Used for traversing up/down the relation type hierarchy.
# Example: "updates" implies "supersedes" — querying for updates also returns supersedes.
ONTOLOGY_HIERARCHY = {
    "updates": ["supersedes"],  # updating something → superseding the old
    "derives": ["refines"],  # deriving from → refining
    "extends": ["cites"],  # extending → citing the source
    "refines": [],  # leaf in ontology (no further narrowing)
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


def connect(
    source_id: int,
    target_id: int,
    relation_type: str = "refines",
    weight: float = 1.0,
    metadata: str = "{}",
    replace: bool = False,
) -> int:
    """Create an edge between two memories.

    Bitemporal semantics (P0-3): an edge carries a validity window
    (``valid_from``/``invalid_at``) instead of being overwritten.
    - Default: same (source, target, relation) with a still-valid edge is
      idempotent — returns the existing edge id without duplicating it.
    - ``replace=True``: marks the currently-valid edge ``invalid_at=now``
      and inserts a fresh edge, preserving the historical version
      ("mark old edge invalid + insert new edge" on write conflict).
    """
    if source_id == target_id:
        raise ValueError("self-connection is not allowed")
    if relation_type not in VALID_RELATIONS:
        raise ValueError(f"invalid relation type: {relation_type}")

    with _pool_conn() as conn:
        for rid in (source_id, target_id):
            if not conn.execute(
                "SELECT 1 FROM memories WHERE id = ?", (rid,)
            ).fetchone():
                raise ValueError(f"memory {rid} does not exist")

        now = datetime.now(timezone.utc).isoformat()
        cur = conn.execute(
            "SELECT id FROM edges WHERE source_id = ? AND target_id = ? AND relation_type = ? AND invalid_at IS NULL",
            (source_id, target_id, relation_type),
        )
        existing = cur.fetchone()
        if existing:
            if not replace:
                return existing["id"]
            # Write conflict: invalidate the old edge, then insert a new one.
            conn.execute(
                "UPDATE edges SET invalid_at = ? WHERE id = ?",
                (now, existing["id"]),
            )

        cur = conn.execute(
            "INSERT INTO edges (source_id, target_id, relation_type, weight, created_at, metadata, valid_from, invalid_at) "
            "VALUES (?,?,?,?,?,?,?,NULL)",
            (source_id, target_id, relation_type, weight, now, metadata, now),
        )
        eid = cur.lastrowid
        conn.commit()
        return eid


def traverse(
    node_id: int,
    depth: int = 1,
    relation_filter: Optional[str] = None,
    thread_aware: bool = False,
    as_of: Optional[str] = None,
) -> dict:
    """BFS graph traversal from ``node_id``.

    Bitemporal (P0-3): by default only edges whose validity window covers
    ``as_of`` (defaults to now) are traversed; invalidated edges are
    excluded. Pass ``as_of`` to reproduce the graph at a past moment
    (both valid_from and invalid_at are compared against it).
    """
    with _pool_conn() as conn:
        seen = {node_id}
        seen_edges = set()
        nodes = {}
        edges_out = []
        current = [node_id]
        now = as_of if as_of is not None else datetime.now(timezone.utc).isoformat()

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
                        "id": nr["id"],
                        "content": nr["content"],
                        "subject": nr["subject"],
                        "category": nr["category"],
                        "level": nr["level"],
                        "confidence": nr["confidence"],
                    }
                # Add same_thread edges from root to all thread-related nodes
                for rid in related:
                    ekey = (node_id, rid, "same_thread")
                    if ekey not in seen_edges:
                        seen_edges.add(ekey)
                        edges_out.append(
                            {
                                "id": None,
                                "source_id": node_id,
                                "target_id": rid,
                                "relation_type": "same_thread",
                                "weight": 0.5,
                            }
                        )
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
                  AND (e.valid_from IS NULL OR e.valid_from <= ?)
                  AND (e.invalid_at IS NULL OR e.invalid_at > ?)
            """
            edge_params = current + current + [now, now]
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
                edges_out.append(
                    {
                        "id": er["id"],
                        "source_id": src,
                        "target_id": tgt,
                        "relation_type": er["relation_type"],
                        "weight": er["weight"],
                    }
                )
                if src not in seen:
                    seen.add(src)
                    next_level.add(src)
                if tgt not in seen:
                    seen.add(tgt)
                    next_level.add(tgt)

            if next_level:
                ph = ",".join("?" for _ in seen)
                node_rows = conn.execute(
                    f"SELECT id, content, subject, category, level, summary, confidence FROM memories WHERE id IN ({ph})",
                    tuple(seen),
                ).fetchall()
                for nr in node_rows:
                    nodes[nr["id"]] = {
                        "id": nr["id"],
                        "content": nr["content"],
                        "subject": nr["subject"],
                        "category": nr["category"],
                        "level": nr["level"],
                        "confidence": nr["confidence"],
                    }
            current = list(next_level)

        root = conn.execute(
            "SELECT id, content, subject, category, level, summary, confidence FROM memories WHERE id = ?",
            (node_id,),
        ).fetchone()
        if root:
            nodes[root["id"]] = {
                "id": root["id"],
                "content": root["content"][:200],
                "subject": root["subject"],
                "category": root["category"],
                "level": root["level"],
                "confidence": root["confidence"],
            }

        return {"root": node_id, "nodes": list(nodes.values()), "edges": edges_out}


def _sanitize_level(level: str, default: str = "P2") -> str:
    """Restrict free-form level input to the canonical enum.

    Delegates to :func:`memall.core.levels.normalize_level` so the value can
    never fall outside what the ``memories.level`` CHECK constraint accepts.
    """
    return normalize_level(level, default)


def smart_store(
    content: str,
    owner: str = "",
    agent_name: str = "",
    subject: str = "",
    project: str = "",
    category: str = "general",
    level: str = "P2",
    dedup_threshold: float = 0.85,
) -> dict:
    """Store memory with content_hash dedup + optional semantic similarity check.

    Returns {"id": memory_id, "status": "new"|"duplicate"}."""

    level = _sanitize_level(level)

    # Pre-store lifecycle hook
    dispatch_lifecycle(
        HOOK_PRE_STORE, content=content, owner=owner, agent_name=agent_name
    )

    # Check exact hash first
    h = content_hash(content)
    with _pool_conn() as conn:
        existing = conn.execute(
            "SELECT id FROM memories WHERE content_hash = ?", (h,)
        ).fetchone()
        if existing:
            result = {
                "id": existing["id"],
                "status": "duplicate",
                "reason": "exact_hash",
            }
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
                    result = {
                        "id": recent[0]["id"],
                        "status": "duplicate",
                        "reason": f"semantic_similarity_{sim:.2f}",
                    }
                    dispatch_lifecycle(HOOK_POST_STORE, result=result)
                    return result

        try:
            mid = capture(
                MemoryInput(
                    content=content,
                    owner=owner,
                    agent_name=agent_name,
                    subject=subject,
                    project=project,
                    category=category,
                    level=level,
                )
            )
        except ValueError as e:
            logger.warning("smart_store: %s", e)
            result = {"id": None, "status": "rejected", "reason": str(e)}
            dispatch_lifecycle(HOOK_POST_STORE, result=result)
            return result
        result = {"id": mid, "status": "new"}
        dispatch_lifecycle(HOOK_POST_STORE, result=result)
        return result


def store_batch(items: list) -> dict:
    """Batch insert multiple memories. Each item is a dict with keys:
    content (required), owner, agent_name, subject, project, category, level.

    Returns {"ids": [...], "count": N}."""
    ids = []
    errors = []
    for item in items:
        try:
            mid = capture(
                MemoryInput(
                    content=item.get("content", ""),
                    owner=item.get("owner", ""),
                    agent_name=item.get("agent_name", ""),
                    subject=item.get("subject", ""),
                    project=item.get("project", ""),
                    category=item.get("category", "general"),
                    level=item.get("level", "P2"),
                )
            )
            ids.append(mid)
        except ValueError as e:
            logger.warning("store_batch: item skipped — %s", e)
            errors.append({"content": item.get("content", "")[:40], "reason": str(e)})
    return {"ids": ids, "count": len(ids), "errors": errors}


# ── Search / rerank extracted to thin_waist_search (F-09) ──────────────
# Moved verbatim to isolate the heavy optional deps (onnxruntime / tokenizers /
# sentence-transformers).  Re-exported here so the public import surface
# (``from memall.core.thin_waist import hybrid_search, vector_search, timeline``)
# is unchanged.
from .thin_waist_search import (  # noqa: E402,F401
    _load_reranker_onnx,
    _onnx_rerank,
    _rerank,
    _context_rerank,
    vector_search,
    hybrid_search,
    timeline,
)
