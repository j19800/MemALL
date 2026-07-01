"""Health metrics for MemALL — shared by ``memall doctor`` and session ``[HEALTH]`` injection.

All metrics are derived from live SQLite queries. The ``collect()`` function returns
a lightweight dict suitable for both CLI output and injection formatting.
"""

import json
import sqlite3
import logging
from datetime import datetime, timezone

from memall.core.db import get_conn, get_db_path
from memall.migrations import get_pending_migrations

logger = logging.getLogger(__name__)

def _now() -> datetime:
    """Return current UTC time (not frozen at import)."""
    return datetime.now(timezone.utc)


def _pct(a: int, b: int) -> float:
    return round(a / b * 100, 1) if b else 0.0


def collect() -> dict:
    """Collect health metrics. Returns a dict safe for both CLI and injection."""
    conn = get_conn()
    try:
        total = conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]

        # Graph coverage: memories that participate in at least one edge
        covered = conn.execute(
            "SELECT COUNT(DISTINCT id) FROM memories "
            "WHERE id IN (SELECT source_id FROM edges) "
            "OR id IN (SELECT target_id FROM edges)"
        ).fetchone()[0]
        coverage_pct = _pct(covered, total)

        # Isolated: old (≥7d) + no edges + never accessed
        isolated = conn.execute(
            "SELECT COUNT(*) FROM memories m "
            "WHERE m.created_at <= datetime('now', '-7 days') "
            "AND m.access_count = 0 "
            "AND NOT EXISTS (SELECT 1 FROM edges e WHERE e.source_id = m.id)"
        ).fetchone()[0]

        # Stale discussions: open discussions > 7 days
        stale_discussions = conn.execute(
            "SELECT COUNT(*) FROM memories "
            "WHERE category = 'discussion_pending' "
            "AND level = 'L5' "
            "AND created_at <= datetime('now', '-7 days')"
        ).fetchone()[0]

        # Low-value memories: never accessed, not system
        zero_access = conn.execute(
            "SELECT COUNT(*) FROM memories "
            "WHERE access_count = 0 "
            "AND category NOT IN ('system', 'heartbeat', 'discussion_pending') "
            "AND created_at <= datetime('now', '-7 days')"
        ).fetchone()[0]

        # Pipeline freshness from pipeline_runs table
        last_pipeline = conn.execute(
            "SELECT MAX(started_at) FROM pipeline_runs WHERE status = 'completed'"
        ).fetchone()[0]

        # Pipeline success rate (last 10 runs)
        recent_runs = conn.execute(
            "SELECT status FROM pipeline_runs ORDER BY id DESC LIMIT 10"
        ).fetchall()
        total_recent = len(recent_runs)
        completed_recent = sum(1 for r in recent_runs if r["status"] == "completed")
        failed_recent = sum(1 for r in recent_runs if r["status"] == "failed")
        pipeline_success_rate = round(completed_recent / max(1, total_recent) * 100, 1)

        # Slowest step from last completed run
        slowest_step_label = "?"
        slowest_ms = 0
        if total_recent > 0:
            last_completed = conn.execute(
                "SELECT steps FROM pipeline_runs WHERE status = 'completed' ORDER BY id DESC LIMIT 1"
            ).fetchone()
            if last_completed and last_completed["steps"]:
                try:
                    steps_list = json.loads(last_completed["steps"])
                    slowest = max(steps_list, key=lambda s: s.get("elapsed_ms", 0) or 0)
                    slowest_step_label = f"{slowest['step']}({slowest['elapsed_ms']}ms)"
                    slowest_ms = slowest.get("elapsed_ms", 0)
                except (json.JSONDecodeError, ValueError, TypeError) as e:
                    logger.warning(f"Failed to parse slowest step from completed steps JSON: {e}")

        # DB size
        db_path = get_db_path()
        import os
        db_size_mb = round(os.path.getsize(db_path) / (1024 * 1024), 1) if os.path.exists(db_path) else 0

        # FTS health
        fts_count = conn.execute(
            "SELECT COUNT(*) FROM memories_fts"
        ).fetchone()[0]
        fts_ok = fts_count == total if total > 0 else True

        # Pending migrations
        try:
            pending_migrations = len(get_pending_migrations(conn))
        except sqlite3.Error:
            pending_migrations = 0

        # Orphan edges
        orphans = conn.execute(
            "SELECT COUNT(*) FROM edges WHERE source_id NOT IN (SELECT id FROM memories)"
        ).fetchone()[0]

        # Embedding index status
        try:
            from memall.graph.embeddings import index_status
            idx_status = index_status()
            pending_embeddings = idx_status.get("un_indexed", 0)
        except Exception:
            pending_embeddings = 0

        # Reflection rate
        l6_count = conn.execute(
            "SELECT COUNT(*) FROM memories WHERE level IN ('L6', 'L7')"
        ).fetchone()[0]
        reflection_pct = _pct(l6_count, total)

        # Level distribution
        level_dist = {}
        for row in conn.execute(
            "SELECT level, COUNT(*) as cnt FROM memories GROUP BY level ORDER BY cnt DESC LIMIT 8"
        ).fetchall():
            level_dist[row["level"]] = row["cnt"]

        pipeline_fresh = True
        if last_pipeline:
            last_run = datetime.fromisoformat(last_pipeline)
            days_since = (_now() - last_run).days
            pipeline_fresh = days_since < 2
        elif total > 10:
            pipeline_fresh = False

        issues = []
        tips = []
        if isolated > 0:
            issues.append(f"{isolated} 条孤立记忆（7 天未关联，未访问）")
            tips.append("孤立记忆不会影响系统运行，但 pipeline 会自动清理")
        if stale_discussions > 0:
            issues.append(f"{stale_discussions} 个讨论超过 7 天未闭合")
            tips.append("运行 memall converge --stale 清理超时讨论")
        if zero_access > 50:
            issues.append(f"{zero_access} 条记忆从未被访问")
            tips.append("低价值记忆将被 decay 步骤自动降级")
        if not pipeline_fresh:
            issues.append("pipeline 超过 2 天未运行")
            tips.append("运行 memall pipeline 或等待定时任务触发")
        if pending_migrations > 0:
            issues.append(f"{pending_migrations} 个迁移待应用")
            tips.append("运行 memall migrate --apply")
        if pending_embeddings > 0:
            issues.append(f"{pending_embeddings} 条待索引嵌入")
            tips.append("运行 memall index-rebuild")
        if orphans > 0:
            issues.append(f"{orphans} 条孤立边")
            tips.append("运行 memall doctor --fix")
        if not fts_ok and total > 0:
            issues.append("FTS 索引不一致")
            tips.append("运行 memall doctor --fix 重建索引")

        # Health score: simple heuristic, 0-100
        score = 100
        if total == 0:
            score = 0
        else:
            score -= max(0, min(15, int(orphans * 3)))
            score -= max(0, min(10, int(pending_embeddings / 10)))
            score -= max(0, min(15, int(isolated / 5)))
            score -= max(0, min(10, stale_discussions * 3))
            score = max(0, min(100, score))

        return {
            "score": score,
            "total_memories": total,
            "graph_coverage_pct": coverage_pct,
            "reflection_pct": reflection_pct,
            "isolated_count": isolated,
            "zero_access_count": zero_access,
            "stale_discussions": stale_discussions,
            "db_size_mb": db_size_mb,
            "fts_ok": fts_ok,
            "pipeline_fresh": pipeline_fresh,
            "pipeline_success_rate": pipeline_success_rate,
            "pipeline_last_run": (last_pipeline or "")[:19],
            "pipeline_slowest_step": slowest_step_label,
            "pending_migrations": pending_migrations,
            "pending_embeddings": pending_embeddings,
            "orphan_edges": orphans,
            "level_distribution": level_dist,
            "issues": issues,
            "tips": tips,
        }
    finally:
        conn.close()
