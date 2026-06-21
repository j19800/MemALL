import json
from memall.core.thin_waist import connect, traverse


def handle_connect(arguments: dict) -> str:
    eid = connect(**arguments)
    return json.dumps({"id": eid, "status": "ok"})


def handle_traverse(arguments: dict) -> str:
    graph = traverse(**arguments)
    return json.dumps(graph, ensure_ascii=False)
