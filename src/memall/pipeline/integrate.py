"""L10 整合层 — 跨主题系统洞察。

触发条件：
  - 同一 agent_name 的 L9 蒸馏记忆中，跨 ≥2 个**真正不同**的 category
    且各 L9 记忆的 access_count 总和 ≥ 阈值。
  - 人工注入：通过 content 前缀 '[L10 ...]' 直接标记，管线不做二次推断。

行为：
  - 将符合条件的跨领域 L9 内容合并为一条 L10 记忆。
  - 自动生成有意义的 subject（包含 agent + 领域 + 时间）
  - 语义去重：与最近 5 条 L10 做 Jaccard 相似度检查，阈值 0.7 以上跳过
  - 标记源 L9 记忆的 metadata.layer_source = 'integrated_into_L10'。
  - 建立 L10 → 各源 L9 的 'integrates' 边。
"""

import hashlib
import json
import re
from collections import defaultdict
from datetime import datetime, timezone
from memall.core.db import get_conn
from memall.core.thin_waist import normalize_agent_name
from memall.pipeline.util import _smart_subject
from memall.core.nlp import jaccard

_MIN_L9_COUNT = 2
_L10_PREFIX_RE = re.compile(r'^\[(L10|L10\s.*?)\]', re.DOTALL)


# Semantic dedup: skip if Jaccard similarity ≥ this threshold vs any recent L10
_SIMILARITY_THRESHOLD = 0.85


def _is_explicit_l10(content: str) -> bool:
    return bool(_L10_PREFIX_RE.match(content.strip()))


def _tokenize(text: str) -> set:
    """Simple CJK-aware tokenization for dedup comparison."""
    tokens = set()
    for seg in re.findall(r'[一-鿿]+|[a-zA-Z]\w*|\d+', text):
        tokens.add(seg.lower())
    return tokens


def _build_subject(agent: str, categories: list[str], source_count: int) -> str:
    """Generate a descriptive subject line for the L10 memory."""
    # Deduplicate and sort categories
    seen = set()
    cats = []
    for c in categories:
        c = c.strip()
        if c and c not in seen:
            seen.add(c)
            cats.append(c)
    cat_str = " + ".join(cats[:3])
    if len(cats) > 3:
        cat_str += f" +{len(cats)-3}"
    return f"[L10 整合] {agent} 跨领域洞察({cat_str})"


def _recent_l10_similar(conn, merged_content: str, agent: str,
                         threshold: float = _SIMILARITY_THRESHOLD) -> int | None:
    """Check if near-duplicate L10 exists already.

    Compares content tokens via Jaccard against the most recent 5 L10s
    for this agent. Returns the existing memory ID if similar enough.
    """
    new_tokens = _tokenize(merged_content)
    recent = conn.execute(
        "SELECT id, content FROM memories WHERE level = 'L10' AND agent_name = ? "
        "ORDER BY created_at DESC LIMIT 5",
        (agent,),
    ).fetchall()
    for r in recent:
        existing_tokens = _tokenize(r["content"])
        sim = jaccard(new_tokens, existing_tokens)
        if sim >= threshold:
            return r["id"]
    return None


