import sqlite3
import json
import math
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from memall.federation.family import get_family_db_path
from memall.core.nlp import tokenize, cosine_sim, compute_tfidf


def _ensure_metrics_table(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS health_metrics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            snapshot_date TEXT NOT NULL,
            total_memories INTEGER,
            total_agents INTEGER,
            open_conflicts INTEGER,
            resolved_conflicts INTEGER,
            summary TEXT,
            UNIQUE(snapshot_date)
        )
    """)
    conn.commit()


def _snapshot_metrics(conn, stats: dict):
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    conn.execute(
        "INSERT OR REPLACE INTO health_metrics (snapshot_date, total_memories, total_agents, open_conflicts, resolved_conflicts) VALUES (?, ?, ?, ?, ?)",
        (now, stats.get("total", 0), len(stats.get("agents", {})), stats.get("open_conflicts", 0), stats.get("resolved_conflicts", 0)),
    )
    conn.commit()


def federation_health(detail: bool = False) -> dict:
    db_path = get_family_db_path()
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        _ensure_metrics_table(conn)
        rows = conn.execute("SELECT * FROM shared_memories").fetchall()
        total = len(rows)

        agents = Counter()
        conflict_status = Counter()
        for r in rows:
            agents[r["source_agent"] or "(unknown)"] += 1
            conflict_status[r["conflict_status"] or "none"] += 1

        open_c = conn.execute("SELECT COUNT(*) FROM conflicts WHERE status='open'").fetchone()[0]
        resolved_c = conn.execute("SELECT COUNT(*) FROM conflicts WHERE status='resolved'").fetchone()[0]

        # Trends
        trends = conn.execute("SELECT * FROM health_metrics ORDER BY snapshot_date DESC LIMIT 7").fetchall()
        trend_data = []
        for t in reversed(trends):
            trend_data.append({
                "date": t["snapshot_date"],
                "total": t["total_memories"],
                "agents": t["total_agents"],
                "open_conflicts": t["open_conflicts"],
                "resolved_conflicts": t["resolved_conflicts"],
            })

        result = {
            "total": total,
            "agents": dict(agents.most_common()),
            "conflict_status": dict(conflict_status),
            "open_conflicts": open_c,
            "resolved_conflicts": resolved_c,
            "trend": trend_data,
            "last_snapshot": trend_data[-1]["date"] if trend_data else None,
        }

        if detail and total >= 2:
            # Limit to prevent O(n^2) from consuming too much time
            subset_size = min(total, 200)
            texts = [r["content"] for r in rows[:subset_size]]
            ids = [r["id"] for r in rows[:subset_size]]
            try:
                tfidf_docs = compute_tfidf(texts)
            except Exception as e:
                logger.warning("TF-IDF computation failed in health check detail mode: %s", e)
                tfidf_docs = []

            duplicates = []
            orphans = []
            if tfidf_docs:
                for i in range(subset_size):
                    max_sim = 0
                    dup_pair = None
                    for j in range(subset_size):
                        if i == j:
                            continue
                        try:
                            sim = cosine_sim(tfidf_docs[i], tfidf_docs[j])
                        except Exception:
                            sim = 0
                        if sim > max_sim:
                            max_sim = sim
                            dup_pair = (j, sim)
                    if max_sim >= 0.85:
                        duplicates.append({
                            "id": ids[i],
                            "content": texts[i][:100],
                            "most_similar_id": ids[dup_pair[0]],
                            "similarity": round(dup_pair[1], 3),
                            "similar_content": texts[dup_pair[0]][:100],
                        })
                    if max_sim < 0.05:
                        orphans.append({
                            "id": ids[i],
                            "content": texts[i][:100],
                            "max_similarity": round(max_sim, 3),
                        })

            result["duplicates"] = duplicates[:20]
            result["orphans"] = orphans[:20]

        _snapshot_metrics(conn, result)
        return result

    finally:
        conn.close()
