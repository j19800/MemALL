import json
from memall.pipeline.session import session_start, session_end, session_summary


def _format_l7_instructions(injection: dict) -> str:
    """Extract L7 lessons and preferences and format as explicit behavioral
    instructions that Claude will read at the top of the session_start response."""
    lines = []

    # L7 Lessons (preference memories)
    lesson_list = (injection or {}).get("l7_lessons") or []
    if lesson_list:
        lines.append("【行为准则（L7 经验教训）】")
        for i, item in enumerate(lesson_list, 1):
            lines.append(f"  {i}. {item.get('lesson', '')[:200]}")
        lines.append("")

    # L7 Preferences from identity_profile
    identity = (injection or {}).get("identity_traits") or {}
    prefs = identity.get("l7_preferences") or []
    if prefs:
        lines.append("【工作偏好（L7 偏好提取）】")
        for p in prefs:
            label = p.get("type", "偏好")
            snippet = p.get("snippet", "")
            lines.append(f"  - [{label}] {snippet}")
        lines.append("")

    # L6 Reflections (corrections/lessons)
    reflection_list = (injection or {}).get("reflections") or []
    if reflection_list:
        lines.append("【历史反思（L6 反思摘要）】")
        for i, item in enumerate(reflection_list, 1):
            lines.append(f"  {i}. {item.get('summary', '')[:200]}")
        lines.append("")

    if lines:
        return "\n".join(lines)
    return ""


def handle_session_start(arguments: dict) -> str:
    result = session_start(
        agent_name=arguments.get("agent_name", ""),
        auto_inject=arguments.get("auto_inject", True),
    )

    injection = (result or {}).get("injection") or {}
    l7_text = _format_l7_instructions(injection)

    # Always return pure JSON — put L7 text inside the result dict
    # so server.py json.loads() doesn't break on mixed text+JSON.
    if l7_text:
        result["l7_instructions"] = l7_text

    return json.dumps(result, ensure_ascii=False, default=str)


def handle_session_end(arguments: dict) -> str:
    result = session_end(
        session_id=arguments.get("session_id", ""),
        auto_extract=arguments.get("auto_extract", False),
    )
    return json.dumps(result, ensure_ascii=False, default=str)


def handle_session_summary(arguments: dict) -> str:
    result = session_summary(
        session_id=arguments.get("session_id"),
        agent_name=arguments.get("agent_name"),
        limit=arguments.get("limit", 5),
    )
    return json.dumps(result, ensure_ascii=False, default=str)
