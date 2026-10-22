"""MCP handlers for the precise task hand-off actions (design #1000007)."""
import json


def handle_task_claim(arguments: dict, agent_name: str = "") -> str:
    from memall.mcp.tasks import claim_task

    task_id = arguments.get("task_id") or arguments.get("memory_id")
    if not task_id:
        return json.dumps({"status": "error",
                           "error": "task_id is required"}, ensure_ascii=False)
    caller = agent_name or arguments.get("agent_name", "")
    return json.dumps(claim_task(int(task_id), caller),
                      ensure_ascii=False, default=str)


def handle_task_list(arguments: dict, agent_name: str = "") -> str:
    from memall.mcp.tasks import list_tasks

    caller = agent_name or arguments.get("agent_name", "")
    return json.dumps(
        list_tasks(caller, status=arguments.get("status"),
                   limit=int(arguments.get("limit") or 20)),
        ensure_ascii=False, default=str)
