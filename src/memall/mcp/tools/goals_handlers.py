"""MCP handlers for the goal lifecycle (intake -> decompose -> deliberate ->
assign -> execute -> verify -> closed)."""
import json


def _dumps(obj):
    return json.dumps(obj, ensure_ascii=False, default=str)


def _as_int(value, field):
    """Parse an id argument, or return (None, error_payload).

    A tool argument arrives as whatever the model typed, so ``"1000948"``,
    ``1000948.0`` and ``"goal #1000948"`` all show up in practice.  The old
    ``int(gid)`` turned those into a bare ValueError, which the MCP layer
    surfaced as an internal error (-32603) with no hint about which field was
    wrong (P3-3).  A structured refusal is actionable; a traceback is not.
    """
    if isinstance(value, bool):
        return None, {"status": "error", "field": field,
                      "error": f"{field} must be an integer id, got a boolean"}
    if isinstance(value, int):
        return value, None
    try:
        # Accept "123", " 123 ", and integral floats like 123.0.
        as_float = float(str(value).strip())
    except (TypeError, ValueError):
        return None, {"status": "error", "field": field,
                      "error": f"{field} must be an integer id, got {value!r}"}
    if not as_float.is_integer():
        return None, {"status": "error", "field": field,
                      "error": f"{field} must be an integer id, got {value!r}"}
    return int(as_float), None


def _goal_id_of(args):
    """Shared (goal_id, error_payload) extraction for every goal action."""
    raw = args.get("goal_id") or args.get("memory_id")
    if raw is None or raw == "":
        return None, {"status": "error", "error": "goal_id is required"}
    return _as_int(raw, "goal_id")


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
    gid, err = _goal_id_of(args)
    if err:
        return _dumps(err)
    return _dumps(decompose(gid, agent_name, args.get("children") or [],
                          parallel_ok=bool(args.get("parallel_ok"))))


def handle_goal_deliberate(args, agent_name=""):
    from memall.pipeline.goals import attach_discussion
    gid, err = _goal_id_of(args)
    if err:
        return _dumps(err)
    did, err = _as_int(args.get("discussion_id"), "discussion_id")
    if err:
        return _dumps(err)
    return _dumps(attach_discussion(gid, agent_name, did))


def handle_goal_finalize(args, agent_name=""):
    from memall.pipeline.goals import finalize
    gid, err = _goal_id_of(args)
    if err:
        return _dumps(err)
    return _dumps(finalize(gid, agent_name))


def handle_goal_request_verification(args, agent_name=""):
    from memall.pipeline.goals import request_verification
    gid, err = _goal_id_of(args)
    if err:
        return _dumps(err)
    return _dumps(request_verification(gid, agent_name))


def handle_goal_verify(args, agent_name=""):
    from memall.pipeline.goals import verify_goal
    gid, err = _goal_id_of(args)
    if err:
        return _dumps(err)
    passed = args.get("passed")
    if passed is None:
        passed = args.get("verdict_passed", True)
    return _dumps(verify_goal(gid, agent_name, bool(passed),
                              args.get("verdict") or args.get("note") or ""))


def handle_goal_status(args, agent_name=""):
    from memall.core.db import get_conn
    from memall.pipeline.goals import _children, _get_goal, _phase_of, goal_tick
    raw = args.get("goal_id") or args.get("memory_id")
    gid, err = (None, None) if raw in (None, "") else _as_int(raw, "goal_id")
    if err:
        return _dumps(err)
    conn = get_conn()
    try:
        if gid is not None:
            g = _get_goal(conn, gid)
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
