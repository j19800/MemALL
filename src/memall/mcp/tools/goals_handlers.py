"""MCP handlers for the goal lifecycle (intake -> decompose -> deliberate ->
assign -> execute -> verify -> closed)."""
import json


def _dumps(obj):
    return json.dumps(obj, ensure_ascii=False, default=str)


def handle_goal_create(args, agent_name=""):
    from memall.pipeline.goals import create_goal
    return _dumps(create_goal(
        objective=args.get("objective") or args.get("content") or "",
        owner=args.get("owner_agent") or args.get("goal_owner") or agent_name,
        acceptance=args.get("acceptance") or "",
        verifier=args.get("verifier") or "",
        created_by=agent_name,
        participants=args.get("participants"),
    ))


def handle_goal_decompose(args, agent_name=""):
    from memall.pipeline.goals import decompose
    gid = args.get("goal_id") or args.get("memory_id")
    if not gid:
        return _dumps({"status": "error", "error": "goal_id is required"})
    return _dumps(decompose(int(gid), agent_name, args.get("children") or []))


def handle_goal_deliberate(args, agent_name=""):
    from memall.pipeline.goals import attach_discussion
    gid = args.get("goal_id") or args.get("memory_id")
    did = args.get("discussion_id")
    if not gid or not did:
        return _dumps({"status": "error",
                       "error": "goal_id and discussion_id are required"})
    return _dumps(attach_discussion(int(gid), agent_name, int(did)))


def handle_goal_finalize(args, agent_name=""):
    from memall.pipeline.goals import finalize
    gid = args.get("goal_id") or args.get("memory_id")
    if not gid:
        return _dumps({"status": "error", "error": "goal_id is required"})
    return _dumps(finalize(int(gid), agent_name))


def handle_goal_request_verification(args, agent_name=""):
    from memall.pipeline.goals import request_verification
    gid = args.get("goal_id") or args.get("memory_id")
    if not gid:
        return _dumps({"status": "error", "error": "goal_id is required"})
    return _dumps(request_verification(int(gid), agent_name))


def handle_goal_verify(args, agent_name=""):
    from memall.pipeline.goals import verify_goal
    gid = args.get("goal_id") or args.get("memory_id")
    if not gid:
        return _dumps({"status": "error", "error": "goal_id is required"})
    passed = args.get("passed")
    if passed is None:
        passed = args.get("verdict_passed", True)
    return _dumps(verify_goal(int(gid), agent_name, bool(passed),
                              args.get("verdict") or args.get("note") or ""))


def handle_goal_status(args, agent_name=""):
    from memall.core.db import get_conn
    from memall.pipeline.goals import _children, _get_goal, _phase_of, goal_tick
    conn = get_conn()
    try:
        gid = args.get("goal_id") or args.get("memory_id")
        if gid:
            g = _get_goal(conn, int(gid))
            if not g:
                return _dumps({"status": "error", "error": f"goal #{gid} not found"})
            return _dumps({"status": "ok", "goal_id": g["id"],
                           "phase": _phase_of(g["meta"]),
                           "objective": g["meta"].get("objective", ""),
                           "owner": g["meta"].get("owner", ""),
                           "verifier": g["meta"].get("verifier", ""),
                           "discussion_id": g["meta"].get("discussion_id", 0),
                           "children": _children(conn, g["id"]),
                           "verdict": g["meta"].get("verdict")})
        return _dumps(goal_tick(conn))
    finally:
        conn.close()
