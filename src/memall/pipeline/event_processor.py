"""Pipeline event processor — reads pending events and dispatches to steps.

Replaces full-table scans with targeted processing of new/changed memories.
For each 'new_memory' event, dispatches the memory_id to the relevant
pipeline steps so they can process it incrementally.
"""

import json
import logging
import re
from datetime import datetime, timezone

from memall.core.db import get_conn
from memall.pipeline.classify import _detect_layers, CATEGORY_RULES

logger = logging.getLogger(__name__)

_BATCH_SIZE = 500

# Pipeline-generated layers — never assigned by inline classify
_PIPELINE_LAYERS = frozenset({"L9", "L10"})


def process_events() -> dict:
    """Read pending pipeline events and dispatch to step handlers.

    Returns:
        ``{"processed": int, "pending": int, "events": [...]}``
    """
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT id, memory_id, event_type, created_at "
            "FROM pipeline_events WHERE processed_at IS NULL "
            "ORDER BY id ASC LIMIT ?",
            (_BATCH_SIZE,),
        ).fetchall()

        if not rows:
            return {"processed": 0, "pending": 0, "events": []}

        now = datetime.now(timezone.utc).isoformat()
        event_ids = [r["id"] for r in rows]

        dispatch_count = 0
        for r in rows:
            if r["event_type"] == "new_memory":
                _dispatch_new_memory(conn, r["memory_id"])
                dispatch_count += 1

        ph = ",".join("?" * len(event_ids))
        conn.execute(
            f"UPDATE pipeline_events SET processed_at = ? WHERE id IN ({ph})",
            (now, *event_ids),
        )
        conn.commit()

        remaining = conn.execute(
            "SELECT COUNT(*) FROM pipeline_events WHERE processed_at IS NULL"
        ).fetchone()[0]

        return {
            "processed": len(rows),
            "pending": remaining,
            "dispatched": dispatch_count,
            "events": [
                {"id": r["id"], "memory_id": r["memory_id"],
                 "event_type": r["event_type"], "created_at": r["created_at"]}
                for r in rows
            ],
        }
    finally:
        conn.close()


def _dispatch_new_memory(conn, memory_id: int) -> None:
    """Run lightweight inline processing for a newly captured memory.

    This runs in the event_processor step (first pipeline step), so the
    memory gets basic enrichment and classification immediately without
    waiting for a full pipeline scan.
    """
    row = conn.execute(
        "SELECT id, content, level, category, metadata, summary FROM memories WHERE id = ?",
        (memory_id,),
    ).fetchone()
    if not row:
        return

    text = row["content"]
    meta = {}
    try:
        meta = json.loads(row["metadata"]) if row["metadata"] else {}
    except (json.JSONDecodeError, TypeError):
        meta = {}

    # ── Inline enrich: entities, time refs, problems ──
    enrich = {}

    entities = re.findall(r'[A-Z][a-zA-Z]*(?:\s[A-Z][a-zA-Z]*)*', text)
    if entities:
        enrich["entities"] = list(set(entities))

    time_refs = re.findall(
        r'(\d{4}-\d{2}-\d{2}|\d{1,2}月\s?\d{1,2}日|上周|这周|下个月|昨天|今天|明天)',
        text,
    )
    if time_refs:
        enrich["time_refs"] = time_refs

    problems = re.findall(r'(问题|瓶颈|不足|太慢|太复杂|不够|没法)[^。]*', text)
    if problems:
        enrich["problems"] = [p.strip() for p in problems]

    decisions = re.findall(r'(决定|选择|采用|改用|替换|用\s+\w+\s+替代)[^。]*', text)
    if decisions:
        enrich["decisions"] = [d.strip() for d in decisions]

    if enrich:
        meta["enrich"] = {
            "value": enrich,
            "_meta": {"version": 1, "written_at": datetime.now(timezone.utc).isoformat()},
        }
        conn.execute(
            "UPDATE memories SET metadata = ? WHERE id = ?",
            (json.dumps(meta, ensure_ascii=False), memory_id),
        )

    # ── Inline classify: level detection from content ──
    _inline_classify(conn, row, meta)


def _inline_classify(conn, row: dict, meta: dict) -> None:
    """Detect memory level from content using the unified priority chain.

    Uses ``_detect_layers()`` from classify.py for consistency with the
    batch classifier. Only sets initial level — never overrides pipeline
    layers (L9, L10).
    """
    content = row["content"] or ""
    current_level = row["level"]

    # Skip pipeline-generated layers
    if current_level in _PIPELINE_LAYERS:
        return

    summary = row["summary"] or ""
    result = _detect_layers(content, summary, current_level=current_level)
    detected = result["primary"]

    # Category detection (simple rule-based)
    best_cat = "general"
    best_score = 0
    for pattern, cat in CATEGORY_RULES:
        matches = re.findall(pattern, content)
        score = len(matches)
        if score > best_score:
            best_score = score
            best_cat = cat

    if detected != current_level or best_cat != row["category"]:
        conn.execute(
            "UPDATE memories SET level = ?, category = ? WHERE id = ?",
            (detected, best_cat, row["id"]),
        )
