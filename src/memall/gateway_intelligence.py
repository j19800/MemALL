"""
Memory Intelligence — AI-powered memory analysis and insights.

Provides endpoints for:
- Memory quality scoring overview
- Knowledge graph statistics
- Agent memory health metrics
- Cross-agent memory patterns
- Memory lifecycle analysis
"""

import json
import logging
from collections import Counter
from datetime import datetime, timezone, timedelta
from typing import Any

from aiohttp import web
from memall.core.db import pool_conn

logger = logging.getLogger(__name__)


async def handle_intelligence(request: web.Request, gw) -> web.Response:
    """GET /api/intelligence — comprehensive memory intelligence overview.

    Returns a unified view of memory system health, quality, and patterns.
    """
    with pool_conn() as conn:
        # 1. Memory distribution by level
        level_dist = conn.execute(
            "SELECT level, COUNT(*) as cnt FROM memories GROUP BY level ORDER BY cnt DESC"
        ).fetchall()

        # 2. Total counts
        total_memories = conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
        total_edges = conn.execute("SELECT COUNT(*) FROM edges").fetchone()[0]
        total_agents = conn.execute(
            "SELECT COUNT(DISTINCT agent_name) FROM memories WHERE agent_name != ''"
        ).fetchone()[0]

        # 3. Memory quality (avg confidence per level)
        quality = conn.execute(
            "SELECT level, ROUND(AVG(confidence), 2) as avg_conf, COUNT(*) as cnt "
            "FROM memories WHERE confidence > 0 GROUP BY level ORDER BY avg_conf DESC"
        ).fetchall()

        # 4. Recent activity (last 24h, 7d, 30d)
        now = datetime.now(timezone.utc).isoformat()
        day_ago = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
        week_ago = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
        month_ago = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()

        recent_24h = conn.execute(
            "SELECT COUNT(*) FROM memories WHERE created_at >= ?", (day_ago,)
        ).fetchone()[0]
        recent_7d = conn.execute(
            "SELECT COUNT(*) FROM memories WHERE created_at >= ?", (week_ago,)
        ).fetchone()[0]
        recent_30d = conn.execute(
            "SELECT COUNT(*) FROM memories WHERE created_at >= ?", (month_ago,)
        ).fetchone()[0]

        # 5. Memory status distribution
        status_dist = conn.execute(
            "SELECT COALESCE(memory_status, 'normal') as status, COUNT(*) as cnt "
            "FROM memories GROUP BY status ORDER BY cnt DESC"
        ).fetchall()

        # 6. Top categories
        cat_dist = conn.execute(
            "SELECT category, COUNT(*) as cnt FROM memories "
            "WHERE category != '' GROUP BY category ORDER BY cnt DESC LIMIT 10"
        ).fetchall()

        # 7. Agent activity ranking
        agent_rank = conn.execute(
            "SELECT agent_name, COUNT(*) as cnt, MAX(created_at) as last_active "
            "FROM memories WHERE agent_name != '' AND agent_name NOT IN ('system','opencode') "
            "GROUP BY agent_name ORDER BY cnt DESC LIMIT 10"
        ).fetchall()

        # 8. Knowledge graph density
        kg_density = 0
        if total_memories > 0:
            kg_density = round(total_edges / total_memories, 2)

        # 9. Entity stats
        total_entities = conn.execute("SELECT COUNT(*) FROM entities").fetchone()[0]
        total_triples = conn.execute("SELECT COUNT(*) FROM knowledge_triples").fetchone()[0]

        # 10. Pipeline health (last 5 runs)
        pipeline_runs = conn.execute(
            "SELECT status, created_at, elapsed_ms FROM pipeline_runs "
            "ORDER BY created_at DESC LIMIT 5"
        ).fetchall()

    return web.json_response({
        "overview": {
            "total_memories": total_memories,
            "total_edges": total_edges,
            "total_agents": total_agents,
            "total_entities": total_entities,
            "total_triples": total_triples,
            "kg_density": kg_density,
        },
        "levels": [{"level": r["level"], "count": r["cnt"]} for r in level_dist],
        "quality": [{"level": r["level"], "avg_confidence": r["avg_conf"], "count": r["cnt"]} for r in quality],
        "activity": {
            "last_24h": recent_24h,
            "last_7d": recent_7d,
            "last_30d": recent_30d,
        },
        "status": [{"status": r["status"], "count": r["cnt"]} for r in status_dist],
        "categories": [{"category": r["category"], "count": r["cnt"]} for r in cat_dist],
        "top_agents": [{"name": r["agent_name"], "count": r["cnt"], "last_active": r["last_active"]} for r in agent_rank],
        "pipeline": [{"status": r["status"], "at": r["created_at"], "elapsed_ms": r["elapsed_ms"]} for r in pipeline_runs],
    })


