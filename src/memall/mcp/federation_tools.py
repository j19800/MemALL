"""Federation MCP tools — query / write shared_memories, auto-inject, auto-extract.

Phase 8: MCP 对接 agent-hub.

Tools exposed:
- memall_federation (action=fed_query): search shared_memories across agents
- memall_federation (action=fed_publish): publish memory to shared_memories
- memall_federation (action=fed_conflicts): list unresolved conflicts
- memall_fed_inject: on session_start, inject Agent Profile + semantic fragments
- memall_fed_extract: on session_end, extract facts to shared_memories

REDACTED: <private>...</private> content stripped before publishing.
"""

import json
import logging
import re
import sqlite3
from datetime import datetime, timezone

from memall.core.db import get_conn
from memall.core.nlp import compute_tfidf, cosine_sim
from memall.pipeline.convergence import check_pending_discussions
from memall.federation.family import get_family_db_path, init_family_db
from memall.federation.conflict import detect_conflicts, list_conflicts
from memall.pipeline.convergence import check_pending_discussions

logger = logging.getLogger(__name__)

_REDACT_RE = re.compile(r'<private>.*?</private>', re.DOTALL | re.IGNORECASE)


def redact_content(content: str) -> str:
    """Strip <private>...</private> tagged sections."""
    return _REDACT_RE.sub('[REDACTED]', content)


