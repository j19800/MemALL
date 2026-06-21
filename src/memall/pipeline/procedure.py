import json
import re
from datetime import datetime, timezone
from memall.core.db import get_conn

_STEP_RE = re.compile(
    r'(?:^|\n|[。！？；])\s*'
    r'(?:步骤\s*\d+[\.\:]?|第\s*\d+\s*步[：:]?|\d+[\.、]\s*|首先|然后|接着|最后|Step\s*\d+[\.\:]?|操作\s*\d+[\.\:]?)'
    r'\s*([^\n。！？]+)',
    re.I,
)

_ACTION_RE = re.compile(r'(?:调用方式|action|操作|接口|方法|函数|tool)\s*[：:]\s*([^\n]+)', re.I)
_OUTCOME_RE = re.compile(r'(?:结果|效果|产出|输出|功能|用途|作用)[：:]\s*([^\n。]+)', re.I)
_TITLE_RE = re.compile(r'^(?:\[L[1-4][ \u3000]*\]\s*)?(.{3,50}?)(?:\s*[（(]\s*(?:步骤|流程|procedure|工作流|模块|API)\s*[）)])?$')


def _extract_steps(text: str) -> list:
    steps = [m.strip() for m in _STEP_RE.findall(text) if len(m.strip()) > 3]
    return steps[:20] if steps else []


def _extract_field(pattern: re.Pattern, text: str) -> str | None:
    m = pattern.search(text)
    return m.group(1).strip() if m else None


def _extract_procedure(text: str) -> dict:
    title_m = _TITLE_RE.match(text.strip())
    title = title_m.group(1).strip() if title_m else ""
    if len(title) > 50:
        title = title[:50]

    steps = _extract_steps(text)
    if not steps:
        actions = [m.strip() for m in _ACTION_RE.findall(text) if len(m.strip()) > 3]
        steps = actions[:10]

    precond = _extract_field(
        re.compile(r'(?:前提|先决条件|前置条件|需要|必须|依赖)[：:]\s*([^\n。]+)', re.I),
        text
    )
    outcome = _extract_field(_OUTCOME_RE, text)

    procedure = {
        "title": title,
        "steps": steps,
        "precondition": precond,
        "outcome": outcome,
        "step_count": len(steps),
    }
    return procedure


def procedure_step() -> int:
    conn = get_conn()
    try:
        rows = conn.execute("""
            SELECT id, content, level, metadata
            FROM memories
            WHERE level IN ('L1', 'L2', 'L3', 'L4')
              AND LENGTH(TRIM(content)) > 20
        """).fetchall()

        count = 0
        for row in rows:
            existing = json.loads(row["metadata"] or "{}")
            if existing.get("procedure"):
                continue

            proc = _extract_procedure(row["content"] or "")
            if not proc["steps"] and not proc["precondition"] and not proc["outcome"]:
                continue

            existing["procedure"] = {
                "value": proc,
                "_meta": {"version": 1, "written_at": datetime.now(timezone.utc).isoformat()},
            }
            conn.execute(
                "UPDATE memories SET metadata = ? WHERE id = ?",
                (json.dumps(existing, ensure_ascii=False), row["id"]),
            )
            count += 1

        conn.commit()
        return count
    finally:
        conn.close()
