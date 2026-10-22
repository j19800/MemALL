import json
from memall.mcp.registry import registry, ToolDef

from . import capture
from . import retrieve
from . import graph
from . import timeline
from . import persona
from . import memory_write
from . import session
from . import discussion
from . import pipeline
from . import federation
from . import hub
from . import manage
from . import gateway
from . import reflect
from . import onboarding
from . import index
from . import distill
from . import msg


# ──────────────────────────────────────────────
# 1. memall_write — capture, smart_store, batch, update, forget, ops, connect
# ──────────────────────────────────────────────

def _handle_write(args: dict) -> str:
    args = dict(args)  # shallow copy to avoid mutating caller's dict
    action = args.pop("action", "")
    if action == "capture":
        return capture.handle(args)
    elif action == "smart_store":
        return memory_write.handle_smart_store(args)
    elif action == "store_batch":
        return memory_write.handle_store_batch(args)
    elif action == "batch_capture":
        return memory_write.handle_batch_capture(args)
    elif action == "update":
        return memory_write.handle_update(args)
    elif action == "connect":
        return graph.handle_connect(args)
    elif action == "forget":
        # forget has its own sub_action vocabulary; ``sub_action`` is kept as a
        # deprecated alias so older callers keep working.
        sub = args.pop("forget_sub_action", None) or args.pop("sub_action", "expired")
        args["action"] = sub
        return manage.handle_forget(args)
    elif action == "ops":
        sub = args.pop("ops_sub_action", None) or args.pop("sub_action", "")
        args["action"] = sub
        return manage.handle_ops(args)
    elif action in ("quick", "记一下", "快速记", "随手记"):
        # Quick capture — auto-fill, just needs content
        content = args.pop("content", "")
        if not content:
            return json.dumps({"error": "content is required"})
        args.setdefault("agent_name", args.get("agent_name", "workbuddy"))
        args.setdefault("level", "P2")
        args.setdefault("category", "general")
        # Auto-infer project from agent + content via the canonical inference
        # (single source of truth in memall.core.project_infer) — replaces the
        # previous divergent hard-coded kw_map in this handler.
        project = args.get("project", "")
        if not project:
            from memall.core.project_infer import infer_project
            project = infer_project(
                agent_name=args.get("agent_name", "workbuddy"),
                category=args.get("category", "general"),
                content=content,
            )
        args.setdefault("project", project)
        # Generate subject from first line
        first_line = content.strip().split("\n")[0][:50]
        args.setdefault("subject", first_line)
        args["content"] = content
        return capture.handle(args)
    elif action == "record_interaction":
        # Search-interaction telemetry (clicks / dwells / impressions).
        # Lives on memall_write — it WRITES a row to search_interactions, so it
        # must not sit behind memall_read's readOnlyHint=true.
        return retrieve.handle_record_interaction(args)
    elif action == "task_claim":
        # Accept a task card addressed to me (design #1000007).  Refuses a
        # claim from any agent the card was not addressed to.
        from memall.mcp.tools import tasks_handlers
        return tasks_handlers.handle_task_claim(args, args.get("agent_name", ""))
    raise ValueError(f"memall_write: unknown action '{action}'")


