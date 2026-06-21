"""
Phase 22: Self-Improvement — extract correction rules from L6 reflections.

Scans recent L6 reflections per agent, clusters repeated patterns,
and generates [CORRECTIONS] rules.  Cross-agent patterns produce
global rules shared by all agents.  Rules auto-expire when enough
new L6 no longer exhibit the same pattern.
"""

import json
from collections import Counter, defaultdict
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Set
from memall.core.db import get_conn


# ── Pattern keywords matched against L6 summary/content ──

_PATTERN_RULES: List[Dict[str, Any]] = [
    {
        "id": "human_first",
        "keywords": ["human-first", "人类行为", "先想人", "不要默认", "改了数据"],
        "rule_text": "涉及完成/解决/归档/清理时，先问'人在真实世界怎么处理'，再写代码，不要默认走'工程师改数据'路线。",
        "min_hits": 1,
        "severity": "STRONG",
        "scenario": "修改数据/归档/清理时",
    },
    {
        "id": "verify_before_act",
        "keywords": ["先查", "check", "验证", "根因", "确认"],
        "rule_text": "修改前先查相关记录和设计文档，确认设计意图未变更再动手。",
        "min_hits": 1,
        "severity": "STRONG",
        "scenario": "修改代码/配置前",
    },
    {
        "id": "session_end_unreliable",
        "keywords": ["session_end", "假想依赖", "不被调用", "没人调", "钩子"],
        "rule_text": "session_end 不是可靠钩子（几乎不被调用）。收尾逻辑应挂在 session_start 的 stale 检测中，而非依赖结束回调。",
        "min_hits": 1,
        "severity": "WARNING",
        "scenario": "设计收尾/清理逻辑时",
    },
    {
        "id": "mark_done_not_alter_level",
        "keywords": ["done", "完成标记", "标记完成", "不改 level", "降级"],
        "rule_text": "完成任务应在 metadata.done=true 标记，不要改 level 或删除。内存应保留在时间线中。",
        "min_hits": 1,
        "severity": "STRONG",
        "scenario": "处理任务状态时",
    },
    {
        "id": "reflection_not_scanner",
        "keywords": ["反思引擎", "reflect_step", "关键词扫描", "不是反思", "被动升级"],
        "rule_text": "真正的反思应主动沉淀（每次改动后存 L6），而非依赖关键词匹配。reflect_step 是辅助不是主力。",
        "min_hits": 1,
        "severity": "WARNING",
        "scenario": "沉淀经验教训时",
    },
    {
        "id": "inject_token_efficiency",
        "keywords": ["token", "注入", "体积", "injection_formatted", "500"],
        "rule_text": "注入段应尽量精简。优先按需查询（timeline/traverse），而非全量推送。简单查询不应携带 10 个段。",
        "min_hits": 1,
        "severity": "WARNING",
        "scenario": "构造 session 上下文时",
    },
]

# Keywords that indicate a correction has been internalized (反向信号)
_INTERNALIZED_KEYWORDS = [
    "吸取教训", "这次注意了", "没再犯", "已经改", "形成了习惯",
    "不再", "避免了", "提前想到",
]