async def handle_memory_timeline(request: web.Request, gw) -> web.Response:
    """GET /api/intelligence/timeline — memory creation timeline (daily counts)."""
    days = int(request.query.get("days", "30"))
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

    with pool_conn() as conn:
        rows = conn.execute(
            "SELECT DATE(created_at) as day, level, COUNT(*) as cnt "
            "FROM memories WHERE created_at >= ? "
            "GROUP BY day, level ORDER BY day",
            (cutoff,),
        ).fetchall()

    # Group by day
    timeline: dict[str, dict[str, int]] = {}
    for r in rows:
        day = r["day"]
        if day not in timeline:
            timeline[day] = {}
        timeline[day][r["level"]] = r["cnt"]

    return web.json_response({
        "days": days,
        "timeline": [{"date": d, "levels": levels} for d, levels in sorted(timeline.items())],
    })


async def handle_agent_profile(request: web.Request, gw) -> web.Response:
    """GET /api/intelligence/agent/{name} — detailed agent memory profile."""
    agent_name = request.match_info.get("name", "")

    with pool_conn() as conn:
        # Level distribution
        levels = conn.execute(
            "SELECT level, COUNT(*) as cnt FROM memories "
            "WHERE LOWER(agent_name) = LOWER(?) GROUP BY level ORDER BY cnt DESC",
            (agent_name,),
        ).fetchall()

        # Category distribution
        categories = conn.execute(
            "SELECT category, COUNT(*) as cnt FROM memories "
            "WHERE LOWER(agent_name) = LOWER(?) AND category != '' "
            "GROUP BY category ORDER BY cnt DESC LIMIT 10",
            (agent_name,),
        ).fetchall()

        # Activity timeline (last 30 days)
        cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        activity = conn.execute(
            "SELECT DATE(created_at) as day, COUNT(*) as cnt FROM memories "
            "WHERE LOWER(agent_name) = LOWER(?) AND created_at >= ? "
            "GROUP BY day ORDER BY day",
            (agent_name, cutoff),
        ).fetchall()

        # Total memories
        total = conn.execute(
            "SELECT COUNT(*) FROM memories WHERE LOWER(agent_name) = LOWER(?)",
            (agent_name,),
        ).fetchone()[0]

        # First and last activity
        times = conn.execute(
            "SELECT MIN(created_at) as first, MAX(created_at) as last FROM memories "
            "WHERE LOWER(agent_name) = LOWER(?)",
            (agent_name,),
        ).fetchone()

        # Entity mentions
        entities = conn.execute(
            "SELECT e.name, e.entity_type, COUNT(*) as cnt FROM entities e "
            "JOIN memory_entities me ON e.id = me.entity_id "
            "JOIN memories m ON me.memory_id = m.id "
            "WHERE LOWER(m.agent_name) = LOWER(?) "
            "GROUP BY e.id ORDER BY cnt DESC LIMIT 10",
            (agent_name,),
        ).fetchall()

    return web.json_response({
        "agent_name": agent_name,
        "total_memories": total,
        "first_active": times["first"] if times else None,
        "last_active": times["last"] if times else None,
        "levels": [{"level": r["level"], "count": r["cnt"]} for r in levels],
        "categories": [{"category": r["category"], "count": r["cnt"]} for r in categories],
        "activity": [{"date": r["day"], "count": r["cnt"]} for r in activity],
        "top_entities": [{"name": r["name"], "type": r["entity_type"], "count": r["cnt"]} for r in entities],
    })