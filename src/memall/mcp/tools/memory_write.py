import json
from memall.core.thin_waist import smart_store, store_batch, update


def handle_smart_store(arguments: dict) -> str:
    result = smart_store(
        content=arguments["content"],
        owner=arguments.get("owner", ""),
        agent_name=arguments.get("agent_name", ""),
        subject=arguments.get("subject", ""),
        project=arguments.get("project", ""),
        category=arguments.get("category", "general"),
        level=arguments.get("level", "P2"),
        dedup_threshold=arguments.get("dedup_threshold", 0.85),
    )
    return json.dumps(result, ensure_ascii=False)


def handle_store_batch(arguments: dict) -> str:
    result = store_batch(arguments.get("items", []))
    return json.dumps(result, ensure_ascii=False)


def handle_update(arguments: dict) -> str:
    mem_id = arguments["memory_id"]
    fields = {k: v for k, v in arguments.items() if k != "memory_id" and v is not None}
    ok = update(mem_id, **fields)
    return json.dumps({"memory_id": mem_id, "updated": ok, "fields": list(fields.keys())})
