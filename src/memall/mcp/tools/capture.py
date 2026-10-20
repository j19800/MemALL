import json

from memall.core.models import MemoryInput
from memall.core.thin_waist import capture as do_capture

# Fields that belong to MemoryInput.  The MCP schema for memall_write
# declares MORE than this (dedup_threshold, items, memory_id,
# interaction_type, ...); those belong to other actions and are routed by
# the write dispatcher.  Building MemoryInput(**arguments) blindly meant a
# client that passed a schema-declared field straight through would crash
# with "unexpected keyword argument" (audit P0-2).
_CAPTURE_FIELDS = (
    "content", "level", "owner", "agent_name", "creator", "subject",
    "project", "category", "stream", "summary", "occurred_at", "supersedes",
    "confidence", "visibility", "metadata", "tags", "thread_id", "source_ids",
)


def handle(arguments: dict) -> str:
    """Capture one memory.

    Project inference is centralized in capture() (thin_waist), so any
    memory written through this handler — or any other path — always gets
    a non-empty project.

    Only MemoryInput fields are forwarded; anything else in the payload is
    reported back as ``ignored_fields`` instead of being silently dropped
    (audit P0-2: the schema declares fields for OTHER actions, and a
    client that passed them to action=capture had no way to notice they
    did nothing).
    """
    payload = {k: v for k, v in arguments.items() if k in _CAPTURE_FIELDS}
    # Fields the validator stripped because they belong to another action
    # (the adapter passes them on as __extra_args__).
    extra = arguments.get("__extra_args__") or {}
    ignored = sorted(set(extra) | (set(arguments) - set(_CAPTURE_FIELDS)
                                    - {"action", "__extra_args__"}))
    inp = MemoryInput(**payload)

    try:
        mid = do_capture(inp)
        out = {"id": mid, "status": "ok"}
        if ignored:
            out["ignored_fields"] = ignored
            out["hint"] = (
                "these fields belong to another action of memall_write "
                "(e.g. items -> store_batch, memory_id -> update, "
                "interaction_type -> record_interaction); they were ignored"
            )
        return json.dumps(out)
    except ValueError as e:
        return json.dumps({"id": None, "status": "rejected", "reason": str(e)})
