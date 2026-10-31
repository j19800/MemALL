"""Memory Explorer API — read-only endpoints backing ``frontend/explorer.html``.

The explorer UI needs more than the legacy ``/memories`` listing: multi-facet
filtering, stable pagination, a detail view with edges/thread context and
aggregate counters.  Everything here is read-only and served through the
gateway's loopback ``GET /api/*`` carve-out.

Routes (registered in ``gateway_routes``)::

    GET /explorer                          -> the explorer page
    GET /api/explorer/memories             -> filtered + paged list
    GET /api/explorer/memories/{id}        -> one memory + edges + thread
    GET /api/explorer/facets               -> facet counters for the sidebar
    GET /api/explorer/stats                -> totals + daily activity
"""

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

from aiohttp import web

from memall.core.db import pool_conn

logger = logging.getLogger("memall.gateway.explorer")

# Columns a client may sort on.  Whitelist — never interpolate user input.
_SORTABLE = {
    "id", "created_at", "updated_at", "occurred_at",
    "access_count", "level", "category", "agent_name", "confidence", "weight",
}

# Fields returned by the list endpoint (metadata is summarised, not dumped).
_LIST_COLS = (
    "id, subject, level, category, agent_name, project, summary, tags, "
    "created_at, updated_at, occurred_at, access_count, dedup_hits, "
    "visibility, confidence, weight, stream, memory_status, thread_id, "
    "content, length(content) AS content_len, metadata"
)

_DETAIL_COLS = "*"


def _safe_int(value, default: int = 0, lo: int | None = None,
              hi: int | None = None) -> int:
    try:
        num = int(value)
    except (TypeError, ValueError):
        return default
    if lo is not None:
        num = max(lo, num)
    if hi is not None:
        num = min(hi, num)
    return num


def _csv(request, name: str) -> list[str]:
    """Read a repeatable/comma-separated query param into a clean list."""
    raw = request.query.getall(name, []) or []
    values: list[str] = []
    for item in raw:
        for part in str(item).split(","):
            part = part.strip()
            if part:
                values.append(part)
    return values


def _like(term: str) -> str:
    """Escape ``LIKE`` wildcards so user input is matched literally."""
    return "%" + term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def _parse_tags(raw) -> list:
    try:
        parsed = json.loads(raw) if raw else []
    except (TypeError, ValueError):
        return []
    return [str(t) for t in parsed] if isinstance(parsed, list) else []


def _meta_summary(raw) -> dict:
    """Extract the handful of metadata keys the UI badges actually use."""
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}

    out: dict = {}
    for key in ("status", "kind", "l6_source", "source", "assignee",
                "consensus", "convergence_rule", "claimed_by", "done",
                "conclusion", "session_id", "phase", "priority", "owner",
                "verifier", "guard", "trigger", "reminder_for", "participant",
                "goal_id", "stance"):
        if key in data and data[key] not in (None, "", [], {}):
            value = data[key]
            # Versioned fields may be stored as {"value": ..., "_meta": {...}}.
            if isinstance(value, dict) and "value" in value:
                value = value["value"]
            out[key] = value if isinstance(value, (str, int, float, bool)) else str(value)[:200]

    # Goal / task lifecycle fields (migrations 033-040): acceptance criteria and
    # dependency ids are lists/strings the UI turns into jump chips.
    acceptance = data.get("acceptance")
    if isinstance(acceptance, str) and acceptance.strip():
        out["acceptance"] = acceptance.strip()[:200]

    depends = data.get("depends_on")
    if isinstance(depends, list) and depends:
        out["depends_on"] = [_safe_int(v, 0) for v in depends][:12]

    evidence = data.get("evidence")
    if isinstance(evidence, list) and evidence:
        ids = []
        for item in evidence[:12]:
            if isinstance(item, (int, float)):
                ids.append(int(item))
            elif isinstance(item, str):
                # Evidence entries are sometimes annotated ("#123 被升 L3").
                head = item.strip().lstrip("#").split()[0] if item.strip() else ""
                if head.isdigit():
                    ids.append(int(head))
        if ids:
            out["evidence"] = ids

    quality = data.get("quality")
    if isinstance(quality, dict):
        value = quality.get("value")
        if isinstance(value, dict):
            if isinstance(value.get("avg"), (int, float)):
                out["quality_avg"] = round(float(value["avg"]), 2)
            if value.get("gate"):
                out["quality_gate"] = str(value["gate"])
        elif isinstance(quality, str):
            out["quality"] = quality

    enrich = data.get("enrich")
    if isinstance(enrich, dict):
        value = enrich.get("value")
        if isinstance(value, dict):
            entities = value.get("entities")
            if isinstance(entities, list):
                out["entities"] = [str(e) for e in entities[:8]]

    original = data.get("original_level")
    if original:
        out["original_level"] = str(original)
    return out


