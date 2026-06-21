import logging

logger = logging.getLogger(__name__)
"""Session lifecycle management for MCP protocol.

Provides session_start / session_end / session_summary for tracking
conversational sessions across Agents.

Table: sessions(session_id TEXT PK, agent_name TEXT, started_at TEXT,
ended_at TEXT, memory_count INTEGER, summary TEXT, status TEXT)
"""

import json
import re
import uuid
from datetime import datetime, timedelta, timezone
from memall.core.db import get_conn


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
    """Inline session-end logic: marks session as ended, counts memories, creates L4 if > 3.

    Uses the provided connection (same as caller) to avoid SQLite lock
    from cross-connection writes that session_end() would trigger.
    """
    row = conn.execute(
        "SELECT started_at, agent_name, status FROM sessions WHERE session_id = ?",
        (session_id,),
    ).fetchone()
    if not row or row["status"] == "ended":
        return
    now = datetime.now(timezone.utc).isoformat()
    started_at = row["started_at"]
    agent_name = row["agent_name"]

    # Count memories since session started
    where = ["created_at >= ?", "agent_name = ?"]
    params = [started_at, agent_name]
    count = conn.execute(
        f"SELECT COUNT(*) FROM memories WHERE {' AND '.join(where)}", params
    ).fetchone()[0]

    # Update session
    cats = conn.execute(
        f"SELECT category, COUNT(*) as cnt FROM memories WHERE {' AND '.join(where)} GROUP BY category ORDER BY cnt DESC LIMIT 3",
        params,
    ).fetchall()
    cat_summary = ", ".join([f"{r['category']}({r['cnt']})" for r in cats])
    summary = f"[{agent_name}] {count} memories in {cat_summary}"[:500]
    conn.execute(
        "UPDATE sessions SET ended_at = ?, memory_count = ?, summary = ?, status = 'ended' WHERE session_id = ?",
        (now, count, summary, session_id),
    )

    # L4 session memory when count > 3 (inline insert, avoid capture() which opens new conn)
    if count > 3:
        decision_rows = conn.execute(
            f"SELECT content FROM memories WHERE {' AND '.join(where)} AND category = 'decision' ORDER BY created_at DESC LIMIT 3",
            params,
        ).fetchall()
        key_decisions = [r["content"][:100] for r in decision_rows]
        l4_content = f"[L4 会话] {agent_name} · {count}条记忆 · {cat_summary}"
        import hashlib
        ch = hashlib.sha256(l4_content.encode()).hexdigest()
        conn.execute(
            "INSERT OR IGNORE INTO memories (content, content_hash, level, owner, agent_name, category, summary, occurred_at, created_at, updated_at, confidence, visibility, metadata) "
            "VALUES (?, ?, 'L4', 'system', ?, 'session', ?, ?, ?, ?, ?, ?, ?)",
            (l4_content[:2000], ch, agent_name, f"会话 {session_id} 摘要",
             now, now, now, 0.5, "shared",
             json.dumps({"session_id": session_id, "key_decisions": key_decisions})),
        )


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


