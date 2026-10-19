"""Tag (keyword) handling for MemALL.

Tags are MemALL's *intent keywords* — a small set of topical terms attached to a
memory so it can be found by what it is **about**, not only by words that happen
to appear in its text.

Why this exists
---------------
Retrieval used to match only ``content`` / ``subject`` / ``summary`` via FTS5.
A memory whose body says "把校验器和处理器各自存的允许值集合统一" is not findable
by the query "契约" even though that is plainly its topic.  The ``tags`` column
existed in the schema and could be written through ``ops:tag``, but it was never
indexed and never read — 0 of 4576 production rows carried a tag.

Tags close that gap: they index *topic* separately from *wording*.

Storage format
--------------
JSON array in ``memories.tags``, e.g. ``["fts5", "契约", "检索"]``.  For FTS the
array is flattened to a space-separated string (see :func:`tags_to_text`) and
indexed as its own column, so a MATCH against any tag recalls the memory.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Iterable, List, Optional

logger = logging.getLogger(__name__)

# Hard ceiling per memory — tags are a recall aid, not a second body.
DEFAULT_MAX_TAGS = 8

# Shortest acceptable tag (CJK counts characters, so 2 is right for both scripts).
_MIN_TAG_LEN = 2
# Longest tag kept; longer strings are phrases, not keywords.
_MAX_TAG_LEN = 24

# Tokens that carry no topical signal.
_TAG_STOPWORDS = frozenset({
    # Chinese
    "这个", "那个", "什么", "怎么", "如何", "因为", "所以", "但是", "如果",
    "可以", "需要", "已经", "没有", "一个", "我们", "他们", "自己", "现在",
    "问题", "方法", "东西", "时候", "地方", "内容", "情况", "结果", "影响",
    # English
    "the", "and", "for", "with", "that", "this", "from", "have", "will",
    "can", "are", "was", "were", "not", "but", "you", "your", "our",
    "should", "would", "could", "there", "their", "then", "than", "when",
    "what", "which", "into", "about", "after", "before", "also", "just",
})

# Identifiers worth keeping as tags: acronyms, dotted/camel names, versions.
_ACRONYM_RE = re.compile(r"\b[A-Z][A-Z0-9_]{1,11}\b")
_CAMEL_RE = re.compile(r"\b[a-z]+[A-Z][A-Za-z0-9]{2,23}\b")
_SNAKE_RE = re.compile(r"\b[a-z][a-z0-9]*(?:_[a-z0-9]+){1,3}\b")
_DOTTED_RE = re.compile(r"\b[a-z][a-z0-9]*(?:\.[a-z0-9]+){1,3}\b")
# CJK runs: with a segmenter unavailable we fall back to 2-character bigrams,
# which are far less noisy than 2-6 character runs (those span word boundaries
# and produce phrases like "检索架构采用" rather than the keywords "检索"/"架构").
_CJK_RUN_RE = re.compile(r"[\u4e00-\u9fff]{2,}")
# A whole short CJK run is itself a useful tag (e.g. "检索架构").
_CJK_RUN_WHOLE_RE = re.compile(r"[\u4e00-\u9fff]{2,6}")

_HAS_JIEBA = False
try:  # optional, mirrors thin_waist.fts_query
    import jieba  # type: ignore

    _HAS_JIEBA = True
except ImportError:  # pragma: no cover - environment dependent
    pass


def _cjk_terms(text: str) -> List[str]:
    """Extract candidate CJK keywords from *text*.

    Uses jieba when available (real word boundaries); otherwise falls back to
    sliding 2-character bigrams.  Both are deterministic and dependency-free in
    the worst case, because this runs on the write path.
    """
    terms: List[str] = []
    if _HAS_JIEBA:
        for run in _CJK_RUN_RE.findall(text):
            for word in jieba.cut(run, cut_all=False):
                w = word.strip()
                if len(w) >= 2 and w not in _TAG_STOPWORDS:
                    terms.append(w)
    else:
        for run in _CJK_RUN_RE.findall(text):
            if len(run) <= 6:
                terms.append(run)
            for i in range(len(run) - 1):
                bg = run[i:i + 2]
                if bg not in _TAG_STOPWORDS:
                    terms.append(bg)
    return terms


def normalize_tags(raw: Any, max_tags: int = DEFAULT_MAX_TAGS) -> List[str]:
    """Normalise any accepted tag representation into a clean, ordered list.

    Accepts a JSON array string, a Python list, a comma/space separated string,
    or ``None``.  Strips, de-duplicates case-insensitively (keeping the first
    spelling seen), drops stopwords/over-long entries, and caps the count.

    Order is preserved so callers can express priority.
    """
    items: List[str] = []
    if raw is None:
        return []
    if isinstance(raw, str):
        s = raw.strip()
        if not s:
            return []
        if s.startswith("["):
            try:
                parsed = json.loads(s)
            except (ValueError, TypeError):
                parsed = None
            if isinstance(parsed, list):
                items = [str(x) for x in parsed]
            else:
                # Malformed JSON array — fall back to splitting the raw text.
                items = [t for t in re.split(r"[,\s]+", s.strip("[]")) if t]
        else:
            items = [t for t in re.split(r"[,\s]+", s) if t]
    elif isinstance(raw, (list, tuple, set, frozenset)):
        items = [str(x) for x in raw]
    else:
        return []

    out: List[str] = []
    seen: set = set()
    for it in items:
        t = it.strip().strip("\"'").strip()
        if not t or len(t) < _MIN_TAG_LEN or len(t) > _MAX_TAG_LEN:
            continue
        low = t.lower()
        if low in _TAG_STOPWORDS or low in seen:
            continue
        # Reject pure punctuation / digits — not topical.
        if not any(ch.isalnum() or "\u4e00" <= ch <= "\u9fff" for ch in t):
            continue
        seen.add(low)
        out.append(t)
        if len(out) >= max_tags:
            break
    return out


def tags_to_text(raw: Any) -> str:
    """Flatten tags to a space-separated string for FTS indexing.

    Used by the ``memall_tags_text`` SQL function that the FTS sync triggers
    call, so it must be total and never raise for any stored value.
    """
    return " ".join(normalize_tags(raw, max_tags=64))


def parse_tags(raw: Any) -> List[str]:
    """Read the ``tags`` column back into a list, tolerating junk."""
    return normalize_tags(raw, max_tags=64)


def extract_auto_tags(
    content: str,
    subject: str = "",
    agent_name: str = "",
    max_tags: int = DEFAULT_MAX_TAGS,
) -> List[str]:
    """Derive tags from content when the caller supplied none.

    Strategy, in priority order (highest signal first):

    1. **Entities** — reuse :func:`memall.core.entity_extractor.extract_entities`
       so tags agree with the entity graph instead of inventing a parallel one.
    2. **Identifiers** — acronyms, camelCase/snake/dotted names, versions. These
       are exactly the terms a later query is most likely to use.
    3. **CJK terms** — 2-6 character runs, minus stopwords.

    Deterministic and dependency-free (no model call), because this runs on the
    write path.
    """
    text = f"{subject}\n{content or ''}"
    if not text.strip():
        return []

    candidates: List[str] = []

    # 1. Entities (already fuzzy-matched and typed).
    try:
        from memall.core.entity_extractor import extract_entities

        for ent in extract_entities(content or "", agent_name=agent_name) or []:
            if isinstance(ent, dict):
                name = ent.get("name") or ent.get("value") or ""
            else:
                name = str(ent)
            if name:
                candidates.append(str(name))
    except Exception:
        logger.debug("extract_auto_tags: entity extraction failed", exc_info=True)

    # 2. Identifiers — highest-precision lexical signal.
    for rx in (_ACRONYM_RE, _CAMEL_RE, _SNAKE_RE, _DOTTED_RE):
        candidates.extend(rx.findall(text))

    # 3. CJK terms (subject first — it is the curated one-liner).
    if subject:
        candidates.extend(_cjk_terms(subject))
    candidates.extend(_cjk_terms(content or ""))

    return normalize_tags(candidates, max_tags=max_tags)


def merge_tags(existing: Any, incoming: Any,
               max_tags: int = DEFAULT_MAX_TAGS) -> List[str]:
    """Union of existing and incoming tags, existing first, order preserved."""
    base = parse_tags(existing)
    add = normalize_tags(incoming, max_tags=max_tags)
    seen = {t.lower() for t in base}
    out = list(base)
    for t in add:
        if t.lower() not in seen:
            seen.add(t.lower())
            out.append(t)
        if len(out) >= max_tags:
            break
    return out


def tag_filter_sql(
    tags: Iterable[str], match_all: bool = False
) -> tuple[str, list]:
    """Build a parameterised SQL predicate matching *tags* on ``memories.tags``.

    Returns ``(sql_fragment, params)`` where the fragment is meant to be AND-ed
    into a WHERE clause.  Matching is exact per tag on the JSON array so a query
    for ``"检"`` cannot accidentally match ``"检索"``.

    ``match_all=True`` requires every tag (AND); the default requires any (OR).

    Three distinct cases, never conflated:
      * no tags requested (empty input)      → ``("1=1", [])``  — no filter
      * tags requested and all valid          → the EXISTS predicate
      * tags requested but all rejected by normalisation
        (e.g. single characters)              → ``("0=1", [])`` — matches nothing.
        Silently falling back to ``1=1`` here would return the *entire* table
        when the caller asked for a specific subset; matching nothing is the
        safe failure and the tool layer surfaces it as "no valid tags".
    """
    raw = list(tags or [])
    clean = normalize_tags(raw, max_tags=32)
    if not raw:
        return "1=1", []
    if not clean:
        # Caller asked to filter but nothing survived validation.
        return "0=1", []
    clause = "EXISTS (SELECT 1 FROM json_each(memories.tags) je WHERE je.value = ?)"
    joiner = " AND " if match_all else " OR "
    return "(" + joiner.join([clause] * len(clean)) + ")", list(clean)


__all__ = [
    "normalize_tags",
    "tags_to_text",
    "parse_tags",
    "extract_auto_tags",
    "merge_tags",
    "tag_filter_sql",
    "DEFAULT_MAX_TAGS",
]