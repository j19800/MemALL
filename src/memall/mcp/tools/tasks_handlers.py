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


def handle_task_resolve(arguments: dict, agent_name: str = "") -> str:
    """Close a task card as resolved (design #1000539).

    Writes through task_lifecycle.resolve_task — the single terminal-transition
    path that also closes the card's reminders (AC5) — and passes the caller
    identity so a non-addressee is refused (AC4).  ``ack`` is deliberately NOT
    exposed: see task_lifecycle.acknowledge_task's note.
    """
    from memall.pipeline.task_lifecycle import resolve_task

    task_id = arguments.get("task_id") or arguments.get("memory_id")
    if not task_id:
        return json.dumps({"status": "error",
                           "error": "task_id is required"}, ensure_ascii=False)
    caller = (agent_name or arguments.get("agent_name", "") or "")
    return json.dumps(
        resolve_task(int(task_id), result=arguments.get("result", "") or "",
                     caller=caller),
        ensure_ascii=False, default=str)


def handle_task_block(arguments: dict, agent_name: str = "") -> str:
    """Mark a task card blocked, needing human input (design #1000539).

    Writes through task_lifecycle.block_task (closes reminders + sets
    blocked_reason/blocked_at) with the caller gate (AC4).
    """
    from memall.pipeline.task_lifecycle import block_task

    task_id = arguments.get("task_id") or arguments.get("memory_id")
    if not task_id:
        return json.dumps({"status": "error",
                           "error": "task_id is required"}, ensure_ascii=False)
    reason = arguments.get("reason") or ""
    if not reason:
        return json.dumps({"status": "error",
                           "error": "reason is required to block a task"},
                          ensure_ascii=False)
    caller = (agent_name or arguments.get("agent_name", "") or "")
    return json.dumps(
        block_task(int(task_id), reason=reason, caller=caller),
        ensure_ascii=False, default=str)