registry.register(ToolDef(
    name="memall_write",
    description="Write/capture/search/update memories, create connections, manage forgetting & ops. Actions: capture | smart_store | store_batch | batch_capture | update | connect | forget | ops | record_interaction | quick (快速记)",
    input_schema={"type": "object", "properties": {
        "action": {"type": "string", "enum": ["capture", "smart_store", "store_batch", "batch_capture", "update", "connect", "forget", "ops", "record_interaction", "task_claim", "quick", "记一下", "快速记", "随手记"]},
        "content": {"type": "string", "description": "Memory content"},
        "owner": {"type": "string"},
        "agent_name": {"type": "string"},
        "subject": {"type": "string"},
        "summary": {"type": "string"},
        "project": {"type": "string"},
        "category": {"type": "string"},
        "level": {"type": "string", "enum": ["P0", "P1", "P2", "L1", "L2", "L3", "L4", "L5", "L6", "L7", "L8", "L9", "L10", "L11"]},
        "metadata": {"type": "string", "description": "JSON metadata string"},
        "thread_id": {"type": "integer", "description": "Parent memory ID for thread context"},
        "memory_id": {"type": "integer", "description": "Memory ID (update/connect/forget/record_interaction)"},
        "source_id": {"type": "integer"},
        "target_id": {"type": "integer"},
        "relation_type": {"type": "string", "enum": ["extends", "contradicts", "refines", "cites", "supersedes"]},
        "weight": {"type": "number"},
        "forget_sub_action": {"type": "string", "enum": ["expired", "low_value", "review", "stats", "all"], "description": "Sub-action for forget"},
        "ops_sub_action": {"type": "string", "enum": ["merge", "split", "tag", "batch_tag", "archive", "restore", "dedup", "undo"], "description": "Sub-action for ops"},
        "sub_action": {"type": "string", "description": "Deprecated alias — use forget_sub_action / ops_sub_action"},
        "days": {"type": "integer"},
        "confirm": {"type": "boolean", "description": "Required to execute destructive ops (forget expired/low_value/all, ops merge/split/dedup/archive)"},
        "dry_run": {"type": "boolean", "description": "Preview-only pass for ops archive/dedup"},
        "tags": {"type": "array", "items": {"type": "string"}},
        # ── capture 支持、此前既未声明也未上报的字段 ──
        # 客户端照 schema 传了也不会生效（校验器在 handler 前剥离），
        # 与 203f03a 修的读侧 project 缺陷同源。
        "creator": {"type": "string", "description": "Who created this memory (provenance)"},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "visibility": {"type": "string", "enum": ["public", "shared", "family", "trusted", "private"], "description": "Access level; non-human agents are clamped to their allowed write level"},
        "stream": {"type": "string", "description": "knowledge (default) or ledger"},
        "occurred_at": {"type": "string", "description": "Event time (ISO 8601); defaults to now"},
        "supersedes": {"type": "array", "items": {"type": "integer"}, "description": "Ids of memories this one supersedes"},
        "dedup_threshold": {"type": "number"},
        "items": {"type": "array", "description": "Items for store_batch and batch_capture"},
        "delimiter": {"type": "string"},
        "mode": {"type": "string", "enum": ["add", "set", "remove"]},
        "threshold": {"type": "number"},
        # record_interaction — search telemetry that feeds personalisation
        "interaction_type": {"type": "string", "enum": ["click", "dwell", "impression", "copy", "skip"], "description": "Kind of search interaction (record_interaction)"},
        "dwell_ms": {"type": "integer", "description": "Dwell time in ms (record_interaction, interaction_type=dwell)"},
        "rank_position": {"type": "integer", "description": "1-based rank of the clicked result (record_interaction)"},
        "session_id": {"type": "string"},
    }, "required": ["action"]},
    handler=_handle_write,
    annotations={"readOnlyHint": False, "idempotentHint": False},
))


# ──────────────────────────────────────────────
# 2. memall_read — retrieve, search, trace, traverse, timeline, fed_query/conflicts
# ──────────────────────────────────────────────

