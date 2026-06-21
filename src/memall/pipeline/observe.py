import logging

logger = logging.getLogger(__name__)

"""
Pipeline observation step (第二刀 第3点).

After pipeline runs, produces a structured "本轮观察" report capturing:
- Level distribution changes
- L6 reflection quality breakdown
- Aggregation stats
- Key anomalies detected
"""

import json
import hashlib
from datetime import datetime, timezone
from pathlib import Path
from memall.core.db import get_conn
from memall.pipeline.metrics import read_history


def observation_step() -> dict:
    """Capture a structured pipeline observation as a memory."""
    conn = get_conn()
    try:
        now = datetime.now(timezone.utc).isoformat()

        # 1. Level distribution snapshot
        level_rows = conn.execute(
            "SELECT level, COUNT(*) as cnt FROM memories GROUP BY level ORDER BY cnt DESC"
        ).fetchall()
        level_dist = {r["level"]: r["cnt"] for r in level_rows}

        # 2. L6 quality breakdown
        l6_total = conn.execute(
            "SELECT COUNT(*) FROM memories WHERE level = 'L6'"
        ).fetchone()[0]
        l6_high = 0
        if l6_total > 0:
            l6_rows = conn.execute(
                "SELECT metadata FROM memories WHERE level = 'L6'"
            ).fetchall()
            for r in l6_rows:
                try:
                    meta = json.loads(r["metadata"]) if r["metadata"] else {}
                    if meta.get("quality") == "high":
                        l6_high += 1
                except (json.JSONDecodeError, TypeError):
                    logger.warning("observe.py: silent error", exc_info=True)

        # 3. Recent L6 count (last 24h)
        l6_recent = 0
        recent_rows = conn.execute(
            "SELECT metadata FROM memories WHERE level = 'L6'"
        ).fetchall()
        for r in recent_rows:
            try:
                meta = json.loads(r["metadata"]) if r["metadata"] else {}
                if meta.get("l6_source") in ("reflect_step", "aggregate", "observation"):
                    l6_recent += 1
            except (json.JSONDecodeError, TypeError):
                logger.warning("observe.py: silent error", exc_info=True)

        # 4. Aggregation info
        agg_count = 0
        if l6_total > 0:
            for r in conn.execute(
                "SELECT metadata FROM memories WHERE level = 'L6'"
            ).fetchall():
                try:
                    meta = json.loads(r["metadata"]) if r["metadata"] else {}
                    if meta.get("l6_source") == "aggregate":
                        agg_count += 1
                except (json.JSONDecodeError, TypeError):
                    logger.warning("observe.py: silent error", exc_info=True)

        # 5. Anomalies
        l9_ratio = level_dist.get("L9", 0) / max(1, sum(level_dist.values())) * 100
        warnings = []
        if l9_ratio > 40:
            warnings.append(f"L9 占比 {l9_ratio:.0f}%，偏高")
        if l6_total > 0 and l6_high < l6_total * 0.2:
            warnings.append(f"L6 高质量反思占比 {l6_high}/{l6_total}，偏低")
        if level_dist.get("P0", 0) > 200:
            warnings.append(f"P0 记忆 {level_dist.get('P0', 0)} 条，接近阈值")

        # 6. Build structured report
        report = {
            "timestamp": now,
            "level_distribution": level_dist,
            "l6_reflections": {
                "total": l6_total,
                "high_quality": l6_high,
                "recent_24h": l6_recent,
                "aggregated_groups": agg_count,
            },
            "warnings": warnings,
        }

        # 7. Capture as memory
        content = json.dumps(report, ensure_ascii=False, indent=2)
        ch = hashlib.sha256(content.encode()).hexdigest()
        existing = conn.execute(
            "SELECT id FROM memories WHERE content_hash = ?",
            (ch,),
        ).fetchone()
        if not existing:
            conn.execute(
                "INSERT INTO memories (content, content_hash, level, agent_name, category, summary, occurred_at, created_at, updated_at, metadata) "
                "VALUES (?, ?, 'L6', 'system', 'reflection', ?, ?, ?, ?, ?)",
                (
                    content, ch,
                    "管线本轮观察 " + now[:10],
                    now, now, now,
                    json.dumps({"l6_source": "observation"}),
                ),
            )
            report["captured_as_memory"] = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

        conn.commit()

        # 8. Self-check anomaly detection (第三刀 第4点)
        alerts = _self_check(level_dist, l6_total)
        report["alerts"] = alerts
        if alerts:
            warnings.extend(f"[{a['level']}] {a['message']}" for a in alerts)
            report["warnings"] = warnings

        # 9. Growth log (第二刀 第13点)
        growth = _update_growth_log(conn, report)
        report["growth_log"] = growth

        # 10. Weekly/monthly summaries (长期 第7点)
        rhythm = _update_weekly_monthly(conn)
        report["rhythm"] = rhythm

        conn.commit()
        return report
    except Exception as e:
        return {"error": str(e), "captured_as_memory": None}
    finally:
        conn.close()


