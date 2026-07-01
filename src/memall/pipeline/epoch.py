"""
Pipeline step: epoch_step — detect and manage period boundaries.

Detects epoch boundaries for each agent using four rules:
1. GAP: >48h between consecutive memories
2. CATEGORY_SHIFT: sliding window category change
3. L6_VIEWPOINT: reflection with viewpoint-change keywords
4. MANUAL: explicit level='epoch' memories

Auto-labels each epoch from its dominant category and top subjects.
"""

import json
import logging
from collections import Counter
from datetime import datetime, timezone
from typing import Optional

from memall.core.db import get_conn
from memall.core.utils import parse_ts

logger = logging.getLogger(__name__)

# ── Detection config ────────────────────────────────────────────
_GAP_THRESHOLD_HOURS = 48
_SHIFT_WINDOW_SIZE = 5
_MIN_BOUNDARY_GAP = 10  # minimum memories between boundaries (consolidation)
_VIEWPOINT_KEYWORDS = [
    "观点变了", "想法变了", "重新认识", "有了新的理解",
    "不再认为", "改变了看法", "shift", "pivot", "rethink",
    "重新思考", "转变", "转型", "转向",
]


# ── Helpers ─────────────────────────────────────────────────────

def _hours_between(ts1: str, ts2: str) -> float:
    """Compute hours between two ISO timestamp strings."""
    dt1 = parse_ts(ts1)
    dt2 = parse_ts(ts2)
    if dt1 is None or dt2 is None:
        return 0.0
    return abs((dt2 - dt1).total_seconds() / 3600.0)


def _dominant_category(memories: list[dict]) -> str:
    """Return the most frequent category in a list of memory dicts."""
    cats = [m.get("category", "general") or "general" for m in memories]
    return Counter(cats).most_common(1)[0][0] if cats else "general"


def _has_viewpoint_keywords(content: str) -> bool:
    """Check if content contains viewpoint-change keywords."""
    if not content:
        return False
    return any(kw in content for kw in _VIEWPOINT_KEYWORDS)


def _auto_label(conn, agent_name: str, epoch_start: str, epoch_end: Optional[str]) -> str:
    """Generate a human-readable epoch label from dominant topics."""
    if epoch_end:
        rows = conn.execute(
            "SELECT category, subject FROM memories "
            "WHERE agent_name = ? AND occurred_at >= ? AND occurred_at < ? "
            "ORDER BY created_at DESC LIMIT 20",
            (agent_name, epoch_start, epoch_end),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT category, subject FROM memories "
            "WHERE agent_name = ? AND occurred_at >= ? "
            "ORDER BY created_at DESC LIMIT 20",
            (agent_name, epoch_start),
        ).fetchall()

    cats = Counter(r["category"] for r in rows if r["category"])
    top_cat = cats.most_common(1)[0][0] if cats else "general"

    # Extract topic fragments from subjects
    topics = []
    for r in rows:
        s = r["subject"] or ""
        for sep in ("·", ":", "：", "—", "-"):
            if sep in s:
                topics.append(s.split(sep)[0].strip())
                break
        else:
            if s:
                topics.append(s[:20])
    top_topics = [t for t, _ in Counter(topics).most_common(3) if t]

    parts = [top_cat] + top_topics[:2]
    return " · ".join(parts)[:80]


