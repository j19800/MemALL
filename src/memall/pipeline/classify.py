import json
import re
from datetime import datetime, timezone
from memall.core.db import get_conn

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

_PREFIX_L_PAT = r'^\[(L10|L[1-9])[ \u3000]*(.*?)[ \u3000]*\]'
RE_FLAGS = re.I | re.DOTALL

_LAYER_PREFIX_RE = re.compile(_PREFIX_L_PAT, RE_FLAGS)

_L1_WORDS = r'(?:^|[\s，。！？、\n，])(?:我的|我叫|我是|habits?|born|name|email|phone|phone_number|住在|生日|擅长|爱好|习惯|我的.?为|我认为|我相信|我的.?是)'
_L2_WORDS = r'(?:^|[\s，。！？、\n，])(?:今天|昨天|上周|这周|下周|Yesterday|Today|Last week|Next week|\d{4}-\d{2}-\d{2}|发布了|上线了|meeting|会议|discussion|讨论|completed|deployed|shipped|released|launched|merged|完成了|结束了|出现了)'
_L3_WORDS = r'(?:^|[\s，。！？、\n，])(?:如何|怎么|步骤|流程|工作流|workflow|procedure|pipelines?|run \w+\(|命令|memall|设置|configure|install|规范|guideline|checklist|首先|然后|接着|最后|以下步骤)'
_L4_WORDS = r'(?:^|[\s，。！？、\n，])(?:本次|此次|当前|这个会话|this session|session_id|对话中|conversation|刚才|前面讨论|交互中|测试\d+|分钟|sessions?)'
_L5_WORDS = r'(?:^|[\s，。！？、\n，])(?:计划|planned|schedule|roadmap|目标|goal|objective|OKR|milestone|待办|todo|task|任务|next step|Phase\s+\d|阶段|迭代|sprint|预计|ETA|截止日期)'
_L6_WORDS = r'(?:^|[\s，。！？、\n，])(?:不对|修正|更正|纠正|错了|错误|应该\n是|实际上|教训|反思|review|retrospective|发现了问题|踩坑|偏了|不应该|做了多余|遗漏了|有效|正确|验证通过|比预期好|学到了|成长|进步|worked|validated|confirmed|learnt|improved)'
_L7_WORDS = r'(?:^|[\s，。！？、\n，])(?:我的偏好|我偏好|我喜欢|我倾向于|偏好|prefer(?:s|red|ring)?|preference|倾向|习惯上|倾向于|更喜欢|喜欢用|更方便|更高效|使用场景|主要用|习惯用)'
# _L8_WORDS — deprecated in Phase 3 (edges-projected, not keyword-classified)
_L11_WORDS = r'(?:^|[\s，。！？、\n，])(?:商业|business(?:es)?|market|变现|盈利|创业|产品定位|domain|行业|领域(?:战略)?|趋势|竞品|定价|收入|增长|growth|机会|蓝海|红海|差异化|壁垒|moat|护城河|网络效应|平台效应|策略|strategy|value.prop|价值主张|投资|投资回报|商业模式|用户画像|场景|用例|use.case|转型|数字化|AI.+落地|单人即公司|一人公司|micro.saaS|community.led|product.led)'
_L10_WORDS = r'(?:^|[\s，。！？、\n，])(?:整合|integrate|整体|系统性|systemic|集成|unified|comprehensive|全景|全局|概括|最终|终局|愿景|top.level|跨.领域|跨.主题|high.level)'

_LAYER_RULE_LIST = [
    ("L6", _LAYER_PREFIX_RE, _L6_WORDS, 100),
    ("L9", None, r'\[L9|L9 蒸馏|蒸馏', 95),
    ("L10", _LAYER_PREFIX_RE, _L10_WORDS, 90),
    ("L1", _LAYER_PREFIX_RE, _L1_WORDS, 80),
    ("L2", _LAYER_PREFIX_RE, _L2_WORDS, 75),
    ("L11", _LAYER_PREFIX_RE, _L11_WORDS, 70),
    ("L7", _LAYER_PREFIX_RE, _L7_WORDS, 65),
    # L8 removed in Phase 3 — now edges-projected, not keyword-classified
    ("L3", _LAYER_PREFIX_RE, _L3_WORDS, 50),
    ("L5", _LAYER_PREFIX_RE, _L5_WORDS, 48),
    ("L4", _LAYER_PREFIX_RE, _L4_WORDS, 45),
]

