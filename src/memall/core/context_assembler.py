import json
from collections import Counter
from datetime import datetime, timezone
from memall.core.db import pool_conn


def get_persona(agent_name: str, limit: int = 20) -> dict:
    with pool_conn() as conn:
        recent = conn.execute(
            "SELECT id, content, summary, level, category, created_at FROM memories WHERE LOWER(agent_name)=LOWER(?) AND level IN ('L6','L7') ORDER BY created_at DESC LIMIT ?",
            (agent_name, limit),
        ).fetchall()

        decisions = []
        topics = []
        contradictions = []
        insights = []

        for r in recent:
            content = r["content"] or ""
            summary = r["summary"] or ""
            text = summary or content

            if any(kw in text for kw in ["决定", "选", "采用", "方案", "结论", "定为"]):
                decisions.append({"id": r["id"], "text": text[:200], "category": r["category"], "at": r["created_at"]})

            cats = r["category"] or ""
            if cats:
                topics.append(cats)

        # 矛盾 (batch loaded, N+1 fix)
        contrad_rows = conn.execute(
            "SELECT e.source_id, e.target_id, e.metadata FROM edges e WHERE e.relation_type='contradicts' AND e.source_id IN (SELECT id FROM memories WHERE LOWER(agent_name)=LOWER(?)) ORDER BY e.id DESC LIMIT 10",
            (agent_name,),
        ).fetchall()
        contra_ids = set()
        for cr in contrad_rows:
            contra_ids.add(cr["source_id"])
            contra_ids.add(cr["target_id"])
        contra_map = {}
        if contra_ids:
            ph = ",".join("?" * len(contra_ids))
            for row in conn.execute(f"SELECT id, content FROM memories WHERE id IN ({ph})", tuple(contra_ids)):
                contra_map[row["id"]] = (row["content"] or "")[:120]
        for cr in contrad_rows:
            meta = json.loads(cr["metadata"]) if cr["metadata"] and cr["metadata"] != "{}" else {}
            contradictions.append({
                "a_id": cr["source_id"], "a": contra_map.get(cr["source_id"], ""),
                "b_id": cr["target_id"], "b": contra_map.get(cr["target_id"], ""),
                "resolved": meta.get("resolved", False),
            })

        # 融合见解 (batch loaded, N+1 fix)
        derived = conn.execute(
            "SELECT source_id FROM edges WHERE relation_type='derived_from' AND source_id IN (SELECT id FROM memories WHERE LOWER(agent_name)=LOWER(?)) ORDER BY id DESC LIMIT 10",
            (agent_name,),
        ).fetchall()
        derived_ids = [d["source_id"] for d in derived]
        insights_map = {}
        if derived_ids:
            ph = ",".join("?" * len(derived_ids))
            for row in conn.execute(f"SELECT id, summary, content FROM memories WHERE id IN ({ph})", tuple(derived_ids)):
                insights_map[row["id"]] = row
        for d in derived:
            mem = insights_map.get(d["source_id"])
            if mem:
                insights.append({"id": mem["id"], "text": (mem["summary"] or mem["content"] or "")[:200]})

        topic_counts = Counter(topics)
        active_topics = [{"category": c, "count": cnt} for c, cnt in topic_counts.most_common(5)]

        return {
            "recent_decisions": decisions[:5],
            "active_topics": active_topics,
            "contradictions_unresolved": [c for c in contradictions if not c["resolved"]][:5],
            "derived_insights": insights[:5],
            "sample_size": len(recent),
        }
