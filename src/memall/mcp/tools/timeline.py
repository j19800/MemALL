import json
from memall.core.thin_waist import timeline


def handle(arguments: dict) -> str:
    items = timeline(**arguments)
    return json.dumps([{
        "id": r.id, "content": r.content, "category": r.category,
        "occurred_at": r.occurred_at,
    } for r in items], ensure_ascii=False)
