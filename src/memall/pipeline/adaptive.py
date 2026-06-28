"""
Phase 12: AI Adaptive Subsystem (P2)

Four-in-one adaptive pipeline:
- AdaptiveCleaner: adjusts cleaning strategy based on memory growth rate
- AdaptiveIndexer: builds/cleans acceleration indexes from query patterns
- AdaptiveDistiller: adjusts distillation frequency based on volume + velocity
- adaptive_step: runs all three; adaptive_report: generates status summary
"""

import logging

logger = logging.getLogger(__name__)

import hashlib
import re
from collections import Counter
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional

from memall.core.db import get_conn


# ══════════════════════════════════════════════════════════════════════
# 1. 自适应清洗 (AdaptiveCleaner)
# ══════════════════════════════════════════════════════════════════════

def adaptive_clean(
    agent_name: Optional[str] = None,
    growth_threshold: float = 0.5,
    low_threshold: float = 0.1,
    total_memory_threshold: int = 10000,
    dup_threshold: float = 0.8,
) -> Dict[str, Any]:
    """Analyze memories table cleaning history to decide cleaning strategy.

    Strategy rules:
        - Growth rate > *growth_threshold* → aggressive: also remove content
          that is empty or only contains punctuation / whitespace.
        - Growth rate < *low_threshold* → standard: delegate to
          ``memall.pipeline.clean`` if available, otherwise lightweight cleanup.
        - Total memories > *total_memory_threshold* → compression: remove
          near-duplicate pairs (Jaccard similarity > *dup_threshold*), keeping
          the longer memory.

    Args:
        agent_name: Optional agent filter (``None`` = all agents).
        growth_threshold: Growth-rate trigger for aggressive mode (0-1).
        low_threshold: Below this growth rate use standard mode (0-1).
        total_memory_threshold: Memory count that triggers compression.
        dup_threshold: Jaccard similarity above which a pair is considered duplicate.

    Returns:
        ``{"mode": str, "cleaned_count": int, "trigger_reason": str}``
    """
    conn = get_conn()
    try:
        now = datetime.now(timezone.utc)
        seven_days_ago = (now - timedelta(days=7)).isoformat()

        # -- Build where clauses --
        where_all = ""
        where_recent = ""
        params_all: list = []
        params_recent: list = []
        if agent_name:
            where_all = " WHERE agent_name = ?"
            where_recent = " WHERE agent_name = ? AND created_at >= ?"
            params_all = [agent_name]
            params_recent = [agent_name, seven_days_ago]
        else:
            where_recent = " WHERE created_at >= ?"
            params_recent = [seven_days_ago]

        total_count = conn.execute(
            f"SELECT COUNT(*) as c FROM memories{where_all}", params_all
        ).fetchone()["c"]
        recent_count = conn.execute(
            f"SELECT COUNT(*) as c FROM memories{where_recent}", params_recent
        ).fetchone()["c"]

        growth_rate = recent_count / max(total_count, 1)
        cleaned_count = 0
        mode = "standard"
        trigger_reason = ""

        # ── Aggressive mode (growth burst) ──
        if growth_rate > growth_threshold:
            mode = "aggressive"
            trigger_reason = (
                f"Growth rate {growth_rate:.2%} > {growth_threshold:.0%} "
                f"({recent_count} new / {total_count} total in 7d)"
            )
            # SQLite GLOB does not support POSIX character classes [:punct:]/[:space:]
            # Use Python-side filter for punctuation/whitespace-only content instead
            cur = conn.execute(
                "SELECT id, content FROM memories WHERE LENGTH(TRIM(content)) = 0"
            )
            punct_ids = []
            for row in cur.fetchall():
                content = row["content"].strip() if row["content"] else ""
                if content and all(c in ".,!?;:()[]{}《》【】\"'、，。！？；：（）" for c in content):
                    punct_ids.append(row["id"])
            if punct_ids:
                placeholders = ",".join("?" for _ in punct_ids)
                conn.execute(f"DELETE FROM memories WHERE id IN ({placeholders})", punct_ids)
                cleaned_count = len(punct_ids)
            else:
                cleaned_count = 0

        # ── Compression mode (volume overflow) ──
        elif total_count > total_memory_threshold:
            mode = "compression"
            trigger_reason = (
                f"Total memories {total_count} > {total_memory_threshold}, "
                f"compression activated"
            )
            rows = conn.execute(
                "SELECT id, content FROM memories ORDER BY id LIMIT 5000"
            ).fetchall()
            to_delete: List[int] = []
            n = len(rows)
            def _tokenize(text: str) -> set:
                """Tokenize for Jaccard: English words + Chinese char bigrams."""
                import re as _re
                tokens = set()
                # English words
                for w in _re.findall(r'[a-zA-Z_]+', text):
                    tokens.add(w.lower())
                # Chinese char bigrams (2-gram)
                chinese_chars = _re.findall(r'[\u4e00-\u9fff]', text)
                for k in range(len(chinese_chars) - 1):
                    tokens.add(chinese_chars[k] + chinese_chars[k+1])
                return tokens

            for i in range(n):
                wi = _tokenize(rows[i]["content"])
                if not wi:
                    continue
                for j in range(i + 1, n):
                    wj = _tokenize(rows[j]["content"])
                    if not wj:
                        continue
                    inter = wi & wj
                    union = wi | wj
                    sim = len(inter) / len(union) if union else 0.0
                    if sim > dup_threshold:
                        # Keep the longer content
                        if len(rows[i]["content"]) >= len(rows[j]["content"]):
                            to_delete.append(rows[j]["id"])
                        else:
                            to_delete.append(rows[i]["id"])
            if to_delete:
                to_delete = list(set(to_delete))
                # Batch delete in chunks to avoid huge placeholders
                chunk = 50
                for k in range(0, len(to_delete), chunk):
                    batch = to_delete[k:k + chunk]
                    ph = ",".join("?" * len(batch))
                    cur = conn.execute(
                        f"DELETE FROM memories WHERE id IN ({ph})", batch
                    )
                    cleaned_count += cur.rowcount

        # ── Standard mode ──
        elif growth_rate < low_threshold:
            mode = "standard"
            trigger_reason = (
                f"Growth rate {growth_rate:.2%} < {low_threshold:.0%}, "
                f"standard cleaning sufficient"
            )
            try:
                from memall.pipeline.clean import clean_step  # type: ignore[import-untyped]
                result = clean_step()
                cleaned_count = (
                    result.get("cleaned", 0)
                    if isinstance(result, dict)
                    else 0
                )
            except ImportError:
                cur = conn.execute(
                    "DELETE FROM memories WHERE content IS NULL OR LENGTH(TRIM(content)) = 0"
                )
                cleaned_count = cur.rowcount
        else:
            mode = "standard"
            trigger_reason = f"Growth rate {growth_rate:.2%} within normal range"
            try:
                from memall.pipeline.clean import clean_step  # type: ignore[import-untyped]
                result = clean_step()
                cleaned_count = (
                    result.get("cleaned", 0)
                    if isinstance(result, dict)
                    else 0
                )
            except ImportError:
                logger.warning("adaptive.py: silent error", exc_info=True)

        conn.commit()
        return {
            "mode": mode,
            "cleaned_count": cleaned_count,
            "trigger_reason": trigger_reason,
        }
    finally:
        conn.close()