def _get_family_conn():
    db_path = get_family_db_path()
    init_family_db()
    conn = sqlite3.connect(str(db_path), timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def fed_query(query: str = "", agent_name: str = "", category: str = "",
              trust_level: str = "", project: str = "", limit: int = 20) -> dict:
    """Query shared_memories across agents."""
    conn = _get_family_conn()
    try:
        where = ["1=1"]
        params = []

        if agent_name:
            where.append("source_agent = ?")
            params.append(agent_name)
        if category:
            where.append("category = ?")
            params.append(category)
        if trust_level:
            where.append("trust_level = ?")
            params.append(trust_level)
        if project:
            where.append("project = ?")
            params.append(project)
        if query:
            where.append("content LIKE ?")
            params.append(f"%{query}%")

        sql = f"SELECT id, original_id, source_agent, content, category, level, project, trust_level, published_at FROM shared_memories WHERE {' AND '.join(where)} ORDER BY published_at DESC LIMIT ?"
        params.append(limit)
        rows = conn.execute(sql, params).fetchall()
        results = []
        for r in rows:
            results.append({
                "id": r["id"],
                "original_id": r["original_id"],
                "source_agent": r["source_agent"],
                "content": redact_content(r["content"][:500]),
                "category": r["category"],
                "level": r["level"],
                "project": r["project"],
                "trust_level": r["trust_level"],
                "published_at": r["published_at"],
            })
        return {"query": query, "results": results, "total": len(results)}
    finally:
        conn.close()


def fed_publish(memory_id: int, source_agent: str = "",
                trust_level: str = "family", category: str = "") -> dict:
    """Publish a memory to shared_memories."""
    from memall.core.thin_waist import retrieve

    mem = retrieve(int(memory_id))
    if not mem:
        return {"error": f"memory {memory_id} not found"}

    content = redact_content(mem.content)
    now = datetime.now(timezone.utc).isoformat()

    conn = _get_family_conn()
    try:
        cur = conn.execute(
            """INSERT OR IGNORE INTO shared_memories
               (original_id, source_agent, source_db, content, category, level, project, owner, published_at, trust_level)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (memory_id, source_agent or mem.agent_name, "",
             content, category or mem.category, mem.level,
             mem.project, mem.owner, now, trust_level),
        )
        conn.commit()
        # Fix Bug-3: 用 cur.rowcount 判断是否真插入，total_changes 是累计值
        # INSERT OR IGNORE 时已存在 rowcount=0，新插入 rowcount=1
        inserted = cur.rowcount > 0
        return {"memory_id": memory_id, "source_agent": source_agent or mem.agent_name,
                "published": inserted, "was_new": inserted, "published_at": now}
    finally:
        conn.close()


def fed_conflicts(limit: int = 20) -> dict:
    """List unresolved conflicts from federation."""
    detection = detect_conflicts()
    conflicts = list_conflicts(status="open")[:limit]
    return {
        "total_detected": detection.get("conflicts_detected", 0),
        "total_shared": detection.get("total_memories", 0),
        "conflicts": conflicts[:limit],
    }


def fed_deliver(target_agent: str, content: str,
                event_type: str = "hub_push",
                category: str = "reflection",
                source: str = "hub",
                subject: str | None = None) -> dict:
    """Deliver a push event from Hub directly to a MemALL agent's inbox.

    This is the Hub → MemALL active push mechanism (S3-05).
    The event is stored as a local memory via capture(), not written to
    the shared family DB (that's fed_publish's job).

    Args:
        target_agent: Recipient agent name.
        content: Event content.
        event_type: Type label stored in subject prefix.
        category: Memory category (default: reflection).
        source: Source identifier ("hub" or agent name).
        subject: Optional subject; auto-generated if omitted.

    Returns:
        {"delivered": bool, "memory_id": int|None, "target_agent": str}
    """
    from memall.core.thin_waist import capture

    if not target_agent or not content:
        return {"delivered": False, "error": "target_agent and content are required"}

    if subject is None:
        subject = f"[hub:push:{event_type}] {source}"

    result = capture(
        content=content,
        agent_name=target_agent,
        subject=subject,
        category=category,
        level="P2",
        project="agent-hub",
        metadata_json=json.dumps({"event_type": event_type, "source": source}, ensure_ascii=False),
    )
    mem_id = result.get("id") if isinstance(result, dict) else None
    return {
        "delivered": True,
        "memory_id": mem_id,
        "target_agent": target_agent,
        "event_type": event_type,
        "source": source,
    }


# ── Module-level auto_inject cache ──
_inject_cache: dict[str, dict] = {}
_INJECT_CACHE_TTL = 300  # seconds


def auto_inject(agent_name: str) -> dict:
    """Auto-inject Agent Profile + evolutionary context for session_start.

    Injects not just who the agent is (persona), but also what it has
    learned (reflections), what it knows (distillations), what it should
    do (suggestions), and how it has been changing (evolution trend).
    This closes the self-evolution feedback loop — see OODA cycle.
    """
    now = datetime.now(timezone.utc)
    cached = _inject_cache.get(agent_name)
    if cached and (now - cached["cached_at"]).total_seconds() < _INJECT_CACHE_TTL:
        return cached["data"]

    conn = get_conn()
    try:
        # ── 1. Agent Profile ──
        from memall.pipeline.persona import generate_persona, get_evolution
        persona = generate_persona(agent_name)

        # ── 2. L6 Reflections (corrections / lessons learned) ──
        reflections = []
        rows = conn.execute(
            "SELECT id, content, summary, category, created_at FROM memories "
            "WHERE agent_name = ? AND level = 'L6' AND LENGTH(TRIM(content)) > 10 "
            "ORDER BY created_at DESC LIMIT 5",
            (agent_name,),
        ).fetchall()
        for r in rows:
            reflections.append({
                "id": r["id"],
                "summary": r["summary"] or r["content"][:200],
                "category": r["category"],
                "learned_at": r["created_at"],
            })


        # ══ 2.5. L7 Lessons (learned patterns extracted from L6 reflections) ══
        l7_lessons = []
        try:
            rows = conn.execute(
                "SELECT id, content, category, created_at FROM memories "
                "WHERE agent_name = ? AND level = 'L7' "
                "AND LENGTH(TRIM(content)) > 10 "
                "ORDER BY created_at DESC LIMIT 5",
                (agent_name,),
            ).fetchall()
            for r in rows:
                lesson_text = (r["content"] or "")[:200]
                if lesson_text.startswith('[L7 '):
                    lesson_text = lesson_text.split(']', 1)[-1].strip()
                l7_lessons.append({
                    "id": r["id"],
                    "lesson": lesson_text,
                    "category": r["category"],
                    "learned_at": r["created_at"],
                })
        except Exception:
            logger.warning("auto_inject l7_lessons failed", exc_info=True)

        # ── 3. L9 Distillations (knowledge summaries by category) ──
        distillations = []
        rows = conn.execute(
            "SELECT id, content, summary, category, created_at FROM memories "
            "WHERE agent_name = ? AND level = 'L9' AND LENGTH(TRIM(content)) > 10 "
            "ORDER BY created_at DESC LIMIT 5",
            (agent_name,),
        ).fetchall()
        for r in rows:
            distillations.append({
                "id": r["id"],
                "summary": r["summary"] or r["content"][:300],
                "category": r["category"],
                "distilled_at": r["created_at"],
            })

        # ── 4. Pending Suggestions ──
        suggestions_list = []
        rows = conn.execute(
            "SELECT id, content, category, created_at FROM suggestions "
            "WHERE status = 'pending' ORDER BY created_at DESC LIMIT 5"
        ).fetchall()
        for r in rows:
            suggestions_list.append({
                "id": r["id"],
                "content": r["content"][:200],
                "category": r["category"],
                "created_at": r["created_at"],
            })

        # ── 5. Persona Evolution Trend ──
        try:
            evolution = get_evolution(agent_name, window_days=30)
        except Exception:
            evolution = {"error": "evolution data not available", "agent_name": agent_name}

        # ── 6. Recent semantic fragments (top 5 by TF-IDF relevance) ──
        rows = conn.execute(
            "SELECT id, content, category, subject, summary FROM memories "
            "WHERE agent_name = ? AND LENGTH(TRIM(content)) > 10 "
            "ORDER BY created_at DESC LIMIT 20",
            (agent_name,),
        ).fetchall()

        fragments = []
        for r in rows:
            fragments.append({
                "id": r["id"],
                "content": r["content"][:300],
                "category": r["category"],
                "subject": r["subject"],
            })

        # Filter top fragments by TF-IDF relevance to agent name
        if len(fragments) > 5:
            texts = [agent_name] + [f["content"][:200] for f in fragments]
            tfidf_docs = compute_tfidf(texts)
            if tfidf_docs and len(tfidf_docs) > 1:
                scored = []
                for i, f in enumerate(fragments):
                    if i + 1 < len(tfidf_docs):
                        sim = cosine_sim(tfidf_docs[0], tfidf_docs[i + 1])
                        scored.append((sim, f))
                scored.sort(key=lambda x: -x[0])
                fragments = [f for _, f in scored[:5]]

        # ── 7. L1/L7 Identity Traits ──
        identity_traits = {"l1_identity": [], "l7_preferences": [], "persona_summary": {}}
        try:
            row = conn.execute(
                "SELECT identity_profile, profile_json, persona_updated_at FROM identities WHERE LOWER(agent_name) = LOWER(?)",
                (agent_name,),
            ).fetchone()
            if row and row["identity_profile"]:
                id_profile = json.loads(row["identity_profile"]) if isinstance(row["identity_profile"], str) else row["identity_profile"]
                if isinstance(id_profile, dict):
                    identity_traits["l1_identity"] = (id_profile.get("l1_identity") or [])[:5]
                    identity_traits["l7_preferences"] = (id_profile.get("l7_preferences") or [])[:5]
            if row and row["profile_json"]:
                pj = json.loads(row["profile_json"]) if isinstance(row["profile_json"], str) else row["profile_json"]
                if isinstance(pj, dict):
                    proto = pj.get("prototype", {})
                    feats = pj.get("features", {})
                    identity_traits["persona_summary"] = {
                        "prototype_cn": proto.get("cn", ""),
                        "prototype_en": proto.get("en", ""),
                        "certainty_score": feats.get("certainty_score", 0),
                        "decision_ratio": feats.get("decision_ratio", 0),
                        "domain_breadth": feats.get("domain_breadth", 0),
                    }
        except Exception:
            logger.warning("auto_inject identity_traits failed", exc_info=True)

        # ── 8. Pending L5 tasks assigned to this agent ──
        pending_tasks = []
        try:
            rows = conn.execute(
                "SELECT id, content, subject, created_at FROM memories "
                "WHERE level='L5' AND category='task' AND agent_name = ? "
                "AND json_extract(metadata, '$.status') = 'active'",
                (agent_name,),
            ).fetchall()
            pending_tasks = [dict(r) for r in rows]
        except Exception:
            logger.warning("auto_inject pending_tasks failed", exc_info=True)

        # ── 9. Pending discussions for this agent ──
        pending_discussions = []
        try:
            pending_discussions = check_pending_discussions(agent_name)
        except Exception:
            logger.warning("auto_inject check_pending_discussions failed", exc_info=True)

        # ── 10. L3 Workflow Skills (usable by any agent) ──
        workflow_skills = []
        try:
            # L3 scope: agent=own agent only, family/shared=all agents, NULL=agent (backward compat)
            rows = conn.execute(
                "SELECT id, subject, content, category, metadata FROM memories "
                "WHERE level = 'L3' AND LENGTH(TRIM(content)) > 50 "
                "AND ("
                "  json_extract(metadata, '$.scope') IN ('family', 'shared')"
                "  OR (COALESCE(json_extract(metadata, '$.scope'), 'agent') = 'agent' AND agent_name = ?)"
                ") "
                "ORDER BY confidence DESC, created_at DESC LIMIT 10",
                (agent_name,),
            ).fetchall()
            for r in rows:
                meta = {}
                try:
                    meta = json.loads(r["metadata"]) if isinstance(r["metadata"], str) else r["metadata"]
                except Exception:
                    logger.warning("federation_tools.py: silent error", exc_info=True)
                trigger_keywords = meta.get("trigger_keywords") or []
                if not isinstance(trigger_keywords, list):
                    trigger_keywords = []
                if not trigger_keywords and r["subject"]:
                    trigger_keywords = [r["subject"][:40]]
                workflow_skills.append({
                    "id": r["id"],
                    "subject": r["subject"][:80],
                    "trigger": trigger_keywords[:5],
                    "workflow": (r["content"] or "")[:500],
                    "category": r["category"],
                })
        except Exception:
            logger.warning("auto_inject workflow_skills failed", exc_info=True)

        # ── 11. L2 Timeline Events (recent happenings) ──
        timeline_events = []
        try:
            rows = conn.execute(
                "SELECT id, subject, content, category, created_at FROM memories "
                "WHERE level = 'L2' "
                "ORDER BY created_at DESC LIMIT 5",
            ).fetchall()
            for r in rows:
                timeline_events.append({
                    "id": r["id"],
                    "subject": r["subject"][:60],
                    "summary": (r["content"] or "")[:120],
                    "category": r["category"],
                    "at": r["created_at"],
                })
        except Exception:
            logger.warning("auto_inject timeline_events failed", exc_info=True)

        # ── 12. L4 Decision Arcs ──
        decision_arcs = {"open": [], "in_progress": [], "closed": []}
        try:
            rows = conn.execute(
                "SELECT id, subject, content, arc_status, created_at FROM memories "
                "WHERE level = 'L4' AND agent_name = ? "
                "ORDER BY created_at DESC LIMIT 20",
                (agent_name,),
            ).fetchall()
            for r in rows:
                status = r["arc_status"] or "open"
                entry = {
                    "id": r["id"],
                    "subject": r["subject"][:60],
                    "summary": (r["content"] or "")[:120],
                    "decided_at": r["created_at"],
                }
                if status in decision_arcs:
                    decision_arcs[status].append(entry)
        except Exception:
            logger.warning("auto_inject decision_arcs failed", exc_info=True)

        # ── 13. L10 Panoramic Overview (terminal, comprehensive) ──
        panoramic_overview = []
        try:
            rows = conn.execute(
                "SELECT id, subject, content, summary, category, created_at FROM memories "
                "WHERE level = 'L10' AND LENGTH(TRIM(content)) > 50 "
                "ORDER BY created_at DESC LIMIT 3"
            ).fetchall()
            for r in rows:
                panoramic_overview.append({
                    "id": r["id"],
                    "subject": r["subject"][:80],
                    "summary": (r["summary"] or r["content"] or "")[:300],
                    "category": r["category"],
                    "at": r["created_at"],
                })
        except Exception:
            logger.warning("auto_inject panoramic_overview failed", exc_info=True)

        # ── 14. [GRAPH] Edges live query (replaces old L8 keyword query) ──
        graph_relations = {}
        try:
            # A — Time-window counts
            cnt_row = conn.execute(
                "SELECT "
                "SUM(CASE WHEN created_at >= datetime('now', '-1 day') THEN 1 ELSE 0 END) as last_24h, "
                "SUM(CASE WHEN created_at >= datetime('now', '-7 days') THEN 1 ELSE 0 END) as last_7d, "
                "COUNT(*) as total FROM edges"
            ).fetchone()
            counts = {"last_24h": cnt_row["last_24h"], "last_7d": cnt_row["last_7d"], "total": cnt_row["total"]}

            # B — Type distribution
            type_rows = conn.execute(
                "SELECT relation_type, COUNT(*) as cnt FROM edges "
                "GROUP BY relation_type ORDER BY cnt DESC"
            ).fetchall()
            types = [{"type": r["relation_type"], "count": r["cnt"]} for r in type_rows]

            # C — Recent 5 edges (IDs only, no JOIN)
            recent_rows = conn.execute(
                "SELECT source_id, target_id, relation_type FROM edges "
                "ORDER BY id DESC LIMIT 5"
            ).fetchall()
            recent = [{"source": r["source_id"], "target": r["target_id"], "type": r["relation_type"]} for r in recent_rows]

            # D — Hub nodes (top 5 most connected)
            hub_rows = conn.execute(
                "SELECT node_id, COUNT(*) as edge_count FROM ("
                "SELECT source_id as node_id FROM edges "
                "UNION ALL "
                "SELECT target_id as node_id FROM edges"
                ") GROUP BY node_id ORDER BY edge_count DESC LIMIT 5"
            ).fetchall()
            hub_ids = [r["node_id"] for r in hub_rows]
            hubs = []
            if hub_ids:
                ph = ",".join("?" for _ in hub_ids)
                subject_map = {
                    r["id"]: r["subject"] or f"#{r['id']}"
                    for r in conn.execute(
                        f"SELECT id, subject FROM memories WHERE id IN ({ph})", hub_ids
                    ).fetchall()
                }
                hubs = [{"id": r["node_id"], "subject": subject_map.get(r["node_id"], f"#{r['node_id']}"), "edge_count": r["edge_count"]} for r in hub_rows]

            graph_relations = {"counts": counts, "types": types, "recent": recent, "hubs": hubs}
        except Exception:
            logger.warning("auto_inject graph_relations failed", exc_info=True)

        # ── 15. L11 Domain Knowledge (strategy / business / domain insights) ──
        domain_knowledge = []
        try:
            rows = conn.execute(
                "SELECT id, subject, content, summary, category, created_at FROM memories "
                "WHERE level = 'L11' AND LENGTH(TRIM(content)) > 30 "
                "ORDER BY created_at DESC LIMIT 5"
            ).fetchall()
            for r in rows:
                domain_knowledge.append({
                    "id": r["id"],
                    "subject": r["subject"][:80],
                    "summary": (r["summary"] or r["content"] or "")[:300],
                    "category": r["category"],
                    "at": r["created_at"],
                })
        except Exception:
            logger.warning("auto_inject domain_knowledge failed", exc_info=True)

        # ── 16. L4 recent summaries (global, for session_start) ──
        l4_recent_global = []
        try:
            rows = conn.execute(
                "SELECT id, content, summary, subject, metadata, created_at FROM memories "
                "WHERE level = 'L4' AND LENGTH(TRIM(content)) > 5 "
                "ORDER BY created_at DESC LIMIT 3"
            ).fetchall()
            for r in rows:
                meta = json.loads(r["metadata"]) if isinstance(r["metadata"], str) else r["metadata"]
                l4_recent_global.append({
                    "id": r["id"],
                    "subject": r["subject"],
                    "summary": r["summary"] or r["content"][:200],
                    "participants": (meta or {}).get("participants", []),
                    "key_decisions": ((meta or {}).get("key_decisions") or [])[:3],
                    "continuation_note": (meta or {}).get("continuation_note", ""),
                    "created_at": r["created_at"],
                })
        except Exception:
            logger.warning("auto_inject l4_recent failed", exc_info=True)

        # ── 17. L5 active todos (global, for session_start) ──
        l5_active_global = []
        try:
            rows = conn.execute(
                "SELECT id, content, summary, subject, metadata, level, created_at FROM memories "
                "WHERE level = 'L5' AND LENGTH(TRIM(content)) > 5 "
                "ORDER BY created_at DESC LIMIT 20"
            ).fetchall()
            seen_subs = set()
            for r in rows:
                meta = json.loads(r["metadata"]) if isinstance(r["metadata"], str) else r["metadata"]
                status = (meta.get("status") or "active") if isinstance(meta, dict) else "active"
                if status != "active":
                    continue
                subj_key = (r["subject"] or "")[:40]
                if subj_key in seen_subs:
                    continue
                seen_subs.add(subj_key)
                l5_active_global.append({
                    "id": r["id"],
                    "subject": r["subject"],
                    "summary": r["summary"] or r["content"][:200],
                    "assignee": (meta or {}).get("assignee", ""),
                    "depends_on": (meta or {}).get("depends_on", []),
                    "level_tag": {"P0": "(P0)", "P1": "(P1)", "P2": "(P2)"}.get(r["level"] or "", ""),
                    "created_at": r["created_at"],
                })
        except Exception:
            logger.warning("auto_inject l5_active failed", exc_info=True)

        # ── 18. BEHAVIOR patterns (agent-specific, for session_start) ──
        behavior_patterns = []
        try:
            rows = conn.execute(
                "SELECT json_extract(metadata, '$.enrich.value.behavior') AS bhv FROM memories "
                "WHERE agent_name = ? AND json_extract(metadata, '$.enrich.value.behavior.dominant_stage') IS NOT NULL "
                "ORDER BY created_at DESC LIMIT 20",
                (agent_name,),
            ).fetchall()
            if rows:
                from memall.pipeline.behavior import format_for_injection
                bhv_list = []
                for r in rows:
                    b = json.loads(r["bhv"]) if isinstance(r["bhv"], str) else r["bhv"]
                    if b and isinstance(b, dict) and b.get("stages"):
                        bhv_list.append(b)
                bhv_text = format_for_injection(bhv_list)
                if bhv_text:
                    behavior_patterns.append(bhv_text)
        except Exception:
            logger.warning("auto_inject behavior_patterns failed", exc_info=True)

        result = {
            "agent_name": agent_name,
            "persona": persona,
            "evolution_trend": evolution,
            "recent_reflections": reflections[:5],
            "l7_lessons": l7_lessons,
            "knowledge_summaries": distillations[:5],
            "pending_actions": suggestions_list[:5],
            "semantic_fragments": fragments[:5],
            "identity_traits": identity_traits,
            "pending_tasks": pending_tasks,
            "pending_discussions": pending_discussions,
            "workflow_skills": workflow_skills,
            "timeline_events": timeline_events,
            "decision_arcs": decision_arcs,
            "panoramic_overview": panoramic_overview,
            "graph_relations": graph_relations,
            "domain_knowledge": domain_knowledge,
            "l4_recent_global": l4_recent_global,
            "l5_active_global": l5_active_global,
            "behavior_patterns": behavior_patterns,
            "injected_at": datetime.now(timezone.utc).isoformat(),
        }
        _inject_cache[agent_name] = {"cached_at": datetime.now(timezone.utc), "data": result}
        return result
    finally:
        conn.close()


# ════════════════════════════════════════════════════════════════
# Agent Hub 桥接 (Phase 8: MCP 对接 agent-hub)
# ════════════════════════════════════════════════════════════════

def hub_connect() -> dict:
    """Check connectivity to Agent Hub and return status summary."""
    from memall.mcp.hub_client import hub_status
    return hub_status()


def hub_sync(direction: str = "bidirectional", limit: int = 20,
              hub_group_id: str = "chat") -> dict:
    """Sync data between MemALL and Agent Hub.

    Push: sends MemALL memories as messages to Hub's group chat.
    Pull: reads Hub group messages and stores them in MemALL.

    Args:
        direction: "to_hub" | "from_hub" | "bidirectional"
        limit: max items per direction
        hub_group_id: Hub group to push/pull from (default: "chat" = 公共频道)

    Returns sync summary.
    """
    from memall.mcp.hub_client import (
        hub_list_agents, hub_list_groups, hub_list_memories,
        hub_send_message, hub_get_group_messages, hub_health,
    )
    from memall.core.thin_waist import capture, retrieve
    from memall.core.db import get_conn as get_db_conf

    result = {"to_hub": {"memories_pushed": 0, "messages_sent": 0, "errors": []},
              "from_hub": {"agents_pulled": 0, "memories_pulled": 0,
                           "messages_pulled": 0, "groups_pulled": 0, "errors": []}}

    # ── Health check ──
    health = hub_health()
    if isinstance(health, dict) and health.get("error"):
        return {"error": f"Hub unreachable: {health['error']}",
                "hint": "Start Agent Hub: cd F:\\memall-agent-hub\\server && go run ."}
    if isinstance(health, str) and health != "ok":
        return {"error": f"Hub unexpected response: {health[:100]}",
                "hint": "Start Agent Hub: cd F:\\memall-agent-hub\\server && go run ."}

    if direction in ("to_hub", "bidirectional"):
        # Push recent MemALL memories as messages to Hub group chat
        db = get_db_conf()
        try:
            db.row_factory = sqlite3.Row
            rows = db.execute(
                "SELECT id, content, agent_name, category, subject FROM memories ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
            for r in rows:
                # Sanitize: strip non-printable chars, limit length
                raw_content = (r["content"] or "")[:300]
                content_preview = "".join(c for c in raw_content if c.isprintable() or c in "\n\r\t")
                subject = (r["subject"] or "").strip()[:100]
                agent = r["agent_name"] or "memall"
                msg = f"[MemALL] {agent}: {subject}\n\n{content_preview}"
                if len(msg) > 500:
                    msg = msg[:500] + "..."
                resp = hub_send_message(hub_group_id, "memall-bridge", msg)
                if isinstance(resp, dict) and resp.get("error"):
                    result["to_hub"]["errors"].append(f"memory {r['id']}: {resp['error']}")
                else:
                    result["to_hub"]["messages_sent"] += 1
                    result["to_hub"]["memories_pushed"] += 1
        finally:
            db.close()

    if direction in ("from_hub", "bidirectional"):
        # Pull agents from Hub
        try:
            agents = hub_list_agents()
            if isinstance(agents, list):
                result["from_hub"]["agents_pulled"] = len(agents)
        except Exception as e:
            result["from_hub"]["errors"].append(f"agents: {e}")

        # Pull groups from Hub
        try:
            groups = hub_list_groups()
            if isinstance(groups, list):
                result["from_hub"]["groups_pulled"] = len(groups)
        except Exception as e:
            result["from_hub"]["errors"].append(f"groups: {e}")

        # Pull Hub group messages and store as MemALL memories
        try:
            msgs = hub_get_group_messages(hub_group_id, limit=limit)
            if isinstance(msgs, list) and msgs:
                for m in msgs:
                    try:
                        sender = m.get("sender_id") or m.get("sender", "hub-agent")
                        content = m.get("content", "")
                        if not content.strip():
                            continue
                        capture(
                        content,
                        agent_name=sender,
                        subject=f"[hub:{hub_group_id}] {sender}",
                        category="reflection",
                        project="agent-hub",
                    )
                        result["from_hub"]["messages_pulled"] += 1
                    except Exception as e:
                        result["from_hub"]["errors"].append(f"msg: {e}")
                # Also try pulling Hub memories via API (best-effort)
                try:
                    hub_mems = hub_list_memories(limit=limit)
                    if isinstance(hub_mems, list):
                        for hm in hub_mems:
                            try:
                                hub_agent = hm.get("agent_id", "hub-agent")
                                title = hm.get("title", hm.get("content", "")[:60])
                                c = hm.get("content", "")[:500]
                                capture(
                                    c[:500],
                                    agent_name=hub_agent,
                                    subject=f"[hub-mem] {title}",
                                    category=hm.get("category", "fact"),
                                    project="agent-hub",
                                )
                                result["from_hub"]["memories_pulled"] += 1
                            except Exception:
                                logger.warning("federation_tools.py: silent error", exc_info=True)
                except Exception:
                    logger.warning("federation_tools.py: silent error", exc_info=True)
        except Exception as e:
            result["from_hub"]["errors"].append(f"messages: {e}")

    # ── Summary ──
    result["hub_url"] = "http://127.0.0.1:12431"
    result["direction"] = direction
    result["success"] = (len(result["to_hub"]["errors"]) == 0 and
                         len(result["from_hub"]["errors"]) == 0)
    return result


def auto_extract(session_id: str) -> dict:
    """Auto-extract facts from session memories into shared_memories.

    Called on session_end. Queries memories from the session period,
    extracts key facts, and publishes to shared_memories.
    """
    conn = get_conn()
    try:
        # Get session boundaries
        row = conn.execute(
            "SELECT started_at, ended_at, agent_name FROM sessions WHERE session_id = ? AND status = 'ended'",
            (session_id,),
        ).fetchone()
        if not row:
            return {"error": "session not found or not ended", "session_id": session_id}

        started_at = row["started_at"]
        agent_name = row["agent_name"]
        ended_at = row["ended_at"] or datetime.now(timezone.utc).isoformat()

        # Get memories during this session
        rows = conn.execute(
            "SELECT id, content, category, level, owner, subject FROM memories WHERE agent_name = ? AND created_at >= ? AND created_at <= ? AND LENGTH(TRIM(content)) > 20 ORDER BY created_at ASC LIMIT 100",
            (agent_name, started_at, ended_at),
        ).fetchall()

        published_ids = []
        now = datetime.now(timezone.utc).isoformat()
        family_conn = _get_family_conn()

        try:
            for r in rows:
                content = redact_content(r["content"][:500])
                family_conn.execute(
                    """INSERT OR IGNORE INTO shared_memories
                       (original_id, source_agent, source_db, content, category, level, owner, published_at, trust_level)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (r["id"], agent_name, "", content, r["category"],
                     r["level"], r["owner"], now, "family"),
                )
                published_ids.append(r["id"])
            family_conn.commit()
        finally:
            family_conn.close()

        return {
            "session_id": session_id,
            "agent_name": agent_name,
            "memories_scanned": len(rows),
            "facts_published": len(published_ids),
            "published_ids": published_ids[:20],
        }
    finally:
        conn.close()
