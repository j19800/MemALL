"""L10 整合层 — 跨主题系统洞察。

触发条件：
  - 同一 agent_name 的 L9 蒸馏记忆中，跨 ≥2 个不同 category 的 L9 记忆
    且各 L9 记忆的 access_count 总和 ≥ 阈值。
  - 人工注入：通过 content 前缀 '[L10 ...]' 直接标记，管线不做二次推断。

行为：
  - 将符合条件的跨领域 L9 内容合并为一条 L10 记忆。
  - 标记源 L9 记忆的 metadata.layer_source = 'integrated_into_L10'。
  - 建立 L10 → 各源 L9 的 'integrates' 边。
"""

import hashlib
import json
from memall.core.nlp import summarize_extractive
import re
from collections import defaultdict
from datetime import datetime, timezone
from memall.core.db import get_conn

_MIN_L9_COUNT = 2
_MIN_L9_ACCESS_TOTAL = 5
_L10_PREFIX_RE = re.compile(r'^\[(L10|L10\s.*?)\]', re.DOTALL)


def _is_explicit_l10(content: str) -> bool:
    return bool(_L10_PREFIX_RE.match(content.strip()))


def integrate_step(access_total_threshold: int = _MIN_L9_ACCESS_TOTAL,
                   min_categories: int = 2) -> dict:
    if access_total_threshold < 2:
        access_total_threshold = _MIN_L9_ACCESS_TOTAL
    if min_categories < 2:
        min_categories = 2

    conn = get_conn()
    try:

        rows = conn.execute(
            "SELECT id, content, agent_name, category, access_count, metadata, created_at "
            "FROM memories WHERE level = 'L9' AND LENGTH(TRIM(content)) > 20"
        ).fetchall()

        by_agent: dict = defaultdict(list)
        for r in rows:
            agent = r["agent_name"] or "unknown"
            by_agent[agent].append(r)

        integrated = 0
        scanned_agents = 0

        for agent, mems in by_agent.items():
            scanned_agents += 1
            by_cat: dict = defaultdict(list)
            for m in mems:
                cat = m["category"] or "general"
                by_cat[cat].append(m)

            unique_cats = len(by_cat)
            if unique_cats < 2 or len(mems) < _MIN_L9_COUNT:
                continue

            candidate_cats = sorted(
                [
                    cat
                    for cat, cat_mems in by_cat.items()
                    if sum(
                        cm["access_count"] if cm["access_count"] is not None else 0
                        for cm in cat_mems
                    )
                    >= access_total_threshold
                ],
                key=lambda c: len(by_cat[c]),
                reverse=True,
            )
            if len(candidate_cats) < 2:
                # Fallback: when all access_count==0, pick the two largest categories
                fallback = sorted(by_cat.keys(), key=lambda c: len(by_cat[c]), reverse=True)[:2]
                if len(fallback) < 2:
                    continue
                candidate_cats = fallback

            targets = []
            seen_ids = set()
            for cat in candidate_cats:
                for m in by_cat[cat]:
                    if m["id"] not in seen_ids:
                        targets.append(m)
                        seen_ids.add(m["id"])
            if len(targets) < _MIN_L9_COUNT:
                continue

            source_ids = [t["id"] for t in targets]
            texts = [t["content"] for t in targets if t["content"]]
            summary_text = summarize_extractive(texts, top_n=5, max_chars=3500)

            cat_counts: dict[str, int] = {}
            for m_cat in (m["category"] for m in targets if m["category"]):
                # If category is already composite, take the first component
                primary = m_cat.split("、")[0] if "、" in m_cat else m_cat
                cat_counts[primary] = cat_counts.get(primary, 0) + 1
            best_cat = max(cat_counts, key=cat_counts.get) if cat_counts else "general"
            merged = (
                f"[L10 整合] {agent} 跨领域系统洞察（{best_cat}）：\n"
                f"来源：{len(source_ids)} 条 L9 蒸馏\n"
                f"{summary_text}"
            )[:4000]

            ch = hashlib.sha256(merged.encode()).hexdigest()
            existing = conn.execute(
                "SELECT id FROM memories WHERE content_hash = ?",
                (ch,),
            ).fetchone()
            if existing:
                continue

            now = datetime.now(timezone.utc).isoformat()
            conn.execute(
                "INSERT INTO memories "
                "(content, content_hash, level, category, agent_name, metadata, occurred_at, created_at, updated_at) "
                "VALUES (?, ?, 'L10', ?, ?, ?, ?, ?, ?)",
                (
                    merged, ch, best_cat, agent,
                    json.dumps({"layer_source": {"value": "integrate_auto_v1", "_meta": {"version": 1, "written_at": now}}}, ensure_ascii=False),
                    now, now, now,
                ),
            )
            l10_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

            for sid in source_ids:
                row2 = conn.execute(
                    "SELECT metadata FROM memories WHERE id = ?", (sid,)
                ).fetchone()
                try:
                    meta = json.loads(row2["metadata"]) if row2 and row2["metadata"] else {}
                except (json.JSONDecodeError, TypeError):
                    meta = {}
                meta["layer_source"] = {
                    "value": "integrated_into_L10",
                    "_meta": {"version": 1, "written_at": datetime.now(timezone.utc).isoformat()},
                }
                conn.execute(
                    "UPDATE memories SET metadata = ? WHERE id = ?",
                    (json.dumps(meta, ensure_ascii=False), sid),
                )
                conn.execute(
                    "INSERT OR IGNORE INTO edges "
                    "(source_id, target_id, relation_type, weight, created_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (l10_id, sid, "integrates", 1.0, now),
                )

            integrated += 1

        conn.commit()
        return {
            "scanned_agents": scanned_agents,
            "integrated": integrated,
        }
    finally:
        conn.close()
