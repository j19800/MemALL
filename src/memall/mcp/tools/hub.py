import json
from memall.mcp.federation_tools import hub_connect, hub_sync


def handle_connect(arguments: dict) -> str:
    result = hub_connect()
    return json.dumps(result, ensure_ascii=False, default=str)


def handle_sync(arguments: dict) -> str:
    result = hub_sync(
        direction=arguments.get("direction", "bidirectional"),
        limit=arguments.get("limit", 20),
    )
    return json.dumps(result, ensure_ascii=False, default=str)
