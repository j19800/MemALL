import logging
import json
from datetime import datetime, timezone, timedelta
from collections import defaultdict
from memall.core.db import get_conn
logger = logging.getLogger(__name__)


NARRATIVE_TEMPLATES = {
    "weekly": {"prefix": "过去一周", "days": 7},
    "monthly": {"prefix": "过去一个月", "days": 30},
    "phase": {"prefix": "本阶段", "phase": True},
}

TRANSITIONS = ["最初", "随后", "接下来", "与此同时", "后来", "最后"]


def _extract_events(rows: list) -> list:
    return [{
        "id": r["id"], "content": r["content"][:200],
        "category": r["category"], "level": r["level"],
        "occurred_at": r["occurred_at"], "agent_name": r["agent_name"],
    } for r in rows]


def _build_narrative(agent: str, events: list, span_end: datetime, narrative_type: str) -> str:
    cfg = NARRATIVE_TEMPLATES.get(narrative_type, NARRATIVE_TEMPLATES["weekly"])

    if not events:
        return f"{cfg['prefix']}，{agent} 没有记录任何记忆活动。"

    total = len(events)
    cats = defaultdict(list)
    for e in events:
        cats[e.get("category") or "未分类"].append(e)

    times = [e["occurred_at"] for e in events if e.get("occurred_at")]
    first_day = times[0][:10] if times else "?"
    last_day = times[-1][:10] if times else "?"

    top_cats = sorted(cats.items(), key=lambda x: -len(x[1]))
    main_cat = top_cats[0][0] if top_cats else "未分类"
    main_pct = round(len(top_cats[0][1]) / total * 100) if top_cats else 0

    levels = defaultdict(int)
    for e in events:
        levels[e.get("level", "P2")] += 1
    p1_count = levels.get("P1", 0)
    p0_count = levels.get("P0", 0)

    paras = []
    paras.append(f"从 {first_day} 到 {last_day}，{agent} 共产生了 {total} 条记忆，主要集中在 {main_cat}（{main_pct}%）。")

    if len(top_cats) > 1:
        others = [f"{c[0]}（{round(len(c[1])/total*100)}%）" for c in top_cats[1:4]]
        paras.append(f"其他活跃领域包括：{'、'.join(others)}。")

    if p0_count > 0 or p1_count > 0:
        paras.append(f"其中有 P0 优先级 {p0_count} 条、P1 优先级 {p1_count} 条，表明这段时间内有明确的重要事项。")

    details = []
    for ci, (cat, mems) in enumerate(top_cats[:3]):
        snippets = [m["content"][:100] for m in mems[:3] if m["content"]]
        if snippets:
            t = TRANSITIONS[min(ci, len(TRANSITIONS) - 1)]
            points = "；".join(snippets)
            details.append(f"{t}，在 {cat} 方面：{points}")
    if details:
        paras.append(" ".join(details))

    return "\n\n".join(paras)


def _get_span_for_phase(conn, agent: str, now):
    row = conn.execute(
        "SELECT MIN(occurred_at) as first_at, MAX(occurred_at) as last_at FROM memories WHERE LOWER(agent_name) = LOWER(?) AND occurred_at != ''",
        (agent,),
    ).fetchone()
    if row and row["first_at"] and row["last_at"]:
        try:
            first_s = row["first_at"].replace("Z", "+00:00")
            last_s = row["last_at"].replace("Z", "+00:00")
            first = datetime.fromisoformat(first_s)
            last = datetime.fromisoformat(last_s)
            if first.tzinfo is None:
                first = first.replace(tzinfo=timezone.utc)
            if last.tzinfo is None:
                last = last.replace(tzinfo=timezone.utc)
            return first, last
        except (ValueError, TypeError):
            logger.warning("narrative.py: silent error", exc_info=True)
    return now - timedelta(days=30), now