def _handle_read(args: dict) -> str:
    args = dict(args)
    action = args.pop("action", "")
    if action == "retrieve":
        return retrieve.handle_retrieve(args)
    elif action == "vector_search":
        return retrieve.handle_vector_search(args)
    elif action == "hybrid_search":
        return retrieve.handle_hybrid_search(args)
    elif action == "batch_search":
        return retrieve.handle_batch_search(args)
    elif action == "search":
        return retrieve.handle_unified_search(args)
    elif action == "unified_search":
        return retrieve.handle_unified_search(args)
    elif action in ("chat", "nl_search", "自然语言搜索"):
        # Natural language chat search — always uses hybrid mode
        args["mode"] = "hybrid"
        args["top_k"] = args.get("top_k", 15)
        return retrieve.handle_unified_search(args)
    elif action == "trace":
        return retrieve.handle_trace(args)
    elif action == "traverse":
        return graph.handle_traverse(args)
    elif action == "timeline":
        return timeline.handle(args)
    elif action == "fed_query":
        return federation.handle_query(args)
    elif action == "fed_conflicts":
        return federation.handle_conflicts(args)
    elif action == "recap":
        return retrieve.handle_recap(args)
    elif action == "task_list":
        # Only the cards addressed to me (design #1000007).
        from memall.mcp.tools import tasks_handlers
        return tasks_handlers.handle_task_list(args, args.get("agent_name", ""))
    raise ValueError(f"memall_read: unknown action '{action}'")


registry.register(ToolDef(
    name="memall_read",
    description="Search/retrieve memories, trace provenance, explore graph, timeline, federated queries, natural language chat search, post-compaction recap. Actions: retrieve | search | unified_search | chat | vector_search | hybrid_search | batch_search | trace | traverse | timeline | fed_query | fed_conflicts | recap",
    input_schema={"type": "object", "properties": {
        "action": {"type": "string", "enum": ["retrieve", "search", "unified_search", "chat", "nl_search", "自然语言搜索", "vector_search", "hybrid_search", "batch_search", "trace", "traverse", "timeline", "fed_query", "fed_conflicts", "recap", "task_list"]},
        "query": {"type": "string", "description": "Search query"},
        "queries": {"type": "array", "items": {"type": "string"}, "description": "Queries for batch_search"},
        "mode": {"type": "string", "enum": ["auto", "direct", "fts5", "vector", "hybrid"]},
        "top_k": {"type": "integer"},
        "rrf_k": {"type": "integer"},
        "category": {"type": "string"},
        "level": {"type": "string"},
        "owner": {"type": "string"},
        "agent_name": {"type": "string"},
        "viewer": {"type": "string", "description": "Viewer identity for trust filtering / personalisation"},
        "project": {"type": "string"},
        "limit": {"type": "integer"},
        "tags": {"type": "array", "items": {"type": "string"}, "maxItems": 32, "description": "Exact-match tag filter (any by default)"},
        "tags_all": {"type": "boolean", "description": "Require every listed tag instead of any"},
        "rerank": {"type": "boolean"},
        "node_id": {"type": "integer", "description": "Starting memory ID for traverse"},
        "depth": {"type": "integer", "maximum": 5},
        "relation_filter": {"type": "string"},
        "memory_id": {"type": "integer", "description": "Memory ID for trace"},
        "hours": {"type": "integer"},
        "trust_level": {"type": "string"},
        # ── Query enhancement layer ──
        "facet_fields": {"type": "array", "items": {"type": "string"}, "maxItems": 8, "description": "Fields to aggregate as facets: level|category|project|agent_name|owner|stream|visibility|memory_status|confidence, or ['auto']"},
        "facet_fields_str": {"type": "string", "description": "Comma-separated facet fields (clients that cannot send arrays)"},
        "expand_query": {"type": "boolean", "description": "Expand query with synonyms / recent query history"},
        "suggest": {"type": "boolean", "description": "Return query completions from the corpus vocabulary"},
        "explain": {"type": "boolean", "description": "Return a per-result score breakdown (matched terms, ranks, boosts)"},
        "cluster": {"type": "boolean", "description": "Group results into topic clusters"},
        "personalize": {"type": "boolean", "description": "Rank results by per-agent interaction affinity"},
        "context_boost": {"type": "boolean", "description": "Boost results matching the active project / category"},
        "session_id": {"type": "string", "description": "Enable per-session novelty demotion for repeat queries"},
    }, "required": ["action"]},
    handler=_handle_read,
    annotations={"readOnlyHint": True, "idempotentHint": True},
))


# ──────────────────────────────────────────────
# 3. memall_persona — persona, profile, identity, ask
# ──────────────────────────────────────────────

