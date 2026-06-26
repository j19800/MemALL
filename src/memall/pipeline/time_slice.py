"""
Pipeline step: time_slice_step — pre-aggregated time window statistics.

Computes per-agent, per-day/week/month aggregations from memories
and stores them in the time_slices table. Also computes temporal_weight
for each processed memory.

Backfill mode: processes ALL memories on first run.
Incremental mode: only processes memories since last_processed_id.
"""

import json
import logging
import math
import sqlite3
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Optional

from memall.core.db import get_conn

logger = logging.getLogger(__name__)

# ── Config ──────────────────────────────────────────────────────
_LAMBDA = 0.01          # decay factor (half-life ~69 days)
_BATCH_SIZE = 5000       # memories per batch during backfill
_EPOCH_BOOST = 1.3      # weight multiplier if memory falls in active epoch

# Certain/uncertain/decision keywords (mirrors persona.py)
_CERTAIN_KEYWORDS = ["必须", "一定", "确定", "结论是", "最终方案", "就这样"]
_UNCERTAIN_KEYWORDS = ["也许", "可能", "考虑", "暂时", "先试试", "不确定", "待定"]
_DECISION_KEYWORDS = ["决定", "选", "采用", "方案", "结论", "定为", "用", "选型"]


# ── Helpers ─────────────────────────────────────────────────────

def _get_slice_key(dt: datetime, granularity: str) -> str:
    """Return ISO key: '2026-06-14', '2026-W24', or '2026-06'."""
    if granularity == "day":
        return dt.strftime("%Y-%m-%d")
    elif granularity == "week":
        return dt.strftime("%Y-W%V")
    else:  # month
        return dt.strftime("%Y-%m")


def _get_window_bounds(slice_key: str, granularity: str) -> tuple[str, str]:
    """Return (window_start, window_end) ISO timestamps for a slice_key."""
    if granularity == "day":
        d = datetime.strptime(slice_key, "%Y-%m-%d")
        end = d + timedelta(days=1)
        return (d.isoformat(), end.isoformat())
    elif granularity == "week":
        # Parse ISO week
        year = int(slice_key[:4])
        week = int(slice_key[6:])
        start = datetime.strptime(f"{year}-W{week:02d}-1", "%Y-W%W-%w")
        return (start.isoformat(), (start + timedelta(days=7)).isoformat())
    else:  # month
        year = int(slice_key[:4])
        month = int(slice_key[5:7])
        start = datetime(year, month, 1)
        if month == 12:
            end = datetime(year + 1, 1, 1)
        else:
            end = datetime(year, month + 1, 1)
        return (start.isoformat(), end.isoformat())


