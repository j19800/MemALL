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
    action = arguments.get("action", "")
    if not action:
        return json.dumps({"error": "sub_action is required (expired|low_value|review|stats|all)"})
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
    action = arguments.get("action", "")
    if not action:
        return json.dumps({"error": "sub_action is required (clean|index|distill|all|report)"})
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
    action = arguments.get("action", "")
    if not action:
        return json.dumps({"error": "sub_action is required (audit|permit|check|score|list)"})

    # The caller's identity.  The adapter re-attaches it to every call, so an
    # empty value means the request never handshook.  ``permit`` needs it
    # because granting a permission level is a privilege operation — see that
    # branch.  ``target_agent`` is the row being configured; it must be a
    # SEPARATE field, because using ``agent_name`` for both made "permit
    # myself" and "permit someone else" indistinguishable (R3-8: an agent
    # could raise its own level, or another's).
    caller = (arguments.get("agent_name") or "").strip()

    if action == "audit":
        agent = arguments.get("target_agent") or caller or None
        result = audit_sensitive(agent_name=agent)
    elif action == "permit":
        target = (arguments.get("target_agent") or arguments.get("target") or "").strip()
        level = arguments.get("level")
        if not target or not level:
            return json.dumps({"error": (
                "target_agent and level are required for permit; target_agent "
                "names the identity being configured (agent_name carries the "
                "caller's own identity)"
            )}, ensure_ascii=False)
        if not caller:
            return json.dumps({"error": (
                "permit requires an agent identity: send agent_name in the MCP "
                "handshake so the grant can be attributed"
            )}, ensure_ascii=False)
        # Authorisation: a supervisor may configure anyone; everyone else may
        # only LOWER their own level.  Raising your own permission is exactly
        # the privilege escalation this gate exists to stop.
        #
        # The deployment's human owner counts as a supervisor here even when
        # not listed in ``security.supervisor_agents``: that list names the
        # AGENT orchestrators, and the human who owns the machine must not be
        # locked out of configuring the agents they supervise.
        from memall.config import get_config
        from memall.core.task_identity import is_supervisor

        human_owner = (get_config("identity.human_owner", "") or "").strip().lower()
        is_human_owner = bool(human_owner) and caller.lower() == human_owner
        supervisor = is_supervisor(caller) or is_human_owner

        same_agent = target.lower() == caller.lower()
        if not supervisor and not same_agent:
            return json.dumps({
                "error": "forbidden",
                "message": (
                    f"'{caller}' may not configure '{target}': only a member of "
                    "security.supervisor_agents can change another agent's "
                    "permission level."
                ),
                "caller": caller,
                "target_agent": target,
            }, ensure_ascii=False)
        if same_agent and not supervisor:
            current = _current_permission_level(target)
            if current and not _is_lower_permission(level, current):
                return json.dumps({
                    "error": "forbidden",
                    "message": (
                        f"'{caller}' may not raise its own permission from "
                        f"'{current}' to '{level}': self-escalation requires a "
                        "supervisor."
                    ),
                    "caller": caller,
                    "target_agent": target,
                    "current": current,
                    "requested": level,
                }, ensure_ascii=False)
        result = set_permission(target, level)
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


# Ordered least → most privileged.  ``private`` is the MOST restrictive
# permission level (it is the top of VISIBILITY_RANK in core.thin_waist),
# so indexing it last is what makes the escalation check read correctly:
# moving from ``private`` down to ``public`` relaxes access and must not be
# something an agent can do to itself.
_PERMISSION_ORDER = ("public", "trusted", "private")


def _current_permission_level(agent_name: str) -> str:
    """Current permission_level of *agent_name*, or "" when unknown."""
    from memall.core.db import get_conn

    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT permission_level FROM identities WHERE agent_name = ?",
            (agent_name,),
        ).fetchone()
        return (row["permission_level"] if row else "") or ""
    except Exception:
        return ""
    finally:
        conn.close()


def _is_lower_permission(requested: str, current: str) -> bool:
    """True when *requested* is LESS privileged than *current*.

    ``permission_level`` is a VISIBILITY requirement, not a rank of power:
    ``private`` = only the agent itself may read its rows (most restrictive),
    ``trusted`` = family circle, ``public`` = anyone (most permissive).

    So "less privileged" means "harder to read" — a HIGHER index in
    ``_PERMISSION_ORDER``.  Relaxing ``private`` into ``public`` widens who
    can read, which is the escalation this gate exists to stop; tightening
    ``public`` into ``private`` is a safe self-service action.
    """
    try:
        return _PERMISSION_ORDER.index(requested) > _PERMISSION_ORDER.index(current)
    except ValueError:
        return False



def handle_ops(arguments: dict) -> str:
    # Canonical field is ``ops_sub_action``; ``sub_action`` stays as a
    # deprecated alias.  ``action`` is already rewritten by the dispatcher.
    action = arguments.get("action") or arguments.get("ops_sub_action") or ""
    if not action:
        return json.dumps({
            "error": "ops_sub_action is required "
                     "(merge|split|tag|batch_tag|archive|restore|dedup|undo)"
        })

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
        if not arguments.get("source_id") or not arguments.get("target_id"):
            return json.dumps({"error": "source_id and target_id are required for ops:merge"})
        result = merge_memories(arguments["source_id"], arguments["target_id"],
                                separator=arguments.get("separator", "\n---\n"))
    elif action == "split":
        if not arguments.get("memory_id"):
            return json.dumps({"error": "memory_id is required for ops:split"})
        delim = arguments.get("delimiter", "\n\n")
        result = split_memory(arguments["memory_id"], delimiter=delim)
    elif action == "tag":
        if not arguments.get("memory_id"):
            return json.dumps({"error": "memory_id is required for ops:tag"})
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
        if not arguments.get("op_id"):
            return json.dumps({"error": "op_id is required for ops:undo"})
        result = undo(arguments["op_id"])
    else:
        return json.dumps({"error": f"unknown action: {action}"})
    return json.dumps(result, ensure_ascii=False, default=str)


def handle_db(arguments: dict) -> str:
    action = arguments.get("action") or arguments.get("db_sub_action") or ""
    if not action:
        return json.dumps({
            "error": "db_sub_action is required "
                     "(optimize|stats|vacuum|archive_stats|archive_vacuum|"
                     "backfill_thread|backfill_project|dedupe_l9|dedupe_l10)"
        })

    # VACUUM rewrites the whole database file (long exclusive lock, not
    # undoable) → gated.  Backfills / L9-L10 de-dup are reversible-ish but still
    # bulk writes, so they are gated too unless called as a dry run.
    if action in ("vacuum", "archive_vacuum"):
        gate = _confirmation_required(arguments, f"db:{action}")
        if gate:
            return gate
    if action in ("backfill_thread", "backfill_project", "backfill_tags",
                  "dedupe_l9", "dedupe_l10"):
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
    elif action == "backfill_tags":
        from memall.pipeline._backfill_tags import backfill_tags
        result = backfill_tags(
            dry_run=arguments.get("dry_run", False),
            limit=int(arguments.get("limit") or 0),
        )
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