def _handle_persona(args: dict) -> str:
    args = dict(args)
    action = args.pop("action", "")
    if action == "persona":
        return persona.handle_persona(args)
    elif action == "persona_profile":
        return persona.handle_persona_profile(args)
    elif action == "identity":
        return persona.handle_identity(args)
    elif action == "ask":
        return persona.handle_ask(args)
    elif action in ("profile_preload", "预加载画像", "加载画像", "画像"):
        return persona.handle_profile_preload(args)
    elif action in ("profile_search", "搜画像", "搜索画像", "查画像", "查找画像"):
        return persona.handle_profile_search(args)
    elif action in ("foresight", "前瞻", "预测", "猜我需要", "下一步"):
        return persona.handle_foresight(args)
    raise ValueError(f"memall_persona: unknown action '{action}'")


registry.register(ToolDef(
    name="memall_persona",
    description="Query agent persona/profile/identity, ask digital twin, preload profile, search profiles, foresight prediction. Actions: persona | persona_profile | identity | ask | profile_preload (预加载画像) | profile_search (搜画像) | foresight (前瞻/预测)",
    input_schema={"type": "object", "properties": {
        "action": {"type": "string", "enum": ["persona", "persona_profile", "identity", "ask",
            "profile_preload", "预加载画像", "加载画像", "画像",
            "profile_search", "搜画像", "搜索画像", "查画像", "查找画像",
            "foresight", "前瞻", "预测", "猜我需要", "下一步"]},
        "agent_name": {"type": "string", "description": "Agent name to query"},
        "query": {"type": "string", "description": "Search query for profile_search/foresight"},
        "question": {"type": "string", "description": "Question for ask action"},
        "mode": {"type": "string", "enum": ["stance", "pattern", "predict"]},
        "subject": {"type": "string", "description": "Subject/agent context"},
        "layer": {"type": "string", "enum": ["1", "2", "3", "all"]},
        "evolution": {"type": "boolean"},
        "window_days": {"type": "integer"},
    }, "required": ["action"]},
    handler=_handle_persona,
    annotations={"readOnlyHint": True, "idempotentHint": True},
))


# ──────────────────────────────────────────────
# 4. memall_discussion — create, respond, status
# ──────────────────────────────────────────────

def _handle_discussion(args: dict) -> str:
    args = dict(args)
    action = args.pop("action", "")
    if action == "create":
        return discussion.handle_create(args)
    elif action == "respond":
        return discussion.handle_respond(args)
    elif action == "status":
        return discussion.handle_status(args)
    raise ValueError(f"memall_discussion: unknown action '{action}'")


registry.register(ToolDef(
    name="memall_discussion",
    description="Create multi-agent discussions, record stances, check status. Actions: create | respond | status",
    input_schema={"type": "object", "properties": {
        "action": {"type": "string", "enum": ["create", "respond", "status"]},
        "title": {"type": "string", "description": "Discussion title (create)"},
        "background": {"type": "string", "description": "Problem description"},
        "options": {"type": "array", "items": {"type": "string"}, "description": "Solution options"},
        "participants": {"type": "array", "items": {"type": "string"}},
        "open_questions": {"type": "array", "items": {"type": "string"}},
        "recommendation": {"type": "string"},
        "convergence_rule": {"type": "string", "enum": ["unanimous", "majority", "any"]},
        "timeout_hours": {"type": "integer"},
        "action_items": {"type": "array", "items": {"type": "object", "properties": {
            "assigned_to": {"type": "string"},
            "description": {"type": "string"},
        }, "required": ["assigned_to", "description"]}},
        "discussion_id": {"type": "integer", "description": "Discussion memory ID (respond/status)"},
        "agent_name": {"type": "string", "description": "Agent responding"},
        "stance": {"type": "string", "enum": ["agree", "disagree", "abstain"]},
        "arguments": {"type": "string", "description": "Reasoning text"},
        "round_num": {"type": "integer"},
    }, "required": ["action"]},
    handler=_handle_discussion,
    annotations={"readOnlyHint": False, "idempotentHint": False},
))


