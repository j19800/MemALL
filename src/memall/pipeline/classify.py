"""Pipeline classify step — layer and category detection.

Uses an exclusion-based priority chain (not weight-based competition) to
assign memory layers. Each layer has distinct admission criteria. First
match wins — no more L6 swallowing everything.
"""

import json
import re
from datetime import datetime, timezone
from memall.core.db import get_conn

# ── Category detection rules (unchanged) ──
CATEGORY_RULES = [
    (r'(决定|选择|采用|替代|改用)\s', 'decision'),
    (r'(问题|瓶颈|不足|错误|bug|故障|缺陷)', 'problem'),
    (r'(架构|设计|方案|模式|原则|权衡|系统)', 'architecture'),
    (r'(代码|实现|函数|类|接口|API|SDK|模块)', 'implementation'),
    (r'(测试|验证|确认|benchmark|压测)', 'testing'),
    (r'(部署|发布|上线|CI|CD|运维)', 'deployment'),
    (r'(讨论|会议|对齐|同步|评审)', 'meeting'),
    (r'(文档|README|手册|指南|规范)', 'documentation'),
    (r'(规划|路线图|计划|目标|OKR|Milestone|阶段)', 'planning'),
    (r'(学习|笔记|教程|概念)', 'learning'),
    (r'(想法|灵感|构思|脑洞|创意)', 'idea'),
    (r'(复盘|反思|总结|教训|回顾)', 'reflection'),
    (r'(修复|修正|解决|纠正)', 'fix'),
    (r'(配置|参数|设置|优化|性能)', 'config'),
    (r'(规则|RULE|原则|标准)', 'rule'),
]

# ── Layer priority chain: first match wins ──
# Each entry: (layer, markers, min_matches, min_content_len)
# - layer: the level to assign
# - markers: list of regex patterns that indicate this layer
# - min_matches: how many distinct markers must hit (counted by distinct pattern, not total matches)
# - min_content_len: minimum content length to qualify

_LAYER_PRIORITY = [
    # L6 — Deep reflection: requires strong evidence (≥2 distinct patterns + ≥40 chars)
    ("L6", [
        r'(?:教训|根因|学到|改进点|lesson)',
        r'(?:修正|更正|纠正|做错了|踩坑|遗漏|偏了)',
        r'(?:反思|回顾|retrospective|反省)',
        r'(?:做对了|做得好的|好的做法|有效|Why)',
        r'(?:下一次|以后.*应该|需要.*注意|避免.*再)',
        r'(?:不应该|不对|本应|本不该)',
    ], 2, 25),

    # L11 — Domain intelligence (business/market/strategy)
    ("L11", [
        r'(?:商业模式|变现|盈利|market|商业|创业|竞品|定价|收入|增长|growth|领域|domain)',
        r'(?:一人公司|OPC|数字游民|产品定位|价值主张|壁垒|moat|网络效应)',
        r'(?:蓝海|红海|差异化|护城河|平台效应|战略)',
        r'(?:use.case|用户画像|场景|投资回报|ROI|GTM|go.to.market|okr)',
    ], 1, 20),

    # L7 — Preferences (likes, dislikes, tooling habits)
    ("L7", [
        r'(?:我喜欢|我偏好|prefer|偏好|倾向于|常用|习惯用|主要用)',
        r'(?:我觉得更好|更高效|更[^过]|使用场景|适用场景)',
        r'(?:我不喜欢|我排斥|避免|我不用|我讨厌|不建议)',
        r'(?:推荐|建议用|更推荐|优先选择)',
    ], 1, 15),

    # L3 — Procedure / How-to (reusable process knowledge)
    ("L3", [
        r'(?:步骤|流程|方法|workflow|如何|方式|procedure|checklist|指南)',
        r'(?:第一步|第二步|接着|然后|最后|以下步骤|pipelines?|cmd)',
        r'(?:安装|配置|部署|设置|configure|install|setup)',
        r'(?:规范|规则|guideline|标准|原则|sop|操作)',
    ], 2, 15),

    # L5 — Planning (tasks, goals, roadmap)
    ("L5", [
        r'(?:计划|规划|路线图|roadmap|目标|goal|milestone)',
        r'(?:待办|todo|task|任务|next.step|下一步|Phase\s+\d)',
        r'(?:迭代|sprint|ETA|截止日期|deadline|预计|安排|安排)',
    ], 1, 15),

    # L1 — Identity (who I am, skills, background)
    ("L1", [
        r'(?:我叫|我是|本人|name|email|phone|contact)',
        r'(?:我从事|我担任|我的角色|我的职位|我的职业)',
        r'(?:我擅长|我精通|我的能力|我的技能|我会|我能|熟悉)',
        r'(?:我的习惯|我习惯|我每天|我认为|我相信|我的理念|我看重|我的价值观)',
        r'(?:我住在|我的家乡|毕业于|我的背景|我的经历)',
    ], 1, 15),

    # L4 — Session context (this conversation, meeting)
    ("L4", [
        r'(?:会话|session|本次|此次|刚才|前面讨论|conversation|对话中|会议|讨论)',
        r'(?:session_id\d*)',
    ], 1, 10),

    # L2 — Event log (temporal facts, day-level events)
    ("L2", [
        r'(今天|昨天|上周|这周|下周|\d{4}-\d{2}-\d{2})',
        r'(来了|开始|结束|上线|merged|deployed|completed)',
    ], 1, 10),
]