def integrate_step(min_categories: int = 2) -> dict:
    if min_categories < 2:
        min_categories = 2

    conn = get_conn()
    try:

        rows = conn.execute(
            "SELECT id, content, agent_name, category, access_count, metadata, created_at "
            "FROM memories WHERE level = 'L9' AND LENGTH(TRIM(content)) > 20 ORDER BY id DESC LIMIT 2000"
        ).fetchall()

        by_agent: dict = defaultdict(list)
        for r in rows:
            agent = normalize_agent_name(r["agent_name"]) or "unknown"
            by_agent[agent].append(r)

        integrated = 0
        scanned_agents = 0
        skipped_no_cross = 0
        skipped_duplicate = 0

        for agent, mems in by_agent.items():
            # Skip "unknown" agent — meaningless for cross-domain insights
            if agent in ("unknown", "", "system"):
                continue

            scanned_agents += 1
            by_cat: dict = defaultdict(list)
            for m in mems:
                cat = m["category"] or "general"
                by_cat[cat].append(m)

            unique_cats = len(by_cat)
            if unique_cats < 2 or len(mems) < _MIN_L9_COUNT:
                continue

            candidate_cats = sorted(
                by_cat.keys(),
                key=lambda c: sum(
                    cm["access_count"] if cm["access_count"] is not None else 0
                    for cm in by_cat[c]
                ),
                reverse=True,
            )[:2]
            if len(candidate_cats) < 2:
                continue

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
            source_categories = list({t["category"] for t in targets if t["category"]})

            # ✅ Genuine cross-domain check: ensure at least 2 different base categories
            if len(set(source_categories)) < 2:
                skipped_no_cross += 1
                continue

            # Show samples from each category (first 2 L9s per category, first 200 chars each)
            sample_lines = []
            seen_cats = set()
            for t in targets:
                cat = t["category"] or "general"
                if cat not in seen_cats and len(sample_lines) < 6:
                    seen_cats.add(cat)
                    text = (t["content"] or "").strip()[:200]
                    if text:
                        sample_lines.append(f"[{cat}] {text}")
                if len(sample_lines) >= 4:
                    break
            summary_text = "\n".join(sample_lines) if sample_lines else ""

            cat_counts: dict[str, int] = {}
            for m_cat in (m["category"] for m in targets if m["category"]):
                primary = m_cat.split("、")[0] if "、" in m_cat else m_cat
                cat_counts[primary] = cat_counts.get(primary, 0) + 1
            # I4: Combine categories instead of taking only majority
            sorted_cats = sorted(cat_counts.keys(), key=lambda c: cat_counts[c], reverse=True)
            best_cat = "+".join(sorted_cats[:3]) if sorted_cats else "general"

            merged = (
                f"[L10 整合] {agent} 跨领域系统洞察（{best_cat}）：\n"
                f"来源：{len(source_ids)} 条 L9 蒸馏\n"
                f"领域：{', '.join(source_categories)}\n"
                f"{summary_text}"
            )[:4000]

            # ✅ Generate meaningful subject from content
            l10_subject = _smart_subject(merged)

            ch = hashlib.sha256(merged.encode()).hexdigest()

            # ✅ Semantic dedup: check against recent L10s
            dup_id = _recent_l10_similar(conn, merged, agent)
            if dup_id is not None:
                skipped_duplicate += 1
                continue

            existing = conn.execute(
                "SELECT id FROM memories WHERE content_hash = ?",
                (ch,),
            ).fetchone()
            if existing:
                skipped_duplicate += 1
                continue

            now = datetime.now(timezone.utc).isoformat()
            # Majority project from source L9 memories
            source_id_params = tuple(source_ids)
            ph = ",".join("?" * len(source_ids))
            proj_row = conn.execute(f"SELECT project, COUNT(*) as cnt FROM memories WHERE id IN ({ph}) AND project IS NOT NULL AND project != '' GROUP BY project ORDER BY cnt DESC LIMIT 1", source_id_params).fetchone()
            l10_project = proj_row["project"] if proj_row else ""
            # I2: L10 is pipeline-generated synthetic insight, no conversation thread
            l10_thread_id = None
            conn.execute(
                "INSERT INTO memories "
                "(content, content_hash, level, category, agent_name, project, subject, metadata, occurred_at, created_at, updated_at, thread_id) "
                "VALUES (?, ?, 'L10', ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    merged, ch, best_cat, agent, l10_project, l10_subject,
                    json.dumps({"layer_source": {"value": "integrate_auto_v2", "_meta": {"version": 1, "written_at": now}}}, ensure_ascii=False),
                    now, now, now, l10_thread_id,
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
        detail = f"integrated={integrated}, scanned={scanned_agents}"
        if skipped_no_cross:
            detail += f", skipped_no_cross_domain={skipped_no_cross}"
        if skipped_duplicate:
            detail += f", skipped_duplicate={skipped_duplicate}"
        return {
            "scanned_agents": scanned_agents,
            "integrated": integrated,
            "skipped_no_cross_domain": skipped_no_cross,
            "skipped_duplicate": skipped_duplicate,
        }
    finally:
        conn.close()