# ══════════════════════════════════════════════════════════════════════
# 2. 自适应索引 (AdaptiveIndexer)
# ══════════════════════════════════════════════════════════════════════

def adaptive_index(agent_name: Optional[str] = None) -> Dict[str, Any]:
    """Optimize indexes based on query patterns extracted from ``query_log``.

    Steps:
        1. Read last 30 days of ``query_log``.
        2. Tokenize query text and compute top-10 frequent terms.
        3. For each term, find matching ``memories.category`` values and create
           ``idx_accel_<hash>`` acceleration tables.
        4. Purge ``query_log`` records older than 30 days.
        5. Drop acceleration tables not used in the last 7 days (tracked via
           ``idx_meta``).

    Creates tables ``query_log`` and ``idx_meta`` if they do not exist.

    Args:
        agent_name: Optional agent filter for query_log.

    Returns:
        ``{"high_freq_terms": [...], "accel_tables_created": N,
          "accel_tables_cleaned": N, "query_log_trimmed": N}``
    """
    conn = get_conn()
    try:
        # ── Ensure infrastructure tables ──
        conn.execute(
            "CREATE TABLE IF NOT EXISTS query_log ("
            "id INTEGER PRIMARY KEY, query_text TEXT, agent_name TEXT, timestamp TEXT)"
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS idx_meta ("
            "table_name TEXT PRIMARY KEY, created_at TEXT, last_used TEXT, "
            "hit_count INTEGER DEFAULT 0)"
        )

        now = datetime.now(timezone.utc)
        thirty_days_ago = (now - timedelta(days=30)).isoformat()

        # a) Query recent 30-day query_log
        if agent_name:
            rows = conn.execute(
                "SELECT query_text FROM query_log "
                "WHERE timestamp >= ? AND agent_name = ?",
                (thirty_days_ago, agent_name),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT query_text FROM query_log WHERE timestamp >= ?",
                (thirty_days_ago,),
            ).fetchall()

        # b) Extract top-10 high-frequency terms
        word_counter: Counter = Counter()
        _token_re = re.compile(r"[\w]+|[\u4e00-\u9fff]+")
        for row in rows:
            text = (row["query_text"] or "").lower()
            for w in _token_re.findall(text):
                if len(w) >= 2:
                    word_counter[w] += 1
        high_freq_terms = [w for w, _ in word_counter.most_common(10)]

        # c) Build acceleration tables for matching categories
        accel_tables_created = 0
        for term in high_freq_terms:
            cats = conn.execute(
                "SELECT DISTINCT category FROM memories WHERE category LIKE ?",
                (f"%{term}%",),
            ).fetchall()
            for cat_row in cats:
                cat = cat_row["category"]
                cat_hash = hashlib.md5(cat.encode()).hexdigest()[:12]
                table_name = f"idx_accel_{cat_hash}"

                exists = conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                    (table_name,),
                ).fetchone()
                if not exists:
                    conn.execute(
                        f"CREATE TABLE IF NOT EXISTS {table_name} AS "
                        "SELECT id, content, category FROM memories "
                        "WHERE category = ? ORDER BY created_at DESC",
                        (cat,),
                    )
                    conn.execute(
                        "INSERT OR REPLACE INTO idx_meta "
                        "(table_name, created_at, last_used, hit_count) "
                        "VALUES (?, ?, ?, 0)",
                        (table_name, now.isoformat(), now.isoformat()),
                    )
                    accel_tables_created += 1
                else:
                    conn.execute(
                        "UPDATE idx_meta SET last_used = ? WHERE table_name = ?",
                        (now.isoformat(), table_name),
                    )

        # d) Clean 30-day-old query_log entries
        cur = conn.execute(
            "DELETE FROM query_log WHERE timestamp < ?", (thirty_days_ago,)
        )
        query_log_trimmed = cur.rowcount

        # e) Clean accel tables unused > 7 days
        seven_days_ago = (now - timedelta(days=7)).isoformat()
        stale = conn.execute(
            "SELECT table_name FROM idx_meta WHERE last_used < ?",
            (seven_days_ago,),
        ).fetchall()
        accel_tables_cleaned = 0
        _ALLOWED_TABLE_PREFIXES = frozenset({"accel_", "idx_", "tmp_", "fts_"})
        for t in stale:
            tn = t["table_name"]
            if not any(tn.startswith(p) for p in _ALLOWED_TABLE_PREFIXES):
                raise ValueError(f"Refusing to DROP table '{tn}': not in allowed prefixes")
            conn.execute(f"DROP TABLE IF EXISTS {tn}")
            conn.execute("DELETE FROM idx_meta WHERE table_name = ?", (tn,))
            accel_tables_cleaned += 1

        conn.commit()
        return {
            "high_freq_terms": high_freq_terms,
            "accel_tables_created": accel_tables_created,
            "accel_tables_cleaned": accel_tables_cleaned,
            "query_log_trimmed": query_log_trimmed,
        }
    finally:
        conn.close()


