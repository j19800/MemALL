import json
from memall.pipeline.forget import forget_stats, forget_review, forget_expired, forget_low_value, forget_step
from memall.pipeline.adaptive import adaptive_clean, adaptive_index, adaptive_distill, adaptive_step, adaptive_report
from memall.pipeline.security import audit_sensitive, set_permission, check_access, security_score, list_agents_by_permission
from memall.pipeline.ops import merge_memories, split_memory, tag_memory, batch_tag, batch_archive, batch_restore, deduplicate, undo
from memall.core.db import optimize_db, db_stats, vacuum_db


def handle_forget(arguments: dict) -> str:
    action = arguments["action"]
    days = arguments.get("days", 90)
    agent = arguments.get("agent_name", None) or None

    if action == "stats":
        result = forget_stats()
    elif action == "review":
        result = forget_review(days=days, agent_name=agent)
    elif action == "expired":
        result = forget_expired(days=days, agent_name=agent)
    elif action == "low_value":
        result = forget_low_value(agent_name=agent)
    elif action == "all":
        result = forget_step(days=days, agent_name=agent)
    else:
        return json.dumps({"error": f"unknown action: {action}"})
    return json.dumps(result, ensure_ascii=False, default=str)


def handle_adaptive(arguments: dict) -> str:
    action = arguments["action"]
    agent = arguments.get("agent_name", None) or None

    if action == "clean":
        result = adaptive_clean(agent_name=agent)
    elif action == "index":
        result = adaptive_index(agent_name=agent)
    elif action == "distill":
        result = adaptive_distill(agent_name=agent)
    elif action == "all":
        result = adaptive_step(agent_name=agent)
    elif action == "report":
        result = adaptive_report()
    else:
        return json.dumps({"error": f"unknown action: {action}"})
    return json.dumps(result, ensure_ascii=False, default=str)


def handle_security(arguments: dict) -> str:
    action = arguments["action"]

    if action == "audit":
        agent = arguments.get("agent_name", None) or None
        result = audit_sensitive(agent_name=agent)
    elif action == "permit":
        agent_name = arguments.get("agent_name")
        level = arguments.get("level")
        if not agent_name or not level:
            return json.dumps({"error": "agent_name and level are required for permit action"})
        result = set_permission(agent_name, level)
    elif action == "check":
        requester = arguments.get("requester")
        target = arguments.get("target")
        if not requester or not target:
            return json.dumps({"error": "requester and target are required for check action"})
        result = check_access(requester, target)
    elif action == "score":
        result = security_score()
    elif action == "list":
        level = arguments.get("level", "private")
        result = list_agents_by_permission(level)
    else:
        return json.dumps({"error": f"unknown action: {action}"})
    return json.dumps(result, ensure_ascii=False, default=str)


def handle_ops(arguments: dict) -> str:
    action = arguments["action"]

    if action == "merge":
        result = merge_memories(arguments["source_id"], arguments["target_id"],
                                separator=arguments.get("separator", "\n---\n"))
    elif action == "split":
        delim = arguments.get("delimiter", "\n\n")
        result = split_memory(arguments["memory_id"], delimiter=delim)
    elif action == "tag":
        result = tag_memory(
            arguments["memory_id"],
            arguments.get("tags", []),
            mode=arguments.get("mode", "add"),
        )
    elif action == "batch_tag":
        result = batch_tag(
            agent_name=arguments.get("agent_name"),
            category=arguments.get("category"),
            tags=arguments.get("tags", []),
            mode=arguments.get("mode", "add"),
            level=arguments.get("level"),
            tags_include=arguments.get("tags_include"),
            before=arguments.get("before"),
            after=arguments.get("after"),
            dry_run=arguments.get("dry_run", False),
        )
    elif action == "archive":
        result = batch_archive(
            agent_name=arguments.get("agent_name"),
            days=arguments.get("days", 30),
            dry_run=arguments.get("dry_run", False),
        )
    elif action == "restore":
        result = batch_restore(
            agent_name=arguments.get("agent_name"),
            dry_run=arguments.get("dry_run", False),
        )
    elif action == "dedup":
        result = deduplicate(
            agent_name=arguments.get("agent_name"),
            threshold=arguments.get("threshold", 0.9),
            max_pairs=arguments.get("max_pairs", 5000),
            max_memories=arguments.get("max_memories", 10000),
            length_ratio_max=arguments.get("length_ratio_max", 5.0),
            dry_run=arguments.get("dry_run", False),
        )
    elif action == "undo":
        result = undo(arguments["op_id"])
    else:
        return json.dumps({"error": f"unknown action: {action}"})
    return json.dumps(result, ensure_ascii=False, default=str)


def handle_db(arguments: dict) -> str:
    action = arguments["action"]

    if action == "optimize":
        result = optimize_db()
        return json.dumps(result)
    elif action == "stats":
        result = db_stats()
        return json.dumps(result, default=str)
    elif action == "vacuum":
        result = vacuum_db()
        return json.dumps(result)
    else:
        return json.dumps({"error": f"unknown action: {action}"})
