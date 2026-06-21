import json
from pathlib import Path
from memall.core.db import get_conn

METRICS_FILE = Path.home() / ".memall" / "metrics.jsonl"


def collect_metrics() -> dict:
    conn = get_conn()
    try:
        total = conn.execute("SELECT COUNT(*) as c FROM memories").fetchone()["c"]
        if total == 0:
            return {"total_memories": 0}

        with_edges = conn.execute(
            "SELECT COUNT(DISTINCT m.id) as c FROM memories m JOIN edges e ON m.id = e.source_id OR m.id = e.target_id"
        ).fetchone()["c"]

        category_stats = conn.execute(
            "SELECT category, COUNT(*) as c FROM memories GROUP BY category ORDER BY c DESC"
        ).fetchall()

        non_general = sum(r["c"] for r in category_stats if r["category"] != "general")
        classified = conn.execute("SELECT COUNT(*) as c FROM memories WHERE category != 'general'").fetchone()["c"]

        edge_count = conn.execute("SELECT COUNT(*) as c FROM edges").fetchone()["c"]

        agent_stats = conn.execute(
            "SELECT agent_name, COUNT(*) as c FROM memories WHERE agent_name != '' GROUP BY agent_name ORDER BY c DESC"
        ).fetchall()

        metrics = {
            "total_memories": total,
            "total_edges": edge_count,
            "connection_density": round(with_edges / total, 3) if total > 0 else 0,
            "classification_coverage": round(classified / total, 3) if total > 0 else 0,
            "categories": len(category_stats),
            "active_agents": len(agent_stats),
        }
        return metrics
    finally:
        conn.close()


def append_metrics(metrics: dict):
    METRICS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(str(METRICS_FILE), "a", encoding="utf-8") as f:
        f.write(json.dumps(metrics, ensure_ascii=False) + "\n")


def show_metrics() -> dict:
    metrics = collect_metrics()
    append_metrics(metrics)
    return metrics


def read_history(limit: int = 10) -> list:
    if not METRICS_FILE.exists():
        return []
    with open(str(METRICS_FILE), "r") as f:
        lines = f.readlines()
    return [json.loads(l) for l in lines[-limit:]]