# Pipeline-generated layers — never assigned by keyword detection
_PIPELINE_LAYERS = frozenset({"L9", "L10"})

# Explicit [Lx] prefix pattern
_PREFIX_PATTERN = re.compile(r'^\[(L10[ 　]|L[1-9])[ 　]', re.DOTALL)


def _detect_layers(content: str, summary: str = "",
                   current_level: str | None = None) -> dict:
    """Detect layer for a memory using exclusion-based priority chain.

    Returns:
        {"primary": str, "secondary": list[str], "all_scores": dict[str, int]}
    """
    text = (summary or "") + " " + (content or "")

    # 0. Explicit [Lx] prefix always wins (pipeline layers are excluded)
    prefix_match = _PREFIX_PATTERN.match(text)
    if prefix_match:
        explicit = prefix_match.group(1).strip()
        if explicit not in _PIPELINE_LAYERS:
            return {"primary": explicit, "secondary": [], "all_scores": {explicit: 100}}

    # 1. MODULE noise: registration records that aren't real content
    if re.search(r'\[MODULE', content or ""):
        return {"primary": "P2", "secondary": [], "all_scores": {"P2": 0}}

    # 2. Priority chain: first matching layer wins
    content_len = len((content or "").strip())
    for layer, markers, min_matches, min_len in _LAYER_PRIORITY:
        if content_len < min_len:
            continue
        # Count how many *distinct patterns* match (not total matches)
        hits = 0
        for pat in markers:
            if re.search(pat, text, re.IGNORECASE):
                hits += 1
                if hits >= min_matches:
                    return {"primary": layer, "secondary": [],
                            "all_scores": {layer: hits * 20}}

    # 3. Fallback to P2 (raw capture) for unrecognized content
    return {"primary": "P2", "secondary": [], "all_scores": {"P2": 0}}


def _check_l8_promotion(conn, memory_id: int, edge_count_map: dict[int, int],
                        primary: str) -> str:
    """Promote to L8 if the memory has rich edge relationships."""
    if primary in _PIPELINE_LAYERS:
        return primary
    if edge_count_map.get(memory_id, 0) >= 3:
        return "L8"
    # Also check module_refs in metadata
    row = conn.execute("SELECT metadata FROM memories WHERE id = ?",
                       (memory_id,)).fetchone()
    if row and row["metadata"]:
        try:
            meta = json.loads(row["metadata"]) if isinstance(row["metadata"], str) else row["metadata"]
            if meta.get("module_refs") or meta.get("enrich", {}).get("value", {}).get("module_refs"):
                return "L8"
        except (json.JSONDecodeError, TypeError):
            pass
    return primary


