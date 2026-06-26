"""Query intent classifier that routes between FTS5, vec0, and hybrid engines."""

import re
from enum import Enum, auto

_CJK_RE = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf\uf900-\ufaff]+")
_AGENT_PATTERN = re.compile(r"^\s*(opencode|claude|deepseek|gpt|agent)\s*[：:]\s*", re.IGNORECASE)
_LEVEL_PATTERN = re.compile(r"\[(L\d+|P\d+)\s")
_ID_PATTERN = re.compile(r"^\s*\d+\s*$")


class SearchIntent(Enum):
    DIRECT_LOOKUP = auto()
    FACT_SEARCH = auto()
    SEMANTIC_SEARCH = auto()
    HYBRID_SEARCH = auto()


def _cjk_chars(text: str) -> int:
    return sum(len(m.group()) for m in _CJK_RE.finditer(text))


def classify(query: str) -> SearchIntent:
    """Classify query intent based on content patterns."""
    q = query.strip()
    if not q:
        return SearchIntent.HYBRID_SEARCH
    if _ID_PATTERN.match(q):
        return SearchIntent.DIRECT_LOOKUP
    if _AGENT_PATTERN.match(q) or _LEVEL_PATTERN.match(q):
        return SearchIntent.FACT_SEARCH

    cjk_count = _cjk_chars(q)
    token_count = len(q.split())

    if cjk_count >= 14 or token_count >= 8:
        return SearchIntent.SEMANTIC_SEARCH
    if token_count < 4 or cjk_count < 10:
        return SearchIntent.FACT_SEARCH
    return SearchIntent.HYBRID_SEARCH


def resolve_mode(intent: SearchIntent) -> str:
    """Map intent to engine mode string."""
    return {
        SearchIntent.DIRECT_LOOKUP: "direct",
        SearchIntent.FACT_SEARCH: "fts5",
        SearchIntent.SEMANTIC_SEARCH: "vector",
        SearchIntent.HYBRID_SEARCH: "hybrid",
    }.get(intent, "hybrid")
