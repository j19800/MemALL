import re
import hashlib
from datetime import datetime, timezone
from memall.core.db import get_conn
from memall.core.thin_waist import normalize_agent_name

import json
CORRECTION_KEYWORDS = ["不对", "修正", "更正", "纠正", "错了", "错误", "应该是", "实际上是", "不对的"]
PROBLEM_KEYWORDS = ["问题", "bug", "缺陷", "错误", "issue", "故障", "报错"]
SUGGESTION_KEYWORDS = ["建议", "推荐", "应该", "改为", "改用", "方案", "对策"]
# Positive reflection triggers (第二刀 第6点)
POSITIVE_KEYWORDS = ["有效", "正确", "验证通过", "比预期好", "学到了", "成长", "进步", "worked", "validated", "confirmed", "learnt", "improved"]


def reflect_step() -> dict:
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT id, content, summary, agent_name, category, created_at, level FROM memories WHERE level NOT IN ('L6', 'L7', 'L9', 'L11') AND LENGTH(TRIM(content)) > 10 ORDER BY created_at DESC LIMIT 500"
        ).fetchall()
        now = datetime.now(timezone.utc).isoformat()
        upgraded = 0
        upgraded_ids = []

        # Cold-start: skip agents with < 50 total memories (长期 第7点)
        cold_start_agents = set()
        agent_names = list(set(r["agent_name"] for r in rows if r["agent_name"]))
        if agent_names:
            ph = ",".join("?" * len(agent_names))
            for r in conn.execute(
                f"SELECT LOWER(agent_name) as ag, COUNT(*) as cnt FROM memories WHERE LOWER(agent_name) IN ({ph}) GROUP BY LOWER(agent_name)",
                [a.lower() for a in agent_names],
            ):
                if r["cnt"] < 50:
                    cold_start_agents.add(r["ag"])
        rows = [r for r in rows if (r["agent_name"] or "").lower() not in cold_start_agents]
        if not rows:
            return {"upgraded_to_l6": 0, "scanned": 0, "positive_triggers": 0, "aggregated_groups": 0, "cold_skipped": len(cold_start_agents)}

        # Pre-compute agent focus areas (第三刀 第12点)
        focus_map = {}
        for r in rows:
            ag = r["agent_name"] or ""
            if ag and ag not in focus_map:
                focus_map[ag] = _agent_reflection_focus(conn, ag)

        for r in rows:
            text = (r["summary"] or "") + " " + (r["content"] or "")
            is_correction = any(kw in text for kw in CORRECTION_KEYWORDS)
            is_positive = any(kw in text for kw in POSITIVE_KEYWORDS)
            if not is_correction and not is_positive:
                continue

            context_snippet = ""
            if is_correction:
                kw_list = PROBLEM_KEYWORDS
                for kw in kw_list:
                    idx = text.find(kw)
                    if idx >= 0:
                        context_snippet = text[max(0, idx - 40):idx + 60]
                        break
                if not context_snippet:
                    context_snippet = text[:120]
            else:
                for kw in POSITIVE_KEYWORDS:
                    idx = text.find(kw)
                    if idx >= 0:
                        context_snippet = text[max(0, idx - 40):idx + 80]
                        break
                if not context_snippet:
                    context_snippet = text[:120]

            # Quality score: has both diagnostic AND improvement signal? (第二刀 第8点)
            has_problem = any(kw in text for kw in CORRECTION_KEYWORDS + PROBLEM_KEYWORDS)
            has_improvement = any(kw in text for kw in SUGGESTION_KEYWORDS + POSITIVE_KEYWORDS)
            if has_problem and has_improvement:
                quality = "high"
            elif has_problem or has_improvement:
                quality = "medium"
            else:
                quality = "low"

            # First-person tone (第二刀 第5点)
            agent_focus = focus_map.get(ag, "")
            focus_tag = f" {agent_focus}" if agent_focus else ""
            if is_correction:
                new_summary = f"[L6 反思{focus_tag}] 我注意到之前有个地方需要调整：{context_snippet[:150]}"
            else:
                new_summary = f"[L6 反思{focus_tag}] 我注意到有个做法效果不错：{context_snippet[:150]}"

            meta = {"l6_source": "reflect_step", "quality": quality}
            conn.execute("UPDATE memories SET level='L6', summary=?, metadata=?, updated_at=? WHERE id=?",
                         (new_summary, json.dumps(meta), now, r["id"]))
            upgraded += 1
            upgraded_ids.append((r["id"], r["agent_name"] or "", r["category"] or "", r["created_at"] or ""))

            # Reflection chain: link to previous L6 from same agent (第三刀 第9点)
            _build_reflection_chain(conn, r["id"], r["agent_name"] or "", is_correction, now)

        conn.commit()

        # Aggregation: group by agent+category+week (第二刀 第2点)
        aggregated = _aggregate_l6(conn, upgraded_ids, now)

        positive_count = sum(1 for r in rows if any(kw in (r["summary"] or "") + " " + (r["content"] or "") for kw in POSITIVE_KEYWORDS))
        return {
            "upgraded_to_l6": upgraded,
            "scanned": len(rows),
            "positive_triggers": positive_count,
            "aggregated_groups": aggregated,
        }
    finally:
        conn.close()


_CHAIN_OVERLAP_THRESHOLD = 20  # min character overlap to consider related