# ──────────────────────────────────────────────
# 5. memall_federation — publish, inject, extract, deliver
# ──────────────────────────────────────────────

def _handle_federation(args: dict) -> str:
    args = dict(args)
    action = args.pop("action", "")
    if action == "query":
        return federation.handle_query(args)
    elif action == "publish":
        return federation.handle_publish(args)
    elif action == "conflicts":
        return federation.handle_conflicts(args)
    elif action == "inject":
        return federation.handle_inject(args)
    elif action == "extract":
        return federation.handle_extract(args)
    elif action == "deliver":
        return federation.handle_deliver(args)
    raise ValueError(f"memall_federation: unknown action '{action}'")


registry.register(ToolDef(
    name="memall_federation",
    description="Cross-agent knowledge federation: query, publish, resolve conflicts, inject/extract, push deliver. Actions: query | publish | conflicts | inject | extract | deliver",
    input_schema={"type": "object", "properties": {
        "action": {"type": "string", "enum": ["query", "publish", "conflicts", "inject", "extract", "deliver"]},
        "query": {"type": "string", "description": "Search keyword"},
        "agent_name": {"type": "string"},
        "category": {"type": "string"},
        "trust_level": {"type": "string", "enum": ["trusted", "family", "shared", "public"]},
        "project": {"type": "string"},
        "limit": {"type": "integer"},
        "memory_id": {"type": "integer"},
        "source_agent": {"type": "string"},
        "session_id": {"type": "string"},
        "target_agent": {"type": "string"},
        "content": {"type": "string"},
        "event_type": {"type": "string"},
        "source": {"type": "string"},
    }, "required": ["action"]},
    handler=_handle_federation,
    annotations={"readOnlyHint": False, "idempotentHint": False},
))


# ──────────────────────────────────────────────
# 6. memall_system — pipeline, distill, gateway, hub, session, db,
#                    security, adaptive, onboarding, reflect, index_rebuild
# ──────────────────────────────────────────────