# ══════════════════════════════════════════════════════════════════════
# 3. 自适应蒸馏 (AdaptiveDistiller)
# ══════════════════════════════════════════════════════════════════════

def adaptive_distill(
    agent_name: Optional[str] = None,
    high_freq_growth: float = 0.3,
    high_freq_interval: float = 43200.0,
    low_freq_growth: float = 0.05,
    low_freq_interval: float = 21600.0,
) -> Dict[str, Any]:
    """Adjust distillation frequency based on memory volume and growth rate.

    Decision rules:
        - ``growth_rate > high_freq_growth`` AND ``avg_interval > high_freq_interval``
          (or no history) → distill immediately (``high_freq``).
        - ``growth_rate < low_freq_growth`` AND ``avg_interval > 0`` AND
          ``avg_interval < low_freq_interval`` → skip this round (``low_freq``).
        - Otherwise → normal distillation.

    Creates ``distill_history`` table if it does not exist.

    Args:
        agent_name: Optional agent filter.
        high_freq_growth: Growth-rate above which to force distil (default 0.3).
        high_freq_interval: Min interval (seconds) to trigger high-freq (default 12h).
        low_freq_growth: Growth-rate below which to skip (default 0.05).
        low_freq_interval: Max interval (seconds) to skip (default 6h).

    Returns:
        ``{"mode": "...", "distilled": bool, "skip_reason": "..."}``
    """
    conn = get_conn()
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS distill_history ("
            "id INTEGER PRIMARY KEY, agent_name TEXT, "
            "memory_count_before INT, memory_count_after INT, "
            "triggered_at TEXT, mode TEXT)"
        )

        now = datetime.now(timezone.utc)
        now_iso = now.isoformat()

        # a) Compute avg interval from last 10 distillations
        query = "SELECT triggered_at FROM distill_history"
        params: list = []
        if agent_name:
            query += " WHERE agent_name = ?"
            params.append(agent_name)
        query += " ORDER BY triggered_at DESC LIMIT 10"
        history_rows = conn.execute(query, params).fetchall()

        intervals: List[float] = []
        for i in range(len(history_rows) - 1):
            t1 = datetime.fromisoformat(history_rows[i]["triggered_at"])
            t2 = datetime.fromisoformat(history_rows[i + 1]["triggered_at"])
            intervals.append((t1 - t2).total_seconds())
        avg_interval = sum(intervals) / len(intervals) if intervals else 0.0

        # b) Compute growth rate (last 24h new / total)
        yesterday = (now - timedelta(days=1)).isoformat()

        where_all = "WHERE agent_name = ?" if agent_name else ""
        where_recent = (
            "WHERE agent_name = ? AND created_at >= ?"
            if agent_name
            else "WHERE created_at >= ?"
        )

        all_params = [agent_name] if agent_name else []
        recent_params = [agent_name, yesterday] if agent_name else [yesterday]

        total_count = conn.execute(
            f"SELECT COUNT(*) as c FROM memories {where_all}", all_params
        ).fetchone()["c"]
        recent_count = conn.execute(
            f"SELECT COUNT(*) as c FROM memories {where_recent}", recent_params
        ).fetchone()["c"]

        growth_rate = recent_count / max(total_count, 1)

        # c) Decide mode
        mode = "normal"
        distilled = False
        skip_reason: Optional[str] = None

        if growth_rate > high_freq_growth and (avg_interval > high_freq_interval or avg_interval == 0.0):
            mode = "high_freq"
        elif growth_rate < low_freq_growth and avg_interval > 0 and avg_interval < low_freq_interval:
            mode = "low_freq"
            skip_reason = (
                f"Growth {growth_rate:.3f} < {low_freq_growth}, "
                f"avg interval {avg_interval:.0f}s < {low_freq_interval:.0f}s"
            )

        # d) Execute distillation (unless low_freq)
        if mode in ("high_freq", "normal"):
            mem_before = total_count
            try:
                from memall.pipeline.distill import distill_step  # type: ignore[import-untyped]
                distill_step()
                distilled = True
            except ImportError:
                skip_reason = "distill module not available"
                distilled = False

            mem_after = conn.execute(
                f"SELECT COUNT(*) as c FROM memories {where_all}", all_params
            ).fetchone()["c"]

            conn.execute(
                "INSERT INTO distill_history "
                "(agent_name, memory_count_before, memory_count_after, "
                "triggered_at, mode) VALUES (?, ?, ?, ?, ?)",
                (agent_name or "all", mem_before, mem_after, now_iso, mode),
            )
            conn.commit()

        return {
            "mode": mode,
            "distilled": distilled,
            "skip_reason": skip_reason or "",
        }
    finally:
        conn.close()


