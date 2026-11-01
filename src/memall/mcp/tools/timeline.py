import json
from memall.core.thin_waist import timeline


def handle(arguments: dict) -> str:
    args = dict(arguments)
    agent_name = args.pop("agent_name", None)
    # Timeline is a read path: pass the session identity down as the viewer so
    # core.timeline can drop rows this caller may not read.  It used to be
    # dropped entirely, which returned every row to every caller.
    items = timeline(viewer=agent_name or None, **args)
    # Include level/owner/agent_name/subject — the previous projection dropped
    # them, forcing every timeline consumer to do a follow-up trace call.
    return json.dumps([{
        "id": r.id, "content": r.content, "subject": getattr(r, "subject", ""),
        "category": r.category, "level": r.level,
        "owner": getattr(r, "owner", ""), "agent_name": getattr(r, "agent_name", ""),
        "occurred_at": r.occurred_at,
    } for r in items], ensure_ascii=False)
