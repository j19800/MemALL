import json
from memall.core.thin_waist import timeline


def handle(arguments: dict) -> str:
    items = timeline(**arguments)
    # Include level/owner/agent_name/subject — the previous projection dropped
    # them, forcing every timeline consumer to do a follow-up trace call.
    return json.dumps([{
        "id": r.id, "content": r.content, "subject": getattr(r, "subject", ""),
        "category": r.category, "level": r.level,
        "owner": getattr(r, "owner", ""), "agent_name": getattr(r, "agent_name", ""),
        "occurred_at": r.occurred_at,
    } for r in items], ensure_ascii=False)
