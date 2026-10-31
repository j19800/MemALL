import json
from memall.core.thin_waist import connect, traverse


def handle_connect(arguments: dict) -> str:
    # The adapter re-attaches the session identity to every call, but
    # connect() has no such parameter — splatting it raised TypeError.
    args = dict(arguments)
    args.pop("agent_name", None)
    eid = connect(**args)
    return json.dumps({"id": eid, "status": "ok"})


def handle_traverse(arguments: dict) -> str:
    args = dict(arguments)
    args.pop("agent_name", None)
    graph = traverse(**args)
    return json.dumps(graph, ensure_ascii=False)
