"""Context assembler for agent memory injection.

Provides ``build_context()`` — a token-budgeted, multi-tiered context
assembler that packs the most relevant memories into a limited context
window.  Tier 1 (identity/tasks/lessons) always takes priority, followed
by Tier 2 (query-relevant sessions/reflections) and Tier 3 (recency).

Also retains ``get_persona()`` as a legacy wrapper for backward compatibility.
"""

import logging
import re
from collections import Counter
from typing import Optional

from memall.core.db import pool_conn
from memall.core.nlp import compute_tfidf, cosine_sim

logger = logging.getLogger(__name__)

# ── Token estimation ───────────────────────────────────────────────────
# Rough heuristic: Chinese chars ~1.5 tokens, ASCII ~1 token
_CJK_RE = re.compile(r"[一-鿿㐀-䶿豈-﫿]")


def _estimate_tokens(text: str) -> int:
    """Estimate LLM token count from raw text (heuristic, no external deps)."""
    cjk = len(_CJK_RE.findall(text))
    ascii_chars = len(text) - cjk
    return cjk * 3 // 2 + ascii_chars // 2


def _format_block(label: str, lines: list[str], max_tokens: int) -> tuple[str, int]:
    """Format a section block, truncating if it exceeds max_tokens.

    Returns (block_text, tokens_used).
    """
    if not lines:
        return "", 0
    header = f"[{label}]\n"
    header_tokens = _estimate_tokens(header)
    budget = max_tokens - header_tokens
    if budget <= 0:
        return "", 0

    chosen: list[str] = []
    tokens_used = header_tokens
    for line in lines:
        line_tokens = _estimate_tokens(line) + 1  # +1 for newline
        if tokens_used + line_tokens > max_tokens:
            break
        chosen.append(line)
        tokens_used += line_tokens

    if not chosen:
        return "", 0
    body = "\n".join(chosen) + "\n"
    return header + body, tokens_used


# ── Tier builders ──────────────────────────────────────────────────────

def _build_tier1(agent_name: str, conn) -> list[str]:
    """Tier 1 — always-included: L1 identity, active L5 tasks, recent L7 lessons."""
    lines: list[str] = []

    # L1 identity
    rows = conn.execute(
        "SELECT content FROM memories WHERE LOWER(agent_name)=LOWER(?) AND level='L1' ORDER BY created_at DESC LIMIT 3",
        (agent_name,),
    ).fetchall()
    for r in rows:
        lines.append(f"[Identity] {r['content'][:200]}")

    # Active L5 tasks
    active = conn.execute(
        "SELECT content, subject FROM memories "
        "WHERE LOWER(agent_name)=LOWER(?) AND level='L5' "
        "AND COALESCE(memory_status, json_extract(metadata, '$.status')) = 'active' "
        "ORDER BY created_at DESC LIMIT 5",
        (agent_name,),
    ).fetchall()
    for r in active:
        subj = r["subject"] or ""
        label = f" ({subj})" if subj else ""
        lines.append(f"[Task{label}] {r['content'][:200]}")

    # Recent L7 lessons
    l7 = conn.execute(
        "SELECT content, weight FROM memories WHERE LOWER(agent_name)=LOWER(?) AND level='L7' ORDER BY created_at DESC LIMIT 5",
        (agent_name,),
    ).fetchall()
    for r in l7:
        w = r["weight"] or 1
        badge = f" (x{w})" if w > 1 else ""
        lines.append(f"[Lesson{badge}] {r['content'][:200]}")

    return lines


def _build_tier2(agent_name: str, query: str, conn) -> list[str]:
    """Tier 2 — relevance-scored: L4 sessions + L6 reflections (if query)."""
    lines: list[str] = []

    rows = conn.execute(
        "SELECT id, content, summary, level, category, created_at FROM memories "
        "WHERE LOWER(agent_name)=LOWER(?) AND level IN ('L4','L6') "
        "ORDER BY created_at DESC LIMIT 30",
        (agent_name,),
    ).fetchall()

    if not rows:
        return []

    candidates = []
    for r in rows:
        text = r["summary"] or r["content"] or ""
        candidates.append({"id": r["id"], "text": text, "level": r["level"], "cat": r["category"], "at": r["created_at"]})

    if query and len(candidates) > 1:
        texts = [query] + [c["text"][:500] for c in candidates]
        try:
            tfidf_docs = compute_tfidf(texts)
            query_vec = tfidf_docs[0]
            for i, c in enumerate(candidates):
                c["score"] = cosine_sim(query_vec, tfidf_docs[i + 1])
            candidates.sort(key=lambda x: x.get("score", 0), reverse=True)
        except Exception:
            pass  # fall through to recency order

    for c in candidates:
        prefix = "[Session]" if c["level"] == "L4" else "[Reflection]"
        lines.append(f"{prefix} {c['text'][:300]}")

    return lines


def _build_tier3(agent_name: str, conn) -> list[str]:
    """Tier 3 — recency-ordered: P0-P2, L2, L9, L10, L11."""
    lines: list[str] = []

    rows = conn.execute(
        "SELECT content, level, subject FROM memories "
        "WHERE LOWER(agent_name)=LOWER(?) AND level IN ('P0','P1','P2','L2','L9','L10','L11') "
        "ORDER BY created_at DESC LIMIT 20",
        (agent_name,),
    ).fetchall()

    for r in rows:
        subj = r["subject"] or ""
        label = f" ({subj})" if subj else ""
        lines.append(f"[{r['level']}{label}] {r['content'][:300]}")

    return lines


