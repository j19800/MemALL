import json as _json
from datetime import datetime, timezone
from memall.core.db import get_conn


def handle(arguments: dict) -> str:
    memory_id = arguments["memory_id"]
    action = arguments["action"]
    context = arguments.get("context", "")
    conn = get_conn()
    try:
        row = conn.execute("SELECT id, level FROM memories WHERE id = ?", (memory_id,)).fetchone()
        if not row:
            return _json.dumps({"error": f"memory {memory_id} not found"})
        if row["level"] != "L6":
            return _json.dumps({"error": f"memory {memory_id} is not an L6 reflection (level={row['level']})"})

        meta_row = conn.execute("SELECT metadata FROM memories WHERE id = ?", (memory_id,)).fetchone()
        try:
            meta = _json.loads(meta_row["metadata"]) if meta_row and meta_row["metadata"] else {}
        except Exception:
            meta = {}
        interactions = meta.get("interactions", [])
        interactions.append({"action": action, "context": context, "timestamp": datetime.now(timezone.utc).isoformat()})
        meta["interactions"] = interactions
        conn.execute("UPDATE memories SET metadata = ?, updated_at = ? WHERE id = ?",
                     (_json.dumps(meta, ensure_ascii=False), datetime.now(timezone.utc).isoformat(), memory_id))
        conn.commit()
        return _json.dumps({"status": "ok", "memory_id": memory_id, "action": action, "total_interactions": len(interactions)})
    finally:
        conn.close()
