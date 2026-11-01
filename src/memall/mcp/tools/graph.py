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
    agent_name = args.pop("agent_name", None)
    # Traversal returns node CONTENT: hand the caller's identity down as the
    # viewer so the trust filter can drop nodes it may not read.  Previously
    # the identity was dropped entirely and `traverse(1)` returned a private
    # memory plus its whole neighbourhood.
    graph = traverse(viewer=agent_name or None, **args)
    return json.dumps(graph, ensure_ascii=False)