def _handle_system(args: dict) -> str:
    args = dict(args)
    action = args.pop("action", "")
    if action == "run_pipeline":
        return pipeline.handle(args)
    elif action == "distill":
        sub = args.pop("distill_sub_action", None) or args.pop("sub_action", "list")
        args["action"] = sub
        return distill.handle(args)
    elif action == "gateway":
        sub = args.pop("gateway_sub_action", None) or args.pop("sub_action", "")
        args["action"] = sub
        return gateway.handle(args)
    elif action == "hub_connect":
        return hub.handle_connect(args)
    elif action == "hub_sync":
        return hub.handle_sync(args)
    elif action == "session_start":
        return session.handle_session_start(args)
    elif action == "session_end":
        return session.handle_session_end(args)
    elif action == "session_summary":
        return session.handle_session_summary(args)
    elif action == "db":
        sub = args.pop("db_sub_action", None) or args.pop("sub_action", "")
        args["action"] = sub
        return manage.handle_db(args)
    elif action == "security":
        sub = args.pop("security_sub_action", None) or args.pop("sub_action", "")
        args["action"] = sub
        return manage.handle_security(args)
    elif action == "adaptive":
        sub = args.pop("adaptive_sub_action", None) or args.pop("sub_action", "")
        args["action"] = sub
        return manage.handle_adaptive(args)
    elif action == "onboarding":
        sub = args.pop("onboarding_sub_action", None) or args.pop("sub_action", "")
        args["action"] = sub
        return onboarding.handle(args)
    elif action == "reflect":
        sub = args.pop("reflect_sub_action", None) or args.pop("sub_action", "")
        args["action"] = sub
        return reflect.handle(args)
    elif action == "index_rebuild":
        return index.handle(args)
    elif action == "import":
        import json, pathlib
        from memall.auto_capture import import_from_jsonl, import_from_csv, import_from_mem0
        fmt = args.get("format", "jsonl")
        path = args.get("path", "")
        agent = args.get("agent_name", "imported")
        if not path or not isinstance(path, str):
            return json.dumps({"error": "path required"}, ensure_ascii=False)
        p = pathlib.Path(path)
        if p.suffix.lower() not in (".jsonl", ".json", ".csv"):
            return json.dumps({"error": "only .jsonl/.json/.csv files can be imported"}, ensure_ascii=False)
        if not p.is_file():
            return json.dumps({"error": "file does not exist"}, ensure_ascii=False)
        if fmt == "mem0":
            result = import_from_mem0(str(p), agent)
        elif fmt == "csv":
            result = import_from_csv(str(p), agent)
        else:
            result = import_from_jsonl(str(p), agent)
        return json.dumps(result, ensure_ascii=False)
    elif action in ("digest", "每日摘要", "日报", "总结"):
        # Daily digest — count today's memories by category with content snippets
        import json, datetime
        from memall.core.db import pool_conn
        # Days roll over at UTC+8 local midnight; created_at is stored UTC.
        now_utc8 = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=8)
        today_date = now_utc8.date()
        today_start = datetime.datetime(today_date.year, today_date.month, today_date.day) \
                     - datetime.timedelta(hours=8)
        today_end = today_start + datetime.timedelta(hours=24) - datetime.timedelta(seconds=1)
        # created_at rows come in two lexical formats:
        #   canonical now_iso(): "2026-10-18T12:34:56+00:00"  (T-separated)
        #   legacy writers:      "2026-10-18 12:34:56"        (space-separated)
        # Lexicographic BETWEEN only matches the format the bounds were built
        # with, so query both — a single-format bound silently returned only
        # legacy rows (or none at all for canonical-only ranges).
        t_lo = today_start.strftime("%Y-%m-%dT%H:%M:%S+00:00")
        t_hi = today_end.strftime("%Y-%m-%dT%H:%M:%S+00:00")
        s_lo = today_start.strftime("%Y-%m-%d %H:%M:%S")
        s_hi = today_end.strftime("%Y-%m-%d %H:%M:%S")
        span_sql = ("(created_at BETWEEN ? AND ? OR created_at BETWEEN ? AND ?) "
                    "AND COALESCE(memory_status, '') != 'archived'")
        span_args = (t_lo, t_hi, s_lo, s_hi)
        with pool_conn() as conn:
            rows = conn.execute(
                f"SELECT category, level, COUNT(*) as cnt FROM memories "
                f"WHERE {span_sql} "
                f"GROUP BY category, level ORDER BY cnt DESC LIMIT 15",
                span_args
            ).fetchall()
            total = sum(r[2] for r in rows)
            if not rows:
                return json.dumps({"date": today_date.isoformat(), "total": 0, "message": "今天暂无新记忆"})
            categories = {}
            for cat, lvl, cnt in rows:
                categories.setdefault(cat, {"total": 0, "levels": {}, "samples": []})
                categories[cat]["total"] += cnt
                categories[cat]["levels"][lvl] = cnt
            # Add sample content for each category
            for cat in categories:
                samples = conn.execute(
                    f"SELECT subject, summary, content FROM memories "
                    f"WHERE category = ? AND {span_sql} AND subject != '' "
                    f"ORDER BY created_at DESC LIMIT 3",
                    (cat,) + span_args
                ).fetchall()
                categories[cat]["samples"] = [
                    {"subject": s[0], "summary": (s[1] or s[2])[:80] if s[2] else ""}
                    for s in samples
                ]
        # Audit P0-1: the pipeline backlog was invisible — events piled up
        # silently when the lightweight pipeline could not keep up (or was
        # not running at all).  Surface it in the daily report.
        try:
            from memall.pipeline.fallback import backlog_report
            backlog = backlog_report()
        except Exception:
            backlog = {"unconsumed_events": None, "warning": False, "message": ""}
        return json.dumps({
            "date": today_date.isoformat(), "total": total,
            "categories": categories,
            "pipeline_backlog": backlog,
        }, ensure_ascii=False, default=str)
    elif action in ("hot", "热门", "热点", "热榜"):
        # Hot topics — most accessed or recently active memories
        from memall.core.db import get_conn
        import json, datetime
        conn = get_conn()
        try:
            now_utc = datetime.datetime.now(datetime.timezone.utc)
            # The cutoff variable is named week_ago but was set to now(), so
            # "created_at > week_ago" matched nothing and hot_topics was always
            # empty in production.  Subtract the window it claims to cover.
            week_ago = (now_utc - datetime.timedelta(days=7)).isoformat()
            rows = conn.execute(
                "SELECT id, subject, category, level, access_count, summary FROM memories "
                "WHERE created_at > ? AND access_count > 0 "
                # Archived rows are soft-deleted — they must not resurface as
                # "hot topics" (a run of archived test rows did exactly that).
                "AND COALESCE(memory_status, '') != 'archived' "
                "ORDER BY access_count DESC, created_at DESC LIMIT 10",
                (week_ago,)
            ).fetchall()
        finally:
            # audit P2-11: without try/finally an exception here leaked the
            # connection (and its WAL handle) for the life of the process.
            conn.close()
        hot = [{"id": r[0], "subject": r[1], "category": r[2], "level": r[3], "views": r[4], "summary": (r[5] or "")[:80]} for r in rows]
        return json.dumps({"period": "7天", "total": len(hot), "hot_topics": hot}, ensure_ascii=False)
    raise ValueError(f"memall_system: unknown action '{action}'")