# ── Public API ─────────────────────────────────────────────────────────

def build_context(
    agent_name: str,
    query: str = "",
    max_tokens: int = 2000,
    include_levels: Optional[list[str]] = None,
) -> dict:
    """Build a token-budgeted context block for agent injection.

    Three tiers of priority:
        Tier 1 (≤400 tokens): L1 identity, active L5 todos, L7 lessons.
        Tier 2 (≤800 tokens): L4 sessions + L6 reflections (TF-IDF scored).
        Tier 3 (≤800 tokens): P0-P2, L2, L9+ by recency.

    Args:
        agent_name: Agent to build context for.  Empty string → empty result.
        query: Optional query string for relevance scoring (Tier 2).
        max_tokens: Upper bound on total output tokens.
        include_levels: If set, only include these levels (e.g. ['L6','L7']).

    Returns:
        {"context": str, "tokens": int, "sources": {"tier1": int, "tier2": int, "tier3": int}}
    """
    if not agent_name:
        return {"context": "", "tokens": 0, "sources": {"tier1": 0, "tier2": 0, "tier3": 0}}

    # Header takes ~20 tokens
    header = f"[MEMORY CONTEXT]\nAgent: {agent_name}  |  Budget: {max_tokens} tokens\n\n"
    header_tokens = _estimate_tokens(header)

    tier1_cap = min(400, max_tokens - header_tokens)
    tier2_cap = min(800, max(0, max_tokens - header_tokens - tier1_cap))
    tier3_cap = min(800, max(0, max_tokens - header_tokens - tier1_cap - tier2_cap))

    with pool_conn() as conn:
        t1_lines = _build_tier1(agent_name, conn)
        t2_lines = _build_tier2(agent_name, query, conn)
        t3_lines = _build_tier3(agent_name, conn)

    if include_levels:
        all_lines = t1_lines + t2_lines + t3_lines
        filtered = []
        for line in all_lines:
            for lv in include_levels:
                if f"[{lv}" in line or f"({lv})" in line:
                    filtered.append(line)
                    break
        body_block, body_tokens = _format_block("Relevant Memories", filtered, max_tokens - header_tokens)
        context = header + body_block
        return {
            "context": context,
            "tokens": _estimate_tokens(context),
            "sources": {"tier1": 0, "tier2": 0, "tier3": body_tokens},
        }

    # Assemble with tier caps
    t1_block, t1_tokens = _format_block("Core Profile", t1_lines, tier1_cap)
    remaining = max_tokens - header_tokens - t1_tokens
    t2_cap = min(800, max(0, remaining))
    t2_block, t2_tokens = _format_block("Recent Context", t2_lines, t2_cap)
    remaining -= t2_tokens
    t3_block, t3_tokens = _format_block("Additional Context", t3_lines, max(0, remaining))

    context = header + t1_block + t2_block + t3_block
    return {
        "context": context,
        "tokens": _estimate_tokens(context),
        "sources": {"tier1": t1_tokens, "tier2": t2_tokens, "tier3": t3_tokens},
    }


def get_persona(agent_name: str, limit: int = 20) -> dict:
    """Legacy wrapper — returns L6/L7 reflection data in the old dict format.

    Delegates to ``build_context()`` and extracts L6/L7 content.
    Also aggregates active topics from L6 memories via direct DB query.
    """
    result = build_context(agent_name, max_tokens=4000)
    context = result["context"]

    # Query DB directly for backward-compatible format
    recent_decisions = []
    derived_insights = []
    active_topics = []

    try:
        with pool_conn() as conn:
            # L7 lessons + L6 decisions → recent_decisions
            dec_rows = conn.execute(
                "SELECT content FROM memories "
                "WHERE LOWER(agent_name)=LOWER(?) AND (level='L7' OR (level='L6' AND category='decision')) "
                "ORDER BY created_at DESC LIMIT 5",
                (agent_name,),
            ).fetchall()
            for r in dec_rows:
                recent_decisions.append({"text": r["content"][:200]})

            # L6 reflections → derived_insights
            l6_rows = conn.execute(
                "SELECT content FROM memories "
                "WHERE LOWER(agent_name)=LOWER(?) AND level='L6' "
                "ORDER BY created_at DESC LIMIT 5",
                (agent_name,),
            ).fetchall()
            for r in l6_rows:
                derived_insights.append({"text": r["content"][:200]})

            # Aggregate active topics from L6 categories
            rows = conn.execute(
                "SELECT category, COUNT(*) as cnt FROM memories "
                "WHERE LOWER(agent_name)=LOWER(?) AND level='L6' "
                "AND category IS NOT NULL AND category != '' "
                "GROUP BY category ORDER BY cnt DESC LIMIT 5",
                (agent_name,),
            ).fetchall()
            for r in rows:
                active_topics.append({"topic": r["category"], "count": r["cnt"]})
    except Exception:
        pass

    # Estimate sample_size from context lines
    items = [l for l in context.split("\n") if l.startswith("[") and ("[Lesson" in l or "[Reflection" in l or "[Session" in l)]

    return {
        "recent_decisions": recent_decisions,
        "active_topics": active_topics,
        "contradictions_unresolved": [],
        "derived_insights": derived_insights,
        "sample_size": len(items),
    }