def classify_step() -> dict:
    """Batch reclassification: cursor-based, 500 per run.

    Unlike the old system, this ALLOWS downgrading — a memory misclassified
    as L6 can be reassigned to the correct layer.
    """
    conn = get_conn()
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS pipeline_cursors "
            "(step TEXT PRIMARY KEY, cursor_id INTEGER, updated_at TEXT)"
        )
        cursor_row = conn.execute(
            "SELECT cursor_id FROM pipeline_cursors WHERE step='classify'"
        ).fetchone()
        cursor = cursor_row["cursor_id"] if cursor_row else None

        if cursor:
            rows = conn.execute(
                "SELECT id, content, category, summary, level, metadata "
                "FROM memories WHERE id < ? ORDER BY id DESC LIMIT 500",
                (cursor,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, content, category, summary, level, metadata "
                "FROM memories ORDER BY id DESC LIMIT 500"
            ).fetchall()

        if rows:
            min_id = min(r["id"] for r in rows)
            now = datetime.now(timezone.utc).isoformat()
            conn.execute(
                "INSERT INTO pipeline_cursors (step, cursor_id, updated_at) VALUES ('classify', ?, ?) "
                "ON CONFLICT(step) DO UPDATE SET cursor_id=excluded.cursor_id, updated_at=excluded.updated_at",
                (min_id, now),
            )
        else:
            # C6: Don't reset cursor on empty — next run would re-scan the same 500 records infinitely
            return {"scanned": 0, "changed": 0, "layer_distribution": {}}

        # Pre-aggregate edge counts
        row_ids = [r["id"] for r in rows]
        edge_count_map: dict[int, int] = {}
        if row_ids:
            ph = ",".join("?" * len(row_ids))
            for edge_row in conn.execute(
                f"SELECT source_id, COUNT(*) AS c FROM edges "
                f"WHERE source_id IN ({ph}) AND relation_type IN "
                f"('delegates','replies_to','extends','contradicts','cites','supersedes') "
                f"GROUP BY source_id",
                row_ids,
            ).fetchall():
                edge_count_map[edge_row["source_id"]] = edge_row["c"]

        layer_counts: dict = {}
        cat_updated = 0

        for row in rows:
            # Skip pipeline-generated layers (L9, L10) — never reclassify
            if row["level"] in _PIPELINE_LAYERS:
                continue

            text = row["content"]

            # Category detection (unchanged)
            best_cat = "general"
            best_score = 0
            for pattern, cat in CATEGORY_RULES:
                matches = re.findall(pattern, text)
                score = len(matches)
                if score > best_score:
                    best_score = score
                    best_cat = cat

            # Layer detection with new priority chain
            result = _detect_layers(text, row["summary"] or "",
                                    current_level=row["level"])
            primary = result["primary"]

            # L8 edge-based promotion (overlay, not replacement)
            primary = _check_l8_promotion(conn, row["id"], edge_count_map, primary)

            # Build metadata with layer_source
            meta = {}
            try:
                meta = json.loads(row["metadata"]) if row["metadata"] else {}
            except (json.JSONDecodeError, TypeError):
                meta = {}
            now2 = datetime.now(timezone.utc).isoformat()
            meta["layer_source"] = {
                "value": "classify_v2_priority",
                "_meta": {"version": 2, "written_at": now2},
            }

            # Update level, category, metadata
            old_level = row["level"]
            conn.execute(
                "UPDATE memories SET level = ?, primary_layer = ?, "
                "secondary_layers = ?, metadata = ?, category = ? WHERE id = ?",
                (primary, primary,
                 json.dumps(result.get("secondary", []), ensure_ascii=False),
                 json.dumps(meta, ensure_ascii=False),
                 best_cat if best_cat != "general" else row["category"],
                 row["id"]),
            )
            if primary != old_level:
                cat_updated += 1

            layer_counts[primary] = layer_counts.get(primary, 0) + 1

        conn.commit()
        return {
            "scanned": len(rows),
            "changed": cat_updated,
            "layer_distribution": layer_counts,
        }
    finally:
        conn.close()