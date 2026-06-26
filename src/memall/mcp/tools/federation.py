import json
from memall.mcp.federation_tools import (
    fed_query, fed_publish, fed_conflicts, fed_deliver,
    auto_inject, auto_extract,
)


def handle_query(arguments: dict) -> str:
    result = fed_query(
        query=arguments.get("query", ""),
        agent_name=arguments.get("agent_name", ""),
        category=arguments.get("category", ""),
        trust_level=arguments.get("trust_level", ""),
        limit=arguments.get("limit", 20),
    )
    return json.dumps(result, ensure_ascii=False, default=str)


def handle_publish(arguments: dict) -> str:
    result = fed_publish(
        memory_id=arguments["memory_id"],
        source_agent=arguments.get("source_agent", ""),
        trust_level=arguments.get("trust_level", "family"),
        category=arguments.get("category", ""),
    )
    return json.dumps(result, ensure_ascii=False)


def handle_conflicts(arguments: dict) -> str:
    result = fed_conflicts(limit=arguments.get("limit", 20))
    return json.dumps(result, ensure_ascii=False, default=str)


def handle_inject(arguments: dict) -> str:
    result = auto_inject(agent_name=arguments["agent_name"])
    return json.dumps(result, ensure_ascii=False, default=str)


def handle_extract(arguments: dict) -> str:
    result = auto_extract(session_id=arguments["session_id"])
    return json.dumps(result, ensure_ascii=False, default=str)


def handle_deliver(arguments: dict) -> str:
    result = fed_deliver(
        target_agent=arguments["target_agent"],
        content=arguments["content"],
        event_type=arguments.get("event_type", "hub_push"),
        category=arguments.get("category", "reflection"),
        source=arguments.get("source", "hub"),
    )
    return json.dumps(result, ensure_ascii=False, default=str)
