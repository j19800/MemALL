import json
from memall.agent_memory import infer_project
from memall.core.models import MemoryInput
from memall.core.thin_waist import capture as do_capture


def handle(arguments: dict) -> str:
    inp = MemoryInput(**arguments)

    # Fallback: if project is empty, infer from agent_name + content
    if not inp.project:
        inp.project = infer_project(
            agent_name=inp.agent_name,
            category=inp.category,
            content=inp.content,
        )

    try:
        mid = do_capture(inp)
        return json.dumps({"id": mid, "status": "ok"})
    except ValueError as e:
        return json.dumps({"id": None, "status": "rejected", "reason": str(e)})