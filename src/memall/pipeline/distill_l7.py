import logging
import re
import sqlite3
from memall.core.db import get_conn, content_hash
from memall.core.thin_waist import capture

logger = logging.getLogger(__name__)

# Patterns that indicate a lesson/improvement takeaway in L6 reflections
_LESSON_PATTERNS = [
    # Chinese patterns
    r"(?:教训|根因|改进点)[：:]\s*(.{10,200})",
    r"(?:下次|以后)(?:应|应该|要|必须)(.{10,200})",
    r"(?:我)?(?:需要|应该|必须|要)注意(.{10,200})",
    r"避免(?:再)?(?:犯|出现)(.{10,200})",
    r"(?:别再|不要再|不要)(.{10,200})",
    r"(?:啊|哦|嗯)，?原来(?:是|如)此(.{10,200})",
    r"学到了[：:!\。]?\s*(.{10,200})",
    r"(?:做对了|做得好的|好的做法)[：:]\s*(.{10,200})",
    r"What went well[：:]\s*(.{10,200})",
    # English patterns
    r"lesson(?: learned)?[:\s]+(.{10,200})",
    r"(?:root cause|root-cause)[:\s]+(.{10,200})",
    r"(?:next time|in the future).{0,20}(.{10,200})",
]

# How many chars of the L6 content to scan
_MAX_SCAN_LEN = 2000


def _extract_lessons(content: str) -> list[str]:
    """Extract lesson snippets from L6 reflection content.
    Returns deduplicated (by lowercase prefix) snippets.
    """
    content = content[:_MAX_SCAN_LEN]
    seen = set()
    lessons = []
    for pat in _LESSON_PATTERNS:
        for m in re.finditer(pat, content, re.IGNORECASE | re.DOTALL):
            snippet = m.group(1).strip()
            if len(snippet) < 12:
                continue
            # Dedup by first 40 chars (lowercase)
            key = snippet[:40].lower()
            if key not in seen:
                seen.add(key)
                lessons.append(snippet)
    return lessons


def distill_l7_step() -> dict:
    """Scan unprocessed L6 reflections and extract L7 preference/lesson memories.

    Finds L6 reflections that have no ``derived_l7_from`` edge recorded,
    extracts lesson patterns from their content, creates L7 memories, and
    records edges to prevent re-processing.
    """
    conn = get_conn()
    try:
        # Find L6 reflections not yet processed for L7
        rows = conn.execute(
            "SELECT m.id, m.content, m.summary, m.agent_name, m.subject "
            "FROM memories m "
            "WHERE m.level = 'L6' AND NOT EXISTS ("
            "  SELECT 1 FROM edges e "
            "  WHERE e.relation_type = 'derived_l7_from' "
            "  AND e.target_id = m.id"
            ") "
            "ORDER BY m.id DESC LIMIT 100"
        ).fetchall()
    finally:
        conn.close()

    total_lessons = 0
    created_l7 = 0
    skipped = 0
    errors = 0

    # Use one connection for all inner-loop operations
    inner_conn = get_conn()
    try:
        for row in rows:
            text = f"{row['summary'] or ''} {row['content'] or ''}"
            lessons = _extract_lessons(text)
            if not lessons:
                skipped += 1
                continue
            agent = row["agent_name"] or "system"

            for i, lesson in enumerate(lessons):
                total_lessons += 1
                try:
                    l7_content = f"[L7 教训] {lesson}"
                    ch = content_hash(l7_content)

                    # Dedup: skip if identical L7 already exists
                    dup = inner_conn.execute(
                        "SELECT id FROM memories WHERE content_hash = ? AND level = 'L7' LIMIT 1",
                        (ch,),
                    ).fetchone()

                    if dup:
                        continue

                    # Content-prefix matching: if same normalized prefix (first 40 chars)
                    # exists, weight++ instead of creating a new entry
                    prefix_key = lesson[:40].lower().strip()
                    try:
                        existing = inner_conn.execute(
                            "SELECT id, content, weight FROM memories "
                            "WHERE level = 'L7' AND LOWER(SUBSTR(TRIM(REPLACE(content, '[L7 教训] ', '')), 1, 40)) = ? "
                            "LIMIT 1",
                            (prefix_key,),
                        ).fetchone()
                    except sqlite3.OperationalError:
                        existing = None

                    if existing:
                        new_weight = (existing["weight"] or 1) + 1
                        inner_conn.execute(
                            "UPDATE memories SET weight = ?, content = ?, updated_at = datetime('now') WHERE id = ?",
                            (new_weight, l7_content, existing["id"]),
                        )
                        inner_conn.commit()
                        src_id = existing["id"]
                        created_l7 += 1
                        logger.info("distill_l7: content-prefix matched → weight=%d (mem_id=%d)", new_weight, existing["id"])
                    else:
                        src_id = capture(
                            {
                                "content": l7_content,
                                "subject": f"lesson: {lesson[:60]}",
                                "category": "reflection",
                                "level": "L7",
                                "owner": "system",
                                "agent_name": agent,
                            }
                        )

                    # Record edge: L7 → derived_l7_from → L6
                    inner_conn.execute(
                        "INSERT OR IGNORE INTO edges (source_id, target_id, relation_type, weight, metadata) "
                        "VALUES (?, ?, 'derived_l7_from', 1.0, '{}')",
                        (src_id, row["id"]),
                    )
                    inner_conn.commit()

                except sqlite3.Error:
                    logger.warning("distill_l7.py: silent error", exc_info=True)
                    errors += 1
    finally:
        inner_conn.close()

    return {
        "scanned": len(rows),
        "skipped_no_lesson": skipped,
        "lessons_found": total_lessons,
        "created_l7": created_l7,
        "errors": errors,
    }