def _parse_ts(ts_str: str) -> Optional[datetime]:
    """Parse ISO timestamp string to timezone-aware datetime."""
    if not ts_str:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(ts_str[:26], fmt)
            return dt.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    try:
        dt = datetime.fromisoformat(ts_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def _count_keywords(content: str, keywords: list[str]) -> int:
    """Count occurrences of any keyword in content."""
    if not content:
        return 0
    return sum(1 for kw in keywords if kw in content)


def _compute_aggregates(rows: list[sqlite3.Row]) -> dict:
    """Compute aggregate fields from a list of memory rows."""
    total = len(rows)
    if total == 0:
        return {
            "memory_count": 0,
            "category_distribution": "{}",
            "level_distribution": "{}",
            "avg_confidence": 0.0,
            "decision_count": 0,
            "certain_count": 0,
            "uncertain_count": 0,
            "domain_set": "[]",
            "top_subjects": "[]",
        }

    cat_counter: Counter = Counter()
    lvl_counter: Counter = Counter()
    subj_counter: Counter = Counter()
    conf_sum = 0.0
    conf_count = 0
    decisions = 0
    certain = 0
    uncertain = 0

    for r in rows:
        cat = r["category"] or "general"
        cat_counter[cat] += 1
        lvl = r["level"] or "P2"
        lvl_counter[lvl] += 1
        subj = r["subject"] or ""
        if subj:
            subj_counter[subj] += 1
        conf = r["confidence"]
        if conf:
            conf_sum += conf
            conf_count += 1
        content = r["content"] or ""
        if _count_keywords(content, _DECISION_KEYWORDS) > 0:
            decisions += 1
        certain += _count_keywords(content, _CERTAIN_KEYWORDS)
        uncertain += _count_keywords(content, _UNCERTAIN_KEYWORDS)

    top_subjs = [s for s, _ in subj_counter.most_common(5)]

    return {
        "memory_count": total,
        "category_distribution": json.dumps(dict(cat_counter), ensure_ascii=False),
        "level_distribution": json.dumps(dict(lvl_counter), ensure_ascii=False),
        "avg_confidence": round(conf_sum / conf_count, 4) if conf_count > 0 else 0.0,
        "decision_count": decisions,
        "certain_count": certain,
        "uncertain_count": uncertain,
        "domain_set": json.dumps(list(cat_counter.keys()), ensure_ascii=False),
        "top_subjects": json.dumps(top_subjs, ensure_ascii=False),
    }


def _upsert_slice(conn, agent_name: str, granularity: str, slice_key: str,
                  agg: dict, now: str) -> None:
    """Upsert a single time_slice row."""
    ws, we = _get_window_bounds(slice_key, granularity)
    conn.execute("""
        INSERT INTO time_slices
            (agent_name, granularity, slice_key, window_start, window_end,
             memory_count, category_distribution, level_distribution,
             avg_confidence, decision_count, certain_count, uncertain_count,
             domain_set, top_subjects, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(agent_name, granularity, slice_key) DO UPDATE SET
            memory_count = excluded.memory_count,
            category_distribution = excluded.category_distribution,
            level_distribution = excluded.level_distribution,
            avg_confidence = excluded.avg_confidence,
            decision_count = excluded.decision_count,
            certain_count = excluded.certain_count,
            uncertain_count = excluded.uncertain_count,
            domain_set = excluded.domain_set,
            top_subjects = excluded.top_subjects,
            updated_at = excluded.updated_at
    """, (
        agent_name, granularity, slice_key, ws, we,
        agg["memory_count"], agg["category_distribution"], agg["level_distribution"],
        agg["avg_confidence"], agg["decision_count"], agg["certain_count"], agg["uncertain_count"],
        agg["domain_set"], agg["top_subjects"],
        now, now,
    ))


def _derive_slices(conn, day_keys: set[str], agent_name: str, now: str) -> None:
    """Derive week and month slices by aggregating day slices."""
    # Map day keys to their parent week/month keys
    week_map: dict[str, list[str]] = {}
    month_map: dict[str, list[str]] = {}

    for dk in day_keys:
        try:
            dt = datetime.strptime(dk, "%Y-%m-%d")
        except ValueError:
            continue
        week_key = dt.strftime("%Y-W%V")
        month_key = dt.strftime("%Y-%m")
        week_map.setdefault(week_key, []).append(dk)
        month_map.setdefault(month_key, []).append(dk)

    for granularity, parent_map in [("week", week_map), ("month", month_map)]:
        for parent_key, child_keys in parent_map.items():
            _derive_single_slice(conn, agent_name, granularity, parent_key, child_keys, now)


def _derive_single_slice(conn, agent_name: str, granularity: str,
                         parent_key: str, child_keys: list[str], now: str) -> None:
    """Aggregate one parent slice from its child day slices."""
    placeholders = ",".join("?" for _ in child_keys)
    params = [agent_name] + child_keys
    rows = conn.execute(
        "SELECT memory_count, category_distribution, level_distribution, "
        "avg_confidence, decision_count, certain_count, uncertain_count, "
        "domain_set, top_subjects FROM time_slices "
        "WHERE agent_name = ? AND granularity = 'day' AND slice_key IN ({})".format(placeholders),
        params,
    ).fetchall()

    if not rows:
        return

    total_count = sum(r["memory_count"] for r in rows)
    merged_cats: Counter = Counter()
    merged_lvls: Counter = Counter()
    all_domains: set[str] = set()
    all_subjects: list[str] = []
    conf_sum = 0.0
    total_decisions = 0
    total_certain = 0
    total_uncertain = 0

    for r in rows:
        total_decisions += r["decision_count"]
        total_certain += r["certain_count"]
        total_uncertain += r["uncertain_count"]
        conf_sum += r["avg_confidence"]
        try:
            merged_cats.update(json.loads(r["category_distribution"]))
        except (json.JSONDecodeError, TypeError):
            logger.warning("time_slice.py: silent error", exc_info=True)
        try:
            merged_lvls.update(json.loads(r["level_distribution"]))
        except (json.JSONDecodeError, TypeError):
            logger.warning("time_slice.py: silent error", exc_info=True)
        try:
            all_domains.update(json.loads(r["domain_set"]))
        except (json.JSONDecodeError, TypeError):
            logger.warning("time_slice.py: silent error", exc_info=True)
        try:
            all_subjects.extend(json.loads(r["top_subjects"]))
        except (json.JSONDecodeError, TypeError):
            logger.warning("time_slice.py: silent error", exc_info=True)

    seen: set[str] = set()
    deduped_subjs = []
    for s in all_subjects:
        if s not in seen:
            seen.add(s)
            deduped_subjs.append(s)

    derived_agg = {
        "memory_count": total_count,
        "category_distribution": json.dumps(dict(merged_cats), ensure_ascii=False),
        "level_distribution": json.dumps(dict(merged_lvls), ensure_ascii=False),
        "avg_confidence": round(conf_sum / len(rows), 4) if rows else 0.0,
        "decision_count": total_decisions,
        "certain_count": total_certain,
        "uncertain_count": total_uncertain,
        "domain_set": json.dumps(list(all_domains), ensure_ascii=False),
        "top_subjects": json.dumps(deduped_subjs[:5], ensure_ascii=False),
    }
    _upsert_slice(conn, agent_name, granularity, parent_key, derived_agg, now)


def _get_epoch_map(conn, memory_ids: list[int]) -> dict[int, bool]:
    """Return {memory_id: is_in_active_epoch} for given memory IDs.

    A memory is in an active epoch if its occurred_at falls within
    any epoch's [started_at, ended_at) range for its agent.
    """
    if not memory_ids:
        return {}

    # Get memories with their agent_name and occurred_at
    rows = conn.execute(
        "SELECT id, agent_name, occurred_at FROM memories WHERE id IN ({})".format(
            ",".join("?" for _ in memory_ids)
        ),
        memory_ids,
    ).fetchall()

    # Group by agent_name
    agent_memories: dict[str, list[tuple[int, str]]] = {}
    for r in rows:
        agent_memories.setdefault(r["agent_name"], []).append((r["id"], r["occurred_at"]))

    result: dict[int, bool] = {}
    for agent_name, mems in agent_memories.items():
        # Fetch active epochs for this agent
        epoch_rows = conn.execute(
            "SELECT started_at, ended_at FROM epochs WHERE agent_name = ?",
            (agent_name,),
        ).fetchall()

        for mid, occurred_at in mems:
            in_epoch = False
            if epoch_rows and occurred_at:
                for e in epoch_rows:
                    start = e["started_at"]
                    end = e["ended_at"] or "9999-12-31T23:59:59"
                    if start <= occurred_at < end:
                        in_epoch = True
                        break
            result[mid] = in_epoch

    return result


def _update_temporal_weights(conn, memory_ids: list[int]) -> int:
    """Update temporal_weight in metadata for the given memory IDs."""
    if not memory_ids:
        return 0

    now = datetime.now(timezone.utc)
    epoch_map = _get_epoch_map(conn, memory_ids)
    updated = 0

    for mid in memory_ids:
        row = conn.execute(
            "SELECT occurred_at, access_count, metadata FROM memories WHERE id = ?",
            (mid,),
        ).fetchone()
        if not row:
            continue

        occurred = _parse_ts(row["occurred_at"])
        if not occurred:
            continue

        age_days = max(0.0, (now - occurred).total_seconds() / 86400.0)
        base = 1.0
        decay = math.exp(-_LAMBDA * age_days)
        epoch_boost = _EPOCH_BOOST if epoch_map.get(mid, False) else 1.0
        recency_bump = 1.0 + min(0.1, (row["access_count"] or 0) / 1000.0)

        temporal_weight = round(base * decay * epoch_boost * recency_bump, 4)

        # Update metadata
        try:
            meta = json.loads(row["metadata"]) if row["metadata"] else {}
        except (json.JSONDecodeError, TypeError):
            meta = {}
        meta["temporal_weight"] = temporal_weight
        meta["temporal_weight_meta"] = {
            "age_days": round(age_days, 1),
            "decay_factor": round(decay, 4),
            "epoch_boost": epoch_boost,
            "recency_bump": recency_bump,
            "updated_at": now.isoformat(),
        }
        conn.execute(
            "UPDATE memories SET metadata = ? WHERE id = ?",
            (json.dumps(meta, ensure_ascii=False), mid),
        )
        updated += 1

    return updated


# ── Main step ───────────────────────────────────────────────────

def time_slice_step() -> dict:
    """Pre-aggregate memories into time_slices and update temporal_weights.

    Backfill mode (first run): processes ALL memories in batches.
    Incremental mode (subsequent runs): processes only new/changed memories.

    Returns:
        dict with counts of slices upserted and temporal weights updated.
    """
    conn = get_conn()
    try:
        now_str = datetime.now(timezone.utc).isoformat()

        # Read pipeline_state
        state = conn.execute(
            "SELECT last_processed_id FROM pipeline_state WHERE step_name = 'time_slice'"
        ).fetchone()

        if state and state["last_processed_id"]:
            # Incremental mode
            last_id = state["last_processed_id"]
            mode = "incremental"
        else:
            # Backfill mode
            last_id = 0
            mode = "backfill"

        # Process memories in ascending ID order
        grand_total_day_keys: set[str] = set()
        grand_slice_count = 0
        grand_weight_count = 0

        while True:
            rows = conn.execute(
                "SELECT id, agent_name, category, level, subject, content, "
                "confidence, occurred_at FROM memories WHERE id > ? "
                "ORDER BY id ASC LIMIT ?",
                (last_id, _BATCH_SIZE),
            ).fetchall()
            if not rows:
                break

            batch_ids = [r["id"] for r in rows]
            last_id = batch_ids[-1]

            # Group by (agent_name, day_key)
            groups: dict[tuple[str, str], list[sqlite3.Row]] = {}
            for r in rows:
                agent = r["agent_name"] or "system"
                occurred = r["occurred_at"]
                dt = _parse_ts(occurred)
                if dt is None:
                    continue
                day_key = _get_slice_key(dt, "day")
                key = (agent, day_key)
                groups.setdefault(key, []).append(r)
                grand_total_day_keys.add(day_key)

            # Compute and upsert day slices
            for (agent, day_key), mems in groups.items():
                agg = _compute_aggregates(mems)
                _upsert_slice(conn, agent, "day", day_key, agg, now_str)
                grand_slice_count += 1

            # Update temporal weights for this batch
            grand_weight_count += _update_temporal_weights(conn, batch_ids)

            conn.commit()
            logger.debug(
                "time_slice_step %s: batch up to id=%d, %d groups, %d weights",
                mode, last_id, len(groups), len(batch_ids),
            )

            if len(rows) < _BATCH_SIZE:
                break  # Last batch

        # Derive week/month slices from day slices
        if grand_total_day_keys and mode == "backfill":
            # Get unique agent names from day slices
            agents = conn.execute(
                "SELECT DISTINCT agent_name FROM time_slices WHERE granularity = 'day'"
            ).fetchall()
            for row in agents:
                agent_name = row["agent_name"]
                _derive_slices(conn, grand_total_day_keys, agent_name, now_str)
            conn.commit()

        # Save pipeline_state
        conn.execute("""
            INSERT INTO pipeline_state (step_name, last_run_at, last_processed_id, metadata)
            VALUES ('time_slice', ?, ?, '{}')
            ON CONFLICT(step_name) DO UPDATE SET
                last_run_at = excluded.last_run_at,
                last_processed_id = excluded.last_processed_id
        """, (now_str, last_id))
        conn.commit()

        return {
            "mode": mode,
            "last_processed_id": last_id,
            "day_slices_upserted": grand_slice_count,
            "temporal_weights_updated": grand_weight_count,
            "batch_count": max(1, (last_id // _BATCH_SIZE) + 1),
        }
    finally:
        conn.close()