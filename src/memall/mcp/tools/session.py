import json
from memall.pipeline.session import session_start, session_end, session_summary


def handle_session_start(arguments: dict) -> str:
    result = session_start(
        agent_name=arguments.get("agent_name", ""),
        auto_inject=arguments.get("auto_inject", False),
    )
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
