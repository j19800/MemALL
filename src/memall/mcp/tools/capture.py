import json
from memall.core.models import MemoryInput
from memall.core.thin_waist import capture as do_capture


def handle(arguments: dict) -> str:
    # Project inference is now centralized in capture() (thin_waist), so any
    # memory written through this handler — or any other path — always gets a
    # non-empty project. We just forward the caller's input as-is.
    inp = MemoryInput(**arguments)

    try:
        mid = do_capture(inp)
        return json.dumps({"id": mid, "status": "ok"})
    except ValueError as e:
        return json.dumps({"id": None, "status": "rejected", "reason": str(e)})