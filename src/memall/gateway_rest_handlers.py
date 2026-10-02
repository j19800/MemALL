"""Gateway REST/JSON API handlers — extracted from gateway.py.

Every ``/api/*`` endpoint plus the v3.0 memory CRUD routes, composed
into ``MemAllGateway`` as ``RestHandlersMixin``.
"""

import json
import logging
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from aiohttp import web
from memall.gateway_utils import (
    _ok,
    _load_debt_cache,
    _save_debt_cache,
    _safe_int,
    _epoch_narrative,
)
from memall.core.db import (
    pool_conn,
    get_conn,
    db_stats,
    optimize_db,
    vacuum_db,
    DB_PATH,
)
from memall.core.thin_waist import (
    capture,
    retrieve,
    connect,
    traverse,
    timeline,
    smart_store,
    store_batch,
    update,
    vector_search,
)
from memall.core.models import MemoryInput
from memall.pipeline.persona import (
    generate_profile_3layer,
    generate_persona,
    get_evolution,
)
from memall.mcp.models import (
    CaptureInput,
    DiscussionCreateInput,
    DiscussionRespondInput,
)
from memall.pipeline.session import session_start, session_end, session_summary
from memall.pipeline.ask import ContextAssembler
from memall.pipeline.forget import (
    forget_expired, forget_low_value, forget_review, forget_stats, forget_step,
)
from memall.pipeline.adaptive import adaptive_step, adaptive_report
from memall.pipeline.security import (
    audit_sensitive, set_permission, check_access,
    list_agents_by_permission, security_score,
)
from memall.pipeline.ops import (
    merge_memories, split_memory, tag_memory, batch_tag,
    batch_archive, batch_restore, deduplicate,
)
from memall.pipeline.observe import reflection_dashboard
from memall.pipeline.pipeline import run_pipeline
from memall.migrations import get_migration_status, run_migrations

logger = logging.getLogger("memall.gateway.api")


