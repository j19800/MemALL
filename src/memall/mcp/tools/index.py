import json
from memall.graph.embeddings import build_index


def handle(arguments: dict) -> str:
    force = arguments.get("force", False)
    result = build_index(force=force)
    return json.dumps(result, ensure_ascii=False, default=str)