registry.register(ToolDef(
    name="memall_system",
    description="Pipeline, sessions, gateway, hub sync, DB maintenance, security, adaptive, onboarding, reflection, index rebuild, daily digest, hot topics. Actions: run_pipeline | distill | gateway | hub_connect | hub_sync | session_start | session_end | session_summary | db | security | adaptive | onboarding | reflect | index_rebuild | digest (每日摘要/日报/总结) | hot (热门/热点/热榜)",
    input_schema={"type": "object", "properties": {
        "action": {"type": "string", "enum": ["run_pipeline", "distill", "gateway", "hub_connect", "hub_sync", "session_start", "session_end", "session_summary", "db", "security", "adaptive", "onboarding", "reflect", "index_rebuild", "digest", "每日摘要", "日报", "总结", "hot", "热门", "热点", "热榜"]},
        # Per-action sub-action vocabularies.  Each action accepts ONLY its own
        # field — a single shared ``sub_action`` enum used to mix seven
        # vocabularies, so almost every declared value was invalid for almost
        # every action.  ``sub_action`` is still accepted as a deprecated alias.
        "distill_sub_action": {"type": "string", "enum": ["list", "summarize"], "description": "distill only"},
        "gateway_sub_action": {"type": "string", "enum": ["status", "start", "stop", "export", "import", "discover", "pair", "peers", "federated"], "description": "gateway only"},
        "db_sub_action": {"type": "string", "enum": ["optimize", "stats", "vacuum", "archive_stats", "archive_vacuum", "backfill_thread", "backfill_project", "backfill_tags", "dedupe_l9", "dedupe_l10"], "description": "db only"},
        "security_sub_action": {"type": "string", "enum": ["audit", "permit", "check", "score", "list"], "description": "security only"},
        "adaptive_sub_action": {"type": "string", "enum": ["clean", "index", "distill", "all", "report"], "description": "adaptive only"},
        "onboarding_sub_action": {"type": "string", "enum": ["status", "start", "reset", "submit_step", "skip"], "description": "onboarding only"},
        "reflect_sub_action": {"type": "string", "enum": ["agree", "disagree", "probe"], "description": "reflect only"},
        "sub_action": {"type": "string", "description": "Deprecated alias — use the <action>_sub_action field that matches your action"},
        "confirm": {"type": "boolean", "description": "Required to execute destructive db ops (vacuum, archive_vacuum, backfill_*, dedupe_l9/l10)"},
        "dry_run": {"type": "boolean", "description": "Preview-only pass for db backfill_*/dedupe_*"},
        "session_id": {"type": "string", "description": "Session ID"},
        "agent_name": {"type": "string"},
        "auto_inject": {"type": "boolean"},
        "auto_extract": {"type": "boolean"},
        "include_reflect": {"type": "boolean"},
        "include_distill": {"type": "boolean"},
        "include_integrate": {"type": "boolean"},
        "include_persona": {"type": "boolean"},
        "timeout": {"type": "integer"},
        "limit": {"type": "integer"},
        "group_id": {"type": "integer"},
        "summary": {"type": "string"},
        "insight": {"type": "string"},
        "gap": {"type": "string"},
        "next": {"type": "string"},
        "port": {"type": "integer"},
        "file_path": {"type": "string"},
        "address": {"type": "string"},
        "max_peers": {"type": "integer"},
        "direction": {"type": "string", "enum": ["bidirectional", "to_hub", "from_hub"]},
        "force": {"type": "boolean", "description": "Force index rebuild"},
        "user_id": {"type": "string"},
        "step": {"type": "integer"},
        "input_data": {"type": "object", "additionalProperties": True},
        "memory_id": {"type": "integer"},
        "context": {"type": "string"},
        "level": {"type": "string", "enum": ["public", "trusted", "private"]},
        "requester": {"type": "string"},
        "target": {"type": "string"},
    }, "required": ["action"]},
    handler=_handle_system,
    annotations={"readOnlyHint": False, "idempotentHint": False},
))


