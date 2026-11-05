"""
Phase 3 (P2): Narrative → L9 knowledge promotion.

Scans narratives and promotes qualifying key insights/decisions into L9
knowledge memories.  Opt-in: registered as pipeline step ``narrative_l9``
gated by ``include_narrative_l9`` (default False), so existing pipeline
runs are completely unaffected.

Promotion criteria
------------------
A narrative qualifies when its ``narrative_text`` or ``summary`` contains
at least one decision/insight signal keyword (Chinese + English):

    決定, 决策, 方案, 架构, 确认, 原则, 规范, 标准, 决议,
    conclusion, decision, architecture, standard, principle

Deduplication
-------------
Before inserting, the function checks whether an L9 memory already exists
whose ``metadata.source_narrative_id`` matches the narrative id.  If so,
the narrative is skipped (idempotent re-runs).

Each promoted memory is written via :func:`memall.core.thin_waist.capture`
with::

    level='L9', category='knowledge',
    subject='[知识] {narrative_type} {span_start}→{span_end}',
    metadata={source_narrative_id, narrative_type, span_start, span_end,
              promoted_at, source='narrative_l9_promotion'}
"""

import json
import logging
from datetime import datetime, timezone, timedelta

from memall.core.db import get_conn
from memall.core.thin_waist import capture

logger = logging.getLogger(__name__)

# Decision / insight signal keywords (Chinese + English).
_PROMOTION_KEYWORDS = (
    "决定", "决策", "方案", "架构", "确认", "原则", "规范", "标准", "决议",
    "conclusion", "decision", "architecture", "standard", "principle",
)

# Look-back window in days for "recent" narratives.
_RECENT_DAYS = 30


def _has_signal(text: str) -> bool:
    """True when *text* contains at least one promotion keyword."""
    if not text:
        return False
    lower = text.lower()
    return any(kw.lower() in lower for kw in _PROMOTION_KEYWORDS)


def _already_promoted(conn, narrative_id: int) -> bool:
    """True when an L9 memory already references *narrative_id* via metadata."""
    row = conn.execute(
        "SELECT id FROM memories WHERE level = 'L9' "
        "AND CAST(json_extract(metadata, '$.source_narrative_id') AS TEXT) = ?",
        (str(narrative_id),),
    ).fetchone()
    return row is not None


def _first_paragraph(text: str) -> str:
    """Return the first paragraph of *text* (up to first blank line)."""
    if not text:
        return ""
    parts = text.split("\n\n")
    return parts[0].strip() if parts else text.strip()


def promote_narratives_to_l9(days: int = _RECENT_DAYS) -> dict:
    """Scan narratives and promote qualifying insights to L9 knowledge.

    Args:
        days: Look-back window in days.  Narratives whose ``generated_at``
            fall within this window are considered.  If none exist in the
            window, *all* narratives are scanned (fallback).

    Returns:
        ``{"scanned": int, "promoted": int, "skipped": int, "errors": int}``
    """
    conn = get_conn()
    try:
        now = datetime.now(timezone.utc)
        cutoff = (now - timedelta(days=days)).isoformat()

        # ── 1. Fetch candidate narratives ──
        recent = conn.execute(
            "SELECT id, agent_name, narrative_type, span_start, span_end, "
            "narrative_text, summary, generated_at FROM narratives "
            "WHERE generated_at >= ? ORDER BY generated_at DESC",
            (cutoff,),
        ).fetchall()

        # Fallback: if no recent narratives, scan all.
        if not recent:
            narratives = conn.execute(
                "SELECT id, agent_name, narrative_type, span_start, span_end, "
                "narrative_text, summary, generated_at FROM narratives "
                "ORDER BY generated_at DESC"
            ).fetchall()
        else:
            narratives = recent

        scanned = len(narratives)
        promoted = 0
        skipped = 0
        errors = 0

        for n in narratives:
            nid = n["id"]
            agent = n["agent_name"] or "unknown"
            ntype = n["narrative_type"] or "unknown"
            span_start = n["span_start"] or ""
            span_end = n["span_end"] or ""
            ntext = n["narrative_text"] or ""
            summary = n["summary"] or ""

            # ── 2. Signal check ──
            combined = f"{summary} {ntext}"
            if not _has_signal(combined):
                skipped += 1
                continue

            # ── 3. Dedup check ──
            try:
                if _already_promoted(conn, nid):
                    skipped += 1
                    continue
            except Exception:
                # json_extract may not be available on older SQLite builds;
                # treat as not promoted so the narrative can still be promoted.
                logger.debug(
                    "narrative_l9: dedup check failed for narrative %s, "
                    "treating as not promoted", nid
                )

            # ── 4. Promote via capture() ──
            try:
                content = summary or _first_paragraph(ntext)
                if not content:
                    skipped += 1
                    continue

                subject = f"[知识] {ntype} {span_start}→{span_end}"

                # MemoryInput.metadata is a JSON *string* field — passing a
                # dict would break json_extract provenance lookups downstream.
                metadata = json.dumps(
                    {
                        "source": "narrative_l9_promotion",
                        "source_narrative_id": str(nid),
                        "narrative_type": ntype,
                        "span_start": span_start,
                        "span_end": span_end,
                        "promoted_at": now.isoformat(),
                    },
                    ensure_ascii=False,
                )

                capture(
                    {
                        "content": content,
                        "level": "L9",
                        "category": "knowledge",
                        "agent_name": agent,
                        "subject": subject,
                        "metadata": metadata,
                        "stream": "knowledge",
                        "visibility": "trusted",
                    }
                )
                promoted += 1
                logger.info(
                    "narrative_l9: promoted narrative %s (%s) as L9 knowledge",
                    nid, ntype,
                )
            except Exception as exc:
                errors += 1
                logger.warning(
                    "narrative_l9: failed to promote narrative %s: %s", nid, exc
                )

        return {"scanned": scanned, "promoted": promoted, "skipped": skipped, "errors": errors}
    finally:
        conn.close()