def narrative_step() -> dict:
    conn = get_conn()
    try:
        now = datetime.now(timezone.utc)
        results = {}

        agents = conn.execute(
            "SELECT DISTINCT LOWER(agent_name) as aname FROM memories WHERE agent_name != '' AND agent_name IS NOT NULL"
        ).fetchall()
        seen_agents = set()

        for row in agents:
            agent = row["aname"]
            if agent in seen_agents:
                continue
            seen_agents.add(agent)

            for ntype, cfg in NARRATIVE_TEMPLATES.items():
                if cfg.get("phase"):
                    span_start, span_end = _get_span_for_phase(conn, agent, now)
                else:
                    span_start = now - timedelta(days=cfg["days"])
                    span_end = now

                existing = conn.execute(
                    "SELECT id FROM narratives WHERE agent_name = ? AND narrative_type = ? AND span_start >= ?",
                    (agent, ntype, span_start.isoformat()),
                ).fetchone()
                if existing:
                    # Update existing narrative with fresh data
                    rows = conn.execute(
                        "SELECT id, content, category, level, occurred_at, agent_name FROM memories WHERE LOWER(agent_name) = LOWER(?) AND occurred_at >= ? ORDER BY occurred_at",
                        (agent, span_start.isoformat()),
                    ).fetchall()
                    events = _extract_events(rows)
                    ntext = _build_narrative(agent, events, now, ntype)
                    summary = ntext.split("\n")[0] if ntext else ""
                    conn.execute(
                        "UPDATE narratives SET narrative_text=?, events=?, summary=?, memory_count=?, generated_at=? WHERE id=?",
                        (ntext, json.dumps(events, ensure_ascii=False), summary, len(events), now.isoformat(), existing["id"]),
                    )
                    key = f"{agent}/{ntype}"
                    results[key] = {"events": len(events), "summary": summary, "updated": True}
                    continue

                if cfg.get("phase"):
                    rows = conn.execute(
                        "SELECT id, content, category, level, occurred_at, agent_name FROM memories WHERE LOWER(agent_name) = LOWER(?) AND occurred_at >= ? ORDER BY occurred_at",
                        (agent, span_start.isoformat()),
                    ).fetchall()
                else:
                    rows = conn.execute(
                        "SELECT id, content, category, level, occurred_at, agent_name FROM memories WHERE LOWER(agent_name) = LOWER(?) AND occurred_at >= ? ORDER BY occurred_at",
                        (agent, span_start.isoformat()),
                    ).fetchall()

                events = _extract_events(rows)
                ntext = _build_narrative(agent, events, now, ntype)
                summary = ntext.split("\n")[0] if ntext else ""

                conn.execute(
                    "INSERT INTO narratives (agent_name, narrative_type, span_start, span_end, narrative_text, events, summary, generated_at, memory_count) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (agent, ntype, span_start.isoformat(), span_end.isoformat(), ntext,
                     json.dumps(events, ensure_ascii=False), summary, now.isoformat(), len(events)),
                )
                conn.commit()

                key = f"{agent}/{ntype}"
                results[key] = {"events": len(events), "summary": summary}

        return {"narratives_created": len(results), "narratives": results}
    finally:
        conn.close()


def generate_agent_narrative(agent_name: str, span_days: int = 7, narrative_type: str = "weekly") -> dict:
    conn = get_conn()
    try:
        now = datetime.now(timezone.utc)

        if narrative_type == "phase":
            span_start, span_end = _get_span_for_phase(conn, agent_name, now)
            span_days = (span_end - span_start).days if span_end > span_start else 1
            rows = conn.execute(
                "SELECT id, content, category, level, occurred_at, agent_name FROM memories WHERE LOWER(agent_name) = LOWER(?) AND occurred_at >= ? ORDER BY occurred_at",
                (agent_name, span_start.isoformat()),
            ).fetchall()
        else:
            span_start = now - timedelta(days=span_days)
            span_end = now
            rows = conn.execute(
                "SELECT id, content, category, level, occurred_at, agent_name FROM memories WHERE LOWER(agent_name) = LOWER(?) AND occurred_at >= ? ORDER BY occurred_at",
                (agent_name, span_start.isoformat()),
            ).fetchall()

        events = _extract_events(rows)
        ntext = _build_narrative(agent_name, events, now, narrative_type)

        return {
            "agent": agent_name,
            "span_days": span_days,
            "narrative_type": narrative_type,
            "events": len(events),
            "narrative": ntext,
            "generated_at": now.isoformat(),
        }
    finally:
        conn.close()