def session_start(agent_name: str = "", auto_inject: bool = False) -> dict:
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

            # L4 cross-session summaries: latest 3 (shared across agents)
            recent_l4 = conn.execute(
                "SELECT id, content, summary, subject, metadata, created_at FROM memories "
                "WHERE level = 'L4' AND LENGTH(TRIM(content)) > 5 "
                "ORDER BY created_at DESC LIMIT 3",
            ).fetchall()
            l4_summaries = []
            for r in recent_l4:
                meta = {}
                try:
                    meta = json.loads(r["metadata"]) if isinstance(r["metadata"], str) else r["metadata"]
                except Exception:
                    logger.warning("session.py: silent error", exc_info=True)
                l4_summaries.append({
                    "id": r["id"],
                    "subject": r["subject"],
                    "summary": r["summary"] or r["content"][:200],
                    "participants": meta.get("participants", []),
                    "key_decisions": (meta.get("key_decisions") or [])[:3],
                    "continuation_note": meta.get("continuation_note", ""),
                    "created_at": r["created_at"],
                })
            result["l4_recent"] = l4_summaries

            # L5 active todos (shared across agents)
            active_l5 = conn.execute(
                "SELECT id, content, summary, subject, metadata, level, created_at FROM memories "
                "WHERE level = 'L5' AND LENGTH(TRIM(content)) > 5 "
                "ORDER BY created_at DESC LIMIT 20",
            ).fetchall()
            l5_todos = []
            seen_subjects = set()
            for r in active_l5:
                meta = {}
                try:
                    meta = json.loads(r["metadata"]) if isinstance(r["metadata"], str) else r["metadata"]
                except Exception:
                    logger.warning("session.py: silent error", exc_info=True)
                status = (meta.get("status") or "active") if isinstance(meta, dict) else "active"
                if status != "active":
                    continue
                if not isinstance(meta, dict):
                    meta = {}
                # Dedup by subject[:40] to avoid duplicate L5 entries
                subject_key = (r["subject"] or "")[:40]
                if subject_key in seen_subjects:
                    continue
                seen_subjects.add(subject_key)
                l5_todos.append({
                    "id": r["id"],
                    "subject": r["subject"],
                    "summary": r["summary"] or r["content"][:200],
                    "assignee": meta.get("assignee", ""),
                    "depends_on": meta.get("depends_on", []),
                    "level_tag": {"P0": "(P0)", "P1": "(P1)", "P2": "(P2)"}.get(r["level"] if r["level"] else "", ""),
                    "created_at": r["created_at"],
                })
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
                        "WHERE level = 'L3' AND category = ? ORDER BY created_at DESC LIMIT 2",
                        (last_cat["category"],),
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

            # ── Formatted injection text (token-efficient, no emoji) ──
            fmt_parts = [
                "[INSTRUCTION] 你必须在第一段回复中向用户概括以下内容，不能只说'收到了'。"
                " 当用户问开放性问题（'最近如何'、'进展'、'我们到哪了'）时，先调用 timeline() 或 memall_ask(mode='pattern') 获取上下文再回应。",
            ]

            # [PROFILE] section from identity_traits
            id_traits = injection.get("identity_traits", {})
            l1_list = id_traits.get("l1_identity", [])
            l7_list = id_traits.get("l7_preferences", [])
            psum = id_traits.get("persona_summary", {})
            if l1_list or l7_list or psum:
                profile_lines = ["[PROFILE] 当前 Agent 画像"]
                if l1_list:
                    l1_text = " · ".join(t["snippet"] for t in l1_list[:4] if t.get("snippet"))
                    profile_lines.append(f"  L1身份: {l1_text}")
                if l7_list:
                    l7_text = " · ".join(t["snippet"] for t in l7_list[:4] if t.get("snippet"))
                    profile_lines.append(f"  L7偏好: {l7_text}")
                if psum.get("prototype_cn"):
                    parts = [f"认知类型: {psum['prototype_cn']}（{psum.get('prototype_en','')}）"]
                    cs = psum.get("certainty_score", 0)
                    if cs:
                        parts.append(f"自信指数 {cs*100:.0f}%")
                    dr = psum.get("decision_ratio", 0)
                    if dr:
                        parts.append(f"决策密度 {dr*100:.0f}%")
                    db = psum.get("domain_breadth", 0)
                    if db:
                        parts.append(f"知识广度 {db} 个领域")
                    profile_lines.append("  " + " · ".join(parts))
                fmt_parts.append("")
                fmt_parts.extend(profile_lines)

                # [L7约束] — derive behavioral constraints from preferences
                if l7_list:
                    l7_constraints = []
                    for t in l7_list[:5]:
                        snip = (t.get("snippet") or "").lower()
                        if not snip:
                            continue
                        # Map preference keywords to behavioral rules
                        if any(kw in snip for kw in ["sqlite", "postgresql", "零依赖", "部署"]):
                            l7_constraints.append("候选方案优先本地零依赖，避免引入外部服务")
                        elif any(kw in snip for kw in ["简洁", "精简", "高效", "最小", "轻量"]):
                            l7_constraints.append("方案应优先选择最简洁的实现路径")
                        elif any(kw in snip for kw in ["python", "pandas", "numpy"]):
                            l7_constraints.append("实现优先用 Python 生态，尽量减少新语言/工具引入")
                        elif any(kw in snip for kw in ["不用", "避免", "不要", "排斥"]):
                            l7_constraints.append("避免使用你表达过排斥的技术方案")
                        elif any(kw in snip for kw in ["mcp", "protocol", "协议"]):
                            l7_constraints.append("优先以 MCP 协议为标准接口")
                    if l7_constraints:
                        fmt_parts.append(f"[L7约束] 基于你的偏好，本次会话应遵循：")
                        for c in l7_constraints:
                            fmt_parts.append(f"  ⚠️ {c}")
            if p0_count > 0:
                fmt_parts.append(f"[ALERT] 有 {p0_count} 条 P0 紧急记忆未处理")
            if l5_todos:
                fmt_parts.append(f"[TODO] 活跃待办 ({len(l5_todos)}项)")
                for t in l5_todos[:10]:
                    fmt_parts.append(f"  - {t['subject'][:60]} {t['level_tag']}")
            if l4_summaries:
                fmt_parts.append("")
                fmt_parts.append(f"[SUMMARY] 最近会话")
                for s in l4_summaries:
                    ds = (s["created_at"] or "")[:10]
                    dec = s["key_decisions"]
                    note = s["continuation_note"]
                    line = f"  [{ds}] {s['summary'][:80]}"
                    if dec:
                        line += " | " + " | ".join(d[:30] for d in dec[:2])
                    if note:
                        line += " > " + note[:40]
                    fmt_parts.append(line)

            # [TIMELINE] top 5 memories by temporal_weight
            timeline_rows = conn.execute("""
                SELECT id, level, category, subject, content, agent_name, created_at, metadata
                FROM memories WHERE LENGTH(TRIM(content)) > 5
                ORDER BY
                  CASE WHEN json_extract(metadata, '$.temporal_weight') IS NOT NULL
                    THEN json_extract(metadata, '$.temporal_weight') ELSE 0.0 END DESC,
                  created_at DESC
                LIMIT 5
            """).fetchall()
            if timeline_rows:
                fmt_parts.append("")
                fmt_parts.append(f"[TIMELINE] 最近 {len(timeline_rows)} 条记忆")
                for t in timeline_rows:
                    raw = t["created_at"] or ""
                    ts = raw[5:10] + " " + raw[11:16] if len(raw) > 16 else raw[5:19]
                    lvl = t["level"] or ""
                    cat = (t["category"] or "")[:10]
                    subj = (t["subject"] or t["content"] or "")[:60]
                    agent = t["agent_name"] or ""
                    line = f"  [{ts}] {lvl} {cat}"
                    if agent:
                        line += f" @{agent}"
                    line += f" · {subj}"
                    fmt_parts.append(line)

            # [CONTINUITY] session continuity bridge
            if agent_name:
                try:
                    # Reuse pre-queried data
                    h = narrative_data.get("hours_elapsed")
                    new_count = narrative_data.get("new_memories", 0)
                    if h is not None:
                        # New epochs since previous session
                        new_epochs = conn.execute(
                            "SELECT COUNT(*) as c FROM epochs WHERE "
                            "agent_name = ? AND started_at > ?",
                            (agent_name, prev_session["ended_at"]),
                        ).fetchone()["c"] if prev_session else 0
                        # New L4 decisions since previous session
                        new_decisions = conn.execute(
                            "SELECT COUNT(*) as c FROM memories WHERE "
                            "agent_name = ? AND level = 'L4' AND category = 'decision' AND created_at > ?",
                            (agent_name, prev_session["ended_at"]),
                        ).fetchone()["c"] if prev_session else 0
                        # Latest narrative (weekly or phase)
                        latest_narrative = conn.execute(
                            "SELECT narrative_text, narrative_type FROM narratives "
                            "WHERE agent_name = ? ORDER BY generated_at DESC LIMIT 1",
                            (agent_name,),
                        ).fetchone()
                        continuity_parts = [""]
                        time_str = f"距上次会话约 {h} 小时"
                        if h > 48:
                            time_str = f"距上次会话约 {h // 24} 天"
                        cont = time_str
                        if new_count > 0:
                            cont += f"，期间新增 {new_count} 条记忆"
                        if new_epochs > 0:
                            cont += f"，进入 {new_epochs} 个新阶段"
                        if new_decisions > 0:
                            cont += f"，做出 {new_decisions} 个关键决策"
                        if latest_narrative and latest_narrative["narrative_text"]:
                            ntype = {"weekly": "周报", "monthly": "月报", "phase": "阶段"}.get(
                                latest_narrative["narrative_type"], "叙事"
                            )
                            nsum = latest_narrative["narrative_text"][:100].replace("\n", " ")
                            cont += f"\n  最新{ntype}: {nsum}"
                        continuity_parts.append(f"[CONTINUITY] {cont}")
                        fmt_parts.extend(continuity_parts)
                except Exception:
                    logger.warning("session.py: silent error", exc_info=True)

            # [EPOCH] current active epoch
            if agent_name:
                epoch_row = conn.execute(
                    "SELECT label, started_at FROM epochs "
                    "WHERE agent_name = ? AND ended_at IS NULL "
                    "ORDER BY started_at DESC LIMIT 1",
                    (agent_name,),
                ).fetchone()
                if epoch_row and epoch_row["label"]:
                    narrative_data["epoch_label"] = epoch_row["label"]
                    fmt_parts.append("")
                    fmt_parts.append(f"[EPOCH] 当前阶段: {epoch_row['label'][:60]} (始于 {epoch_row['started_at'][:10]})")

            # [ARC STATUS] open decisions
            if agent_name:
                open_arcs = conn.execute(
                    "SELECT id, subject, arc_status, created_at FROM memories "
                    "WHERE level = 'L4' AND arc_status IS NOT NULL AND arc_status != 'closed' "
                    "AND agent_name = ? ORDER BY created_at DESC LIMIT 5",
                    (agent_name,),
                ).fetchall()
                if open_arcs:
                    narrative_data["open_arcs_count"] = len(open_arcs)
                    fmt_parts.append("")
                    fmt_parts.append(f"[ARC STATUS] 未闭合决策 ({len(open_arcs)}条)")
                    stale_cutoff = (datetime.now(timezone.utc) - timedelta(days=21)).isoformat()
                    for a in open_arcs:
                        badge = "进行中" if a["arc_status"] == "in_progress" else "无进展"
                        if a["arc_status"] == "open" and (a["created_at"] or "") < stale_cutoff:
                            badge = "已搁置(>21d)"
                        fmt_parts.append(f"  · {a['subject'][:60]} [{badge}]")

            # [DISCUSSION] active L5 discussions
            from memall.pipeline.convergence import list_active_discussions
            active_topics = list_active_discussions()
            if active_topics:
                fmt_parts.append("")
                fmt_parts.append(f"[DISCUSSION] 活跃讨论 ({len(active_topics)}个话题)")
                for t in active_topics:
                    participants = t.get("participants") or []
                    responded = t.get("responded_agents") or []
                    response_count = t.get("response_count") or 0
                    unconfirmed = [a for a in participants if a not in responded]
                    if unconfirmed:
                        badge = f"待回复: {' '.join(unconfirmed)}"
                    else:
                        badge = "全员已回复"
                    title = t.get("subject", "(无标题)")[:50]
                    fmt_parts.append(f"  · {title} [{response_count}/{len(participants)}] {badge}")

            # [TASKS] active L5 tasks assigned to this agent (execution-side closure)
            if agent_name:
                try:
                    from .task_lifecycle import list_active_tasks, list_blocked_tasks
                    my_tasks = list_active_tasks(agent_name)
                    if my_tasks:
                        fmt_parts.append("")
                        fmt_parts.append(f"[TASKS] ????? ({len(my_tasks)}?)")
                        for t in my_tasks:
                            ack = "???" if t.get("acknowledged_at") else "???"
                            age = t.get("created_at", "")[:10]
                            fmt_parts.append(f"  ? #{t['task_id']} {t['subject'][:50]} [{ack}] ({age})")
                    blocked = list_blocked_tasks(agent_name)
                    if blocked:
                        fmt_parts.append(f"[TASKS] ??? ({len(blocked)}?, ?????)")
                        for b in blocked:
                            fmt_parts.append(f"  ? #{b['task_id']} {b['subject'][:50]} ? {b.get('blocked_reason', '')[:60]}")
                except Exception:
                    logger.warning("session.py: silent error", exc_info=True)

            if l3_workflows:
                fmt_parts.append("")
                fmt_parts.append(f"[WORKFLOW] 相关流程")
                for w in l3_workflows:
                    txt = w["summary"] or w["subject"]
                    fmt_parts.append(f"  1. {w['subject'][:50]} — {txt[:100]}")

            # [CORRECTIONS] active correction rules (personal + global)
            if agent_name:
                try:
                    from .improve import get_active_corrections
                    corrections = get_active_corrections(agent_name)
                    if corrections:
                        fmt_parts.append("")
                        fmt_parts.append(f"━━━ 修正规则 ({len(corrections)}条) ━━━")
                        # STRONG rules first (mandatory — must follow)
                        strong = [c for c in corrections if c.get("severity", "SUGGEST") == "STRONG"]
                        for c in strong:
                            badge = "全局" if c["category"] == "global" else "个人"
                            fmt_parts.append(f"⚠️  [必须遵守]{badge} {c['rule_text'][:120]}")
                        # SUGGEST rules (advisory)
                        suggest = [c for c in corrections if c.get("severity", "SUGGEST") != "STRONG"]
                        for c in suggest:
                            badge = "全局" if c["category"] == "global" else "个人"
                            fmt_parts.append(f"💡  [建议]{badge} {c['rule_text'][:120]}")
                        # Verification prompt
                        if strong:
                            fmt_parts.append(f"   ⚠️ 上述 {len(strong)} 条为硬性修正规则，本次会话必须遵守。")
                            fmt_parts.append(f"   session_end 时将验证是否已落实，请主动避坑。")
                except Exception:
                    logger.warning("session.py: silent error", exc_info=True)

            # [CROSS-AGENT] other agents' perspectives on related topics (Direction 3)
            try:
                cross_lines = _build_cross_agent_section(conn, agent_name)
                if cross_lines:
                    fmt_parts.append("")
                    fmt_parts.extend(cross_lines)
            except Exception:
                logger.warning("session.py: silent error", exc_info=True)

            # [WEEKLY] weekly narrative surfacing
            try:
                weekly = conn.execute(
                    "SELECT narrative_text, generated_at, agent_name FROM narratives "
                    "WHERE narrative_type = 'weekly' "
                    "ORDER BY generated_at DESC LIMIT 1"
                ).fetchone()
                if weekly and weekly["narrative_text"]:
                    raw = weekly["narrative_text"]
                    # Remove leading bracketed tags like [周报]
                    clean = re.sub(r'^\[[^\]]*\]\s*', '', raw)
                    # Take first 120 chars as the gist
                    gist = clean[:120].rsplit('。', 1)[0] + '。' if '。' in clean[:120] else clean[:120]
                    fmt_parts.append("")
                    fmt_parts.append(f"[WEEKLY] 本周回顾: {gist}")
                    narrative_data["has_weekly"] = True
            except Exception:
                logger.warning("session.py: silent error", exc_info=True)

            # [PULSE] memory pulse — gentle nudge for stale P0/P1 items
            try:
                stale_items = conn.execute(
                    "SELECT id, subject, level FROM memories "
                    "WHERE level IN ('P0', 'P1') "
                    "AND COALESCE(json_extract(metadata, '$.done'), 0) = 0 "
                    "AND COALESCE(json_extract(metadata, '$.status.value'), 'active') != 'archived' "
                    "AND subject NOT LIKE '[%' "  # skip system-internal tagged subjects
                    "AND subject != '' "
                    "AND agent_name NOT LIKE 't_%' "  # skip test agents
                    "AND datetime(created_at) < datetime('now', '-2 days') "
                    "ORDER BY level ASC, created_at ASC LIMIT 3"
                ).fetchall()
                if stale_items:
                    pulse_parts = []
                    for s in stale_items:
                        tag = {"P0": "紧急", "P1": "重要"}.get(s["level"], "")
                        pulse_parts.append(f"{tag}「{s['subject'][:40]}」")
                    fmt_parts.append("")
                    fmt_parts.append(f"[PULSE] 顺便一提: {'，'.join(pulse_parts)} 已经搁置好几天了")
                    narrative_data["has_pulse"] = True
            except Exception:
                logger.warning("session.py: silent error", exc_info=True)

            # ── [NARRATIVE] human-readable greeting (built last so all data is populated) ──
            narrative_line = _build_narrative_greeting(narrative_data, agent_name)
            if narrative_line:
                # Insert right after INSTRUCTION line (index 0)
                # Skip any blank lines after it
                insert_idx = 1
                while insert_idx < len(fmt_parts) and fmt_parts[insert_idx].strip() == "":
                    insert_idx += 1
                fmt_parts.insert(insert_idx, "")
                fmt_parts.insert(insert_idx, narrative_line)

            result["injection_formatted"] = "\n".join(fmt_parts)

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

            # Build L4 content
            l4_content = f"[L4 会话] {agent_name or 'unknown'} · {count}条记忆 · {cat_summary}"

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
                conn.execute(
                    "INSERT OR IGNORE INTO memories "
                    "(content, content_hash, level, owner, agent_name, category, summary, "
                    "occurred_at, created_at, updated_at, confidence, visibility, metadata) "
                    "VALUES (?, ?, 'L6', 'system', ?, 'reflection', ?, ?, ?, ?, ?, ?, ?)",
                    (l6_content[:2000], l6_ch, agent_name or "system",
                     f"会话 {session_id} 自动反思", now, now, now, 0.6, "private", "{}"),
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