_METRICS_BASELINE_COUNT = 3


def _self_check(level_dist: dict, l6_total: int) -> list:
    """分层异常自检: INFO / WARN / ALERT."""
    history = read_history(_METRICS_BASELINE_COUNT)
    alerts = []

    if not history or len(history) < 2:
        return alerts  # not enough data to compare

    latest = history[-1]
    prev = history[0]

    # 1. 遗忘率突增 (total memory count drop)
    curr_total = sum(level_dist.values())
    prev_total = prev.get("total_memories", curr_total)
    if prev_total > 100 and curr_total < prev_total * 0.7:
        alerts.append({
            "level": "ALERT",
            "type": "forget_spike",
            "message": f"记忆总量从 {prev_total} 降至 {curr_total}（降幅 {((prev_total - curr_total) / prev_total * 100):.0f}%），可能遗忘率异常",
        })
    elif prev_total > 100 and curr_total < prev_total * 0.85:
        alerts.append({
            "level": "WARN",
            "type": "forget_spike",
            "message": f"记忆总量从 {prev_total} 降至 {curr_total}，请关注遗忘节奏",
        })

    # 2. 领域萎缩 (category count drop)
    curr_cats = len([k for k in level_dist if k not in ("P0", "P1", "P2", "P3", "deleted")])
    prev_cats = prev.get("categories", curr_cats)
    if prev_cats > 5 and curr_cats < prev_cats * 0.6:
        alerts.append({
            "level": "WARN",
            "type": "domain_shrink",
            "message": f"活跃领域从 {prev_cats} 缩至 {curr_cats}，领域多样性下降",
        })

    # 3. 转化率异常 (L6 ratio vs total)
    if l6_total > 0:
        l6_ratio = l6_total / max(1, curr_total) * 100
        if l6_ratio < 1:
            alerts.append({
                "level": "INFO",
                "type": "low_reflection",
                "message": f"L6 反思占比 {l6_ratio:.1f}%，低于 1%，反思系统可能未激活",
            })
        elif l6_ratio > 30:
            alerts.append({
                "level": "INFO",
                "type": "high_reflection",
                "message": f"L6 反思占比 {l6_ratio:.1f}%，超过 30%，反思可能过量",
            })

    # 4. 连接密度异常
    curr_density = latest.get("connection_density", 0)
    prev_density = prev.get("connection_density", 0)
    if prev_density > 0 and curr_density < prev_density * 0.5:
        alerts.append({
            "level": "WARN",
            "type": "disconnection",
            "message": f"连接密度从 {prev_density} 降至 {curr_density}，记忆图可能在劣化",
        })

    return alerts


