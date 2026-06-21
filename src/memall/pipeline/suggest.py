import re
from datetime import datetime, timezone
from memall.core.db import get_conn

SUGGESTION_PATTERNS = [
    r'(?:建议|推荐|应当|应该|建议你|强烈建议)\s*[：:】]?\s*([^。\n]{10,200})',
    r'(?:TODO|FIXME|HACK|XXX)[：:】]?\s*([^。\n]{10,200})',
    r'(?:下一步|接下来|可做|值得做)\s*[：:】]?\s*([^。\n]{10,200})',
    r'##?\s*对\s*MemALL\s*的\s*可行动建议[^#]*?\n((?:\s*[-*]\s*[^\n]+(?:\n|$))+)',
    r'(?:可行动建议|actionable[:\s]+)((?:[-*\d]+\.?\s*[^\n]+(?:\n|$))+)',
]

CATEGORY_KEYWORDS = {
    "architecture": ["架构", "无状态", "mcp", "session", "设计", "重构"],
    "security": ["安全", "权限", "敏感", "加密", "隐私"],
    "performance": ["性能", "优化", "缓存", "延迟", "embedding", "向量"],
    "ux": ["ui", "界面", "cli", "输出", "提示", "易用"],
    "ops": ["部署", "备份", "监控", "日志", "迁移", "配置"],
    "product": ["开源", "发布", "readme", "定位", "产品", "市场"],
    "quality": ["指标", "度量", "质量", "测试", "验收"],
}


def _detect_category(text: str) -> str:
    text_lower = text.lower()
    scores = {}
    for cat, kws in CATEGORY_KEYWORDS.items():
        score = sum(1 for kw in kws if kw in text_lower)
        if score:
            scores[cat] = score
    if scores:
        return max(scores, key=scores.get)
    return "other"


def _hash_content(content: str) -> str:
    return hashlib.md5(content.encode("utf-8")).hexdigest()


def _extract_from_content(content: str) -> list:
    results = []
    for pat in SUGGESTION_PATTERNS:
        for m in re.finditer(pat, content, re.IGNORECASE | re.MULTILINE):
            raw = m.group(1).strip()
            if len(raw) < 15:
                continue
            results.append(raw)
    return results


def _is_duplicate(conn, content: str) -> bool:
    return conn.execute("SELECT COUNT(*) FROM suggestions WHERE content = ?", (content,)).fetchone()[0] > 0


def suggest_step(limit: int = 50) -> dict:
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT id, content, category, agent_name FROM memories WHERE level IN ('L5', 'P0', 'P1') ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        now = datetime.now(timezone.utc).isoformat()
        extracted = 0

        for r in rows:
            raw_suggestions = _extract_from_content(r["content"])
            for s in raw_suggestions:
                if _is_duplicate(conn, s):
                    continue
                cat = _detect_category(s)
                conn.execute(
                    """INSERT INTO suggestions
                (source_type, source_id, content, category, priority, status, created_by, created_at, tags)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    ("memory", r["id"], s, cat, "P2", "pending", r["agent_name"] if r["agent_name"] else "marvis", now, ""),
                )
                extracted += 1

        conn.commit()
        return {"extracted": extracted, "limit": limit}
    finally:
        conn.close()
