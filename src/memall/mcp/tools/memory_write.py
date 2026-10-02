import json
from memall.config import get_config
from memall.core.db import get_conn
from memall.core.thin_waist import smart_store, store_batch, update, normalize_agent_name


def handle_smart_store(arguments: dict) -> str:
    # Project inference is centralized in capture(); smart_store → capture() will
    # populate it automatically. Pass the caller's project through unchanged.
    project = arguments.get("project", "")
    result = smart_store(
        content=arguments["content"],
        owner=arguments.get("owner", ""),
        agent_name=arguments.get("agent_name", ""),
        subject=arguments.get("subject", ""),
        project=project,
        category=arguments.get("category", "general"),
        level=arguments.get("level", "P2"),
        dedup_threshold=arguments.get("dedup_threshold", 0.85),
    )
    return json.dumps(result, ensure_ascii=False)


def handle_store_batch(arguments: dict) -> str:
    result = store_batch(arguments.get("items", []))
    return json.dumps(result, ensure_ascii=False)


def _is_supervisor(agent: str) -> bool:
    """True if ``agent`` is listed in ``security.supervisor_agents``."""
    supes = get_config("security.supervisor_agents", []) or []
    if isinstance(supes, str):
        supes = [s for s in supes.split(",") if s.strip()]
    allowed = {normalize_agent_name(str(s)) for s in supes}
    return normalize_agent_name(agent) in allowed


def handle_update(arguments: dict) -> str:
    mem_id = arguments["memory_id"]

    # ── Cross-agent ownership enforcement (on by default) ───────────────
    # Without this gate any agent can overwrite another agent's memory *and*
    # reassign ``agent_name`` to itself — i.e. silently take ownership of it.
    # That is a privilege-boundary violation, so the gate is on by default;
    # supervisor/orchestrator agents that legitimately curate others' memories
    # are exempted explicitly via ``security.supervisor_agents``.
    if get_config("security.enforce_agent_ownership", True):
        caller = arguments.get("agent_name", "")
        if caller and not _is_supervisor(caller):
            conn = get_conn()
            try:
                row = conn.execute(
                    "SELECT agent_name FROM memories WHERE id = ?", (mem_id,)
                ).fetchone()
            finally:
                conn.close()
            if row and row["agent_name"]:
                owner = row["agent_name"]
                if owner != normalize_agent_name(caller):
                    return json.dumps({
                        "error": "ownership violation",
                        "message": f"memory {mem_id} belongs to agent '{owner}', "
                                   f"caller is '{caller}'. Add the caller to "
                                   f"security.supervisor_agents if it is a "
                                   f"supervisor, or set "
                                   f"security.enforce_agent_ownership=false.",
                        "memory_id": mem_id,
                    }, ensure_ascii=False)

    # Ownership is not a casual field: ``agent_name`` identifies the *caller*
    # (used by the gate above) and must never be written through this path,
    # otherwise any agent could re-assign a memory to itself.  ``confirm``/
    # ``dry_run`` are control flags, not row columns.
    fields = {k: v for k, v in arguments.items()
              if k not in ("memory_id", "agent_name", "confirm", "dry_run")
              and v is not None}
    ok = update(mem_id, **fields)
    return json.dumps({"memory_id": mem_id, "updated": ok, "fields": list(fields.keys())})
