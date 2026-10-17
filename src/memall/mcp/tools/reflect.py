import json as _json
from datetime import datetime, timezone
from memall.core.db import get_conn
from memall.core.thin_waist import update as _tw_update

_ALLOWED_ACTIONS = ("agree", "disagree", "probe")


def handle(arguments: dict) -> str:
    missing = [k for k in ("memory_id", "action") if not arguments.get(k)]
    if missing:
        return _json.dumps({"error": f"missing required fields: {', '.join(missing)}"})
    memory_id = arguments["memory_id"]
    action = arguments["action"]
    if action not in _ALLOWED_ACTIONS:
        return _json.dumps({"error": f"unknown action: {action} (expected one of {', '.join(_ALLOWED_ACTIONS)})"})
    context = arguments.get("context", "")

    conn = get_conn()
    try:
        row = conn.execute("SELECT id, level, metadata FROM memories WHERE id = ?", (memory_id,)).fetchone()
        if not row:
            return _json.dumps({"error": f"memory {memory_id} not found"})
        if row["level"] != "L6":
            return _json.dumps({"error": f"memory {memory_id} is not an L6 reflection (level={row['level']})"})
    finally:
        conn.close()

    # Merge through the single write funnel (thin_waist.update) so the
    # whitelist, agent_name normalization, and updated_at stamping all apply —
    # this handler previously did its own UPDATE and bypassed them.
    try:
        meta = _json.loads(row["metadata"]) if row["metadata"] else {}
    except (ValueError, TypeError):
        meta = {}
    interactions = meta.get("interactions", [])
    interactions.append({"action": action, "context": context,
                         "timestamp": datetime.now(timezone.utc).isoformat()})
    meta["interactions"] = interactions
    if not _tw_update(memory_id, metadata=_json.dumps(meta, ensure_ascii=False)):
        return _json.dumps({"error": f"memory {memory_id} not found"})
    return _json.dumps({"status": "ok", "memory_id": memory_id, "action": action,
                        "total_interactions": len(interactions)})