def _update_weekly_monthly(conn) -> dict:
    """Produce weekly and monthly L6 reflection summaries (长期 第7点)."""
    import hashlib
    from collections import defaultdict

    now = datetime.now(timezone.utc).isoformat()
    today = now[:10]
    week_start = today[:7]  # YYYY-MM
    month_key = today[:7]   # YYYY-MM

    # Collect L6 reflections by agent
    rows = conn.execute(
        "SELECT id, agent_name, summary, created_at, category FROM memories WHERE level = 'L6' ORDER BY created_at"
    ).fetchall()

    by_agent = defaultdict(list)
    for r in rows:
        ag = r["agent_name"] or "unknown"
        by_agent[ag].append(r)

    results = {"weekly": 0, "monthly": 0}

    for agent, mems in by_agent.items():
        if len(mems) < 3:
            continue

        # Weekly: group by ISO week (YYYY-MM-WW)
        weeks = defaultdict(list)
        months = defaultdict(list)
        for m in mems:
            dt = (m["created_at"] or "")[:10]
            weeks[dt[:7]].append(m)  # use YYYY-MM as weekly bucket
            months[dt[:7]].append(m)

        # Check if weekly summary already exists for this period
        for wk, wk_mems in weeks.items():
            if len(wk_mems) < 3:
                continue
            existing = conn.execute(
                "SELECT id FROM memories WHERE level = 'L6' AND agent_name = ? AND summary = ?",
                (agent, f"📅 周反思 {wk}"),
            ).fetchone()
            if existing:
                continue
            summaries = [m["summary"] for m in wk_mems[-20:] if m["summary"]]
            content = f"[L6 周反思] {agent} {wk} 共 {len(wk_mems)} 条反思：\n" + "\n".join(f"- {s[:200]}" for s in summaries)
            ch = hashlib.sha256(content.encode()).hexdigest()
            cur = conn.execute("SELECT id FROM memories WHERE content_hash = ?", (ch,)).fetchone()
            if cur:
                continue
            conn.execute(
                "INSERT INTO memories (content, content_hash, level, agent_name, category, summary, occurred_at, created_at, updated_at, metadata) "
                "VALUES (?, ?, 'L6', ?, 'reflection', ?, ?, ?, ?, ?)",
                (content, ch, agent, f"📅 周反思 {wk}", now, now, now,
                 json.dumps({"l6_source": "weekly_aggregate", "source_count": len(wk_mems)})),
            )
            results["weekly"] += 1

        # Monthly: same logic
        for mk, mo_mems in months.items():
            if len(mo_mems) < 5:
                continue
            existing = conn.execute(
                "SELECT id FROM memories WHERE level = 'L6' AND agent_name = ? AND summary = ?",
                (agent, f"📅 月反思 {mk}"),
            ).fetchone()
            if existing:
                continue
            summaries = [m["summary"] for m in mo_mems[-50:] if m["summary"]]
            content = f"[L6 月反思] {agent} {mk} 共 {len(mo_mems)} 条反思：\n" + "\n".join(f"- {s[:200]}" for s in summaries)
            ch = hashlib.sha256(content.encode()).hexdigest()
            if conn.execute("SELECT id FROM memories WHERE content_hash = ?", (ch,)).fetchone():
                continue
            conn.execute(
                "INSERT INTO memories (content, content_hash, level, agent_name, category, summary, occurred_at, created_at, updated_at, metadata) "
                "VALUES (?, ?, 'L6', ?, 'reflection', ?, ?, ?, ?, ?)",
                (content, ch, agent, f"📅 月反思 {mk}", now, now, now,
                 json.dumps({"l6_source": "monthly_aggregate", "source_count": len(mo_mems)})),
            )
            results["monthly"] += 1

    conn.commit()
    return results