def _detect_boundaries(memories: list[dict], l6_ids: set[int]) -> list[dict]:
    """Detect epoch boundaries in an ordered list of memory dicts.

    Each memory dict must have: id, agent_name, category, subject, content,
    occurred_at, level, created_at.

    Returns list of {memory_id, reason, timestamp, ...}.
    """
    if len(memories) < 2:
        return []

    boundaries = []

    for i in range(1, len(memories)):
        prev = memories[i - 1]
        curr = memories[i]

        # Rule 1: GAP detection
        gap_hours = _hours_between(prev["occurred_at"], curr["occurred_at"])
        if gap_hours > _GAP_THRESHOLD_HOURS:
            boundaries.append({
                "memory_id": curr["id"],
                "reason": "gap",
                "timestamp": curr["occurred_at"],
                "gap_hours": round(gap_hours, 1),
            })
            continue  # Don't apply other rules at the same point

        # Rule 2: Category shift (sliding window)
        if i >= _SHIFT_WINDOW_SIZE * 2:
            before = memories[i - _SHIFT_WINDOW_SIZE:i]
            after = memories[i:i + _SHIFT_WINDOW_SIZE]
            if len(after) >= _SHIFT_WINDOW_SIZE:
                before_dom = _dominant_category(before)
                after_dom = _dominant_category(after)
                if before_dom and after_dom and before_dom != after_dom:
                    boundaries.append({
                        "memory_id": curr["id"],
                        "reason": "category_shift",
                        "timestamp": curr["occurred_at"],
                        "from_category": before_dom,
                        "to_category": after_dom,
                    })
                    continue

        # Rule 3: L6 viewpoint change
        if curr["id"] in l6_ids and _has_viewpoint_keywords(curr.get("content", "")):
            boundaries.append({
                "memory_id": curr["id"],
                "reason": "l6_viewpoint_change",
                "timestamp": curr["occurred_at"],
            })

    # Consolidate: remove boundaries within _MIN_BOUNDARY_GAP indices of each other
    if len(boundaries) > 1:
        lookup = _build_lookup(memories)
        consolidated = [boundaries[0]]
        for b in boundaries[1:]:
            prev_idx = lookup.get(consolidated[-1]["memory_id"], 0)
            curr_idx = lookup.get(b["memory_id"], 999999)
            if curr_idx - prev_idx >= _MIN_BOUNDARY_GAP:
                consolidated.append(b)
        boundaries = consolidated

    return boundaries


def _build_lookup(memories: list[dict]) -> dict[int, int]:
    """Build {memory_id: index} lookup from a list of memory dicts."""
    return {m["id"]: i for i, m in enumerate(memories)}


def _process_manual_epochs(conn) -> int:
    """Find memories with level='epoch' that haven't been processed.

    Returns count of new manual epoch declarations found.
    """
    rows = conn.execute(
        "SELECT id, content, agent_name, occurred_at, subject, metadata "
        "FROM memories WHERE level = 'epoch'"
    ).fetchall()
    count = 0
    now = datetime.now(timezone.utc).isoformat()
    for r in rows:
        meta = {}
        try:
            meta = json.loads(r["metadata"]) if isinstance(r["metadata"], str) else r["metadata"]
        except (json.JSONDecodeError, TypeError):
            meta = {}
        if meta.get("epoch_processed"):
            continue

        # Check if this is a start or end marker
        content = r["content"] or ""
        agent_name = r["agent_name"]
        occurred = r["occurred_at"] or now

        label = ""
        if r["subject"]:
            label = r["subject"][:80]
        if not label:
            label = content[:60]

        # Upsert into epochs
        conn.execute("""
            INSERT INTO epochs (agent_name, label, started_at, boundary_reason,
                                category, summary, metadata, created_at)
            VALUES (?, ?, ?, 'manual', ?, ?, ?, ?)
            ON CONFLICT(agent_name, started_at) DO UPDATE SET
                label = excluded.label,
                boundary_reason = 'manual'
        """, (
            agent_name, label, occurred,
            "general", content[:200],
            json.dumps({"memory_id": r["id"]}, ensure_ascii=False),
            now,
        ))
        count += 1

    return count


# ── Main step ───────────────────────────────────────────────────

