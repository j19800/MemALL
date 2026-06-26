"""Pipeline utilities — shared functions for memory generation steps.

Centralized subject generation ensures all pipeline-created memories
get human-readable titles derived from content, not metadata concatenation.
"""

import re


def _smart_subject(content: str, max_len: int = 80) -> str:
    """Extract a human-readable subject from memory content.

    Strips structural prefixes (``[L9 聚合]``, ``[MODULE:``, etc.) and
    picks the first meaningful sentence from the content body.
    Falls back to a clean truncated snippet if no sentence boundary found.

    Args:
        content: The full memory content text.
        max_len: Maximum subject length (default 80).

    Returns:
        A human-readable subject string (never empty — falls back to ``"记录"``).
    """
    if not content or not content.strip():
        return "记录"

    text = content.strip()

    # ── 1. Strip structural header prefixes ──
    # Strip leading bracket tags: [L9 聚合], [MODULE:path], [讨论], [任务], [??], etc.
    text = re.sub(
        r'^\[[^\]\n]+\]\s*',
        '', text
    )
    # Strip leading === or separator lines
    text = re.sub(r'^[═=\-—]{3,}.*', '', text).strip()

    # ── 2. Pick the first meaningful line ──
    lines = text.split("\n")
    candidate = ""
    for line in lines:
        stripped = line.strip()
        # Skip empty lines, pure separators, and pure markup
        if not stripped:
            continue
        if re.match(r'^[═=\-—•·#*>\s]{1,20}$', stripped):
            continue
        candidate = stripped
        break

    if not candidate:
        candidate = text[:max_len]

    # ── 3. Extract first sentence ──
    # Chinese sentence end: 。！？;
    # English sentence end: . ! ? (but not in abbreviations like "v0.1" or "U.S.")
    sentence_end = re.search(
        r'[。！？；……～](?:\s|$)|[.!?](?:\s+[A-Z"「『]|\s*$|$)',
        candidate
    )
    if sentence_end:
        subject = candidate[:sentence_end.end()].strip()
    else:
        subject = candidate

    # ── 4. Clean up: leading/trailing punctuation, whitespace ──
    subject = subject.strip(" 　,，.。!！?？;；:")
    if not subject:
        return "记录"

    # ── 5. Truncate if still too long ──
    if len(subject) > max_len:
        # Try to truncate at a natural boundary (， or 、 or space)
        trunc = subject[:max_len]
        last_break = max(
            trunc.rfind("，"), trunc.rfind("、"),
            trunc.rfind(" "), trunc.rfind("："),
        )
        if last_break > max_len // 2:
            subject = trunc[:last_break] + "…"
        else:
            subject = trunc[:max_len - 1] + "…"

    return subject