_LAYER_SCORE_THRESHOLD = 2

# Layer ranking for "only upgrade, never downgrade"
# Higher number = higher rank. Terminal layers (L6/L8/L9/L10/L11) are immutable.
_LAYER_RANK = {
    "L10": 100, "L11": 95, "L9": 90, "L6": 80,
    "P0": 70, "P1": 60,
    "L7": 55, "L8": 50, "L5": 48,
    "L4": 45, "L3": 40, "L2": 35, "L1": 30,
    "P2": 20, "P3": 10, "P4": 5,
}
_TERMINAL_LAYERS = {"L6", "L8", "L9", "L10", "L11"}


def _detect_layers(
    content: str,
    summary: str = "",
    *,
    already_l6: bool = False,
    already_l9: bool = False,
) -> dict:
    """Detect layers for a memory. Returns primary, secondary candidates, and scores.

    Returns:
        {"primary": str, "secondary": list[str], "all_scores": dict[str, int]}
    """
    text = (summary or "") + " " + content
    # Noise filter: MODULE registration records match date patterns (L2) but aren't events
    # Check for [MODULE anywhere in content — not just prefix — to catch all MODULE refs
    is_module_noise = bool(re.search(r'\[MODULE', content))
    prefix_match = _LAYER_PREFIX_RE.match(text)
    explicit_prefix = prefix_match.group(1) if prefix_match else None

    if explicit_prefix == "L6" or already_l6:
        return {"primary": "L6", "secondary": [], "all_scores": {"L6": 100}}
    if explicit_prefix == "L9" or already_l9:
        return {"primary": "L9", "secondary": [], "all_scores": {"L9": 100}}
    if explicit_prefix:
        return {"primary": explicit_prefix, "secondary": [], "all_scores": {explicit_prefix: 100}}

    scores: dict = {}
    for layer, _prefix_re, pattern_re, weight in _LAYER_RULE_LIST:
        matches = re.findall(pattern_re, text)
        scores[layer] = len(matches) * weight

    # Sort layers by score descending, keep only those above threshold
    ranked = sorted(
        [(k, v) for k, v in scores.items() if v >= _LAYER_SCORE_THRESHOLD],
        key=lambda x: -x[1],
    )

    if not ranked:
        fallback = "L1" if scores and max(scores.values()) > 0 else "L2"
        return {"primary": fallback, "secondary": [], "all_scores": scores}

    # Module noise: if highest score is L2 (date match), suppress to fallback
    primary = ranked[0][0]
    if is_module_noise and primary == "L2":
        fallback = "L1" if len(ranked) > 1 and ranked[1][0] != "L2" else "L7"
        return {"primary": fallback, "secondary": [], "all_scores": scores}

    secondary = [layer for layer, _ in ranked[1:]]
    return {"primary": primary, "secondary": secondary, "all_scores": scores}


