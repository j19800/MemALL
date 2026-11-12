"""
Phase 22: Self-Improvement — extract correction rules from L6 reflections.

Scans recent L6 reflections per agent, clusters repeated patterns,
and generates [CORRECTIONS] rules.  Cross-agent patterns produce
global rules shared by all agents.  Rules auto-expire when enough
new L6 no longer exhibit the same pattern.
"""

import json
import logging
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Set
from memall.core.db import get_conn

logger = logging.getLogger(__name__)


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
    # TD-EVOL-TRACK-01: effectiveness/lifecycle observability columns (additive;
    # default 0/empty so old rows and old readers are unaffected).
    for _col, _ddl in (
        ("injected_count", "INTEGER NOT NULL DEFAULT 0"),
        ("last_injected_at", "TEXT DEFAULT ''"),
        ("adopted_count", "INTEGER NOT NULL DEFAULT 0"),
        ("last_adopted_at", "TEXT DEFAULT ''"),
    ):
        if _col not in cols:
            conn.execute(f"ALTER TABLE corrections ADD COLUMN {_col} {_ddl}")
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
                    "SELECT id, status, hit_count, source_l6_ids, severity FROM corrections "
                    "WHERE agent_name = ? AND pattern_id = ? AND category = 'personal'",
                    (ag, pat_id),
                ).fetchone()

                if existing:
                    if existing["status"] == "active":
                        try:
                            prev_ids = set(json.loads(existing["source_l6_ids"] or "[]"))
                        except Exception:
                            prev_ids = set()
                        merged_ids = sorted(prev_ids | {int(x) for x in l6_ids})
                        new_hit_count = len(merged_ids) if merged_ids else int(existing["hit_count"] or 0)
                        # Auto-escalate: hit >= 50 → STRONG severity
                        base_sev = existing["severity"] or "SUGGEST"
                        new_severity = "STRONG" if (new_hit_count >= 50 or base_sev == "STRONG") else "SUGGEST"
                        conn.execute(
                            "UPDATE corrections SET hit_count = ?, severity = ?, source_l6_ids = ?, updated_at = ? WHERE id = ?",
                            (new_hit_count, new_severity, json.dumps(merged_ids), now, existing["id"]),
                        )
                else:
                    severity = rule_info.get("severity", "SUGGEST")
                    scenario = rule_info.get("scenario", "")
                    unique_ids = sorted({int(x) for x in l6_ids})
                    conn.execute(
                        "INSERT INTO corrections (agent_name, pattern_id, rule_text, summary, category, severity, scenario, source_l6_ids, status, hit_count, created_at) "
                        "VALUES (?, ?, ?, ?, 'personal', ?, ?, ?, 'active', ?, ?)",
                        (ag, pat_id, rule_info["rule_text"], f"基于 {len(unique_ids)} 条反思", severity, scenario, json.dumps(unique_ids), len(unique_ids), now),
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
                all_l6_ids = sorted(set(
                    int(lid) for ag in agents for lid in agent_pattern_hits[ag].get(pat_id, [])
                ))
                if not existing_global:
                    severity = rule_info.get("severity", "SUGGEST")
                    scenario = rule_info.get("scenario", "")
                    conn.execute(
                        "INSERT INTO corrections (agent_name, pattern_id, rule_text, summary, category, severity, scenario, source_l6_ids, status, hit_count, created_at) "
                        "VALUES ('', ?, ?, ?, 'global', ?, ?, ?, 'active', ?, ?)",
                        (pat_id, rule_info["rule_text"],
                         f"跨 {len(agents)} 个 Agent 共识 ({', '.join(sorted(agents))})",
                         severity, scenario, json.dumps(all_l6_ids), len(all_l6_ids), now),
                    )
                    global_created += 1
                else:
                    # Merge evidence into existing global rule without creating duplicates.
                    try:
                        prev = conn.execute(
                            "SELECT source_l6_ids, hit_count, severity FROM corrections WHERE id = ?",
                            (existing_global["id"],),
                        ).fetchone()
                        prev_ids = set(json.loads((prev["source_l6_ids"] if prev else "[]") or "[]"))
                    except Exception:
                        prev = None
                        prev_ids = set()
                    merged = sorted(prev_ids | set(all_l6_ids))
                    new_hit_count = len(merged) if merged else int((prev["hit_count"] if prev else 0) or 0)
                    base_sev = (prev["severity"] if prev else None) or rule_info.get("severity", "SUGGEST")
                    new_severity = "STRONG" if (new_hit_count >= 50 or base_sev == "STRONG") else base_sev
                    conn.execute(
                        "UPDATE corrections SET source_l6_ids = ?, hit_count = ?, severity = ?, summary = ?, updated_at = ? WHERE id = ?",
                        (json.dumps(merged), new_hit_count, new_severity,
                         f"跨 {len(agents)} 个 Agent 共识 ({', '.join(sorted(agents))})",
                         now, existing_global["id"]),
                    )

        # ── 5. Expire corrections ──
        # Expire only when (a) internalization signals appear in recent L6, OR
        # (b) the correction is stale (not updated/hit recently) and no recent
        # agent/global L6 activity suggests it is still relevant. Avoid expiring
        # merely because there were no recent L6 rows at all for an inactive agent.
        expired = 0
        stale_cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        active_corrections = conn.execute(
            "SELECT id, agent_name, pattern_id, updated_at, created_at, hit_count FROM corrections WHERE status = 'active'"
        ).fetchall()
        # Preload recent L6 texts once for internalization checks.
        l6_recent_texts = [r["content"] or "" for r in conn.execute(
            "SELECT content FROM memories WHERE level = 'L6' AND created_at >= ?",
            (cutoff,),
        ).fetchall()]
        global_has_recent_l6 = conn.execute(
            "SELECT COUNT(*) AS c FROM memories WHERE level = 'L6' AND created_at >= ?",
            (cutoff,),
        ).fetchone()["c"] > 0
        for c in active_corrections:
            c_agent = c["agent_name"] or ""
            c_pat = c["pattern_id"]
            rule_info = _PATTERN_LOOKUP.get(c_pat, {})
            keywords = rule_info.get("keywords", [])
            # Recent same-scope L6 rows: if pattern keywords still appear, keep active.
            if c_agent:
                scope_rows = conn.execute(
                    "SELECT content, summary FROM memories WHERE level = 'L6' AND created_at >= ? "
                    "AND LOWER(agent_name) = LOWER(?)",
                    (cutoff, c_agent),
                ).fetchall()
            else:
                scope_rows = conn.execute(
                    "SELECT content, summary FROM memories WHERE level = 'L6' AND created_at >= ?",
                    (cutoff,),
                ).fetchall()
            still_hits = False
            for row in scope_rows:
                text = (row["summary"] or row["content"] or "").lower()
                if any(kw.lower() in text for kw in keywords):
                    still_hits = True
                    break
            if still_hits:
                continue

            has_internalized = any(
                any(kw in txt for kw in _INTERNALIZED_KEYWORDS)
                for txt in l6_recent_texts
            ) if (c_agent == "" or scope_rows or global_has_recent_l6) else False

            touched = c["updated_at"] or c["created_at"] or ""
            is_stale = bool(touched and touched < stale_cutoff)
            # Expire if internalized with recent activity, or stale with no recent pattern hits.
            if has_internalized or (is_stale and (scope_rows or global_has_recent_l6)):
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
            "SELECT pattern_id, rule_text, summary, category, severity, "
            "COALESCE(injected_count,0) AS injected_count, "
            "COALESCE(adopted_count,0) AS adopted_count FROM corrections "
            "WHERE status = 'active' AND (LOWER(agent_name) = LOWER(?) OR agent_name = '') "
            "ORDER BY severity DESC, category ASC, hit_count DESC LIMIT 1000",
            (agent_name,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# Build lookup dict from pattern list
_PATTERN_LOOKUP = {p["id"]: p for p in _PATTERN_RULES}


# ── TD-EVOL-TRACK-01: correction effectiveness / lifecycle observability ──
# Additive tracking on top of the existing corrections table. All write
# helpers are config-gated (default OFF) and fail-open: they never change
# extraction, expiry, or injection behavior; they only count.

def _tracking_enabled(kind: str) -> bool:
    """Read evolution.correction_<kind>_tracking_enabled (default False)."""
    try:
        from memall.config import get_config
        return bool(get_config(f"evolution.correction_{kind}_tracking_enabled", False))
    except Exception:
        return False


def record_corrections_injection(agent_name: str, pattern_ids: List[str]) -> int:
    """Best-effort: count that these active corrections were surfaced to `agent_name`.

    No-op (returns 0) unless `evolution.correction_injection_tracking_enabled`
    is True. Matches the same scope as get_active_corrections (personal +
    global). Never raises.
    """
    if not pattern_ids:
        return 0
    if not _tracking_enabled("injection"):
        return 0
    conn = get_conn()
    try:
        _ensure_corrections_table(conn)
        now = datetime.now(timezone.utc).isoformat()
        updated = 0
        for pid in {str(p) for p in pattern_ids if p}:
            cur = conn.execute(
                "UPDATE corrections SET injected_count = injected_count + 1, "
                "last_injected_at = ? WHERE status = 'active' AND pattern_id = ? "
                "AND (LOWER(agent_name) = LOWER(?) OR agent_name = '')",
                (now, pid, agent_name or ""),
            )
            updated += cur.rowcount or 0
        conn.commit()
        return updated
    except Exception:
        logger.warning("improve: correction injection tracking failed", exc_info=True)
        return 0
    finally:
        conn.close()


def record_correction_adoption(agent_name: str, pattern_id: str, *, source: str = "") -> bool:
    """Best-effort: count one observed adoption/compliance event for a correction.

    No-op unless `evolution.correction_adoption_tracking_enabled` is True.
    Callers (session-end compliance checks, manual labeling, future
    LLM-judged evaluation) decide what counts as adoption; this only records.
    """
    if not pattern_id:
        return False
    if not _tracking_enabled("adoption"):
        return False
    conn = get_conn()
    try:
        _ensure_corrections_table(conn)
        now = datetime.now(timezone.utc).isoformat()
        cur = conn.execute(
            "UPDATE corrections SET adopted_count = adopted_count + 1, last_adopted_at = ? "
            "WHERE rowid = (SELECT rowid FROM corrections "
            "WHERE pattern_id = ? AND (LOWER(agent_name) = LOWER(?) OR agent_name = '') "
            "ORDER BY CASE WHEN status = 'active' THEN 0 ELSE 1 END, updated_at DESC LIMIT 1)",
            (now, str(pattern_id), agent_name or ""),
        )
        conn.commit()
        return (cur.rowcount or 0) > 0
    except Exception:
        logger.warning("improve: correction adoption tracking failed", exc_info=True)
        return False
    finally:
        conn.close()


def _auto_adoption_eval_enabled() -> bool:
    """Master gate for the automatic adoption judge (default OFF)."""
    try:
        from memall.config import get_config
        return bool(get_config("evolution.correction_auto_adoption_eval", False))
    except Exception:
        return False


def evaluate_session_corrections(agent_name: str, started_at: str, ended_at: str,
                                 session_id: str = "") -> Dict[str, Any]:
    """TD-EVOL-TRACK-01: automatic adoption/violation judge for a closed session.

    Three-state signal per active correction (conservative by design):
      * adoption   — the rule was surfaced during this session
        (`last_injected_at` within the window) AND no session memory re-shows
        the pattern keywords → `record_correction_adoption()` is attempted.
      * violation  — pattern keywords appear in session-window content; the
        adoption counter is NOT touched and the violation is reported.
      * no_signal  — no injection evidence for this rule (injection tracking
        off, or the rule was never surfaced) → no counter moves at all.

    Gates (all default-off, fail-open, never raises):
      * `evolution.correction_auto_adoption_eval`  — enables this evaluator.
      * `evolution.correction_adoption_tracking_enabled` — required for actual
        writes; with eval on but this off the result is a DRY RUN (verdicts
        only, `written=0`), which is the intended gray-release preview mode.

    Heuristic limitation: keyword presence is a proxy for recurrence, not a
    proof (e.g. "已验证" contains "验证"); that is why adoption additionally
    requires injection evidence and violation only blocks counting (nothing is
    decremented or expired here).
    """
    if not _auto_adoption_eval_enabled():
        return {"evaluated": False, "reason": "disabled"}
    if not agent_name or not started_at:
        return {"evaluated": False, "reason": "missing_window"}
    conn = get_conn()
    try:
        _ensure_corrections_table(conn)
        corr_rows = conn.execute(
            "SELECT id, pattern_id, category, severity, COALESCE(last_injected_at,'') AS last_injected_at "
            "FROM corrections WHERE status = 'active' "
            "AND (LOWER(agent_name) = LOWER(?) OR agent_name = '')",
            (agent_name,),
        ).fetchall()
        if not corr_rows:
            return {"evaluated": True, "session_id": session_id, "rules": [],
                    "adopted": 0, "violations": 0, "no_signal": 0, "written": 0}

        end_cmp = ended_at or datetime.now(timezone.utc).isoformat()
        mem_rows = conn.execute(
            "SELECT COALESCE(summary,'') || ' ' || COALESCE(content,'') AS txt "
            "FROM memories WHERE LOWER(agent_name) = LOWER(?) "
            "AND created_at >= ? AND created_at <= ? LIMIT 200",
            (agent_name, started_at, end_cmp),
        ).fetchall()
        corpus = " ".join((r["txt"] or "") for r in mem_rows).lower()

        adopted = violations = no_signal = written = 0
        detail = []
        for c in corr_rows:
            injected_in_window = bool(c["last_injected_at"]) and (
                started_at <= c["last_injected_at"] <= end_cmp)
            keywords = _PATTERN_LOOKUP.get(c["pattern_id"], {}).get("keywords", [])
            hit_kw = [kw for kw in keywords if kw and kw.lower() in corpus] if keywords else []
            if not injected_in_window:
                no_signal += 1
                verdict = "no_signal"
            elif hit_kw:
                violations += 1
                verdict = "violation"
            else:
                adopted += 1
                verdict = "adoption"
                if record_correction_adoption(agent_name, c["pattern_id"]):
                    written += 1
            detail.append({"pattern_id": c["pattern_id"], "category": c["category"],
                           "severity": c["severity"], "verdict": verdict,
                           "violation_keywords": hit_kw[:5]})
        return {
            "evaluated": True,
            "session_id": session_id,
            "dry_run": written == 0 and adopted > 0 and not _tracking_enabled("adoption"),
            "adopted": adopted,
            "violations": violations,
            "no_signal": no_signal,
            "written": written,
            "rules": detail,
        }
    except Exception as e:
        logger.warning("improve: correction auto-adoption eval failed", exc_info=True)
        return {"evaluated": False, "reason": "error", "error": str(e)[:200]}
    finally:
        conn.close()


def correction_effectiveness_report(agent_name: Optional[str] = None) -> Dict[str, Any]:
    """Read-only lifecycle/effectiveness snapshot of the corrections ledger.

    Additive observability (TD-EVOL-TRACK-01): counts by status, injection and
    adoption counters, and adoption_rate (adopted/injected) for rules that have
    been injected at least once. Works regardless of whether tracking writes are
    enabled (counters simply stay 0). Never raises beyond DB errors.
    """
    conn = get_conn()
    try:
        _ensure_corrections_table(conn)
        where = ""
        params: list = []
        if agent_name:
            where = "AND (LOWER(agent_name) = LOWER(?) OR agent_name = '')"
            params.append(agent_name)
        rows = conn.execute(
            "SELECT status, COUNT(*) AS n, "
            "SUM(COALESCE(injected_count,0)) AS injected, "
            "SUM(COALESCE(adopted_count,0)) AS adopted "
            f"FROM corrections WHERE 1=1 {where} GROUP BY status",
            params,
        ).fetchall()
        by_status = {}
        total_injected = 0
        total_adopted = 0
        for r in rows:
            st = r["status"] or "unknown"
            by_status[st] = {
                "rules": int(r["n"] or 0),
                "injected": int(r["injected"] or 0),
                "adopted": int(r["adopted"] or 0),
            }
            total_injected += int(r["injected"] or 0)
            total_adopted += int(r["adopted"] or 0)
        top = conn.execute(
            "SELECT pattern_id, agent_name, category, severity, "
            "COALESCE(injected_count,0) AS injected, COALESCE(adopted_count,0) AS adopted, "
            "last_injected_at, last_adopted_at "
            "FROM corrections WHERE status='active' AND COALESCE(injected_count,0) > 0 "
            "ORDER BY injected_count DESC LIMIT 10",
        ).fetchall()
        return {
            "by_status": by_status,
            "total_rules": sum(v["rules"] for v in by_status.values()),
            "total_injected": total_injected,
            "total_adopted": total_adopted,
            "adoption_rate": (total_adopted / total_injected) if total_injected else None,
            "top_injected": [dict(t) for t in top],
            "tracking": {
                "injection_enabled": _tracking_enabled("injection"),
                "adoption_enabled": _tracking_enabled("adoption"),
            },
        }
    finally:
        conn.close()
