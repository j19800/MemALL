import json
from typing import Optional
from memall.pipeline.forget import forget_stats, forget_review, forget_expired, forget_low_value, forget_step
from memall.pipeline.adaptive import adaptive_clean, adaptive_index, adaptive_distill, adaptive_step, adaptive_report
from memall.pipeline.security import audit_sensitive, set_permission, check_access, security_score, list_agents_by_permission
from memall.pipeline.ops import merge_memories, split_memory, tag_memory, batch_tag, batch_archive, batch_restore, deduplicate, undo
from memall.core.db import (
    optimize_db, db_stats, vacuum_db,
    archive_db_stats as _arch_stats, vacuum_archive_db as _vac_arch,
)


def _confirm(value) -> bool:
    """Interpret a ``confirm`` argument (bool / "yes" / "true" / "1")."""
    if value is True:
        return True
    if isinstance(value, str):
        return value.strip().lower() in ("yes", "true", "1", "y")
    return False


def _confirmation_required(arguments: dict, op: str, hint: str = "") -> Optional[str]:
    """Return an error payload unless the caller explicitly confirmed ``op``.

    Destructive maintenance ops (bulk delete / merge / archive / VACUUM) are
    irreversible, so they must be requested twice: once to see the plan, once
    with ``confirm=true``.  A ``dry_run=true`` call also counts as the
    read-only pass and is never gated.
    """
    if _confirm(arguments.get("confirm")) or arguments.get("dry_run") is True:
        return None
    return json.dumps({
        "status": "confirmation_required",
        "op": op,
        "message": f"'{op}' is destructive and cannot be undone. "
                   f"Inspect the impact first (stats / dry_run=true), then "
                   f"re-call with confirm=true to execute."
                   + (f" {hint}" if hint else ""),
    }, ensure_ascii=False)


def handle_forget(arguments: dict) -> str:
    action = arguments["action"]
    days = arguments.get("days", 90)
    agent = arguments.get("agent_name", None) or None

    # Deletion is irreversible → require an explicit confirmation pass.
    if action in ("expired", "low_value", "all"):
        gate = _confirmation_required(
            arguments, f"forget:{action}",
            hint="Run forget sub_action=review (or stats) first to see what would go.",
        )
        if gate:
            return gate

    if action == "stats":
        result = forget_stats()
    elif action == "review":
        result = forget_review(days=days, agent_name=agent)
    elif action == "expired":
        result = forget_expired(days=days, agent_name=agent)
    elif action == "low_value":
        result = forget_low_value(agent_name=agent)
    elif action == "all":
        result = forget_step(days=days, agent_name=agent)
    else:
        return json.dumps({"error": f"unknown action: {action}"})
    return json.dumps(result, ensure_ascii=False, default=str)


def handle_adaptive(arguments: dict) -> str:
    action = arguments["action"]
    agent = arguments.get("agent_name", None) or None

    if action == "clean":
        result = adaptive_clean(agent_name=agent)
    elif action == "index":
        result = adaptive_index(agent_name=agent)
    elif action == "distill":
        result = adaptive_distill(agent_name=agent)
    elif action == "all":
        result = adaptive_step(agent_name=agent)
    elif action == "report":
        result = adaptive_report()
    else:
        return json.dumps({"error": f"unknown action: {action}"})
    return json.dumps(result, ensure_ascii=False, default=str)


def handle_security(arguments: dict) -> str:
    action = arguments["action"]

    if action == "audit":
        agent = arguments.get("agent_name", None) or None
        result = audit_sensitive(agent_name=agent)
    elif action == "permit":
        agent_name = arguments.get("agent_name")
        level = arguments.get("level")
        if not agent_name or not level:
            return json.dumps({"error": "agent_name and level are required for permit action"})
        result = set_permission(agent_name, level)
    elif action == "check":
        requester = arguments.get("requester")
        target = arguments.get("target")
        if not requester or not target:
            return json.dumps({"error": "requester and target are required for check action"})
        result = check_access(requester, target)
    elif action == "score":
        result = security_score()
    elif action == "list":
        level = arguments.get("level", "private")
        result = list_agents_by_permission(level)
    else:
        return json.dumps({"error": f"unknown action: {action}"})
    return json.dumps(result, ensure_ascii=False, default=str)