def _ensure_corrections_table(conn) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS corrections (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agent_name TEXT NOT NULL DEFAULT '',
            pattern_id TEXT NOT NULL,
            rule_text TEXT NOT NULL,
            summary TEXT DEFAULT '',
            category TEXT NOT NULL DEFAULT 'personal',
            source_l6_ids TEXT NOT NULL DEFAULT '[]',
            status TEXT NOT NULL DEFAULT 'active',
            severity TEXT NOT NULL DEFAULT 'SUGGEST',
            scenario TEXT DEFAULT '',
            hit_count INTEGER DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT
        )
    """)
    # Migrate: add severity + scenario columns if table already existed
    cols = [r[1] for r in conn.execute("PRAGMA table_info(corrections)").fetchall()]
    if "severity" not in cols:
        conn.execute("ALTER TABLE corrections ADD COLUMN severity TEXT NOT NULL DEFAULT 'SUGGEST'")
    if "scenario" not in cols:
        conn.execute("ALTER TABLE corrections ADD COLUMN scenario TEXT DEFAULT ''")
    conn.commit()


def improve_step(agent_name: Optional[str] = None) -> Dict[str, Any]:
    """Extract correction rules from recent L6 reflections.

    Steps:
    1. Scan L6 from last 30 days per agent
    2. Match against known pattern keywords
    3. For repeated patterns (>= min_hits), create/update correction rule
    4. Cross-agent: if > 2 agents hit same pattern, promote to global
    5. Expire: deactivate rules with no new hits that have internalized signals

    Args:
        agent_name: Optional filter (scan all agents if None).

    Returns:
        ``{"personal_created": int, "global_created": int,
          "expired": int, "scanned_l6": int}``
    """
    conn = get_conn()
    try:
        _ensure_corrections_table(conn)
        now = datetime.now(timezone.utc).isoformat()
        cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()

        # ── 1. Scan recent L6 ──
        if agent_name:
            l6_rows = conn.execute(
                "SELECT id, content, summary, agent_name, category FROM memories "
                "WHERE level = 'L6' AND created_at >= ? AND LOWER(agent_name) = LOWER(?)",
                (cutoff, agent_name),
            ).fetchall()
        else:
            l6_rows = conn.execute(
                "SELECT id, content, summary, agent_name, category FROM memories "
                "WHERE level = 'L6' AND created_at >= ?",
                (cutoff,),
            ).fetchall()

        if not l6_rows:
            return {"personal_created": 0, "global_created": 0, "expired": 0, "scanned_l6": 0}

        # ── 2. Match patterns per agent ──
        agent_pattern_hits: Dict[str, Dict[str, List[int]]] = defaultdict(lambda: defaultdict(list))
        for r in l6_rows:
            text = f"{r['summary'] or ''} {r['content'] or ''}"
            ag = r["agent_name"] or "unknown"
            for pat in _PATTERN_RULES:
                if any(kw in text for kw in pat["keywords"]):
                    agent_pattern_hits[ag][pat["id"]].append(r["id"])

        # ── 3. Create/update corrections ──
        personal_created = 0
        global_created = 0
        pattern_agent_count: Dict[str, Set[str]] = defaultdict(set)

        for ag, patterns in agent_pattern_hits.items():
            for pat_id, l6_ids in patterns.items():
                if len(l6_ids) < _PATTERN_LOOKUP[pat_id].get("min_hits", 2):
                    continue
                pattern_agent_count[pat_id].add(ag)
                rule_info = _PATTERN_LOOKUP[pat_id]

                # Check if correction already exists for this agent+pattern
                existing = conn.execute(
                    "SELECT id, status, hit_count FROM corrections "
                    "WHERE agent_name = ? AND pattern_id = ? AND category = 'personal'",
                    (ag, pat_id),
                ).fetchone()

                if existing:
                    if existing["status"] == "active":
                        new_hit_count = existing["hit_count"] + len(l6_ids)
                        # Auto-escalate: hit >= 50 → STRONG severity
                        new_severity = "STRONG" if new_hit_count >= 50 else "SUGGEST"
                        conn.execute(
                            "UPDATE corrections SET hit_count = ?, severity = ?, updated_at = ? WHERE id = ?",
                            (new_hit_count, new_severity, now, existing["id"]),
                        )
                else:
                    severity = rule_info.get("severity", "SUGGEST")
                    scenario = rule_info.get("scenario", "")
                    conn.execute(
                        "INSERT INTO corrections (agent_name, pattern_id, rule_text, summary, category, severity, scenario, source_l6_ids, status, created_at) "
                        "VALUES (?, ?, ?, ?, 'personal', ?, ?, ?, 'active', ?)",
                        (ag, pat_id, rule_info["rule_text"], f"基于 {len(l6_ids)} 条反思", severity, scenario, json.dumps(l6_ids), now),
                    )
                    personal_created += 1

        # ── 4. Cross-agent: promote to global ──
        for pat_id, agents in pattern_agent_count.items():
            if len(agents) >= 2:  # > 2 agents → global
                rule_info = _PATTERN_LOOKUP[pat_id]
                existing_global = conn.execute(
                    "SELECT id FROM corrections WHERE agent_name = '' AND pattern_id = ? AND category = 'global'",
                    (pat_id,),
                ).fetchone()
                if not existing_global:
                    all_l6_ids = list(set(
                        lid for ag in agents for lid in agent_pattern_hits[ag].get(pat_id, [])
                    ))
                    severity = rule_info.get("severity", "SUGGEST")
                    scenario = rule_info.get("scenario", "")
                    conn.execute(
                        "INSERT INTO corrections (agent_name, pattern_id, rule_text, summary, category, severity, scenario, source_l6_ids, status, created_at) "
                        "VALUES ('', ?, ?, ?, 'global', ?, ?, ?, 'active', ?)",
                        (pat_id, rule_info["rule_text"],
                         f"跨 {len(agents)} 个 Agent 共识 ({', '.join(sorted(agents))})",
                         severity, scenario, json.dumps(all_l6_ids), now),
                    )
                    global_created += 1

        # ── 5. Expire corrections with no recent hits + internalized signals ──
        expired = 0
        active_corrections = conn.execute(
            "SELECT id, agent_name, pattern_id FROM corrections WHERE status = 'active'"
        ).fetchall()
        for c in active_corrections:
            c_agent = c["agent_name"]
            c_pat = c["pattern_id"]
            # Check recent L6 for this agent+pattern
            recent_matches = conn.execute(
                "SELECT COUNT(*) AS c FROM memories WHERE level = 'L6' AND created_at >= ? "
                "AND (LOWER(agent_name) = LOWER(?) OR ? = '')",
                (cutoff, c_agent, c_agent),
            ).fetchone()["c"]
            if recent_matches > 0:
                continue  # still active, skip expiry
            # No recent matches → check internalization signals
            l6_recent = conn.execute(
                "SELECT content FROM memories WHERE level = 'L6' AND created_at >= ?",
                (cutoff,),
            ).fetchall()
            has_internalized = any(
                any(kw in (r["content"] or "") for kw in _INTERNALIZED_KEYWORDS)
                for r in l6_recent
            )
            if has_internalized or recent_matches == 0:
                conn.execute(
                    "UPDATE corrections SET status = 'expired', updated_at = ? WHERE id = ?",
                    (now, c["id"]),
                )
                expired += 1

        conn.commit()
        return {
            "personal_created": personal_created,
            "global_created": global_created,
            "expired": expired,
            "scanned_l6": len(l6_rows),
        }
    finally:
        conn.close()


def get_active_corrections(agent_name: str) -> List[Dict[str, Any]]:
    """Get active correction rules for an agent (personal + global).

    Returns active rules sorted by hit_count descending (most relevant first).
    """
    conn = get_conn()
    try:
        _ensure_corrections_table(conn)
        rows = conn.execute(
            "SELECT pattern_id, rule_text, summary, category, severity FROM corrections "
            "WHERE status = 'active' AND (LOWER(agent_name) = LOWER(?) OR agent_name = '') "
            "ORDER BY severity DESC, category ASC, hit_count DESC",
            (agent_name,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# Build lookup dict from pattern list
_PATTERN_LOOKUP = {p["id"]: p for p in _PATTERN_RULES}
