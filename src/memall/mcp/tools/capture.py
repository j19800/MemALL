import json
from memall.core.models import MemoryInput
from memall.core.thin_waist import capture as do_capture


def handle(arguments: dict) -> str:
    inp = MemoryInput(**arguments)
    mid = do_capture(inp)
    return json.dumps({"id": mid, "status": "ok"})