def epoch_step() -> dict:
    """Detect epoch boundaries and label epochs for all agents.

    Processes agents that have new/changed memories since last run.

    Returns:
        dict with boundaries_found, epochs_created, agents_processed.
    """
    conn = get_conn()
    try:
        now = datetime.now(timezone.utc).isoformat()

        # Read pipeline_state to get last run time
        state = conn.execute(
            "SELECT last_run_at FROM pipeline_state WHERE step_name = 'epoch'"
        ).fetchone()
        last_run = state["last_run_at"] if state else None

        # Determine which agents to process
        if last_run:
            agents = conn.execute(
                "SELECT DISTINCT agent_name FROM memories "
                "WHERE updated_at > ? AND agent_name != '' "
                "ORDER BY agent_name",
                (last_run,),
            ).fetchall()
        else:
            agents = conn.execute(
                "SELECT DISTINCT agent_name FROM memories "
                "WHERE agent_name != '' ORDER BY agent_name"
            ).fetchall()

        agent_names = [r["agent_name"] for r in agents]
        total_boundaries = 0
        total_epochs_created = 0

        for agent_name in agent_names:
            # Fetch all memories for this agent, ordered by occurred_at
            rows = conn.execute(
                "SELECT id, agent_name, category, subject, content, "
                "level, occurred_at, created_at FROM memories "
                "WHERE agent_name = ? ORDER BY occurred_at ASC",
                (agent_name,),
            ).fetchall()

            if len(rows) < 2:
                continue

            # Convert to dicts for easier handling
            memories = [dict(r) for r in rows]

            # Find L6 memory IDs for viewpoint detection
            l6_ids = set()
            for m in memories:
                if m["level"] == "L6":
                    l6_ids.add(m["id"])

            # Detect boundaries
            boundaries = _detect_boundaries(memories, l6_ids)
            if not boundaries:
                continue

            total_boundaries += len(boundaries)

            # Create/update epochs from boundaries
            prev_epoch_start = None
            for boundary in boundaries:
                epoch_start = boundary["timestamp"]
                epoch_label = ""
                epoch_category = ""

                # Try to generate label from preceding memories
                preceding = [m for m in memories if m["occurred_at"] < epoch_start]
                if preceding:
                    cats = Counter(m["category"] for m in preceding[-10:])
                    epoch_category = cats.most_common(1)[0][0] if cats else "general"

                # Compute label
                epoch_label = boundary["reason"]
                if boundary.get("to_category"):
                    epoch_label = f"shift to {boundary['to_category']}"

                if prev_epoch_start:
                    # End previous epoch
                    conn.execute(
                        "UPDATE epochs SET ended_at = ? WHERE agent_name = ? AND started_at = ?",
                        (epoch_start, agent_name, prev_epoch_start),
                    )
                    # Update memory_count and label for previous epoch
                    prev_label = _auto_label(conn, agent_name, prev_epoch_start, epoch_start)
                    prev_count = conn.execute(
                        "SELECT COUNT(*) FROM memories WHERE agent_name = ? "
                        "AND occurred_at >= ? AND occurred_at < ?",
                        (agent_name, prev_epoch_start, epoch_start),
                    ).fetchone()[0]
                    conn.execute(
                        "UPDATE epochs SET label = ?, memory_count = ? "
                        "WHERE agent_name = ? AND started_at = ?",
                        (prev_label[:80], prev_count, agent_name, prev_epoch_start),
                    )

                # Create new epoch
                conn.execute("""
                    INSERT INTO epochs (agent_name, label, started_at, boundary_reason,
                                        category, metadata, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(agent_name, started_at) DO NOTHING
                """, (
                    agent_name, epoch_label[:80], epoch_start,
                    boundary["reason"], epoch_category,
                    json.dumps(boundary, ensure_ascii=False, default=str),
                    now,
                ))
                total_epochs_created += 1
                prev_epoch_start = epoch_start

        # Process manual (level='epoch') memories
        manual_count = _process_manual_epochs(conn)

        # Save pipeline_state
        conn.execute("""
            INSERT INTO pipeline_state (step_name, last_run_at, last_processed_id, metadata)
            VALUES ('epoch', ?, 0, '{}')
            ON CONFLICT(step_name) DO UPDATE SET last_run_at = excluded.last_run_at
        """, (now,))
        conn.commit()

        return {
            "agents_scanned": len(agent_names),
            "boundaries_detected": total_boundaries,
            "epochs_created": total_epochs_created,
            "manual_epochs": manual_count,
        }
    finally:
        conn.close()
