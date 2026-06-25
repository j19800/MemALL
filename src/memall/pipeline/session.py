"""Session lifecycle management for MCP protocol.

Provides session_start / session_end / session_summary for tracking
conversational sessions across Agents.

Table: sessions(session_id TEXT PK, agent_name TEXT, started_at TEXT,
ended_at TEXT, memory_count INTEGER, summary TEXT, status TEXT)
"""

import logging

logger = logging.getLogger(__name__)

import hashlib
import json
import re
import uuid
from datetime import datetime, timedelta, timezone
from memall.core.db import get_conn
from memall.core.health import collect as collect_health
from memall.pipeline.util import _smart_subject


def _ensure_sessions_table(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            session_id TEXT PRIMARY KEY,
            previous_session_id TEXT DEFAULT '',
            agent_name TEXT NOT NULL DEFAULT '',
            started_at TEXT NOT NULL,
            ended_at TEXT,
            memory_count INTEGER DEFAULT 0,
            summary TEXT DEFAULT '',
            status TEXT DEFAULT 'active'
        )
    """)
    # Add column if table already existed without it (check first to avoid warning)
    cols = [r[1] for r in conn.execute("PRAGMA table_info(sessions)").fetchall()]
    if "previous_session_id" not in cols:
        try:
            conn.execute("ALTER TABLE sessions ADD COLUMN previous_session_id TEXT DEFAULT ''")
        except Exception:
            logger.warning("session.py: silent error", exc_info=True)
    conn.commit()


def _mark_session_inline(conn, session_id: str) -> None:
    """Inline session-end logic: marks session as ended, counts memories, creates L4 if > 3."""
    row = conn.execute(
        "SELECT started_at, agent_name, status FROM sessions WHERE session_id = ?",
        (session_id,),
    ).fetchone()
    if not row or row["status"] == "ended":
        return
    _harvest_session(conn, session_id, row["started_at"], row["agent_name"], end_session=True)


def _harvest_session(conn, session_id: str, started_at: str, agent_name: str,
                     end_session: bool = False) -> dict:
    """Count a session's memories and generate L4/L6 if missing. Does NOT end session by default.

    Args:
        conn: DB connection (shared with caller).
        session_id: The session to process.
        started_at: Session start timestamp.
        agent_name: Agent name for memory queries.
        end_session: If True, also mark session as ended (legacy _mark_session_inline behavior).

    Returns:
        dict with keys ``memory_count``, ``l4_created``, ``l6_created``.
    """
    now = datetime.now(timezone.utc).isoformat()
    result = {"memory_count": 0, "l4_created": False, "l6_created": False}

    where = ["created_at >= ?", "agent_name = ?"]
    params = [started_at, agent_name]
    count = conn.execute(
        f"SELECT COUNT(*) FROM memories WHERE {' AND '.join(where)}", params
    ).fetchone()[0]
    result["memory_count"] = count

    cats = conn.execute(
        f"SELECT category, COUNT(*) as cnt FROM memories WHERE {' AND '.join(where)} GROUP BY category ORDER BY cnt DESC LIMIT 3",
        params,
    ).fetchall()
    cat_summary = ", ".join([f"{r['category']}({r['cnt']})" for r in cats])
    summary = f"[{agent_name}] {count} memories in {cat_summary}"[:500]

    # Infer project from session memories
    project_row = conn.execute(
        f"SELECT project, COUNT(*) as cnt FROM memories WHERE {' AND '.join(where)} "
        f"AND project IS NOT NULL AND project != '' GROUP BY project ORDER BY cnt DESC LIMIT 1",
        params,
    ).fetchone()
    session_project = project_row["project"] if project_row else ""

    if end_session:
        conn.execute(
            "UPDATE sessions SET ended_at = ?, memory_count = ?, summary = ?, status = 'ended' WHERE session_id = ?",
            (now, count, summary, session_id),
        )
    else:
        conn.execute(
            "UPDATE sessions SET memory_count = ?, summary = ? WHERE session_id = ?",
            (count, summary, session_id),
        )

    # L4 session memory when count > 3 (generate only if not already existing)
    if count > 3:
        existing_l4 = conn.execute(
            "SELECT id FROM memories WHERE level = 'L4' AND category = 'session' "
            "AND json_extract(metadata, '$.session_id') = ? LIMIT 1",
            (session_id,),
        ).fetchone()

        # Shared extraction for L4 and L6
        decision_rows = conn.execute(
            f"SELECT content FROM memories WHERE {' AND '.join(where)} AND category = 'decision' ORDER BY created_at DESC LIMIT 3",
            params,
        ).fetchall()
        key_decisions = [r["content"][:100] for r in decision_rows]
        last_row = conn.execute(
            f"SELECT content FROM memories WHERE {' AND '.join(where)} ORDER BY created_at DESC LIMIT 1",
            params,
        ).fetchone()
        continuation_note = ""
        if last_row:
            for pat in [r'下一步[：:\s]*(.{5,80})', r'继续[：:\s]*(.{5,80})', r'next[：:\s]*(.{5,80})']:
                m = re.search(pat, last_row["content"], re.I)
                if m:
                    continuation_note = m.group(1).strip()[:100]
                    break

        if not existing_l4:
            decision_text = ""
            if key_decisions:
                decision_text = "；".join(d[:80] for d in key_decisions[:3] if d)
            parts = [f"[L4 会话] {agent_name} · {count}条记忆 · {cat_summary}"]
            if decision_text:
                parts.append(f"关键决策：{decision_text}")
            if continuation_note:
                parts.append(f"后续：{continuation_note}")

            l4_content = "。".join(parts)
            ch = hashlib.sha256(l4_content.encode()).hexdigest()

            # Participants
            participant_rows = conn.execute(
                f"SELECT DISTINCT agent_name FROM memories WHERE {' AND '.join(where)} "
                f"AND agent_name IS NOT NULL AND agent_name != ''",
                params,
            ).fetchall()
            participants = [r["agent_name"] for r in participant_rows]

            conn.execute(
                "INSERT OR IGNORE INTO memories (content, content_hash, level, owner, agent_name, category, project, subject, summary, occurred_at, created_at, updated_at, confidence, visibility, metadata) "
                "VALUES (?, ?, 'L4', 'system', ?, 'session', ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (l4_content[:2000], ch, agent_name, session_project,
                 _smart_subject(l4_content), _smart_subject(l4_content), now, now, now, 0.5, "shared",
                 json.dumps({"session_id": session_id, "key_decisions": key_decisions,
                             "participants": participants, "continuation_note": continuation_note,
                             "source": "pipeline_harvest"})),
            )
            result["l4_created"] = True

        # L6 auto-reflection with distinctive content
        distinctive_words = []
        try:
            session_rows = conn.execute(
                f"SELECT content FROM memories WHERE {' AND '.join(where)} AND LENGTH(TRIM(content)) > 10 LIMIT 30",
                params,
            ).fetchall()
            session_text = " ".join(r["content"] for r in session_rows if r["content"])
            if len(session_text) > 50:
                from collections import Counter
                words = re.findall(r'[一-鿿]{2,4}|[a-zA-Z]\w{2,}', session_text.lower())
                if words:
                    wf = Counter(w for w in words)
                    distinctive_words = [w for w, _ in wf.most_common(8) if wf[w] >= 2]
        except Exception:
            pass

        l6_parts = [f"会话总结：本次会话记录了 {count} 条记忆"]
        if cat_summary:
            l6_parts.append(f"集中在 {cat_summary}")
        if distinctive_words:
            l6_parts.append(f"关键话题：{'、'.join(distinctive_words[:6])}")
        if key_decisions:
            dec_text = "；".join(d[:60] for d in key_decisions[:3] if d)
            l6_parts.append(f"关键决策：{dec_text}")
        if continuation_note:
            l6_parts.append(f"后续关注：{continuation_note}")
        l6_content = "。".join(l6_parts) + "。"
        l6_ch = hashlib.sha256(l6_content.encode()).hexdigest()

        existing_l6 = conn.execute(
            "SELECT id FROM memories WHERE content_hash = ?", (l6_ch,)
        ).fetchone()
        if not existing_l6:
            # Generate meaningful subject
            l6_subject = _smart_subject(l6_content)
            conn.execute(
                "INSERT OR IGNORE INTO memories "
                "(content, content_hash, level, owner, agent_name, category, project, subject, summary, "
                "occurred_at, created_at, updated_at, confidence, visibility, metadata) "
                "VALUES (?, ?, 'L6', 'system', ?, 'reflection', ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (l6_content[:2000], l6_ch, agent_name, session_project,
                 l6_subject, l6_subject, now, now, now, 0.6, "private",
                 json.dumps({"session_id": session_id, "source": "pipeline_harvest"})),
            )
            result["l6_created"] = True

    return result



def _build_narrative_greeting(data: dict, agent_name: str) -> str:
    """Build a natural-language greeting paragraph from session context data."""
    parts = []

    # Time since last session
    h = data.get("hours_elapsed")
    if h is not None:
        if h < 2:
            parts.append("不久未见")
        elif h < 48:
            parts.append(f"距离上次会话约 {h} 小时")
        else:
            days = h // 24
            if days == 1:
                parts.append("隔了一天")
            elif days <= 7:
                parts.append(f"隔了 {days} 天")
            else:
                parts.append(f"距上次会话约 {days} 天")
    else:
        parts.append("新会话开始")

    # New memories since last session
    new_mem = data.get("new_memories", 0)
    if new_mem > 0:
        parts.append(f"期间新增 {new_mem} 条记忆")

    # Current epoch
    epoch_label = data.get("epoch_label")
    if epoch_label:
        parts.append(f"当前阶段「{epoch_label[:30]}」")

    # TODOs
    if data.get("has_todo"):
        tc = data["todo_count"]
        if data.get("todo_p0"):
            parts.append(f"有 {tc} 件待办（含 P0 紧急项）")
        elif tc > 0:
            parts.append(f"有 {tc} 件待办未处理")

    # Open arcs
    if data.get("open_arcs_count", 0) > 0:
        parts.append(f"{data['open_arcs_count']} 条决策未闭合")

    # Weekly summary
    if data.get("has_weekly"):
        parts.append("本周有回顾总结")

    # Pulse (stale items)
    if data.get("has_pulse"):
        parts.append("有长期未动的待办")

    if not parts:
        return ""

    return f"[NARRATIVE] {'，'.join(parts)}。"


def _build_cross_agent_section(conn, agent_name: str) -> list:
    """Build a [CROSS-AGENT] section showing related memories from other agents."""
    if not agent_name:
        return []

    # Find this agent's recent topics (categories with most activity)
    recent_cats = conn.execute(
        "SELECT category, COUNT(*) as cnt FROM memories "
        "WHERE agent_name = ? AND category != 'general' AND category != '' "
        "GROUP BY category ORDER BY cnt DESC LIMIT 3",
        (agent_name,),
    ).fetchall()
    if not recent_cats:
        return []

    cat_list = [r["category"] for r in recent_cats]

    # Query memories table for other agents' content on same categories
    placeholders = ",".join("?" for _ in cat_list)
    other_memories = conn.execute(
        f"SELECT agent_name, category, subject, content, created_at "
        f"FROM memories "
        f"WHERE agent_name NOT IN (?, 'system', '') AND agent_name IS NOT NULL "
        f"AND category IN ({placeholders}) "
        f"AND LENGTH(TRIM(content)) > 5 "
        f"ORDER BY created_at DESC LIMIT 5",
        (agent_name, *cat_list),
    ).fetchall()

    if not other_memories:
        return []

    lines = [f"[CROSS-AGENT] 其他智能体也记过相关话题 ({len(other_memories)}条)"]
    seen = set()
    for r in other_memories:
        agent = r["agent_name"]
        cat = r["category"]
        # Prefer subject, fall back to content (skip JSON-looking content)
        subj = (r["subject"] or "").strip()
        if not subj:
            content_raw = (r["content"] or "").strip()
            if content_raw.startswith("{") or content_raw.startswith("["):
                continue  # skip raw JSON
            subj = content_raw[:60]
        if not subj:
            continue
        key = (agent, subj[:30])
        if key in seen:
            continue
        seen.add(key)
        ts = (r["created_at"] or "")[5:10]
        lines.append(f"  · {agent} ({ts}) «{cat}» {subj}")

    return lines if len(lines) > 1 else []


def session_start(agent_name: str = "", auto_inject: bool = True) -> dict:
    """Start a new session. Optionally auto-inject Agent Profile + semantic fragments."""
    conn = get_conn()
    try:
        _ensure_sessions_table(conn)

        # Auto-close stale active session for same agent (> 2h idle)
        stale = conn.execute(
            "SELECT session_id, started_at FROM sessions "
            "WHERE agent_name = ? AND status = 'active' "
            "ORDER BY started_at DESC LIMIT 1",
            (agent_name,),
        ).fetchone()
        if stale and stale["started_at"]:
            try:
                stale_start = datetime.fromisoformat(stale["started_at"])
                if (datetime.now(timezone.utc) - stale_start) > timedelta(hours=2):
                    _mark_session_inline(conn, stale["session_id"])
            except Exception:
                logger.warning("session.py: silent error", exc_info=True)

        sid = str(uuid.uuid4())[:8]
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "INSERT INTO sessions (session_id, agent_name, started_at, status) VALUES (?, ?, ?, ?)",
            (sid, agent_name, now, "active"),
        )
        conn.commit()

        result = {"session_id": sid, "agent_name": agent_name, "started_at": now, "status": "active"}

        # Phase 8: auto-inject if agent_name given
        if auto_inject and agent_name:
            from memall.mcp.federation_tools import auto_inject as _auto_inject
            injection = _auto_inject(agent_name)
            result["injection"] = injection

            result["l4_recent"] = injection.get("l4_recent_global", [])

            l5_todos = injection.get("l5_active_global", [])
            result["l5_active"] = l5_todos

            # L3 workflow matching by category (infer from last memory)
            l3_workflows = []
            try:
                last_cat = conn.execute(
                    "SELECT category FROM memories WHERE agent_name = ? AND category != 'general' "
                    "ORDER BY created_at DESC LIMIT 1",
                    (agent_name,),
                ).fetchone()
                if last_cat and last_cat["category"]:
                    l3_rows = conn.execute(
                        "SELECT id, subject, summary, content FROM memories "
                        "WHERE level = 'L3' AND category = ? "
                        "AND ("
                        "  json_extract(metadata, '$.scope') IN ('family', 'shared')"
                        "  OR (COALESCE(json_extract(metadata, '$.scope'), 'agent') = 'agent' AND agent_name = ?)"
                        ") "
                        "ORDER BY created_at DESC LIMIT 2",
                        (last_cat["category"], agent_name),
                    ).fetchall()
                    for w in l3_rows:
                        l3_workflows.append({
                            "id": w["id"],
                            "subject": w["subject"],
                            "summary": w["summary"] or w["content"][:200],
                        })
            except Exception:
                logger.warning("session.py: silent error", exc_info=True)
            result["l3_matched"] = l3_workflows

            # P0 urgent count: if any P0 exists, inject alert
            p0_count = 0
            try:
                p0_count = conn.execute(
                    "SELECT COUNT(*) FROM memories WHERE level = 'P0' AND COALESCE(json_extract(metadata, '$.done'), 0) = 0"
                ).fetchone()[0]
            except Exception:
                logger.warning("session.py: silent error", exc_info=True)

            # ── Collect data for narrative greeting (Direction 1) ──
            # (queries happen inline below; results collected here)
            narrative_data: dict[str, any] = {
                "hours_elapsed": None,
                "new_memories": 0,
                "epoch_label": None,
                "has_todo": bool(l5_todos),
                "todo_count": len(l5_todos),
                "todo_p0": any(t.get("level_tag") == "(P0)" for t in l5_todos),
                "open_arcs_count": 0,
                "discussions": [],
                "p0_alert": p0_count > 0,
                "has_weekly": False,
                "has_pulse": False,
            }

            # [CONTINUITY] data — reused for narrative
            prev_session = None
            if agent_name:
                try:
                    prev_session = conn.execute(
                        "SELECT ended_at, session_id FROM sessions "
                        "WHERE agent_name = ? AND ended_at IS NOT NULL "
                        "ORDER BY ended_at DESC LIMIT 1",
                        (agent_name,),
                    ).fetchone()
                    if prev_session and prev_session["ended_at"]:
                        prev_dt = datetime.fromisoformat(prev_session["ended_at"])
                        now_dt = datetime.now(timezone.utc)
                        narrative_data["hours_elapsed"] = int((now_dt - prev_dt).total_seconds() / 3600)
                        narrative_data["new_memories"] = conn.execute(
                            "SELECT COUNT(*) as c FROM memories WHERE "
                            "agent_name = ? AND created_at > ?",
                            (agent_name, prev_session["ended_at"]),
                        ).fetchone()["c"]
                except Exception:
                    logger.warning("session.py: silent error", exc_info=True)

                        # ── Formatted injection (4 sections, ~800 chars) ──
            fmt_parts = [
                "[CONTEXT] 会话上下文（首段先概括以下信息）：",
            ]

            # [PROFILE]
            l5_todos = injection.get("pending_tasks", [])[:5]
            id_traits = injection.get("identity_traits", {})
            l1_list = id_traits.get("l1_identity", [])
            l7_list = id_traits.get("l7_preferences", [])
            psum = id_traits.get("persona_summary", {})
            if l1_list or l7_list or psum:
                parts = ["[PROFILE]"]
                if l1_list:
                    parts.append("L1=" + "·".join(t["snippet"] for t in l1_list[:3] if t.get("snippet")))
                if l7_list:
                    parts.append("L7=" + "·".join(t["snippet"] for t in l7_list[:3] if t.get("snippet")))
                if psum.get("prototype_cn"):
                    parts.append(f"类型:{psum['prototype_cn']}")
                fmt_parts.append(" ".join(parts))

            # [TODO]
            if l5_todos:
                items = " | ".join(t["subject"][:40] for t in l5_todos[:5])
                fmt_parts.append(f"[TODO] {items}")

            # [CONTINUITY]
            if agent_name:
                try:
                    h = narrative_data.get("hours_elapsed")
                    nc = narrative_data.get("new_memories", 0)
                    if h is not None:
                        cont = f"[CONTINUITY] 距上次约{h if h<48 else h//24}{'h' if h<48 else 'd'}"
                        if nc > 0:
                            cont += f" +{nc}mem"
                        if prev_session:
                            nd = conn.execute(
                                "SELECT COUNT(*) FROM memories WHERE agent_name=? AND level='L4' AND category='decision' AND created_at>?",
                                (agent_name, prev_session["ended_at"]),
                            ).fetchone()[0]
                            if nd > 0:
                                cont += f" {nd}dec"
                        fmt_parts.append(cont)
                except Exception:
                    pass

            # [NARRATIVE] greeting
            narrative_line = _build_narrative_greeting(narrative_data, agent_name)
            if narrative_line:
                fmt_parts.insert(1, "")
                fmt_parts.insert(1, narrative_line)

            # [CORRECTIONS] L6 reflections — lessons learned
            reflections = injection.get("recent_reflections", [])
            if reflections:
                tips = " · ".join(r.get("summary", "")[:60] for r in reflections[:3] if r.get("summary"))
                if not tips:
                    tips = " · ".join(r.get("category", "") for r in reflections[:3])
                fmt_parts.append(f"[CORRECTIONS] {tips}")

            
            # [LESSONS] L7 learned patterns (from L6 reflection distill)
            l7_lessons = injection.get("l7_lessons", [])
            if l7_lessons:
                lessons_tips = " · ".join(l["lesson"][:80] for l in l7_lessons[:3])
                fmt_parts.append(f"[LESSONS] {lessons_tips}")

# [WORKFLOW] L3 workflow skills — available tools/routines
            skills = injection.get("workflow_skills", [])
            if skills:
                names = " · ".join(s["subject"] for s in skills[:3])
                fmt_parts.append(f"[WORKFLOW] {names}")

            # [BEHAVIOR] L3 behavioral stage patterns (from auto_inject cache)
            bhv_lines = injection.get("behavior_patterns", [])
            if bhv_lines:
                fmt_parts.append(f"[BEHAVIOR] {bhv_lines[0]}")

            # [TIMELINE] L2 recent events
            timeline = injection.get("timeline_events", [])
            if timeline:
                events = " · ".join(t["subject"] for t in timeline[:3] if t.get("subject"))
                fmt_parts.append(f"[TIMELINE] {events}")

            # [DECISIONS] L4 decision arcs
            darcs = injection.get("decision_arcs", {})
            open_dec = darcs.get("open", [])
            in_prog = darcs.get("in_progress", [])
            if open_dec or in_prog:
                parts = ["[DECISIONS]"]
                if open_dec:
                    parts.append(f"待决({len(open_dec)})")
                if in_prog:
                    parts.append(f"进行中({len(in_prog)})")
                fmt_parts.append(" ".join(parts))

            # [OVERVIEW] L10 terminal-level panoramic
            overview = injection.get("panoramic_overview", [])
            if overview:
                tops = " · ".join(o["subject"] for o in overview[:2] if o.get("subject"))
                fmt_parts.append(f"[OVERVIEW] {tops}")

            # [DOMAIN] L11 domain strategy knowledge
            domain = injection.get("domain_knowledge", [])
            if domain:
                tops = " · ".join(d["subject"] for d in domain[:2] if d.get("subject"))
                fmt_parts.append(f"[DOMAIN] {tops}")

            # [GRAPH] edges live query — replaces old L8 keyword query
            graph = injection.get("graph_relations", {})
            counts = graph.get("counts", {}) if isinstance(graph, dict) else {}
            if counts.get("total", 0) > 0:
                parts = []
                # Line 1: time-window counts
                parts.append(f"[GRAPH] Edges: 24h={counts['last_24h']} | 7d={counts['last_7d']} | total={counts['total']}")

                # Line 2: type distribution (top 6, cap at ~120 chars)
                types = graph.get("types", [])
                if types:
                    type_str = ", ".join(f"{t['type']} {t['count']}" for t in types[:6])
                    if len(type_str) > 120:
                        type_str = type_str[:117] + "..."
                    parts.append(f"Types: {type_str}")

                # Line 3: recent edges (up to 3)
                recent = graph.get("recent", [])
                if recent:
                    recent_str = " | ".join(
                        f"#{r['source']} → #{r['target']} ({r['type']})"
                        for r in recent[:3]
                    )
                    parts.append(f"Recent: {recent_str}")

                # Line 4: hub nodes (up to 3)
                hubs = graph.get("hubs", [])
                if hubs:
                    hubs_str = ", ".join(
                        f"#{h['id']} ({h['edge_count']} edges)" for h in hubs[:3]
                    )
                    parts.append(f"Hubs: {hubs_str}")

                fmt_parts.append("\n".join(parts))

            # [DISTILL] pending groups
            try:
                from memall.mcp.tools.distill import handle as _distill_handle
                _distill_result = json.loads(_distill_handle({"action": "list", "limit": 5}))
                _pending = _distill_result.get("pending", [])
                if _pending:
                    fmt_parts.append(f"[DISTILL] {len(_pending)} 组待写摘要，调 memall_distill_pending 查看")
            except Exception:
                pass

            result["injection_formatted"] = chr(10).join(fmt_parts)

        return result
    finally:
        conn.close()


def session_end(session_id: str, auto_extract: bool = False) -> dict:
    """End a session and auto-summarize captured memories.
    Optionally auto-extract facts to shared_memories (Phase 8).
    Creates L4 session memory when count > 3."""
    conn = get_conn()
    try:
        _ensure_sessions_table(conn)
        now = datetime.now(timezone.utc).isoformat()

        # Count memories captured during this session (since started_at)
        # Fix Bug-4: 改为幂等 - 去掉 status='active' 限制，重复 end 返回已有 ended 信息
        row = conn.execute(
            "SELECT started_at, agent_name, status, ended_at, memory_count, summary FROM sessions WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        if not row:
            return {"error": "session not found", "session_id": session_id}

        # Fix Bug-4: 幂等 - 如果已 ended，直接返回已有信息
        if row["status"] == "ended":
            return {
                "session_id": session_id,
                "agent_name": row["agent_name"],
                "memory_count": row["memory_count"],
                "summary": row["summary"],
                "ended_at": row["ended_at"],
                "status": "ended",
                "note": "session was already ended (idempotent)"
            }

        started_at = row["started_at"]
        agent_name = row["agent_name"]

        # Build safe parameterized query
        where_parts = ["created_at >= ?"]
        params = [started_at]
        if agent_name:
            where_parts.append("agent_name = ?")
            params.append(agent_name)
        where = " AND ".join(where_parts)
        count = conn.execute(
            f"SELECT COUNT(*) FROM memories WHERE {where}", params
        ).fetchone()[0]

        # Simple summary: top 5 categories and sample content
        cats = conn.execute(
            f"SELECT category, COUNT(*) as cnt FROM memories WHERE {where} GROUP BY category ORDER BY cnt DESC LIMIT 5",
            params,
        ).fetchall()
        cat_summary = ", ".join([f"{r['category']}({r['cnt']})" for r in cats])

        samples = conn.execute(
            f"SELECT content FROM memories WHERE {where} ORDER BY created_at DESC LIMIT 3",
            params,
        ).fetchall()
        sample_text = " | ".join([r["content"][:80] for r in samples])

        summary = f"[{agent_name or 'unknown'}] {count} memories in {cat_summary}. Latest: {sample_text}"[:500]

        conn.execute(
            "UPDATE sessions SET ended_at = ?, memory_count = ?, summary = ?, status = 'ended' WHERE session_id = ?",
            (now, count, summary, session_id),
        )
        conn.commit()

        result = {
            "session_id": session_id,
            "memory_count": count,
            "summary": summary,
            "ended_at": now,
            "status": "ended",
        }

        # L4 session memory: only when session has enough content
        if count > 3:
            # Determine confidence: highest confidence among session memories
            conf_row = conn.execute(
                f"SELECT MAX(confidence) as max_conf FROM memories WHERE {where}", params
            ).fetchone()
            session_conf = conf_row["max_conf"] if conf_row and conf_row["max_conf"] else 0.5
            # Boost confidence for sessions with P0/P1 content
            has_high = conn.execute(
                f"SELECT COUNT(*) as cnt FROM memories WHERE {where} AND level IN ('P0', 'P1')", params
            ).fetchone()[0]
            if has_high > 0:
                session_conf = max(session_conf, 0.8)

            # Determine visibility: shared unless ALL session memories are private
            non_private = conn.execute(
                f"SELECT COUNT(*) as cnt FROM memories WHERE {where} AND visibility != 'private'", params
            ).fetchone()[0]
            session_vis = "private" if non_private == 0 else "shared"

            # Build structured L4 metadata
            participants_rows = conn.execute(
                f"SELECT DISTINCT agent_name FROM memories WHERE {where} AND agent_name IS NOT NULL AND agent_name != ''",
                params,
            ).fetchall()
            participants = [r["agent_name"] for r in participants_rows]

            decision_rows = conn.execute(
                f"SELECT content FROM memories WHERE {where} AND category = 'decision' ORDER BY created_at DESC LIMIT 3",
                params,
            ).fetchall()
            key_decisions = [r["content"][:100] for r in decision_rows]

            last_row = conn.execute(
                f"SELECT content FROM memories WHERE {where} ORDER BY created_at DESC LIMIT 1",
                params,
            ).fetchone()
            continuation_note = ""
            if last_row:
                text = last_row["content"]
                for pat in [r'下一步[：:\s]*(.{5,80})', r'继续[：:\s]*(.{5,80})', r'next[：:\s]*(.{5,80})', r'follow.up[：:\s]*(.{5,80})']:
                    m = re.search(pat, text, re.I)
                    if m:
                        continuation_note = m.group(1).strip()[:100]
                        break

            l4_metadata = {
                "session_id": session_id,
                "participants": participants,
                "key_decisions": key_decisions,
                "continuation_note": continuation_note,
            }

            # Build L4 content with key decisions
            parts = [f"[L4 会话] {agent_name or 'unknown'} · {count}条记忆 · {cat_summary}"]
            if key_decisions:
                dec_text = "；".join(d[:80] for d in key_decisions[:3] if d)
                parts.append(f"关键决策：{dec_text}")
            if continuation_note:
                parts.append(f"后续：{continuation_note}")
            l4_content = "。".join(parts)

            from memall.core.models import MemoryInput
            from memall.core.thin_waist import capture
            l4_id = capture(MemoryInput(
                content=l4_content[:2000],
                level="L4",
                owner="system",
                agent_name=agent_name or "system",
                category="session",
                subject=f"会话 {session_id} 摘要",
                confidence=session_conf,
                visibility=session_vis,
                metadata=l4_metadata,
            ))
            result["l4_memory_id"] = l4_id

            # ── Auto L6 reflection (natural language session summary) ──
            try:
                l6_parts = [f"会话总结：本次会话记录了 {count} 条记忆"]
                if cat_summary:
                    l6_parts.append(f"集中在 {cat_summary}")
                if key_decisions:
                    dec_text = "；".join(d[:60] for d in key_decisions[:3] if d)
                    l6_parts.append(f"关键决策：{dec_text}")
                if continuation_note:
                    l6_parts.append(f"后续关注：{continuation_note}")
                l6_content = "。".join(l6_parts) + "。"
                l6_ch = hashlib.sha256(l6_content.encode()).hexdigest()
                l6_subj = f"[L6] {agent_name or 'system'} · {count}条·{cat_summary[:20]}"
                conn.execute(
                    "INSERT OR IGNORE INTO memories "
                    "(content, content_hash, level, owner, agent_name, category, project, summary, "
                    "occurred_at, created_at, updated_at, confidence, visibility, metadata) "
                    "VALUES (?, ?, 'L6', 'system', ?, 'reflection', ?, ?, ?, ?, ?, ?, ?, ?)",
                    (l6_content[:2000], l6_ch, agent_name or "system",
                     session_project, l6_subj, now, now, now, 0.6, "private", "{}"),
                )
            except Exception:
                logger.warning("session.py: silent error", exc_info=True)

        # Phase 8: auto-extract facts to shared_memories
        if auto_extract:
            from memall.mcp.federation_tools import auto_extract as _auto_extract

            extraction = _auto_extract(session_id)
            result["extraction"] = extraction

        # ── Correction compliance check ──
        # Log whether the session agent has STRONG corrections it should have followed
        try:
            from .improve import get_active_corrections
            strong = [c for c in get_active_corrections(agent_name or "")
                      if c.get("severity", "SUGGEST") == "STRONG"]
            if strong:
                result["corrections_check"] = {
                    "total_strong": len(strong),
                    "note": f"session had {len(strong)} mandatory correction rules that should have been followed",
                }
        except Exception:
            logger.warning("session.py: silent error", exc_info=True)

        return result
    finally:
        conn.close()


# ── Pipeline harvest step ─────────────────────────────────────────────


def harvest_step() -> dict:
    """Pipeline step: scan active sessions, generate L4/L6 for sessions
    that have memories but no output yet.

    Does NOT end sessions — only generates missing L4/L6 output.
    Human-friendly: if you return hours later, your session is still active.
    """
    conn = get_conn()
    try:
        _ensure_sessions_table(conn)
        active = conn.execute(
            "SELECT session_id, started_at, agent_name FROM sessions WHERE status = 'active'"
        ).fetchall()
        harvested = 0
        l4_count = 0
        l6_count = 0
        for row in active:
            sid = row["session_id"]
            started_at = row["started_at"]
            agent_name = row["agent_name"]
            if not agent_name:
                continue
            result = _harvest_session(conn, sid, started_at, agent_name, end_session=False)
            conn.commit()
            if result["l4_created"] or result["l6_created"]:
                harvested += 1
                l4_count += 1 if result["l4_created"] else 0
                l6_count += 1 if result["l6_created"] else 0
        return {
            "scanned": len(active),
            "harvested": harvested,
            "l4_created": l4_count,
            "l6_created": l6_count,
        }
    finally:
        conn.close()


def session_summary(session_id: str = None, agent_name: str = None, limit: int = 5) -> dict:
    """Get session summary. If session_id given, return that session.
    Otherwise returns recent sessions for agent_name."""
    conn = get_conn()
    try:
        _ensure_sessions_table(conn)

        if session_id:
            row = conn.execute(
                "SELECT * FROM sessions WHERE session_id = ?", (session_id,)
            ).fetchone()
            if not row:
                return {"error": "session not found"}
            return {
                "session_id": row["session_id"],
                "agent_name": row["agent_name"],
                "started_at": row["started_at"],
                "ended_at": row["ended_at"],
                "memory_count": row["memory_count"],
                "summary": row["summary"],
                "status": row["status"],
            }

        where_parts = ["1=1"]
        params = []
        if agent_name:
            where_parts.append("agent_name = ?")
            params.append(agent_name)

        rows = conn.execute(
            f"SELECT * FROM sessions WHERE {' AND '.join(where_parts)} ORDER BY started_at DESC LIMIT ?",
            params + [limit],
        ).fetchall()

        sessions = []
        for r in rows:
            sessions.append({
                "session_id": r["session_id"],
                "agent_name": r["agent_name"],
                "started_at": r["started_at"],
                "ended_at": r["ended_at"],
                "memory_count": r["memory_count"],
                "status": r["status"],
            })
        return {"sessions": sessions, "total": len(sessions)}
    finally:
        conn.close()
