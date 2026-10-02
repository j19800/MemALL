import json
from memall.pipeline.convergence import create_discussion, confirm_discussion, get_discussion, list_active_discussions


def handle_create(arguments: dict) -> str:
    result = create_discussion(
        title=arguments["title"],
        background=arguments.get("background", ""),
        options=arguments.get("options"),
        open_questions=arguments.get("open_questions"),
        action_items=arguments.get("action_items"),
        recommendation=arguments.get("recommendation", ""),
        creator=arguments.get("agent_name", "system"),
        participants=arguments.get("participants", []),
        timeout_hours=arguments.get("timeout_hours", 24),
    )
    return json.dumps(result, ensure_ascii=False, default=str)


def handle_respond(arguments: dict) -> str:
    result = confirm_discussion(
        discussion_id=arguments["discussion_id"],
        agent_name=arguments["agent_name"],
        stance=arguments["stance"],
        note=arguments.get("arguments", ""),
    )
    return json.dumps(result, ensure_ascii=False, default=str)


def handle_status(arguments: dict) -> str:
    disc_id = arguments.get("discussion_id", 0)
    if disc_id:
        result = get_discussion(disc_id)
    else:
        result = {"active_topics": list_active_discussions()}
    return json.dumps(result, ensure_ascii=False, default=str)


def handle_suggest_participants(arguments: dict) -> str:
    from memall.pipeline.agent_routing import suggest_participants
    result = suggest_participants(
        title=arguments.get("title", ""),
        background=arguments.get("background", ""),
        options=arguments.get("options"),
        k=int(arguments.get("k", 3)),
        exclude=arguments.get("exclude"),
    )
    return json.dumps({"suggested_participants": result}, ensure_ascii=False, default=str)