def _build_reflection_chain(conn, new_l6_id: int, agent_name: str, is_correction: bool, now: str) -> None:
    """Link new L6 to previous L6 from same agent via contradicts/refines edge."""
    if not agent_name:
        return

    prev = conn.execute(
        "SELECT id, content FROM memories WHERE level = 'L6' AND id != ? AND LOWER(agent_name) = LOWER(?) ORDER BY created_at DESC LIMIT 1",
        (new_l6_id, agent_name),
    ).fetchone()
    if not prev:
        return

    # Check edge doesn't already exist (either direction)
    dup = conn.execute(
        "SELECT 1 FROM edges WHERE (source_id = ? AND target_id = ?) OR (source_id = ? AND target_id = ?)",
        (new_l6_id, prev["id"], prev["id"], new_l6_id),
    ).fetchone()
    if dup:
        return

    # Fetch new L6 content
    new_row = conn.execute("SELECT content FROM memories WHERE id = ?", (new_l6_id,)).fetchone()
    if not new_row:
        return

    new_text = new_row["content"] or ""
    prev_text = prev["content"] or ""

    tokens = lambda t: set(re.findall(r'[\u4e00-\u9fff]|[a-zA-Z0-9_]+', t))
    overlap = len(tokens(new_text) & tokens(prev_text))
    if is_correction and overlap > _CHAIN_OVERLAP_THRESHOLD:
        rel = "contradicts"
    else:
        rel = "refines"

    conn.execute(
        "INSERT OR IGNORE INTO edges (source_id, target_id, relation_type, weight, created_at) VALUES (?, ?, ?, ?, ?)",
        (new_l6_id, prev["id"], rel, 1.0, now),
    )


_FOCUS_MAP = {
    "implementation": "工程实践",
    "architecture": "架构决策",
    "deployment": "部署运维",
    "testing": "质量保障",
    "meeting": "协作沟通",
    "planning": "规划推进",
    "learning": "学习成长",
    "documentation": "文档规范",
    "config": "配置优化",
    "problem": "问题排查",
    "fix": "修复验证",
    "reflection": "反思沉淀",
    "idea": "创新探索",
    "decision": "决策复盘",
}


def _agent_reflection_focus(conn, agent_name: str) -> str:
    """Determine reflection focus area from agent's category distribution."""
    if not agent_name:
        return ""
    rows = conn.execute(
        "SELECT category, COUNT(*) as cnt FROM memories WHERE LOWER(agent_name) = LOWER(?) AND category != '' GROUP BY category ORDER BY cnt DESC LIMIT 3",
        (agent_name,),
    ).fetchall()
    if not rows:
        return ""
    top_cat = rows[0]["category"]
    return _FOCUS_MAP.get(top_cat, "")


def _aggregate_l6(conn, upgraded_ids: list, now: str) -> int:
    """Group newly-upgraded L6 by (agent, category, ISO week), consolidate if >3."""
    if len(upgraded_ids) < 4:
        return 0

    from collections import defaultdict

    groups = defaultdict(list)
    for mid, agent, cat, ts in upgraded_ids:
        week = ts[:10] if ts else "unknown"  # Use date as week key (YYYY-MM-DD)
        groups[(agent, cat, week)].append(mid)

    aggregated = 0
    for (agent, cat, week), mids in groups.items():
        if len(mids) < 4:  # >3 threshold
            continue

        # Fetch the actual L6 summaries
        placeholders = ",".join("?" * len(mids))
        l6_rows = conn.execute(
            f"SELECT id, summary FROM memories WHERE id IN ({placeholders})",
            mids,
        ).fetchall()

        summaries = [r["summary"] for r in l6_rows if r["summary"]]
        if not summaries:
            continue

        # Consolidate: extract key points from each
        bullets = "\n".join(f"- {s[:200]}" for s in summaries[:10])
        agent_label = agent or "unknown"
        content = (
            f"[L6 聚合] {agent_label} 在 {cat}/{week} 的 {len(mids)} 条反思汇总：\n{bullets}"
        )[:3000]

        ch = hashlib.sha256(content.encode()).hexdigest()
        cur = conn.execute(
            "SELECT id FROM memories WHERE content_hash = ?", (ch,)
        )
        if cur.fetchone():
            continue  # already exists

        # Majority project from source memories
        mid_ph = ",".join("?" * len(mids))
        proj_row = conn.execute(f"SELECT project, COUNT(*) as cnt FROM memories WHERE id IN ({mid_ph}) AND project IS NOT NULL AND project != '' GROUP BY project ORDER BY cnt DESC LIMIT 1", mids).fetchone()
        l6_project = proj_row["project"] if proj_row else ""

        conn.execute(
            "INSERT INTO memories (content, content_hash, level, agent_name, category, project, summary, occurred_at, created_at, updated_at, metadata) "
            "VALUES (?, ?, 'L6', ?, ?, ?, ?, ?, ?, ?, ?)",
            (content, ch, normalize_agent_name(agent_label), cat, l6_project,
             f"{len(mids)} 条反思聚合", now, now, now,
             json.dumps({"l6_source": "aggregate", "source_ids": mids, "quality": "aggregated"})),
        )
        agg_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

        # Create edges
        for mid in mids:
            conn.execute(
                "INSERT OR IGNORE INTO edges (source_id, target_id, relation_type, weight, created_at) "
                "VALUES (?, ?, 'refines', 1.0, ?)",
                (agg_id, mid, now),
            )
        aggregated += 1

    conn.commit()
    return aggregated