def handle_ops(arguments: dict) -> str:
    action = arguments["action"]

    # merge / split / dedup rewrite or delete memories → gated.
    if action in ("merge", "split", "dedup"):
        gate = _confirmation_required(
            arguments, f"ops:{action}",
            hint="Pass dry_run=true for dedup to preview the pairs first.",
        )
        if gate:
            return gate
    # batch_archive moves memories out of the active set → gated unless dry-run.
    if action == "archive":
        gate = _confirmation_required(
            arguments, f"ops:{action}",
            hint="Pass dry_run=true to preview what would be archived.",
        )
        if gate:
            return gate

    if action == "merge":
        result = merge_memories(arguments["source_id"], arguments["target_id"],
                                separator=arguments.get("separator", "\n---\n"))
    elif action == "split":
        delim = arguments.get("delimiter", "\n\n")
        result = split_memory(arguments["memory_id"], delimiter=delim)
    elif action == "tag":
        result = tag_memory(
            arguments["memory_id"],
            arguments.get("tags", []),
            mode=arguments.get("mode", "add"),
        )
    elif action == "batch_tag":
        result = batch_tag(
            agent_name=arguments.get("agent_name"),
            category=arguments.get("category"),
            tags=arguments.get("tags", []),
            mode=arguments.get("mode", "add"),
            level=arguments.get("level"),
            tags_include=arguments.get("tags_include"),
            before=arguments.get("before"),
            after=arguments.get("after"),
            dry_run=arguments.get("dry_run", False),
        )
    elif action == "archive":
        result = batch_archive(
            agent_name=arguments.get("agent_name"),
            days=arguments.get("days", 30),
            dry_run=arguments.get("dry_run", False),
        )
    elif action == "restore":
        result = batch_restore(
            agent_name=arguments.get("agent_name"),
            dry_run=arguments.get("dry_run", False),
        )
    elif action == "dedup":
        result = deduplicate(
            agent_name=arguments.get("agent_name"),
            threshold=arguments.get("threshold", 0.9),
            max_pairs=arguments.get("max_pairs", 5000),
            max_memories=arguments.get("max_memories", 10000),
            length_ratio_max=arguments.get("length_ratio_max", 5.0),
            dry_run=arguments.get("dry_run", False),
        )
    elif action == "undo":
        result = undo(arguments["op_id"])
    else:
        return json.dumps({"error": f"unknown action: {action}"})
    return json.dumps(result, ensure_ascii=False, default=str)


def handle_db(arguments: dict) -> str:
    action = arguments["action"]

    # VACUUM rewrites the whole database file (long exclusive lock, not
    # undoable) → gated.  Backfills / L9-L10 de-dup are reversible-ish but still
    # bulk writes, so they are gated too unless called as a dry run.
    if action in ("vacuum", "archive_vacuum"):
        gate = _confirmation_required(arguments, f"db:{action}")
        if gate:
            return gate
    if action in ("backfill_thread", "backfill_project", "dedupe_l9", "dedupe_l10"):
        gate = _confirmation_required(
            arguments, f"db:{action}",
            hint="Pass dry_run=true to preview the changes first.",
        )
        if gate:
            return gate

    if action == "optimize":
        result = optimize_db()
        return json.dumps(result)
    elif action == "stats":
        result = db_stats()
        return json.dumps(result, default=str)
    elif action == "vacuum":
        result = vacuum_db()
        return json.dumps(result)
    elif action == "archive_stats":
        result = _arch_stats()
        return json.dumps(result, default=str)
    elif action == "archive_vacuum":
        result = _vac_arch()
        return json.dumps(result)
    elif action == "backfill_thread":
        from memall.pipeline._backfill_thread import backfill_thread_ids
        result = backfill_thread_ids(dry_run=arguments.get("dry_run", False))
        return json.dumps(result, ensure_ascii=False, default=str)
    elif action == "backfill_project":
        from memall.pipeline._backfill_project import backfill_project_ids
        result = backfill_project_ids(dry_run=arguments.get("dry_run", False))
        return json.dumps(result, ensure_ascii=False, default=str)
    elif action == "dedupe_l9":
        from memall.pipeline.distill import dedupe_l9
        result = dedupe_l9(
            dry_run=arguments.get("dry_run", False),
            archive_corrupt=arguments.get("archive_corrupt", True),
        )
        return json.dumps(result, ensure_ascii=False, default=str)
    elif action == "dedupe_l10":
        from memall.pipeline.integrate import dedupe_l10
        result = dedupe_l10(dry_run=arguments.get("dry_run", False))
        return json.dumps(result, ensure_ascii=False, default=str)
    else:
        return json.dumps({"error": f"unknown action: {action}"})
