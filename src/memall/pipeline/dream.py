"""
Dynamic Dreaming — active contradiction detection on memory capture.

When a new memory is stored, scans the most recent N memories of the same
agent+category for semantic conflicts.  Contradictions found are recorded
as ``contradicts`` edges with a timestamp-based resolution verdict.

Usage (called automatically from thin_waist.capture)::

    from memall.pipeline.dream import dream_scan
    conflicts = dream_scan(conn, new_mem_id, agent_name, content, category)

Each conflict dict returned::

    {
        "conflict_with": int,       # existing memory ID
        "content": str,             # existing memory content (first 200 chars)
        "stance": "oppose",         # fixed: this memory contradicts the new one
        "resolved": "newer_wins",   # verdict: "newer_wins" | "older_wins" | "undecided"
        "edge_id": int,             # edge created (or 0 if existing)
    }
"""

import json
import logging
import re
from datetime import datetime, timezone
from memall.core.nlp import tokenize, jaccard

logger = logging.getLogger(__name__)

# Reuse contradiction detection from link.py
_CONTRADICT_PAIRS = [
    (r'用\s+\S+|采用\s+\S+|选择\s+\S+|替代\s+\S+|迁移到\s+\S+', r'不用|废弃|放弃|拒绝|回退|不推荐|反对'),
    (r'推荐|可靠|好方案|最优|首选', r'不推荐|不可靠|差方案|有问题|不好'),
    (r'应该|需要|必须|一定要', r'不应该|不需要|不必|不该|没必要'),
    (r'同意|支持|认可|赞同', r'反对|不同意|不认可|不赞同|质疑'),
    (r'简单|容易|方便|快速', r'复杂|困难|麻烦|缓慢'),
    (r'保留|继续用|维持', r'迁移|替换|改用|替代'),
    (r'好|优|有利|优势|优点', r'差|劣|不利|劣势|缺点|不足'),
]

# Pre-compiled patterns for hot-path performance
_COMPILED_PAIRS: list[tuple[re.Pattern, re.Pattern]] = [
    (re.compile(pos, re.IGNORECASE), re.compile(neg, re.IGNORECASE))
    for pos, neg in _CONTRADICT_PAIRS
]

# Jaccard threshold for candidate selection (lower = more candidates)
_JACCARD_THRESHOLD = 0.15
# Only flag clear contradictions above this threshold
_CONTRADICT_JACCARD_MIN = 0.10
# Number of recent memories to scan per call
_DEFAULT_SCAN_WINDOW = 50


def _check_contradiction(text_a: str, text_b: str) -> bool:
    """Check if two texts express opposing stances on the same subject.

    Uses pre-compiled CONTRADICT_PAIRS patterns.
    Returns True if one text takes positive stance on a topic and the other
    takes a negative stance on the same topic.
    """
    for pos_re, neg_re in _COMPILED_PAIRS:
        pos_a = pos_re.search(text_a)
        pos_b = pos_re.search(text_b)
        neg_a = neg_re.search(text_a)
        neg_b = neg_re.search(text_b)
        if (pos_a and neg_b) or (neg_a and pos_b):
            return True
    return False