def _update_growth_log(conn, report: dict) -> dict:
    """Maintain a running reflection timeline (反思时间线) as a diary."""
    import hashlib

    now = datetime.now(timezone.utc).isoformat()
    today = now[:10]

    # Find existing timeline
    row = conn.execute(
        "SELECT id, content FROM memories WHERE level = 'L6' AND summary = '📅 反思时间线' ORDER BY id DESC LIMIT 1"
    ).fetchone()

    # Build today's entry
    l6 = report.get("l6_reflections", {})
    today_entry = f"## {today}\n"
    today_entry += f"- 新 L6 反思: {l6.get('recent_24h', 0)} 条\n"
    today_entry += f"- 高质量: {l6.get('high_quality', 0)} 条\n"
    today_entry += f"- 聚合组: {l6.get('aggregated_groups', 0)} 组\n"
    warnings = report.get("warnings", [])
    if warnings:
        today_entry += "- 告警:\n" + "\n".join(f"  - {w}" for w in warnings)

    if row:
        # Append to existing timeline (newest first)
        existing_content = row["content"]
        timeline_id = row["id"]

        # Check if today's entry already exists
        if f"## {today}" in existing_content:
            return {"updated": False, "reason": "today already logged", "id": timeline_id}

        new_content = today_entry + "\n\n---\n\n" + existing_content[:50000]
        ch = hashlib.sha256(new_content.encode()).hexdigest()
        conn.execute(
            "UPDATE memories SET content = ?, content_hash = ?, updated_at = ? WHERE id = ?",
            (new_content, ch, now, timeline_id),
        )
        return {"updated": True, "appended": True, "id": timeline_id}
    else:
        # Create new timeline
        content = today_entry + "\n\n*反思时间线始于 " + today + "*"
        ch = hashlib.sha256(content.encode()).hexdigest()
        conn.execute(
            "INSERT INTO memories (content, content_hash, level, agent_name, category, summary, occurred_at, created_at, updated_at, metadata) "
            "VALUES (?, ?, 'L6', 'system', 'reflection', '📅 反思时间线', ?, ?, ?, ?)",
            (content, ch, now, now, now, json.dumps({"l6_source": "growth_log"})),
        )
        new_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        return {"updated": True, "created": True, "id": new_id}

def reflection_dashboard(days: int = 30) -> dict:
    """Generate dashboard data for L6 reflections (长期 第14点).

    Returns structured data for frontend visualization:
    - daily_density: count of L6 reflections per day over the period
    - topic_distribution: category breakdown of L6 reflections
    - correction_chain: contradicts/refines edge counts
    - quality_breakdown: high/medium/low counts
    """
    import hashlib
    from collections import defaultdict
    from datetime import datetime, timezone, timedelta

    conn = get_conn()
    try:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

        # Daily density
        rows = conn.execute(
            "SELECT DATE(created_at) as day, COUNT(*) as cnt FROM memories WHERE level = 'L6' AND created_at >= ? GROUP BY day ORDER BY day",
            (cutoff,),
        ).fetchall()
        daily_density = {r["day"]: r["cnt"] for r in rows}

        # Topic distribution
        topic_rows = conn.execute(
            "SELECT category, COUNT(*) as cnt FROM memories WHERE level = 'L6' AND category != '' GROUP BY category ORDER BY cnt DESC"
        ).fetchall()
        topic_dist = {r["category"]: r["cnt"] for r in topic_rows}

        # Quality breakdown
        l6_rows = conn.execute(
            "SELECT metadata FROM memories WHERE level = 'L6'"
        ).fetchall()
        quality = {"high": 0, "medium": 0, "low": 0}
        for r in l6_rows:
            try:
                meta = json.loads(r["metadata"]) if r["metadata"] else {}
                q = meta.get("quality", "medium")
                if isinstance(q, dict):
                    q = q.get("value", "medium")
                if q in quality:
                    quality[q] += 1
            except (json.JSONDecodeError, TypeError):
                logger.warning("observe.py: quality parse error", exc_info=True)

        # Correction chain
        chain_rows = conn.execute(
            "SELECT relation_type, COUNT(*) as cnt FROM edges WHERE relation_type IN ('contradicts', 'refines') GROUP BY relation_type"
        ).fetchall()
        chain = {r["relation_type"]: r["cnt"] for r in chain_rows}

        # Agent breakdown
        agent_rows = conn.execute(
            "SELECT agent_name, COUNT(*) as cnt FROM memories WHERE level = 'L6' AND agent_name != '' GROUP BY agent_name ORDER BY cnt DESC"
        ).fetchall()
        agent_breakdown = {r["agent_name"]: r["cnt"] for r in agent_rows}

        return {
            "period_days": days,
            "daily_density": daily_density,
            "topic_distribution": topic_dist,
            "quality_breakdown": quality,
            "correction_chain": chain,
            "agent_breakdown": agent_breakdown,
            "total_l6": sum(quality.values()),
        }
    finally:
        conn.close()