# ──────────────────────────────────────────────
# 7. memall_hooks_recent — peek into recent hook activity
# ──────────────────────────────────────────────

def _handle_hooks(args: dict) -> str:
    from memall.mcp.hook_effects import peek_recent, format_activity
    n = args.get("n", 10)
    events = peek_recent(n)
    activity = format_activity(events) if events else None
    if activity:
        return json.dumps({"activity": activity})
    return json.dumps({"activity": "No recent hook activity to show."})


registry.register(ToolDef(
    name="memall_hooks_recent",
    description="Show recent async hook activity (pipeline runs, notifications, reminder checks, etc.)",
    input_schema={"type": "object", "properties": {
        "n": {"type": "integer", "description": "Number of recent events (default 10)"},
    }},
    handler=_handle_hooks,
    annotations={"readOnlyHint": True, "idempotentHint": True},
))


# ──────────────────────────────────────────────
# 7. memall_msg — 实时总线（memall-bus）投递
# ──────────────────────────────────────────────
# L5 任务卡是唯一事实源；总线只负责「告诉对方有这张卡」。载荷建议只带
# memory_id 指针，正文用 memall_read 取 —— 这样两个库不会各存一份任务
# 状态，重复投递也能按 memory_id 幂等去重。

registry.register(ToolDef(
    name="memall_msg",
    description=(
        "Real-time delivery over the memall-bus (an independent resident "
        "process). The L5 task card stays the single source of truth — the "
        "bus carries a POINTER (memory_id), not the task body. Actions: "
        "send | inbox | ack | status"
    ),
    input_schema={"type": "object", "properties": {
        "action": {"type": "string", "enum": ["send", "inbox", "ack", "status"]},
        "agent_name": {"type": "string", "description": "Calling agent (also the bus identity)"},
        "to": {"type": "string", "description": "Recipient agent (send action)"},
        "payload": {"type": "object", "description": "Message payload — prefer {memory_id: N}"},
        "type": {"type": "string", "description": "Message type: task | ack | report | verdict | msg"},
        "ids": {"type": "array", "items": {"type": "integer"},
                "description": "Message ids to mark read (ack action)"},
    }},
    handler=msg.handle,
    annotations={"readOnlyHint": False, "idempotentHint": False},
))