# ══════════════════════════════════════════════════════════════════════
# 4. 自适应总控
# ══════════════════════════════════════════════════════════════════════

def adaptive_step(agent_name: Optional[str] = None) -> Dict[str, Any]:
    """Run cleaner + indexer + distiller and return merged results.

    Args:
        agent_name: Optional agent filter forwarded to all sub-modules.

    Returns:
        ``{"cleaner": {...}, "indexer": {...}, "distiller": {...}}``
    """
    return {
        "cleaner": adaptive_clean(agent_name=agent_name),
        "indexer": adaptive_index(agent_name=agent_name),
        "distiller": adaptive_distill(agent_name=agent_name),
    }


# ══════════════════════════════════════════════════════════════════════
# 5. 自适应报告
# ══════════════════════════════════════════════════════════════════════

def adaptive_report() -> Dict[str, Any]:
    """Generate a human-readable status summary of the adaptive subsystem.

    Returns:
        dict with:
            - query_log_total
            - accel_table_count
            - distill_history_recent (last 5)
            - total_memories
            - recent_7d_memories
            - growth_rate_7d
            - mode_suggestion
    """
    conn = get_conn()
    try:
        # Ensure support tables exist (idempotent)
        conn.execute(
            "CREATE TABLE IF NOT EXISTS query_log ("
            "id INTEGER PRIMARY KEY, query_text TEXT, agent_name TEXT, timestamp TEXT)"
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS idx_meta ("
            "table_name TEXT PRIMARY KEY, created_at TEXT, last_used TEXT, "
            "hit_count INTEGER DEFAULT 0)"
        )

        query_log_total = conn.execute(
            "SELECT COUNT(*) as c FROM query_log"
        ).fetchone()["c"]
        accel_table_count = conn.execute(
            "SELECT COUNT(*) as c FROM idx_meta"
        ).fetchone()["c"]

        conn.execute(
            "CREATE TABLE IF NOT EXISTS distill_history ("
            "id INTEGER PRIMARY KEY, agent_name TEXT, "
            "memory_count_before INT, memory_count_after INT, "
            "triggered_at TEXT, mode TEXT)"
        )
        distill_rows = conn.execute(
            "SELECT * FROM distill_history ORDER BY triggered_at DESC LIMIT 5"
        ).fetchall()
        distill_recent = [
            {
                "id": r["id"],
                "agent_name": r["agent_name"],
                "memory_before": r["memory_count_before"],
                "memory_after": r["memory_count_after"],
                "triggered_at": r["triggered_at"],
                "mode": r["mode"],
            }
            for r in distill_rows
        ]

        total_memories = conn.execute(
            "SELECT COUNT(*) as c FROM memories"
        ).fetchone()["c"]

        now = datetime.now(timezone.utc)
        seven_days_ago = (now - timedelta(days=7)).isoformat()
        recent_7d = conn.execute(
            "SELECT COUNT(*) as c FROM memories WHERE created_at >= ?",
            (seven_days_ago,),
        ).fetchone()["c"]
        growth_7d = recent_7d / max(total_memories, 1)

        if total_memories > 10000:
            mode_suggestion = "compression"
        elif total_memories == 0:
            mode_suggestion = "idle"
        else:
            mode_suggestion = "standard"

        return {
            "query_log_total": query_log_total,
            "accel_table_count": accel_table_count,
            "distill_history_recent": distill_recent,
            "total_memories": total_memories,
            "recent_7d_memories": recent_7d,
            "growth_rate_7d": round(growth_7d, 4),
            "mode_suggestion": mode_suggestion,
        }
    finally:
        conn.close()
