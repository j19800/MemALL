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
from memall.core.nlp import tokenize

logger = logging.getLogger(__name__)

_BATCH_SIZE = 500

# Module-level constants for inline classify (avoids re-computing on every call)
_LAYER_RANK = {
    "P0": 70, "P1": 60, "P2": 20, "P3": 10, "P4": 5,
    "L1": 30, "L2": 35, "L3": 40, "L4": 45, "L5": 48,
    "L6": 80, "L7": 55, "L8": 50, "L9": 90, "L10": 100, "L11": 95,
}
_TERMINAL_LAYERS = frozenset({"L6", "L8", "L9", "L10", "L11"})
_RANK_TO_LEVEL = {v: k for k, v in _LAYER_RANK.items()}

_KEYWORD_RULES = [
    (70, [r'\b(bug|crash|hotfix|security|vulnerability|数据丢失|安全漏洞|崩溃)\b']),
    (60, [r'\b(fix|error|fail|issue|缺陷|故障|报错)\b']),
    (55, [r'\blesson|教训|经验|做错了|不对|修正|更正|纠正\b']),
    (50, [r'\b(module_ref|edge|graph|图谱|关系|关联)\b']),
    (48, [r'\b(discussion|讨论|decision|决定|方案选型)\b']),
    (45, [r'\b(decide|选择|采用|改用|替换|会议|结论)\b']),
    (40, [r'\b(workflow|流程|step|步骤|阶段)\b']),
    (35, [r'\b(pipeline|enrich|classify|distill)\b']),
    (30, [r'\b(identity|profile|persona|agent|角色)\b']),
]


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
        "SELECT id, content, level, category, metadata FROM memories WHERE id = ?",
        (memory_id,),
    ).fetchone()
    if not row:
        return

    text = row["content"]
    meta = {}
    try:
        import json
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
    """Detect memory level from content for a single memory.

    Uses module-level _LAYER_RANK and _KEYWORD_RULES.
    Only upgrades: if the detected level rank is higher than current, update.
    """
    text = row["content"].lower()
    current_level = row["level"]

    if current_level in _TERMINAL_LAYERS:
        return

    detected_level = current_level
    detected_rank = _LAYER_RANK.get(current_level, 0)

    for rank, patterns in _KEYWORD_RULES:
        if rank <= detected_rank:
            continue
        for pat in patterns:
            if re.search(pat, text, re.IGNORECASE):
                candidate = _RANK_TO_LEVEL.get(rank)
                if candidate and rank > detected_rank and candidate not in _TERMINAL_LAYERS:
                    detected_level = candidate
                    detected_rank = rank
                break

    if detected_level != current_level:
        conn.execute(
            "UPDATE memories SET level = ? WHERE id = ?",
            (detected_level, row["id"]),
        )