def dream_scan(conn, new_mem_id: int, agent_name: str, content: str,
               category: str = "", scan_window: int = _DEFAULT_SCAN_WINDOW,
               threshold: float = _JACCARD_THRESHOLD) -> list[dict]:
    """Scan recent memories for contradictions with the newly stored memory.

    Args:
        conn: Database connection (must have memories + edges tables).
        new_mem_id: ID of the just-inserted memory.
        agent_name: Agent that owns the new memory (used for scoping scan).
        content: Text content of the new memory.
        category: Category of the new memory (scoped if non-empty).
        scan_window: Number of most recent memories to scan (default 50).
        threshold: Jaccard similarity threshold for candidate selection.

    Returns:
        List of conflict dicts (empty if none found).
    """
    conflicts: list[dict] = []

    # 1. Fetch recent memories from the same agent
    if category:
        rows = conn.execute(
            "SELECT id, content, created_at FROM memories "
            "WHERE LOWER(agent_name) = LOWER(?) AND category = ? AND id != ? "
            "ORDER BY created_at DESC LIMIT ?",
            (agent_name, category, new_mem_id, scan_window),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT id, content, created_at FROM memories "
            "WHERE LOWER(agent_name) = LOWER(?) AND id != ? "
            "ORDER BY created_at DESC LIMIT ?",
            (agent_name, new_mem_id, scan_window),
        ).fetchall()

    if not rows:
        return conflicts

    new_tokens = set(tokenize(content))

    # 2. Scan for contradictions
    for row in rows:
        existing_text = row["content"] or ""
        if not existing_text.strip():
            continue

        # Quick Jaccard filter
        existing_tokens = set(tokenize(existing_text))
        sim = jaccard(new_tokens, existing_tokens)

        if sim < threshold:
            # Still check for strong contradiction at lower similarity
            if sim >= _CONTRADICT_JACCARD_MIN:
                if not _check_contradiction(content, existing_text):
                    continue
            else:
                continue

        # Deeper contradiction check
        is_contradiction = _check_contradiction(content, existing_text)
        if not is_contradiction:
            # Fallback: check existing_text vs content (reverse)
            is_contradiction = _check_contradiction(existing_text, content)

        if not is_contradiction:
            continue

        # 3. Timestamp-based resolution
        new_ts = _parse_ts_or_fallback()
        existing_ts = _parse_ts_or_fallback(row["created_at"])
        verdict = "newer_wins" if new_ts >= existing_ts else "older_wins"

        # 4. Create or update contradiction edge
        # Always create: new_mem_id → existing_id  with contradicts
        now = datetime.now(timezone.utc).isoformat()
        meta = json.dumps({
            "resolved_by": "timestamp",
            "winner_id": new_mem_id if verdict == "newer_wins" else row["id"],
            "verdict": verdict,
            "detected_at": now,
        }, ensure_ascii=False)

        existing_edge = conn.execute(
            "SELECT id FROM edges WHERE source_id = ? AND target_id = ? AND relation_type = 'contradicts'",
            (new_mem_id, row["id"]),
        ).fetchone()

        if existing_edge:
            edge_id = existing_edge["id"]
            conn.execute(
                "UPDATE edges SET weight = weight + 0.1, metadata = ? WHERE id = ?",
                (meta, edge_id),
            )
        else:
            conn.execute(
                "INSERT INTO edges (source_id, target_id, relation_type, weight, created_at, metadata) "
                "VALUES (?, ?, 'contradicts', ?, ?, ?)",
                (new_mem_id, row["id"], round(sim, 2), now, meta),
            )
            edge_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

        # If the existing memory had a contradict edge with another memory
        # that now agrees with the new memory, mark it resolved
        if verdict == "newer_wins":
            conn.execute(
                "UPDATE edges SET metadata = json_set(COALESCE(metadata, '{}'), '$.resolved_by_winner', ?) "
                "WHERE relation_type = 'contradicts' AND target_id = ? AND source_id != ?",
                (now, row["id"], new_mem_id),
            )

        conflicts.append({
            "conflict_with": row["id"],
            "content": existing_text[:200],
            "stance": "oppose",
            "resolved": verdict,
            "edge_id": edge_id,
        })
        logger.info("dream: memory #%d contradicts #%d (%s)", new_mem_id, row["id"], verdict)

        # Mark both memories with conflict status for agent visibility
        conn.execute(
            "UPDATE memories SET memory_status = 'conflict' WHERE id = ?",
            (new_mem_id,),
        )
        conn.execute(
            "UPDATE memories SET memory_status = 'conflict' WHERE id = ? AND memory_status IS NULL",
            (row["id"],),
        )

        # Limit to 3 conflicts per capture to avoid noise
        if len(conflicts) >= 3:
            break

    return conflicts


def _parse_ts_or_fallback(ts_str: str | None = None) -> str:
    """Return a stable sortable timestamp string for comparison.

    Falls back to current UTC time if ts_str is None or unparseable.
    """
    if ts_str:
        # Strip trailing Z for consistent comparison
        clean = ts_str.rstrip("Z").split(".")[0]
        if clean:
            return clean
    return datetime.now(timezone.utc).isoformat().split(".")[0]