def _snippet(text: str, terms: list[str], width: int = 320) -> str:
    """Return a short excerpt centred on the first search term hit."""
    if not text:
        return ""
    if not terms:
        return text[:width].strip()
    lowered = text.lower()
    pos = -1
    for term in terms:
        pos = lowered.find(term.lower())
        if pos >= 0:
            break
    if pos < 0:
        return text[:width].strip()
    start = max(0, pos - width // 3)
    end = min(len(text), start + width)
    prefix = "…" if start > 0 else ""
    suffix = "…" if end < len(text) else ""
    return (prefix + text[start:end].strip() + suffix)


def _build_filters(request: web.Request) -> tuple[str, list, list[str]]:
    """Translate query params into a parameterised WHERE clause + search terms."""
    where: list[str] = []
    params: list = []

    levels = _csv(request, "level")
    if levels:
        where.append("level IN (%s)" % ",".join("?" * len(levels)))
        params.extend(levels)

    for column, param in (("category", "category"), ("agent_name", "agent"),
                          ("project", "project"), ("stream", "stream")):
        values = _csv(request, param)
        if values:
            where.append("%s IN (%s)" % (column, ",".join("?" * len(values))))
            params.extend(values)

    tags = _csv(request, "tag")
    if tags:
        clauses = []
        for tag in tags:
            clauses.append(
                "(json_valid(memories.tags) AND EXISTS ("
                "SELECT 1 FROM json_each(memories.tags) WHERE json_each.value = ?))"
            )
            params.append(tag)
        where.append("(" + " OR ".join(clauses) + ")")

    statuses = _csv(request, "status")
    if statuses:
        where.append(
            "json_extract(metadata, '$.status') IN (%s)" % ",".join("?" * len(statuses))
        )
        params.extend(statuses)

    mstatus = _csv(request, "mstatus")
    if mstatus:
        where.append("memory_status IN (%s)" % ",".join("?" * len(mstatus)))
        params.extend(mstatus)

    date_from = request.query.get("from", "").strip()
    if date_from:
        where.append("substr(created_at, 1, 10) >= ?")
        params.append(date_from[:10])
    date_to = request.query.get("to", "").strip()
    if date_to:
        where.append("substr(created_at, 1, 10) <= ?")
        params.append(date_to[:10])

    has_thread = request.query.get("thread", "")
    if has_thread == "1":
        where.append("thread_id IS NOT NULL")
    elif has_thread == "0":
        where.append("thread_id IS NULL")

    # Goal lifecycle (migrations 033-040): a task card points at its objective
    # through metadata.goal_id, the objective itself carries metadata.phase.
    goal_id = _safe_int(request.query.get("goal_id", "0"), 0)
    if goal_id > 0:
        where.append("(id = ? OR json_extract(metadata, '$.goal_id') = ?)")
        params.extend([goal_id, goal_id])

    has_goal = request.query.get("has_goal", "")
    if has_goal == "1":
        where.append("json_extract(metadata, '$.goal_id') IS NOT NULL")
    elif has_goal == "0":
        where.append("json_extract(metadata, '$.goal_id') IS NULL")

    phases = _csv(request, "phase")
    if phases:
        where.append("json_extract(metadata, '$.phase') IN (%s)" % ",".join("?" * len(phases)))
        params.extend(phases)

    query = (request.query.get("q") or "").strip()
    terms: list[str] = []
    if query:
        if query.startswith("#") and query[1:].isdigit():
            where.append("id = ?")
            params.append(int(query[1:]))
        else:
            for term in query.replace("，", " ").split()[:5]:
                where.append(
                    "(content LIKE ? ESCAPE '\\' OR subject LIKE ? ESCAPE '\\' "
                    "OR summary LIKE ? ESCAPE '\\' OR category LIKE ? ESCAPE '\\')"
                )
                pattern = _like(term)
                params.extend([pattern, pattern, pattern, pattern])
                terms.append(term)

    sql = " AND ".join(where) if where else "1=1"
    return sql, params, terms


async def handle_list(request: web.Request, gw) -> web.Response:
    """GET /api/explorer/memories — filtered, sorted, paged memory listing."""
    where_sql, params, terms = _build_filters(request)

    sort_by = request.query.get("sort", "id")
    if sort_by not in _SORTABLE:
        sort_by = "id"
    order = "ASC" if request.query.get("order", "desc").lower() == "asc" else "DESC"
    # Stable tie-breaker so pagination never repeats/skips rows.
    order_sql = f"{sort_by} {order}" + (", id DESC" if sort_by != "id" else "")
    page = _safe_int(request.query.get("page", "1"), 1, lo=1)
    per_page = _safe_int(request.query.get("per_page", "50"), 50, lo=1, hi=200)
    offset = (page - 1) * per_page

    with pool_conn() as conn:
        total = conn.execute(
            f"SELECT COUNT(*) FROM memories WHERE {where_sql}", params
        ).fetchone()[0]
        rows = conn.execute(
            f"SELECT {_LIST_COLS} FROM memories WHERE {where_sql} "
            f"ORDER BY {order_sql} LIMIT ? OFFSET ?",
            params + [per_page, offset],
        ).fetchall()

    items = []
    for row in rows:
        record = dict(row)
        record["tags"] = _parse_tags(record.get("tags"))
        record["meta"] = _meta_summary(record.pop("metadata", ""))
        record["snippet"] = _snippet(record.get("content") or "", terms)
        record["content"] = None  # full body only via the detail endpoint
        items.append(record)

    return web.json_response({
        "success": True,
        "data": {
            "items": items,
            "total": total,
            "page": page,
            "per_page": per_page,
            "pages": (total + per_page - 1) // per_page if per_page else 0,
            "sort": sort_by,
            "order": order.lower(),
            "terms": terms,
        },
    })


async def handle_detail(request: web.Request, gw) -> web.Response:
    """GET /api/explorer/memories/{id} — full record with edges + thread."""
    memory_id = _safe_int(request.match_info.get("memory_id", "0"), 0)
    if memory_id <= 0:
        return web.json_response({"error": "invalid id"}, status=400)

    with pool_conn() as conn:
        row = conn.execute(
            f"SELECT {_DETAIL_COLS} FROM memories WHERE id = ?", (memory_id,)
        ).fetchone()
        if not row:
            return web.json_response({"error": "not found"}, status=404)

        memory = dict(row)
        raw_meta = memory.get("metadata") or "{}"
        memory["tags"] = _parse_tags(memory.get("tags"))
        try:
            memory["meta"] = json.loads(raw_meta) if raw_meta else {}
        except (TypeError, ValueError):
            memory["meta"] = {}
        if not isinstance(memory["meta"], dict):
            memory["meta"] = {}

        edge_rows = conn.execute(
            "SELECT e.id, e.source_id, e.target_id, e.relation_type, e.weight, "
            "e.created_at, ms.subject AS src_subject, ms.level AS src_level, "
            "mt.subject AS tgt_subject, mt.level AS tgt_level "
            "FROM edges e "
            "LEFT JOIN memories ms ON ms.id = e.source_id "
            "LEFT JOIN memories mt ON mt.id = e.target_id "
            "WHERE e.source_id = ? OR e.target_id = ? "
            "ORDER BY e.id DESC LIMIT 100",
            (memory_id, memory_id),
        ).fetchall()

        edges_out, edges_in = [], []
        for edge in edge_rows:
            edge = dict(edge)
            if edge["source_id"] == memory_id:
                edges_out.append({
                    "edge_id": edge["id"],
                    "id": edge["target_id"],
                    "relation": edge["relation_type"],
                    "weight": edge["weight"],
                    "subject": edge["tgt_subject"],
                    "level": edge["tgt_level"],
                    "created_at": edge["created_at"],
                })
            else:
                edges_in.append({
                    "edge_id": edge["id"],
                    "id": edge["source_id"],
                    "relation": edge["relation_type"],
                    "weight": edge["weight"],
                    "subject": edge["src_subject"],
                    "level": edge["src_level"],
                    "created_at": edge["created_at"],
                })

        thread_rows = []
        thread_id = memory.get("thread_id")
        if thread_id:
            thread_rows = conn.execute(
                "SELECT id, subject, level, agent_name, created_at FROM memories "
                "WHERE thread_id = ? AND id != ? ORDER BY id ASC LIMIT 60",
                (thread_id, memory_id),
            ).fetchall()

        # Goal lifecycle (migration 033+): an objective owns the task cards that
        # carry metadata.goal_id; a task links back to exactly one objective.
        goal_parent = None
        goal_children = []
        member_goal = _safe_int((memory["meta"] or {}).get("goal_id") or 0, 0)
        if member_goal:
            parent = conn.execute(
                "SELECT id, subject, level, agent_name, created_at, metadata FROM memories "
                "WHERE id = ?", (member_goal,)
            ).fetchone()
            if parent:
                pmeta = _meta_summary(parent["metadata"])
                goal_parent = {
                    "id": parent["id"], "subject": parent["subject"],
                    "level": parent["level"], "agent_name": parent["agent_name"],
                    "created_at": parent["created_at"],
                    "phase": pmeta.get("phase"), "status": pmeta.get("status"),
                }
        if memory.get("category") == "goal":
            kids = conn.execute(
                "SELECT id, subject, level, agent_name, created_at, "
                "json_extract(metadata, '$.status') AS status "
                "FROM memories WHERE json_extract(metadata, '$.goal_id') = ? "
                "ORDER BY id ASC LIMIT 60",
                (memory_id,),
            ).fetchall()
            goal_children = [dict(r) for r in kids]

    return web.json_response({
        "success": True,
        "data": {
            "memory": memory,
            "edges": {"out": edges_out, "in": edges_in},
            "thread": [dict(r) for r in thread_rows],
            "thread_id": thread_id,
            "goal": {"parent": goal_parent, "children": goal_children},
        },
    })


async def handle_facets(request: web.Request, gw) -> web.Response:
    """GET /api/explorer/facets — counters for every sidebar facet."""
    def _rows(rows):
        return [{"label": r["label"], "count": r["count"]} for r in rows]

    with pool_conn() as conn:
        total = conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
        levels = _rows(conn.execute(
            "SELECT level AS label, COUNT(*) AS count FROM memories "
            "GROUP BY level ORDER BY count DESC"
        ).fetchall())
        categories = _rows(conn.execute(
            "SELECT category AS label, COUNT(*) AS count FROM memories "
            "GROUP BY category ORDER BY count DESC LIMIT 60"
        ).fetchall())
        agents = _rows(conn.execute(
            "SELECT agent_name AS label, COUNT(*) AS count FROM memories "
            "WHERE agent_name != '' GROUP BY agent_name ORDER BY count DESC LIMIT 60"
        ).fetchall())
        projects = _rows(conn.execute(
            "SELECT project AS label, COUNT(*) AS count FROM memories "
            "WHERE project != '' GROUP BY project ORDER BY count DESC LIMIT 60"
        ).fetchall())
        streams = _rows(conn.execute(
            "SELECT stream AS label, COUNT(*) AS count FROM memories "
            "GROUP BY stream ORDER BY count DESC"
        ).fetchall())
        statuses = _rows(conn.execute(
            "SELECT json_extract(metadata, '$.status') AS label, COUNT(*) AS count "
            "FROM memories WHERE json_type(metadata, '$.status') = 'text' "
            "GROUP BY label ORDER BY count DESC LIMIT 20"
        ).fetchall())
        mstatuses = _rows(conn.execute(
            "SELECT memory_status AS label, COUNT(*) AS count "
            "FROM memories WHERE memory_status IS NOT NULL AND memory_status != '' "
            "GROUP BY memory_status ORDER BY count DESC"
        ).fetchall())
        phases = _rows(conn.execute(
            "SELECT json_extract(metadata, '$.phase') AS label, COUNT(*) AS count "
            "FROM memories WHERE json_type(metadata, '$.phase') = 'text' "
            "GROUP BY label ORDER BY count DESC LIMIT 12"
        ).fetchall())
        tag_counts: dict[str, int] = {}
        for (raw,) in conn.execute(
            "SELECT tags FROM memories WHERE tags IS NOT NULL "
            "AND tags != '[]' AND json_valid(tags)"
        ):
            for tag in _parse_tags(raw):
                tag_counts[tag] = tag_counts.get(tag, 0) + 1

    tags = [{"label": k, "count": v}
            for k, v in sorted(tag_counts.items(), key=lambda kv: -kv[1])[:40]]
    return web.json_response({
        "success": True,
        "data": {
            "total": total,
            "level": levels,
            "category": categories,
            "agent": agents,
            "project": projects,
            "stream": streams,
            "status": statuses,
            "mstatus": mstatuses,
            "phase": phases,
            "tag": tags,
        },
    })


async def handle_stats(request: web.Request, gw) -> web.Response:
    """GET /api/explorer/stats — headline totals and 60-day activity."""
    with pool_conn() as conn:
        total = conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
        edges = conn.execute("SELECT COUNT(*) FROM edges").fetchone()[0]
        today = conn.execute(
            "SELECT COUNT(*) FROM memories WHERE created_at >= ?",
            (datetime.now(timezone.utc).strftime("%Y-%m-%dT00:00:00"),),
        ).fetchone()[0]
        week = conn.execute(
            "SELECT COUNT(*) FROM memories WHERE created_at >= datetime('now', '-7 days')"
        ).fetchone()[0]
        daily = conn.execute(
            "SELECT substr(created_at, 1, 10) AS day, COUNT(*) AS count "
            "FROM memories GROUP BY day ORDER BY day DESC LIMIT 60"
        ).fetchall()

    db_path = Path(os.environ.get("USERPROFILE") or str(Path.home())) / ".memall" / "data.db"
    try:
        db_size = db_path.stat().st_size
    except OSError:
        db_size = 0

    return web.json_response({
        "success": True,
        "data": {
            "total": total,
            "edges": edges,
            "today": today,
            "week": week,
            "db_size": db_size,
            "daily": [{"day": r["day"], "count": r["count"]} for r in reversed(daily)],
        },
    })


async def handle_graph(request: web.Request, gw) -> web.Response:
    """GET /api/explorer/graph — neighbourhood of one memory over ``edges``.

    ``center`` picks the seed (highest-degree memory when omitted), ``depth``
    controls how many hops to expand and ``limit`` caps the node count so the
    browser never receives a hairball.
    """
    center = _safe_int(request.query.get("center", "0"), 0)
    depth = _safe_int(request.query.get("depth", "1"), 1, lo=1, hi=2)
    limit = _safe_int(request.query.get("limit", "60"), 60, lo=5, hi=150)

    with pool_conn() as conn:
        if center <= 0:
            row = conn.execute(
                "SELECT id FROM (SELECT source_id AS id FROM edges "
                "UNION ALL SELECT target_id FROM edges) "
                "GROUP BY id ORDER BY COUNT(*) DESC, id DESC LIMIT 1"
            ).fetchone()
            center = row["id"] if row else 0
        if center <= 0:
            return web.json_response({
                "success": True, "data": {"center": 0, "nodes": [], "links": []},
            })

        seen = {center}
        frontier = [center]
        link_map: dict = {}
        for _ in range(depth):
            if not frontier or len(seen) >= limit:
                break
            marks = ",".join("?" * len(frontier))
            params = list(frontier) * 2
            rows = conn.execute(
                "SELECT id, source_id, target_id, relation_type, weight FROM edges "
                f"WHERE source_id IN ({marks}) OR target_id IN ({marks}) "
                "ORDER BY weight DESC, id DESC LIMIT 400",
                params,
            ).fetchall()
            nxt: list = []
            for edge in rows:
                key = (edge["source_id"], edge["target_id"], edge["relation_type"])
                if key not in link_map:
                    link_map[key] = {
                        "source": edge["source_id"],
                        "target": edge["target_id"],
                        "relation": edge["relation_type"],
                        "weight": edge["weight"],
                    }
                for other in (edge["source_id"], edge["target_id"]):
                    if other not in seen and len(seen) < limit:
                        seen.add(other)
                        nxt.append(other)
            frontier = nxt

        marks = ",".join("?" * len(seen))
        node_rows = conn.execute(
            "SELECT id, subject, level, agent_name, category, project, access_count "
            f"FROM memories WHERE id IN ({marks})",
            list(seen),
        ).fetchall()

    present = {r["id"] for r in node_rows}
    links = [l for l in link_map.values()
             if l["source"] in present and l["target"] in present]

    degree: dict = {}
    for link in links:
        degree[link["source"]] = degree.get(link["source"], 0) + 1
        degree[link["target"]] = degree.get(link["target"], 0) + 1

    nodes = []
    for row in node_rows:
        node = dict(row)
        node["subject"] = (node.get("subject") or "")[:160]
        node["degree"] = degree.get(node["id"], 0)
        node["center"] = node["id"] == center
        nodes.append(node)

    return web.json_response({
        "success": True,
        "data": {
            "center": center,
            "depth": depth,
            "nodes": nodes,
            "links": links,
        },
    })


async def handle_timeline(request: web.Request, gw) -> web.Response:
    """GET /api/explorer/timeline — daily buckets + per-day entries.

    Reuses the list filters so the timeline always mirrors the current
    sidebar selection instead of showing a disconnected feed.
    """
    where_sql, params, _terms = _build_filters(request)
    limit = _safe_int(request.query.get("limit", "300"), 300, lo=20, hi=1000)
    cond = f"({where_sql}) AND created_at IS NOT NULL AND created_at != ''"

    with pool_conn() as conn:
        days = conn.execute(
            "SELECT substr(created_at, 1, 10) AS day, COUNT(*) AS count "
            f"FROM memories WHERE {cond} GROUP BY day ORDER BY day DESC LIMIT 180",
            params,
        ).fetchall()
        rows = conn.execute(
            "SELECT id, subject, level, category, agent_name, project, "
            "created_at, summary, thread_id "
            f"FROM memories WHERE {cond} ORDER BY created_at DESC LIMIT ?",
            params + [limit],
        ).fetchall()

    buckets: dict = {}
    for row in rows:
        item = dict(row)
        day = str(item.get("created_at") or "")[:10]
        buckets.setdefault(day, []).append(item)

    return web.json_response({
        "success": True,
        "data": {
            "days": [{"day": r["day"], "count": r["count"]} for r in days],
            "items": buckets,
            "limit": limit,
            "returned": sum(len(v) for v in buckets.values()),
        },
    })


async def handle_root(request: web.Request, gw) -> web.Response:
    """GET / — send the root entry point to the memory explorer.

    The legacy dashboard page stays reachable at ``/classic`` so existing
    bookmarks and agent scripts keep working.
    """
    raise web.HTTPFound("/explorer")


async def handle_page(request: web.Request, gw) -> web.Response:
    """GET /explorer — serve the memory explorer single-page app."""
    page = Path(__file__).resolve().parent.parent.parent / "frontend" / "explorer.html"
    if not page.exists():
        return web.json_response({"error": "explorer page missing"}, status=404)
    return web.Response(
        text=page.read_text(encoding="utf-8"),
        content_type="text/html",
        headers={"Cache-Control": "no-store"},
    )