def classify_step() -> dict:
    conn = get_conn()
    try:
        # Cursor-based pagination: each run processes 500, tracks progress
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
                "FROM memories WHERE level IN ('P0', 'P1', 'P2', 'P3', 'P4', 'L1', 'L2', 'L3', 'L4', 'L5') "
                "AND id < ? ORDER BY id DESC LIMIT 500",
                (cursor,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, content, category, summary, level, metadata "
                "FROM memories WHERE level IN ('P0', 'P1', 'P2', 'P3', 'P4', 'L1', 'L2', 'L3', 'L4', 'L5') "
                "ORDER BY id DESC LIMIT 500"
            ).fetchall()

        if rows:
            # Advance cursor: next run starts below the min id in this batch
            min_id = min(r["id"] for r in rows)
            now = datetime.now(timezone.utc).isoformat()
            conn.execute(
                "INSERT INTO pipeline_cursors (step, cursor_id, updated_at) VALUES ('classify', ?, ?) "
                "ON CONFLICT(step) DO UPDATE SET cursor_id=excluded.cursor_id, updated_at=excluded.updated_at",
                (min_id, now),
            )
        else:
            # No more unprocessed rows — reset cursor for next cycle
            conn.execute("DELETE FROM pipeline_cursors WHERE step='classify'")
        counts: dict = {}
        layer_counts: dict = {}

        # Pre-aggregate edge counts for all rows in this batch (avoid N+1)
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

        for row in rows:
            text = row["content"]
            best_cat = "general"
            best_score = 0

            for pattern, cat in CATEGORY_RULES:
                matches = re.findall(pattern, text)
                score = len(matches)
                if score > best_score:
                    best_score = score
                    best_cat = cat

            already_l6 = row["level"] == "L6"
            already_l9 = row["level"] == "L9"
            current_level = row["level"] or "P2"
            layer_result = _detect_layers(
                text,
                row["summary"] or "",
                already_l6=already_l6,
                already_l9=already_l9,
            )
            primary = layer_result["primary"]
            secondary = layer_result["secondary"]

            # L8 edges-based promotion (Phase 3: keyword _L8_WORDS removed)
            # promote to L8 regardless of keyword matching
            meta_raw = row["metadata"] or "{}"
            try:
                meta_val = json.loads(meta_raw) if isinstance(meta_raw, str) else meta_raw
            except (json.JSONDecodeError, TypeError):
                meta_val = {}
            has_module_refs = bool(
                meta_val.get("module_refs") or
                meta_val.get("enrich", {}).get("value", {}).get("module_refs")
            )
            if primary not in _TERMINAL_LAYERS and current_level not in _TERMINAL_LAYERS:
                if has_module_refs:
                    primary = "L8"
                    secondary = [s for s in secondary if s != "L8"]
                else:
                    edge_count = edge_count_map.get(row["id"], 0)
                    if edge_count > 0:
                        primary = "L8"
                        secondary = [s for s in secondary if s != "L8"]

            # L2 noise reclassification: MODULE registration records that slipped into L2
            # before the noise filter was added — downgrade to P2 (raw capture level)
            if current_level == "L2" and re.search(r'\[MODULE', text):
                primary = "P2"
                secondary = []

            if primary in ("L6", "L9", "L10", "L11"):
                counts[primary] = counts.get(primary, 0) + 1
            else:
                counts["classified"] = counts.get("classified", 0) + 1

            layer_counts[primary] = layer_counts.get(primary, 0) + 1

            # Build metadata with layer_source (fix: persist to DB)
            metadata = row["metadata"] or "{}"
            try:
                meta = json.loads(metadata) if isinstance(metadata, str) else metadata
            except (json.JSONDecodeError, TypeError):
                meta = {}
            now = datetime.now(timezone.utc).isoformat()
            meta["layer_source"] = {
                "value": "classify_auto_v1",
                "_meta": {"version": 1, "written_at": now},
            }

            # Level discipline: only upgrade, never downgrade
            # Terminal layers (L6/L9/L10) are immutable — skip entirely
            if current_level in _TERMINAL_LAYERS:
                # Still update metadata (bugfix: persist layer_source) and category
                conn.execute(
                    "UPDATE memories SET metadata = ?, category = ? WHERE id = ?",
                    (json.dumps(meta, ensure_ascii=False),
                     best_cat if best_cat != "general" else row["category"],
                     row["id"]),
                )
                continue

            # Skip if detected layer is lower rank than current level
            current_rank = _LAYER_RANK.get(current_level, 0)
            detected_rank = _LAYER_RANK.get(primary, 0)
            if detected_rank <= current_rank:
                # Exception: force-reclassify L2 MODULE noise back to P2 (correction, not upgrade)
                if current_level == "L2" and primary == "P2" and re.search(r'\[MODULE', row["content"]):
                    pass  # fall through to force-write
                else:
                    continue

            # Write level, primary_layer, secondary_layers, metadata, and category
            conn.execute(
                "UPDATE memories SET level = ?, primary_layer = ?, secondary_layers = ?, metadata = ?, category = ? WHERE id = ?",
                (primary, primary,
                 json.dumps(secondary, ensure_ascii=False),
                 json.dumps(meta, ensure_ascii=False),
                 best_cat if best_cat != "general" else row["category"],
                 row["id"]),
            )
            counts["cat_updated"] = counts.get("cat_updated", 0) + 1

        conn.commit()
        return {
        "scanned": len(rows),
        "category_updates": counts.get("cat_updated", 0),
        "layer_distribution": layer_counts,
        }
    finally:
        conn.close()
