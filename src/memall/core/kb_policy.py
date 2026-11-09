"""KB (knowledge-base) evidence policy helpers.

KB/imported document chunks are *evidence*, not user-state. They must not leak
into continuity/session injection, recap, or be conflated with dialogue memory.
"""
from __future__ import annotations

KB_SOURCE_PIPELINES = ("document.import", "kb_ingest")
_KB_SP_SET = set(KB_SOURCE_PIPELINES)


def is_kb_source_pipeline(sp: str | None) -> bool:
    return bool(sp) and str(sp).strip() in _KB_SP_SET


def source_kind_from_pipeline(sp: str | None, meta: dict | None = None) -> str:
    s = (sp or "").strip() if isinstance(sp, str) else str(sp or "").strip()
    if not s:
        m = meta or {}
        if m.get("kb_kind") or m.get("doc_id"):
            return "kb_ingest"
        return "user_state"
    if s in _KB_SP_SET:
        return s.replace(".", "_")
    if s.startswith("conversation.") or s in ("smart_store", "manual", "observe"):
        return "user_state"
    return s.replace(".", "_")


def kb_sql_exclusion_clauses(column: str = "metadata", table_alias: str = "") -> list[str]:
    """SQL fragments (parameter-free) excluding KB evidence from user-state surfaces."""
    p = f"{table_alias}." if table_alias else ""
    m = f"COALESCE(json_extract({p}{column}, '$.source_pipeline'), '')"
    cat = f"COALESCE({p}category, '')" if not table_alias else f"COALESCE({table_alias}.category, '')"
    return [
        f"{m} NOT IN ('document.import','kb_ingest')",
        f"NOT ({cat}='knowledge' AND {m} LIKE 'document.%')",
    ]