class RestHandlersMixin:
    """Extracted gateway handlers."""

    async def _handle_api_graph(self, request: web.Request) -> web.Response:
        with pool_conn() as conn:
            mem_count = conn.execute("SELECT COUNT(*) AS c FROM memories").fetchone()["c"]
            edge_count = conn.execute("SELECT COUNT(*) AS c FROM edges").fetchone()["c"]
            type_rows = conn.execute(
                "SELECT relation_type, COUNT(*) AS cnt FROM edges GROUP BY relation_type ORDER BY cnt DESC"
            ).fetchall()
            hub_rows = conn.execute(
                "SELECT node_id, COUNT(*) AS edge_count FROM ("
                "SELECT source_id AS node_id FROM edges UNION ALL SELECT target_id AS node_id FROM edges"
                ") GROUP BY node_id ORDER BY edge_count DESC LIMIT 20"
            ).fetchall()
            hub_ids = [r["node_id"] for r in hub_rows]
            hub_map = {}
            if hub_ids:
                ph = ",".join("?" for _ in hub_ids)
                for r in conn.execute(f"SELECT id, subject FROM memories WHERE id IN ({ph})", hub_ids).fetchall():
                    hub_map[r["id"]] = r["subject"] or f"#{r['id']}"
        total = edge_count or 1
        return web.json_response(
            {
                "totals": {"memories": mem_count, "edges": edge_count, "density": round(edge_count / max(mem_count, 1), 2)},
                "types": [{"type": r["relation_type"], "count": r["cnt"], "pct": round(r["cnt"] / total * 100, 1)} for r in type_rows],
                "hubs": [{"id": r["node_id"], "subject": hub_map.get(r["node_id"], f"#{r['node_id']}"), "edge_count": r["edge_count"]} for r in hub_rows],
            },
                    )

    async def _handle_api_graph_center(self, request: web.Request) -> web.Response:
        """GET /api/graph/center — recommend a central (highest-degree) node as graph-view default."""
        with pool_conn() as conn:
            row = conn.execute(
                "SELECT node_id, COUNT(*) AS edge_count FROM ("
                "SELECT source_id AS node_id FROM edges UNION ALL SELECT target_id AS node_id FROM edges"
                ") GROUP BY node_id ORDER BY edge_count DESC LIMIT 1"
            ).fetchone()
            if not row:
                return web.json_response({"node_id": None, "subject": None, "edge_count": 0})
            node_id = row["node_id"]
            mem = conn.execute(
                "SELECT subject, content FROM memories WHERE id = ?", (node_id,)
            ).fetchone()
            if mem and mem["subject"]:
                subject = mem["subject"]
            elif mem and mem["content"]:
                subject = mem["content"][:40]
            else:
                subject = f"#{node_id}"
        return web.json_response({"node_id": node_id, "subject": subject, "edge_count": row["edge_count"]})

    async def _handle_api_slices(self, request: web.Request) -> web.Response:
        agent_name = request.query.get("agent_name", "").strip() or "*"
        granularity = request.query.get("granularity", "day")
        days = _safe_int(request.query.get("days", 30))

        from datetime import timedelta
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

        with pool_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM time_slices WHERE agent_name = ? AND granularity = ? "
                "AND window_start >= ? ORDER BY window_start",
                (agent_name, granularity, cutoff),
            ).fetchall()

        return web.json_response({
            "agent_name": agent_name,
            "granularity": granularity,
            "slices": [dict(r) for r in rows],
        },)

    async def _handle_api_timeline_density(self, request: web.Request) -> web.Response:
        days = _safe_int(request.query.get("days", 30))
        agent_name = request.query.get("agent_name", "").strip() or None
        from datetime import timedelta
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        with pool_conn() as conn:
            if agent_name:
                rows = conn.execute(
                    "SELECT slice_key, memory_count, category_distribution FROM time_slices "
                    "WHERE agent_name = ? AND granularity = 'day' AND window_start >= ? "
                    "ORDER BY slice_key",
                    (agent_name, cutoff),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT slice_key, SUM(memory_count) as memory_count FROM time_slices "
                    "WHERE granularity = 'day' AND window_start >= ? "
                    "GROUP BY slice_key ORDER BY slice_key",
                    (cutoff,),
                ).fetchall()
        return web.json_response({
            "days": len(rows),
            "density": [{"date": r["slice_key"], "count": r["memory_count"]} for r in rows],
        },)

    async def _handle_api_timeline_epochs(self, request: web.Request) -> web.Response:
        days = _safe_int(request.query.get("days", 7))
        agent_name = request.query.get("agent_name", "").strip() or None

        # Get memories in time window
        results = timeline(days=days)
        if agent_name:
            results = [r for r in results if r.agent_name and r.agent_name.lower() == agent_name.lower()]

        # Get epochs overlapping the time window
        from datetime import timedelta
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        with pool_conn() as conn:
            if agent_name:
                epoch_rows = conn.execute(
                    "SELECT * FROM epochs WHERE agent_name = ? AND "
                    "(ended_at IS NULL OR ended_at >= ?) AND started_at <= ? "
                    "ORDER BY started_at",
                    (agent_name, cutoff, datetime.now(timezone.utc).isoformat()),
                ).fetchall()
            else:
                epoch_rows = conn.execute(
                    "SELECT * FROM epochs WHERE "
                    "(ended_at IS NULL OR ended_at >= ?) AND started_at <= ? "
                    "ORDER BY agent_name, started_at",
                    (cutoff, datetime.now(timezone.utc).isoformat()),
                ).fetchall()

        # Assign each memory to an epoch
        epoch_map = {e["id"]: dict(e) for e in epoch_rows}
        epoch_map[0] = {"id": 0, "label": "未归属", "started_at": cutoff, "ended_at": None,
                        "boundary_reason": "auto", "memory_count": 0, "agent_name": ""}

        epoch_children: dict[int, list] = {eid: [] for eid in epoch_map}

        for mem in results:
            mem_occurred = (mem.occurred_at or mem.created_at or "")
            assigned = False
            for e in sorted(epoch_map.values(), key=lambda x: x.get("started_at", "")):
                e_start = e.get("started_at", "")
                e_end = e.get("ended_at") or "9999"
                if e_start <= mem_occurred <= e_end:
                    epoch_children.setdefault(e["id"], []).append(mem)
                    assigned = True
                    break
            if not assigned:
                epoch_children.setdefault(0, []).append(mem)

        # Build response with edge counts
        result_epochs = []
        for eid, mems in epoch_children.items():
            if eid == 0 and not mems:
                continue
            e = epoch_map[eid]
            with pool_conn() as conn:
                mem_list = []
                for m in mems:
                    sup_cnt = conn.execute(
                        "SELECT COUNT(*) as c FROM edges WHERE source_id = ? AND relation_type = 'supersedes'",
                        (m.id,),
                    ).fetchone()["c"]
                    ref_cnt = conn.execute(
                        "SELECT COUNT(*) as c FROM edges WHERE source_id = ? AND relation_type = 'refines'",
                        (m.id,),
                    ).fetchone()["c"]
                    mem_list.append({
                        "id": m.id,
                        "content": (m.content or "")[:250],
                        "level": m.level,
                        "category": m.category,
                        "agent_name": m.agent_name,
                        "occurred_at": m.occurred_at,
                        "supersedes_count": sup_cnt,
                        "refines_count": ref_cnt,
                    })

            result_epochs.append({
                "epoch": {
                    "id": e["id"],
                    "label": e.get("label", "")[:60],
                    "narrative": _epoch_narrative(mem_list),
                    "started_at": e.get("started_at", ""),
                    "ended_at": e.get("ended_at"),
                    "boundary_reason": e.get("boundary_reason", "auto"),
                    "memory_count": len(mems),
                    "agent_name": e.get("agent_name", ""),
                },
                "memories": mem_list,
            })

        # Sort epochs by start time (newest first), put unassigned at end
        result_epochs.sort(key=lambda x: x["epoch"]["started_at"], reverse=True)
        unassigned = [x for x in result_epochs if x["epoch"]["id"] == 0]
        assigned = [x for x in result_epochs if x["epoch"]["id"] != 0]
        result_epochs = assigned + unassigned

        return web.json_response({
            "epochs": result_epochs,
            "total_memories": len(results),
        },)

    async def _handle_api_epochs(self, request: web.Request) -> web.Response:
        with pool_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM epochs ORDER BY agent_name, started_at LIMIT 1000"
            ).fetchall()

        return web.json_response({
            "epochs": [dict(r) for r in rows],
            "count": len(rows),
        },)

    async def _handle_api_epochs_agent(self, request: web.Request) -> web.Response:
        agent_name = request.match_info.get("agent_name", "").strip().lower()
        with pool_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM epochs WHERE agent_name = ? ORDER BY started_at LIMIT 1000",
                (agent_name,),
            ).fetchall()

        return web.json_response({
            "agent_name": agent_name,
            "epochs": [dict(r) for r in rows],
        },)

    async def _handle_api_arcs(self, request: web.Request) -> web.Response:
        agent_name = request.query.get("agent", "").strip().lower() or None
        status_filter = request.query.get("status", "").strip() or None

        where = ["level = 'L4' AND arc_status IS NOT NULL"]
        params = []
        if agent_name:
            where.append("agent_name = ?")
            params.append(agent_name)
        if status_filter in ("open", "in_progress", "closed"):
            where.append("arc_status = ?")
            params.append(status_filter)

        with pool_conn() as conn:
            rows = conn.execute(
                f"SELECT id, level, category, subject, agent_name, created_at, arc_status "
                f"FROM memories WHERE {' AND '.join(where)} ORDER BY created_at DESC LIMIT 1000",
                params,
            ).fetchall()

            stale_count = 0
            stale_ids = set()
            if not status_filter or status_filter == "open":
                stale_cutoff = (date.today() - timedelta(days=21)).isoformat()
                stale_rows = conn.execute(
                    "SELECT id FROM memories WHERE level = 'L4' AND arc_status = 'open' "
                    "AND created_at < ? AND id NOT IN ("
                    "  SELECT DISTINCT source_id FROM edges WHERE relation_type != 'deleted' "
                    "  AND target_id IN (SELECT id FROM memories WHERE level = 'L5')"
                    "  UNION "
                    "  SELECT DISTINCT target_id FROM edges WHERE relation_type != 'deleted' "
                    "  AND source_id IN (SELECT id FROM memories WHERE level = 'L5')"
                    ")",
                    (stale_cutoff,),
                ).fetchall()
                stale_ids = {r["id"] for r in stale_rows}

            stats = conn.execute(
                "SELECT arc_status, COUNT(*) as cnt FROM memories WHERE level = 'L4' "
                "AND arc_status IS NOT NULL GROUP BY arc_status"
            ).fetchall()
            status_counts = {r["arc_status"]: r["cnt"] for r in stats}

        arcs = []
        for r in rows:
            arc = dict(r)
            if not status_filter or status_filter == "open":
                arc["stale"] = r["id"] in stale_ids
                if arc.get("stale"):
                    stale_count += 1
            else:
                arc["stale"] = False
            arcs.append(arc)

        return web.json_response({
            "arcs": arcs,
            "stats": status_counts,
            "stale_count": stale_count,
        },)

    async def _handle_api_arcs_detail(self, request: web.Request) -> web.Response:
        try:
            decision_id = int(request.match_info.get("decision_id", "0"))
        except ValueError:
            return web.json_response({"error": "invalid decision_id"}, status=400)

        with pool_conn() as conn:
            decision = conn.execute(
                "SELECT * FROM memories WHERE id = ? AND level = 'L4'", (decision_id,)
            ).fetchone()
            if not decision:
                return web.json_response({"error": "decision not found"}, status=404)

            tasks = conn.execute(
                "SELECT m.id, m.level, m.subject, m.content, m.created_at "
                "FROM memories m JOIN edges e ON "
                "  (e.source_id = m.id OR e.target_id = m.id) "
                "WHERE m.level = 'L5' AND e.relation_type != 'deleted' "
                "AND (e.source_id = ? OR e.target_id = ?)",
                (decision_id, decision_id),
            ).fetchall()

            reflections = conn.execute(
                "SELECT m.id, m.level, m.subject, m.content, m.created_at "
                "FROM memories m JOIN edges e ON "
                "  (e.source_id = m.id OR e.target_id = m.id) "
                "WHERE m.level = 'L6' AND e.relation_type != 'deleted' "
                "AND (e.source_id = ? OR e.target_id = ?)",
                (decision_id, decision_id),
            ).fetchall()

            # Stale check
            stale = False
            if decision["arc_status"] == "open":
                cutoff = (date.today() - timedelta(days=21)).isoformat()
                if (decision["created_at"] or "")[:10] < cutoff and not tasks:
                    stale = True

        return web.json_response({
            "decision": dict(decision),
            "tasks": [dict(t) for t in tasks],
            "reflections": [dict(r) for r in reflections],
            "arc_status": decision["arc_status"],
            "stale": stale,
        },)

    async def _handle_api_epoch_arcs(self, request: web.Request) -> web.Response:
        try:
            epoch_id = int(request.match_info.get("epoch_id", "0"))
        except ValueError:
            return web.json_response({"error": "invalid epoch_id"}, status=400)

        with pool_conn() as conn:
            epoch = conn.execute(
                "SELECT id, agent_name, label, started_at, ended_at FROM epochs WHERE id = ?",
                (epoch_id,),
            ).fetchone()
            if not epoch:
                return web.json_response({"error": "epoch not found"}, status=404)

            start = epoch["started_at"][:10]
            end = (epoch["ended_at"] or "9999-12-31")[:10]

            arcs = conn.execute(
                "SELECT id, level, category, subject, agent_name, created_at, arc_status "
                "FROM memories WHERE level = 'L4' AND arc_status IS NOT NULL "
                "AND agent_name = ? AND created_at >= ? AND created_at <= ? "
                "ORDER BY created_at",
                (epoch["agent_name"], start, end),
            ).fetchall()

            status_counts = {"open": 0, "in_progress": 0, "closed": 0}
            arc_list = []
            for a in arcs:
                s = a["arc_status"] or "open"
                if s in status_counts:
                    status_counts[s] += 1
                arc_list.append({
                    "id": a["id"],
                    "subject": a["subject"],
                    "arc_status": s,
                })

            total = len(arc_list)
            closure_rate = round(status_counts["closed"] / total, 2) if total > 0 else 0.0

        return web.json_response({
            "epoch_id": epoch_id,
            "epoch_label": epoch["label"],
            "total_arcs": total,
            "open": status_counts["open"],
            "in_progress": status_counts["in_progress"],
            "closed": status_counts["closed"],
            "closure_rate": closure_rate,
            "arcs": arc_list,
        },)

    async def _handle_api_discussions(self, request: web.Request) -> web.Response:
        """JSON: list all active L5 discussions."""
        from memall.pipeline.convergence import list_active_discussions
        topics = list_active_discussions()
        return web.json_response({"topics": topics},)

    async def _handle_api_discussion_detail(self, request: web.Request) -> web.Response:
        """JSON: full detail for a single L5 discussion including all responses."""
        topic_id = request.match_info.get("topic_id", "")
        from memall.pipeline.convergence import get_discussion
        result = get_discussion(int(topic_id))
        return web.json_response(result,)

    async def _handle_api_discussion_create(self, request: web.Request) -> web.Response:
        """JSON: create a new L5 discussion and return memory_id."""
        data = await self._read_json(request)
        if not data:
            return web.json_response({"error": "invalid JSON"}, status=400,)
        validated, err = self._validate(data, DiscussionCreateInput)
        if err:
            return web.json_response({"error": err}, status=400,)
        from memall.pipeline.convergence import create_discussion
        result = create_discussion(
            title=validated["title"],
            background=validated.get("background", ""),
            options=validated.get("options"),
            open_questions=validated.get("open_questions"),
            recommendation=validated.get("recommendation", ""),
            action_items=validated.get("action_items"),
            participants=validated.get("participants", []),
            timeout_hours=validated.get("timeout_hours", 24),
        )
        return web.json_response(result,)

    async def _handle_api_discussion_respond(self, request: web.Request) -> web.Response:
        """JSON: record an agent's response via L5 P2 + edge."""
        data = await self._read_json(request)
        if not data:
            return web.json_response({"error": "invalid JSON"}, status=400,)
        validated, err = self._validate(data, DiscussionRespondInput)
        if err:
            return web.json_response({"error": err}, status=400,)
        from memall.pipeline.convergence import confirm_discussion
        result = confirm_discussion(
            discussion_id=validated["discussion_id"],
            agent_name=validated["agent_name"],
            stance=validated["stance"],
            note=validated.get("arguments", ""),
        )
        return web.json_response(result,)

    async def _handle_api_capture(self, request: web.Request) -> web.Response:
        """POST /memories — store a memory."""
        data = await self._read_json(request)
        if data is None:
            return web.json_response({"error": "invalid JSON body"}, status=400,)
        validated, err = self._validate(data, CaptureInput)
        if err:
            return web.json_response({"error": err}, status=400,)
        mid = capture(MemoryInput(**validated))
        return web.json_response({"id": mid, "status": "ok"},)

    async def _handle_api_search(self, request: web.Request) -> web.Response:
        """GET /memories/search — search memories."""
        query = request.query.get("query", "")
        owner = request.query.get("owner", "")
        agent_name = request.query.get("agent_name", "")
        category = request.query.get("category", "")
        limit = _safe_int(request.query.get("limit", "20"))
        results = retrieve(query, owner=owner, agent_name=agent_name, category=category, limit=limit)
        if not isinstance(results, list):
            results = [results] if results else []
        conn2 = get_conn()
        try:
            extra = {}
            for r in results[:limit]:
                row = conn2.execute("SELECT subject, summary, project, created_at, updated_at, tags, metadata, access_count FROM memories WHERE id = ?", (r.id,)).fetchone()
                extra[r.id] = dict(row) if row else {}
        except Exception:
            extra = {}
        finally:
            conn2.close()
        result_list = [
            {"id": r.id, "content": r.content[:200] if r.content else "",
             "subject": (e := extra.get(r.id, {})).get("subject", ""),
             "summary": e.get("summary", ""), "category": r.category, "level": r.level,
             "agent_name": r.agent_name, "project": e.get("project", ""),
             "tags": e.get("tags", "[]"), "metadata": e.get("metadata", "{}"),
             "access_count": e.get("access_count", 0),
             "created_at": e.get("created_at", r.occurred_at or ""),
             "updated_at": e.get("updated_at", "")}
            for r in results
        ]
        return web.json_response(result_list,)

    async def _handle_api_vector_search(self, request: web.Request) -> web.Response:
        """GET /memories/vector-search — semantic vector search."""
        query = request.query.get("query", "")
        top_k = _safe_int(request.query.get("top_k", "10"))
        return web.json_response(vector_search(query, top_k),)

    async def _handle_api_update(self, request: web.Request) -> web.Response:
        """PUT /memories — update a memory."""
        data = await request.json()
        memory_id = data.get("memory_id", 0)
        kwargs = {k: v for k, v in data.items() if v is not None and k != "memory_id"}
        ok = update(memory_id, **kwargs)
        return web.json_response({"status": "ok" if ok else "error"},)

    async def _handle_api_smart_store(self, request: web.Request) -> web.Response:
        """POST /memories/smart-store — store with dedup."""
        data = await request.json()
        return web.json_response(smart_store(**data),)

    async def _handle_api_batch_store(self, request: web.Request) -> web.Response:
        """POST /memories/batch — batch store."""
        items = await request.json()
        return web.json_response(store_batch(items),)

    async def _handle_api_import(self, request: web.Request) -> web.Response:
        """POST /memories/import — import memories from file."""
        data = await request.json()
        path = data.get("path", "")
        fmt = data.get("format", "jsonl")
        agent = data.get("agent_name", "imported")

        # Security: restrict import to existing regular files with known extensions
        if not path or not isinstance(path, str):
            return web.json_response({"error": "path required"}, status=400)
        allowed_exts = (".jsonl", ".json", ".csv")
        import pathlib
        p = pathlib.Path(path)
        if p.suffix.lower() not in allowed_exts:
            return web.json_response(
                {"error": "forbidden", "message": f"only {', '.join(allowed_exts)} files can be imported"},
                status=400,
            )
        if not p.is_file():
            return web.json_response({"error": "not_found", "message": "file does not exist"}, status=404)

        try:
            if fmt == "mem0":
                from memall.auto_capture import import_from_mem0
                result = import_from_mem0(str(p), agent)
            elif fmt == "csv":
                from memall.auto_capture import import_from_csv
                result = import_from_csv(str(p), agent)
            else:
                from memall.auto_capture import import_from_jsonl
                result = import_from_jsonl(str(p), agent)
            return web.json_response(result)
        except Exception as e:
            logger.warning("import failed path=%s fmt=%s: %s", path, fmt, e, exc_info=True)
            return web.json_response({"error": "import_failed", "message": "import failed, see server log"}, status=500)

    async def _handle_api_memories_stats(self, request: web.Request) -> web.Response:
        """GET /memories/stats — memory statistics."""
        conn = get_conn()
        total = conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
        recent = conn.execute("SELECT COUNT(*) FROM memories WHERE created_at >= datetime('now', '-24 hours')").fetchone()[0]
        by_level = dict(conn.execute("SELECT level, COUNT(*) as cnt FROM memories GROUP BY level").fetchall())
        conn.close()
        return web.json_response(
            {"success": True, "data": {"total": total, "total_memories": total, "recent_24h": recent, "by_level": by_level, "total_links": 0, "total_agents": len(by_level)}},
            )

    async def _handle_api_get_memory(self, request: web.Request) -> web.Response:
        """GET /memories/{memory_id} — get a memory by ID."""
        memory_id = request.match_info.get("memory_id", "")
        try:
            mid = int(memory_id)
        except ValueError:
            return web.json_response({"error": "invalid id"}, status=400,)
        r = retrieve(mid)
        if not r:
            return web.json_response({"error": "not found"}, status=404,)
        return web.json_response(
            {"id": r.id, "content": r.content, "category": r.category, "level": r.level,
             "agent_name": r.agent_name, "subject": r.subject, "summary": r.summary,
             "created_at": r.created_at},
            )

    async def _handle_api_timeline(self, request: web.Request) -> web.Response:
        """GET /timeline — get time-ordered memories."""
        query = request.query.get("query", "")
        hours = _safe_int(request.query.get("hours", "24"))
        category = request.query.get("category", "")
        project = request.query.get("project", "")
        limit = _safe_int(request.query.get("limit", "50"))
        days = request.query.get("days", None)
        items = timeline(query=query, hours=hours, category=category, project=project, limit=limit, days=_safe_int(days) if days else None)
        return web.json_response(
            [{"id": r.id, "content": r.content, "category": r.category, "level": r.level,
              "occurred_at": r.occurred_at, "memory_status": getattr(r, "memory_status", None)} for r in items],
            )

    async def _handle_api_edges(self, request: web.Request) -> web.Response:
        """POST /edges — create relationship."""
        data = await request.json()
        eid = connect(source_id=data.get("source_id", 0), target_id=data.get("target_id", 0),
                      relation_type=data.get("relation_type", "refines"), weight=data.get("weight", 1.0))
        return web.json_response({"id": eid, "status": "ok"},)

    async def _handle_api_graph_traverse(self, request: web.Request) -> web.Response:
        """GET /graph/{node_id} — traverse knowledge graph."""
        node_id = _safe_int(request.match_info.get("node_id", "0"))
        depth = min(_safe_int(request.query.get("depth", "1")), 5)
        relation_filter = request.query.get("relation_filter", "")
        thread_aware = request.query.get("thread_aware", "false").lower() == "true"
        kwargs = {"node_id": node_id, "depth": depth}
        if relation_filter:
            kwargs["relation_filter"] = relation_filter
        if thread_aware:
            kwargs["thread_aware"] = True
        return web.json_response(_ok(traverse(**kwargs)),)

    async def _handle_api_graph_search(self, request: web.Request) -> web.Response:
        """GET /graph/search — alias for traverse."""
        node_id = _safe_int(request.query.get("node_id", "0"))
        depth = min(_safe_int(request.query.get("depth", "1")), 5)
        relation_filter = request.query.get("relation_filter", "")
        kwargs = {"node_id": node_id, "depth": depth}
        if relation_filter:
            kwargs["relation_filter"] = relation_filter
        return web.json_response(_ok(traverse(**kwargs)),)

    async def _handle_api_persona(self, request: web.Request) -> web.Response:
        """GET /persona/{agent_name} — get agent persona."""
        agent_name = request.match_info.get("agent_name", "")
        evolution = request.query.get("evolution", "false").lower() == "true"
        window_days = _safe_int(request.query.get("window_days", "30"))
        p = generate_persona(agent_name)
        if evolution:
            p["evolution"] = get_evolution(agent_name, window_days)
        return web.json_response(p,)

    async def _handle_api_persona_profile(self, request: web.Request) -> web.Response:
        """GET /persona/{agent_name}/profile — full 3-layer profile."""
        agent_name = request.match_info.get("agent_name", "")
        return web.json_response(generate_profile_3layer(agent_name),)

    async def _handle_api_ask(self, request: web.Request) -> web.Response:
        """POST /ask — query digital twin."""
        data = await request.json()
        result = ContextAssembler.ask(
            query=data.get("question", ""), mode=data.get("mode", "stance"),
            subject=data.get("agent_name", ""), scope=data.get("scope", "local"),
        )
        return web.json_response(result,)

    async def _handle_api_session_start(self, request: web.Request) -> web.Response:
        """POST /sessions — start a new session."""
        data = await request.json()
        return web.json_response(
            session_start(agent_name=data.get("agent_name", ""), auto_inject=data.get("auto_inject", True)),
            )

    async def _handle_api_session_end(self, request: web.Request) -> web.Response:
        """POST /sessions/{session_id}/end — end a session."""
        session_id = request.match_info.get("session_id", "")
        data = await request.json() if request.can_read_body else {}
        return web.json_response(
            session_end(session_id, auto_extract=data.get("auto_extract", False)),
            )

    async def _handle_api_session_summary(self, request: web.Request) -> web.Response:
        """GET /sessions/{session_id} — get session summary."""
        session_id = request.match_info.get("session_id", "")
        return web.json_response(session_summary(session_id=session_id),)

    async def _handle_api_sessions_list(self, request: web.Request) -> web.Response:
        """GET /sessions — list recent sessions."""
        agent_name = request.query.get("agent_name", "")
        limit = _safe_int(request.query.get("limit", "5"))
        return web.json_response(session_summary(agent_name=agent_name, limit=limit),)

    async def _handle_api_forget(self, request: web.Request) -> web.Response:
        """POST /forget — automatic forgetting."""
        data = await request.json()
        days = data.get("days", 90)
        agent = data.get("agent_name")
        actions = {
            "expired": lambda: forget_expired(days, agent),
            "low_value": lambda: forget_low_value(agent),
            "review": lambda: forget_review(days, agent),
            "stats": lambda: forget_stats(),
            "all": lambda: forget_step(days, agent),
        }
        fn = actions.get(data.get("action", ""))
        return web.json_response(fn() if fn else {"error": f"unknown action: {data.get('action')}"},
                                 )

    async def _handle_api_adaptive(self, request: web.Request) -> web.Response:
        """POST /adaptive — run adaptive subsystem."""
        data = await request.json()
        agent_name = data.get("agent_name")
        return web.json_response(adaptive_step(agent_name=agent_name) if agent_name else adaptive_report(),
                                 )

    async def _handle_api_adaptive_report(self, request: web.Request) -> web.Response:
        """GET /adaptive/report — adaptive status report."""
        return web.json_response(adaptive_report(),)

    async def _handle_api_security(self, request: web.Request) -> web.Response:
        """POST /security — security governance."""
        data = await request.json()
        action = data.get("action", "")
        actions = {
            "audit": lambda: audit_sensitive(agent_name=data.get("agent_name")),
            "permit": lambda: set_permission(data.get("agent_name"), data.get("level")),
            "check": lambda: check_access(data.get("requester"), data.get("target")),
            "score": lambda: security_score(),
            "list": lambda: list_agents_by_permission(data.get("level")),
        }
        fn = actions.get(action)
        if not fn:
            return web.json_response({"error": f"unknown action: {action}"},)
        try:
            return web.json_response(fn(),)
        except Exception as e:
            return web.json_response({"error": str(e)},)

    async def _handle_api_ops(self, request: web.Request) -> web.Response:
        """POST /ops — memory operations."""
        data = await request.json()
        action = data.get("action", "")
        actions = {
            "merge": lambda: merge_memories(data.get("source_id"), data.get("target_id")),
            "split": lambda: split_memory(data.get("memory_id"), data.get("delimiter")),
            "tag": lambda: tag_memory(data.get("memory_id"), data.get("tags"), data.get("mode", "add")),
            "batch_tag": lambda: batch_tag(data.get("agent_name"), data.get("category"), data.get("tags"), data.get("mode", "add")),
            "archive": lambda: batch_archive(data.get("agent_name"), data.get("days")),
            "restore": lambda: batch_restore(data.get("agent_name")),
            "dedup": lambda: deduplicate(data.get("agent_name"), data.get("threshold", 0.85)),
        }
        fn = actions.get(action)
        return web.json_response(fn() if fn else {"error": f"unknown action: {action}"},
                                 )

    async def _handle_api_ops_dedup(self, request: web.Request) -> web.Response:
        """GET /ops/dedup — check for duplicates."""
        return web.json_response({"note": "Use POST /ops with action=dedup to execute"},
                                 )

    async def _handle_api_gateway(self, request: web.Request) -> web.Response:
        """POST /gateway — gateway operations."""
        data = await request.json()
        action = data.get("action", "")
        from memall.gateway import export_bundle, discover_peers, list_peers, federated_retrieve
        actions = {
            "export": lambda: export_bundle(data.get("agent_name")),
            "discover": lambda: discover_peers(timeout=5),
            "peers": lambda: list_peers(),
            "federated": lambda: federated_retrieve(data.get("query"), data.get("max_peers", 3)),
        }
        fn = actions.get(action)
        if fn:
            return web.json_response(fn(),)
        port = data.get("port", 9919)
        return web.json_response(
            {"error": f"gateway action '{action}' available via aiohttp server on port {port}"},
            )

    async def _handle_api_db_optimize(self, request: web.Request) -> web.Response:
        """POST /db/optimize — analyze, vacuum, optimize."""
        return web.json_response(optimize_db(),)

    async def _handle_api_agents(self, request: web.Request) -> web.Response:
        """GET /agents — list all agents."""
        from memall.core.thin_waist import _pool_conn
        with _pool_conn() as conn:
            rows = conn.execute(
                "SELECT agent_name, COUNT(*) as cnt FROM memories WHERE agent_name != '' AND agent_name IS NOT NULL GROUP BY agent_name ORDER BY cnt DESC"
            ).fetchall()
            return web.json_response(_ok([{"name": r["agent_name"], "count": r["cnt"]} for r in rows]),
                                     )

    async def _handle_api_db_stats(self, request: web.Request) -> web.Response:
        """GET /db/stats — database statistics."""
        return web.json_response(db_stats(),)

    async def _handle_api_db_vacuum(self, request: web.Request) -> web.Response:
        """POST /db/vacuum — reclaim disk space."""
        return web.json_response(vacuum_db(),)

    async def _handle_api_debt_stats(self, request: web.Request) -> web.Response:
        """GET /debt/stats — debt dashboard statistics."""
        stats = db_stats()
        tables = stats.get("tables", {})
        total_memories = tables.get("memories", 0)
        total_edges = tables.get("edges", 0)
        total_agents = tables.get("identities", 0)
        file_size_mb = stats.get("file_size_mb", 0)
        conn = get_conn()
        try:
            level_rows = conn.execute(
                "SELECT level, COUNT(*) as cnt FROM memories WHERE level IS NOT NULL AND level != '' GROUP BY level ORDER BY level"
            ).fetchall()
            cat_rows = conn.execute(
                "SELECT category, COUNT(*) as cnt FROM memories WHERE category IS NOT NULL AND category != '' GROUP BY category ORDER BY cnt DESC"
            ).fetchall()
        finally:
            conn.close()
        level_dist = {str(r["level"]): r["cnt"] for r in level_rows}
        cat_dist = {str(r["category"]): r["cnt"] for r in cat_rows}
        archive_path = Path(str(Path.home() / ".memall" / "archive.db"))
        archive_count = 0
        if archive_path.exists():
            import sqlite3
            try:
                aconn = sqlite3.connect(str(archive_path))
                archive_count = aconn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
                aconn.close()
            except Exception:
                logger.warning("archive.db stats query failed", exc_info=True)
        cached = _load_debt_cache()
        scan = cached.get("scan", {}) if cached else {}
        counts = scan.get("counts", {}) if scan else {}
        debt = {
            "s0_count": counts.get("critical", 0), "s1_count": counts.get("major", 0),
            "s2_count": counts.get("minor", 0), "s3_count": counts.get("info", 0),
            "total": sum(counts.values()),
            "scan_time": scan.get("scan_time", "") if scan else "",
            "line_count": scan.get("line_count", 0) if scan else 0,
            "density": scan.get("density", 0) if scan else 0,
            "severity_summary": scan.get("severity_summary", "") if scan else "",
            "details": scan.get("details", []) if scan else [],
            "file_summary": scan.get("file_summary", []) if scan else [],
            "history": cached.get("history", []) if cached else [],
        }
        return web.json_response({
            "total_memories": total_memories, "total_edges": total_edges, "total_agents": total_agents,
            "file_size_mb": file_size_mb, "level_distribution": level_dist, "category_distribution": cat_dist,
            "archive_count": archive_count, "scanned": cached is not None, "debt": debt,
        },)

    async def _handle_api_debt_scan(self, request: web.Request) -> web.Response:
        """POST /debt/scan — run live debt scan."""
        try:
            project_root = Path(__file__).resolve().parent.parent.parent.parent
            scan_py = project_root / "debt" / "scan.py"
            import sys as _sys
            _sys.path.insert(0, str(project_root))
            import importlib.util
            spec = importlib.util.spec_from_file_location("debt_scanner", str(scan_py))
            scanner = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(scanner)
            result = scanner.scan_known_patterns()
            line_count = scanner.count_lines()
            counts = result["counts"]
            details = result["details"]
            total = sum(counts.values())
            kloc = line_count / 1000
            density = round(float(total) / kloc, 2) if kloc > 0 else 0
            parts = []
            for k, v in [("critical", counts.get("critical", 0)), ("major", counts.get("major", 0)), ("minor", counts.get("minor", 0)), ("info", counts.get("info", 0))]:
                if v:
                    parts.append(f"{v} {k.capitalize()}")
            severity_summary = " / ".join(parts) if parts else "All clean"
            file_map = {}
            for d in details:
                m = re.match(r'^\s*\[(\w+)\]\s*([^:]+):(\d+)\s*[—–-]\s*(.+)', d)
                if m:
                    sev = m.group(1).lower()
                    fpath = m.group(2)
                    entry = file_map.setdefault(fpath, {"critical": 0, "major": 0, "minor": 0, "info": 0})
                    if sev in entry:
                        entry[sev] += 1
            file_summary = [{"path": fpath, "total": sum(sv.values()), **sv} for fpath, sv in sorted(file_map.items(), key=lambda x: -sum(x[1].values()))]
            scan_result = {
                "scan_time": datetime.now(timezone.utc).isoformat(),
                "line_count": line_count, "counts": counts, "details": details,
                "density": density, "severity_summary": severity_summary, "file_summary": file_summary,
            }
            _save_debt_cache({"scan": scan_result})
            return web.json_response(scan_result,)
        except Exception as e:
            logger.error("debt scan failed: %s", e, exc_info=True)
            return web.json_response({"error": "debt scan failed"}, status=500)

    async def _handle_api_reflection_dashboard(self, request: web.Request) -> web.Response:
        """GET /reflection/dashboard — L6 reflection dashboard."""
        days = _safe_int(request.query.get("days", "30"))
        return web.json_response(_ok(reflection_dashboard(days=days)),)

    async def _handle_api_reflection_interact(self, request: web.Request) -> web.Response:
        """POST /reflection/interact — interact with L6 reflection."""
        body = await request.json()
        memory_id = body["memory_id"]
        action = body["action"]
        context = body.get("context", "")
        conn = get_conn()
        try:
            row = conn.execute("SELECT id, level, metadata FROM memories WHERE id = ?", (memory_id,)).fetchone()
            if not row:
                return web.json_response({"error": f"memory {memory_id} not found"}, status=404,)
            if row["level"] != "L6":
                return web.json_response({"error": f"memory {memory_id} is not L6"}, status=400,)
            meta = json.loads(row["metadata"]) if row["metadata"] else {}
            interactions = meta.get("interactions", [])
            interactions.append({"action": action, "context": context, "timestamp": datetime.now(timezone.utc).isoformat()})
            meta["interactions"] = interactions
            conn.execute("UPDATE memories SET metadata = ?, updated_at = ? WHERE id = ?",
                         (json.dumps(meta), datetime.now(timezone.utc).isoformat(), memory_id))
            conn.commit()
            return web.json_response({"status": "ok", "memory_id": memory_id, "action": action, "total_interactions": len(interactions)},
                                     )
        finally:
            conn.close()

    async def _handle_api_run_pipeline(self, request: web.Request) -> web.Response:
        """POST /pipeline/run — run memory pipeline."""
        data = await request.json() if request.can_read_body else {}
        return web.json_response(run_pipeline(
            include_reflect=data.get("include_reflect", True),
            include_distill=data.get("include_distill", True),
            include_integrate=data.get("include_integrate", True),
            include_persona=data.get("include_persona", True),
            include_archive=data.get("include_archive", True),
        ),)

    async def _handle_api_migration_status(self, request: web.Request) -> web.Response:
        """GET /migrations/status — migration status."""
        conn = get_conn()
        try:
            return web.json_response(get_migration_status(conn),)
        finally:
            conn.close()

    async def _handle_api_run_migrations(self, request: web.Request) -> web.Response:
        """POST /migrations/run — run pending migrations."""
        conn = get_conn()
        try:
            result = run_migrations(conn, db_path=str(DB_PATH))
            conn.commit()
            return web.json_response(result,)
        finally:
            conn.close()

    async def _handle_root_list_memories(self, request: web.Request) -> web.Response:
        """GET /memories — list memories (JSON for API)."""
        hours = _safe_int(request.query.get("hours", "8760"))
        limit = _safe_int(request.query.get("limit", "50"))
        offset = _safe_int(request.query.get("offset", "0"))
        level = request.query.get("level", "")
        conn = get_conn()
        try:
            where = "1=1"
            params = []
            if level:
                where += " AND level = ?"
                params.append(level)
            total = conn.execute(f"SELECT COUNT(*) FROM memories WHERE {where}", params).fetchone()[0]
            rows = conn.execute(
                f"SELECT id, content, subject as title, level, agent_name, category, project, summary, tags, created_at, updated_at, access_count, metadata FROM memories WHERE {where} ORDER BY id DESC LIMIT ? OFFSET ?",
                params + [limit, offset]
            ).fetchall()
            return web.json_response({"success": True, "data": {"items": [dict(r) for r in rows], "total": total}},
                                     )
        finally:
            conn.close()


    async def _handle_api_routes(self, request: web.Request) -> web.Response:
        """GET /api/routes — list available API routes."""
        routes_list = []
        for route in self._app.router.routes():
            if hasattr(route, "method") and hasattr(route, "resource"):
                path = str(route.resource)
                routes_list.append({"method": route.method, "path": path})
        return web.json_response({"routes": routes_list},)
