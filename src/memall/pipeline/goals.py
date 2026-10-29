"""Goal lifecycle: one human objective becomes a verified, landed outcome.

The requirement this implements (verbatim):
    给人一个 agent 发布一个任务 → 该 agent 拆解 → 与其他 agent 论证 →
    分配各自的任务 → 执行 → 验收落地

MemALL already had the pieces (task cards, claim, discussions, notification
bar, identity) but nothing that DRIVES them in sequence.  Each agent could see
its own queue; nothing knew what the whole objective needed next.

Design decisions worth knowing:

  * A goal is an ordinary memory (``level='L5', category='goal'``).  No new
    table, no workflow DSL: Azure's orchestration guidance is to use the
    lowest complexity that works, and an L5 row already is a queryable,
    retrievable, distillable record.  Children are ordinary task cards, so
    every existing guard, the notification bar and the task view keep working
    on them unchanged.

  * The driver is a DETERMINISTIC tick, not an LLM.  It answers "whose turn is
    it, and what is the single next thing they must do" from stored state.
    That is what makes the loop automatic for session-based agents: they are
    told one action, they act when their session runs, the state advances.
    Only two transitions need an agent's judgement (decompose, deliberate) —
    everything else is mechanical and is advanced by the tick.

  * Transitions are guarded.  A goal cannot be assigned before its discussion
    has converged, cannot be verified before every child is delivered, and
    cannot be verified by the agent that produced the work (§五 生产方不自验).

Phase machine::

    intake ──decompose──▶ decomposed ──deliberate──▶ deliberating
      ──finalize(论证已收敛)──▶ assigned ──(子任务被认领)──▶ executing
      ──(全部交付)──▶ verifying ──verify(pass)──▶ closed
                          └────verify(fail)────▶ assigned
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from memall.core.task_identity import effective_executor

logger = logging.getLogger("memall.pipeline.goals")

GOAL_CATEGORY = "goal"
GOAL_LEVEL = "L5"

_PHASES = ("intake", "decomposed", "deliberating", "assigned",
           "executing", "verifying", "closed")

# A child in one of these states no longer blocks the goal from verifying.
# This is the ONLY definition: the "return the goal to the producer" path uses
# it too, so a state that counts as delivered here cannot be missed there.
# ("verified" was listed before but no code path ever writes it — a target that
# can never occur makes the terminal check look broader than it is.)
_CHILD_TERMINAL = ("done", "resolved", "archived", "converged")

# A goal in one of these states is finished.  Used by create_goal's dedup: a
# terminal goal with the same objective is history (the objective may be
# re-filed as a new cycle), while a live one is a genuine duplicate.
_GOAL_TERMINAL = ("done", "closed", "resolved", "archived", "converged",
                  "verified")

# verdict text is stored and echoed back; both paths use one bound so the
# stored note is never truncated differently from the returned one.
_VERDICT_MAX = 2000

# A finalize run that dies between "marker written" and "phase advanced"
# leaves this lease behind; after it expires another caller may take over
# (a crashed process must not freeze the goal forever).
_FINALIZE_LEASE_SECONDS = 600

_NOT_ARCHIVED = "COALESCE(memory_status, '') != 'archived'"


# ── small helpers ────────────────────────────────────────────────────────

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _meta_of(raw) -> dict:
    try:
        m = json.loads(raw) if isinstance(raw, str) else (raw or {})
    except (json.JSONDecodeError, TypeError):
        return {}
    return m if isinstance(m, dict) else {}


def _status_of(raw) -> str:
    if isinstance(raw, dict):
        raw = raw.get("value")
    if not isinstance(raw, str):
        return "active"
    return raw.strip().lower() or "active"


def _get_goal(conn, goal_id: int) -> Optional[dict]:
    row = conn.execute(
        "SELECT id, subject, content, agent_name, metadata, category, level "
        f"FROM memories WHERE id = ? AND {_NOT_ARCHIVED}", (goal_id,)).fetchone()
    if not row:
        return None
    if row["category"] != GOAL_CATEGORY:
        return None
    return {"id": row["id"], "subject": row["subject"], "content": row["content"],
            "creator": row["agent_name"], "meta": _meta_of(row["metadata"]),
            # The raw blob is the compare-and-set token for _save_meta: every
            # transition writes back the version it read.
            "raw": row["metadata"]}


def _children(conn, goal_id: int) -> list[dict]:
    """Task cards that belong to this goal (metadata.goal_id)."""
    rows = conn.execute(
        "SELECT id, subject, agent_name, metadata FROM memories "
        "WHERE level = 'L5' AND category IN ('task', 'todo') "
        f"AND {_NOT_ARCHIVED} "
        "AND json_extract(metadata, '$.goal_id') = ? ORDER BY id",
        (goal_id,)).fetchall()
    out = []
    for r in rows:
        m = _meta_of(r["metadata"])
        out.append({
            "id": r["id"], "subject": r["subject"],
            "assignee": m.get("assignee") or r["agent_name"],
            "claimed_by": m.get("claimed_by") or "",
            "executor": effective_executor(m, r["agent_name"]),
            "status": _status_of(m.get("status")),
            "acceptance": m.get("acceptance") or "",
            "depends_on": m.get("depends_on") or [],
            "delivery": m.get("delivery") or "",
        })
    return out


def _save_meta(conn, goal_id: int, meta: dict, expect_raw: Optional[str] = None) -> bool:
    """Compare-and-set the goal's metadata blob.

    A plain ``UPDATE ... WHERE id = ?`` lets the last writer win, and two
    writers do share a goal: the driver tick runs on a timer while an agent
    calls finalize/verify on the same row.  Measured hazard: a tick that read
    ``deliberating`` could write back its stale copy after finalize had
    already advanced to ``assigned``, dropping ``task_ids`` and orphaning the
    cards it had just materialised.  Conditioning on the blob we read turns
    that into a detectable conflict instead of silent data loss.

    Returns False when the row moved on (the caller must re-read and retry).
    ``expect_raw=None`` is only for writes that have nothing to compare
    against (the goal was just captured).
    """
    payload = json.dumps(meta, ensure_ascii=False)
    if expect_raw is None:
        cur = conn.execute(
            "UPDATE memories SET metadata = ?, updated_at = ? WHERE id = ?",
            (payload, _now(), goal_id))
    else:
        cur = conn.execute(
            "UPDATE memories SET metadata = ?, updated_at = ? "
            "WHERE id = ? AND metadata = ?",
            (payload, _now(), goal_id, expect_raw))
    return (cur.rowcount or 0) > 0


def _mutate_meta(conn, goal_id: int, mutate, precondition=None,
                 attempts: int = 4) -> bool:
    """Read-modify-write the goal metadata, tolerating unrelated churn.

    The row is written by background work too (quality scores, access
    counters), so a single compare-and-set against a long-held read turns that
    churn into a spurious conflict — finalize holds its read across the whole
    card-materialisation loop, which is exactly when a pipeline step lands
    (measured: a goal-finalize guard passed alone and returned ``conflict`` in
    a batch where the scheduler was active).

    Each attempt re-reads, applies ``mutate`` to the FRESH blob (so unrelated
    fields written by others are preserved rather than clobbered) and CASes
    against that read.  A lost CAS only means someone wrote in between and the
    caller's transition is idempotent, so it is simply re-applied.
    ``precondition`` is the lifecycle guard: it still refuses when another
    transition actually moved the goal.
    """
    for _ in range(max(1, attempts)):
        row = conn.execute("SELECT metadata FROM memories WHERE id = ?",
                           (goal_id,)).fetchone()
        if not row:
            return False
        raw = row["metadata"] or "{}"
        fresh = _meta_of(raw)
        if precondition is not None and not precondition(fresh):
            return False
        mutate(fresh)
        cur = conn.execute(
            "UPDATE memories SET metadata = ?, updated_at = ? "
            "WHERE id = ? AND metadata = ?",
            (json.dumps(fresh, ensure_ascii=False), _now(), goal_id, raw))
        conn.commit()
        if (cur.rowcount or 0) > 0:
            return True
    return False


def _conflict(goal_id: int) -> dict:
    """Structured answer for a lost compare-and-set."""
    return {"status": "conflict", "goal_id": goal_id,
            "reason": "goal metadata changed concurrently; re-read and retry",
            "next": f"重新读取 goal #{goal_id}（memall_read action=goal_status）后再执行该操作"}


def _phase_of(meta: dict) -> str:
    p = meta.get("phase")
    return p if p in _PHASES else "intake"


def _lease_is_stale(lease: dict) -> bool:
    """True when a finalize lease outlived its usefulness (holder died)."""
    try:
        started = datetime.fromisoformat(str(lease.get("at")))
    except (TypeError, ValueError):
        return True
    if started.tzinfo is None:
        started = started.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - started).total_seconds() > _FINALIZE_LEASE_SECONDS


def _card_exists(conn, task_id: int) -> bool:
    """Whether a previously materialised child card is still live."""
    row = conn.execute(
        "SELECT id FROM memories WHERE id = ? AND level = 'L5' "
        "AND category IN ('task', 'todo') "
        f"AND {_NOT_ARCHIVED}", (task_id,)).fetchone()
    return row is not None


# ── lifecycle operations ────────────────────────────────────────────────

def create_goal(objective: str, owner: str, acceptance: str = "",
                verifier: str = "", created_by: str = "",
                participants: Optional[list] = None) -> dict:
    """File a human objective. Phase starts at ``intake``, waiting for the owner."""
    from memall.core.thin_waist import capture

    owner = (owner or "").strip()
    if not owner:
        return {"status": "error", "error": "owner (the orchestrating agent) is required"}
    if not (objective or "").strip():
        return {"status": "error", "error": "objective is required"}

    # Dedup by normalised objective.  The only prior backstop was capture's
    # content_hash, but the body embeds owner/verifier, so a field-level
    # difference defeats it and a retry (or a racing tick) mints a second
    # goal that drives a whole extra decompose-deliberate-finalize cycle.
    norm = " ".join(objective.strip().lower().split())
    from memall.core.db import get_conn as _get_conn
    _conn = _get_conn()
    try:
        _rows = _conn.execute(
            "SELECT id, metadata FROM memories "
            "WHERE level = ? AND category = ? "
            "AND COALESCE(memory_status, '') != 'archived' "
            "ORDER BY id",
            (GOAL_LEVEL, GOAL_CATEGORY)).fetchall()
    finally:
        _conn.close()
    prior_closed = None
    same_objective = []
    for _r in _rows:
        _rm = _meta_of(_r["metadata"])
        if " ".join((_rm.get("objective") or "").strip().lower().split()) != norm:
            continue
        same_objective.append(_r["id"])
        if _status_of(_rm.get("status")) not in _GOAL_TERMINAL:
            return {"status": "duplicate", "goal_id": _r["id"],
                    "phase": _rm.get("phase"),
                    "message": f"an active goal with the same objective already "
                               f"exists (#{_r['id']})"}
        prior_closed = _r["id"]  # keep the newest closed cycle

    body = (f"[目标] {objective.strip()}\n\n"
            f"验收标准：{acceptance.strip() or '（未填写，验收前必须补齐）'}\n"
            f"编排人：{owner}\n"
            f"验收人：{verifier.strip() or '（待定，须为非生产方）'}")
    cycle = 1
    if prior_closed is not None:
        # Rebuilding an objective whose previous cycle closed.  The body MUST
        # differ: ``memories.content_hash`` is UNIQUE, so an identical body
        # cannot be inserted, and the insert fallback hands back the CLOSED
        # row's id — the caller then believes a fresh goal exists while every
        # write lands on a dead card.  The line below makes the new cycle a
        # distinct, self-documenting record.
        cycle = len(same_objective) + 1
        body += (f"\n\n第 {cycle} 轮：上一轮 #{prior_closed} 已 closed，"
                 f"本轮为新周期。")
    gid = capture(body, level=GOAL_LEVEL, category=GOAL_CATEGORY,
                  agent_name=owner, owner=created_by or owner,
                  subject=f"[目标] {objective.strip()[:80]}",
                  metadata=json.dumps({
                      "status": "active",
                      "phase": "intake",
                      "owner": owner,
                      "verifier": verifier.strip(),
                      "acceptance": acceptance.strip(),
                      "objective": objective.strip(),
                      "decomposed_by": "",
                      "discussion_id": 0,
                      "task_ids": [],
                      "phase_history": [{"phase": "intake", "at": _now()}],
                      **({"cycle": cycle, "previous_goal_id": prior_closed}
                         if prior_closed is not None else {}),
                      **({"participants": participants} if participants else {}),
                  }, ensure_ascii=False))
    return {"status": "created", "goal_id": gid, "phase": "intake",
            "owner": owner,
            **({"cycle": cycle, "previous_goal_id": prior_closed}
               if prior_closed is not None else {}),
            "next": f"编排人 {owner} 需要拆解这个目标"}


# Sequencing language that usually implies "this child consumes an earlier
# child's output".  Deliberately NARROW: a false positive here only produces a
# warning the caller can dismiss with parallel_ok, but a noisy detector teaches
# agents to ignore it.
_SEQ_MARKERS = (
    "然后", "之后", "接着", "最后", "基于", "在此基础上", "在其基础上",
    "等上一步", "等前面", "再执行", "再实现", "再验证",
    "then ", "after that", "based on", "on top of", "once ", "follow-up",
    "follow up",
)


def _seq_evidence(text: str) -> str:
    """Return the sequencing marker found in *text*, or ""."""
    low = (text or "").lower()
    for m in _SEQ_MARKERS:
        if m in low:
            return m
    return ""


def decompose(goal_id: int, agent_name: str, children: list[dict],
              parallel_ok: bool = False) -> dict:
    """Record the decomposition: child tasks + dependencies + proposed assignees.

    ``children`` = [{"title", "detail", "assignee", "acceptance", "depends_on_idx"}]
    where ``depends_on_idx`` refers to positions in this same list (0-based).
    Nothing is materialised yet — 论证 comes first, so the plan is a proposal.

    ``parallel_ok=True`` declares that these children genuinely can run in
    parallel, which silences the dependency warnings below.

    Dependency sanity is split by what the evidence actually supports:

    * HARD errors (rejected): self-dependency, a dependency on a later sibling
      (a decomposition is topologically ordered), an out-of-range index, a
      missing title or acceptance criterion.
    * WARNINGS (accepted, reported): a child whose wording implies it consumes
      an earlier child's output while declaring no dependency, and duplicate
      titles.  Warnings never block: a decomposition may legitimately be
      parallel, and guessing intent from words is exactly the kind of
      character-rule gate that misfires on honest work.  Each warning names the
      child and the two ways to resolve it.
    """
    from memall.core.db import get_conn

    if not children:
        return {"status": "error", "error": "children list is empty"}
    conn = get_conn()
    try:
        if not (agent_name or "").strip():
            return {"status": "forbidden",
                    "reason": "agent_name is required: an empty identity is rejected",
                    "caller": agent_name}
        g = _get_goal(conn, goal_id)
        if not g:
            return {"status": "error", "error": f"goal #{goal_id} not found"}
        meta = g["meta"]
        if _phase_of(meta) not in ("intake", "decomposed"):
            return {"status": "error",
                    "error": f"goal is in phase '{_phase_of(meta)}', cannot decompose"}
        owner = (meta.get("owner") or "").strip()
        if owner and agent_name.lower() != owner.lower():
            return {"status": "forbidden",
                    "reason": "only the goal's owner may decompose it",
                    "owner": owner, "caller": agent_name}

        plan, problems = [], []
        for i, c in enumerate(children):
            title = (c.get("title") or "").strip()
            if not title:
                problems.append(f"child {i} has no title")
            acc = (c.get("acceptance") or "").strip()
            if not acc:
                problems.append(f"child {i} ('{title}') has no acceptance criteria")
            deps = []
            for d in (c.get("depends_on_idx") or []):
                try:
                    di = int(d)
                except (TypeError, ValueError):
                    problems.append(f"child {i} has a non-integer dependency {d!r}")
                    continue
                if di == i:
                    problems.append(f"child {i} depends on itself")
                elif di > i:
                    problems.append(
                        f"child {i} depends on a later sibling (index {di}); "
                        f"a decomposition is topologically ordered, so a child may "
                        f"only depend on earlier siblings")
                elif not (0 <= di < len(children)):
                    problems.append(f"child {i} depends on out-of-range index {di}")
                else:
                    deps.append(di)
            plan.append({
                "idx": i, "title": title,
                "detail": (c.get("detail") or "").strip(),
                "assignee": (c.get("assignee") or "").strip(),
                "acceptance": acc, "depends_on_idx": sorted(set(deps)),
            })
        if problems:
            return {"status": "rejected", "problems": problems}

        # --- Dependency sanity (warnings, never rejections) ----------------
        # A child that reads like a follow-up but declares no dependency is the
        # failure this was added for: measured live, an agent decomposed a goal
        # into "设计场景 / 执行验证" with deps=[] on both, so finalize would have
        # released them in parallel and the verification could have started
        # before the thing it verifies existed.
        warnings = []
        # Scheduling intent must be explicit, and it is reported rather than
        # blocked HERE on purpose: 论证 is the stage that reviews the plan, so
        # stopping decompose would keep the un-scheduled plan away from the very
        # discussion that should fix it.  Measured: a hard rejection at
        # decompose made a woken agent give up and left the goal stuck at
        # intake, because the discussion never received a plan to argue about.
        # finalize enforces it (see there).
        if len(plan) >= 2 and not parallel_ok and not any(p["depends_on_idx"] for p in plan):
            warnings.append(
                f"{len(plan)} children declare no dependency at all: if any child "
                f"consumes another's output, set depends_on_idx; if they truly run "
                f"in parallel, pass parallel_ok=true. finalize will refuse this "
                f"plan until one of the two is stated")

        if not parallel_ok:
            for entry in plan:
                j = entry["idx"]
                if j == 0 or entry["depends_on_idx"]:
                    continue
                evidence = _seq_evidence(entry["title"] + " " + entry["detail"])
                if evidence:
                    warnings.append(
                        f"child {j} ('{entry['title'][:40]}') uses sequencing "
                        f"language ('{evidence.strip()}') but declares no "
                        f"dependency: add depends_on_idx=[...], or call "
                        f"goal_decompose with parallel_ok=true if these really "
                        f"can run at the same time")

        seen_titles = {}
        for entry in plan:
            key = entry["title"].strip().lower()
            if not key:
                continue
            if key in seen_titles:
                warnings.append(
                    f"child {entry['idx']} repeats child {seen_titles[key]}'s "
                    f"title ('{entry['title'][:40]}'); if they are the same piece "
                    f"of work, merge them")
            else:
                seen_titles[key] = entry["idx"]

        meta["plan"] = plan
        meta["decomposed_by"] = agent_name
        meta["parallel_ok"] = bool(parallel_ok)
        if warnings:
            meta["dependency_warnings"] = warnings
        else:
            meta.pop("dependency_warnings", None)
        meta["phase"] = "decomposed"
        meta.setdefault("phase_history", []).append(
            {"phase": "decomposed", "at": _now(), "children": len(plan)})
        if not _save_meta(conn, goal_id, meta, g["raw"]):
            conn.rollback()
            return _conflict(goal_id)
        conn.commit()
        result = {"status": "decomposed", "goal_id": goal_id,
                  "children": len(plan), "phase": "decomposed",
                  "next": f"编排人 {owner} 需要发起论证（memall_discussion）"}
        if warnings:
            # Surfaced, not blocked: the plan is usable, but the caller owes an
            # answer (fix the deps, or restate with parallel_ok).
            result["warnings"] = warnings
            result["next"] = ("先处理 warnings：为相关 child 补 depends_on_idx，"
                              "或若确实可并行则用 parallel_ok=true 重述；"
                              + result["next"])
        return result
    finally:
        conn.close()


def attach_discussion(goal_id: int, agent_name: str, discussion_id: int) -> dict:
    """Link the deliberation to the goal and move to ``deliberating``."""
    from memall.core.db import get_conn

    conn = get_conn()
    try:
        if not (agent_name or "").strip():
            return {"status": "forbidden",
                    "reason": "agent_name is required: an empty identity is rejected",
                    "caller": agent_name}
        g = _get_goal(conn, goal_id)
        if not g:
            return {"status": "error", "error": f"goal #{goal_id} not found"}
        meta = g["meta"]
        if _phase_of(meta) != "decomposed":
            return {"status": "error",
                    "error": f"expected phase 'decomposed', got '{_phase_of(meta)}'"}
        owner = (meta.get("owner") or "").strip()
        if owner and agent_name.lower() != owner.lower():
            return {"status": "forbidden", "reason": "only the owner may open deliberation",
                    "owner": owner}
        meta["discussion_id"] = int(discussion_id)
        meta["phase"] = "deliberating"
        meta.setdefault("phase_history", []).append(
            {"phase": "deliberating", "at": _now(), "discussion_id": int(discussion_id)})
        if not _save_meta(conn, goal_id, meta, g["raw"]):
            conn.rollback()
            return _conflict(goal_id)
        conn.commit()
        return {"status": "deliberating", "goal_id": goal_id,
                "discussion_id": int(discussion_id),
                "next": "等待论证收敛（收敛后编排人落任务卡）"}
    finally:
        conn.close()


def finalize(goal_id: int, agent_name: str) -> dict:
    """Materialise task cards from the approved plan (requires converged discussion)."""
    from memall.core.db import get_conn
    from memall.core.thin_waist import capture

    conn = get_conn()
    try:
        if not (agent_name or "").strip():
            return {"status": "forbidden",
                    "reason": "agent_name is required: an empty identity is rejected",
                    "caller": agent_name}
        g = _get_goal(conn, goal_id)
        if not g:
            return {"status": "error", "error": f"goal #{goal_id} not found"}
        meta = g["meta"]
        if _phase_of(meta) != "deliberating":
            return {"status": "error",
                    "error": f"expected phase 'deliberating', got '{_phase_of(meta)}'"}
        owner = (meta.get("owner") or "").strip()
        if owner and agent_name.lower() != owner.lower():
            return {"status": "forbidden", "reason": "only the owner may finalise",
                    "owner": owner}

        did = int(meta.get("discussion_id") or 0)
        if did:
            drow = conn.execute("SELECT metadata FROM memories WHERE id = ?",
                                (did,)).fetchone()
            dmeta = _meta_of(drow["metadata"]) if drow else {}
            if _status_of(dmeta.get("status")) != "converged":
                return {"status": "blocked",
                        "reason": f"discussion #{did} has not converged yet",
                        "discussion_status": _status_of(dmeta.get("status"))}

        plan = meta.get("plan") or []
        if not plan:
            return {"status": "error", "error": "no plan recorded; decompose first"}

        # Scheduling intent, enforced where it matters: materialising an
        # un-scheduled multi-child plan would release every card in parallel at
        # once, so a verification card could be claimed before the work it
        # verifies exists.  decompose REPORTS this (and the discussion is where
        # it gets argued out); finalize is the last point before the dependency
        # graph becomes real, so it REFUSES.
        if (len(plan) >= 2 and not meta.get("parallel_ok")
                and not any((p.get("depends_on_idx") or []) for p in plan)):
            return {"status": "rejected",
                    "goal_id": goal_id,
                    "reason": "the plan declares no dependency among its children",
                    "how_to_fix": ("call goal_decompose again with depends_on_idx on "
                                   "the children that consume an earlier child's "
                                   "output, or with parallel_ok=true if they can "
                                   "all run at the same time"),
                    "children": len(plan)}

        # Validate the whole plan BEFORE taking the lease.  The decompose layer
        # already rejects a forward dependency (di > i), but a plan written
        # before that fix (or by hand) can still carry one; materialising it
        # would silently drop the edge and leave the card's dependency gate
        # vacuous.  Fail loudly, and do it while nothing has been created.
        known: set[int] = set()
        for item in plan:
            missing = [i for i in (item.get("depends_on_idx") or [])
                       if i not in known]
            if missing:
                return {"status": "error",
                        "error": (f"child {item['idx']} depends on unresolved index "
                                  f"{missing}; finalize refuses to silently drop a "
                                  f"forward dependency"),
                        "goal_id": goal_id, "unresolved": missing}
            known.add(int(item["idx"]))

        # ── finalize lease (P2-3: non-atomic materialisation) ──────────
        # capture() writes on its own connection, so it cannot join this
        # transaction.  Without a lease the loop had three failure modes: a
        # capture that raised aborted the whole call AFTER some cards were
        # already committed (goal stayed 'deliberating', half a plan on the
        # board); the old ``if isinstance(tid, int)`` then skipped a failure
        # and still advanced to 'assigned', so the missing card was invisible;
        # and a retry re-created every card it had already made.
        #
        # The lease is a compare-and-set on the blob we read, which serialises
        # concurrent finalize calls (tick + agent), and the ids already
        # materialised are recorded in meta['finalized_tasks'] keyed by
        # plan idx so a retry REUSES them instead of minting duplicates.
        inflight = meta.get("finalize_inflight") or {}
        if inflight and not _lease_is_stale(inflight):
            return {"status": "in_progress", "goal_id": goal_id,
                    "started_by": inflight.get("by", ""),
                    "started_at": inflight.get("at", ""),
                    "next": "上一次 finalize 仍在进行；崩溃后的租约会自动过期，可重试"}

        token = uuid.uuid4().hex

        def _take_lease(m):
            m["finalize_inflight"] = {"token": token, "by": agent_name,
                                      "at": _now()}

        def _no_live_lease(m):
            current = m.get("finalize_inflight") or {}
            return not current or _lease_is_stale(current)

        # _mutate_meta commits, which also releases the write lock before
        # capture() opens its own connection — otherwise every insert would
        # wait on this transaction.
        if not _mutate_meta(conn, goal_id, _take_lease,
                            precondition=_no_live_lease):
            return _conflict(goal_id)

        finalized = {}
        for k, v in (meta.get("finalized_tasks") or {}).items():
            try:
                finalized[int(k)] = int(v)
            except (TypeError, ValueError):
                continue

        idx_to_id: dict[int, int] = {}
        created, reused, failed = [], [], []
        stop_at = None
        for pos, item in enumerate(plan):
            idx = int(item["idx"])
            deps = [idx_to_id[i] for i in (item.get("depends_on_idx") or [])]
            acc = item.get("acceptance") or meta.get("acceptance") or ""
            assignee = item.get("assignee") or owner

            prev = finalized.get(idx)
            if prev is not None and _card_exists(conn, prev):
                idx_to_id[idx] = prev
                reused.append({"idx": idx, "task_id": prev,
                               "assignee": assignee, "depends_on": deps})
                continue

            body = (f"{item.get('detail') or item['title']}\n\n"
                    f"验收标准：{acc}\n"
                    f"所属目标：#{goal_id} {meta.get('objective', '')}")
            try:
                tid = capture(body, level="L5", category="task",
                              agent_name=assignee, owner=g["creator"] or owner,
                              subject=f"[任务] {item['title'][:80]}",
                              metadata=json.dumps({
                                  "status": "active",
                                  "assignee": assignee,
                                  "acceptance": acc,
                                  "depends_on": deps,
                                  "goal_id": goal_id,
                              }, ensure_ascii=False))
            except Exception as exc:  # quality gate / DB: one child, not the goal
                logger.warning("finalize %s: child %s failed: %s",
                               goal_id, idx, exc)
                failed.append({"idx": idx, "title": item.get("title", ""),
                               "error": str(exc)})
                stop_at = pos
                break
            if not isinstance(tid, int):
                failed.append({"idx": idx, "title": item.get("title", ""),
                               "error": f"capture returned {tid!r}, not a memory id"})
                stop_at = pos
                break
            finalized[idx] = tid
            idx_to_id[idx] = tid
            created.append({"idx": idx, "task_id": tid,
                            "assignee": assignee, "depends_on": deps})

        # Dependents are skipped, not attempted against a missing edge.
        not_attempted = ([int(p["idx"]) for p in plan[(stop_at or 0) + 1:]]
                         if failed else [])

        def _finish(m):
            m.pop("finalize_inflight", None)
            m["finalized_tasks"] = {str(k): finalized[k] for k in sorted(finalized)}
            m["task_ids"] = [finalized[k] for k in sorted(finalized)]
            if failed:
                m["phase"] = "deliberating"      # deliberately unchanged
                m["partial_finalize"] = {
                    "at": _now(), "by": agent_name,
                    "created": [c["task_id"] for c in created],
                    "reused": [r["task_id"] for r in reused],
                    "failed": failed, "not_attempted": not_attempted,
                }
                m.setdefault("phase_history", []).append(
                    {"phase": "deliberating", "at": _now(),
                     "event": "finalize_partial", "failed": len(failed)})
            else:
                m.pop("partial_finalize", None)
                m["phase"] = "assigned"
                m.setdefault("phase_history", []).append(
                    {"phase": "assigned", "at": _now(), "tasks": len(finalized),
                     "created": len(created), "reused": len(reused)})

        def _still_ours(m):
            return (m.get("finalize_inflight") or {}).get("token") == token

        if not _mutate_meta(conn, goal_id, _finish, precondition=_still_ours):
            return _conflict(goal_id)

        if failed:
            return {"status": "partial", "goal_id": goal_id,
                    "phase": "deliberating",
                    "created": created, "reused": reused,
                    "failed": failed, "not_attempted": not_attempted,
                    "next": ("目标仍停在 deliberating；修复失败的子项后重试 "
                             "goal_finalize，已建卡片会按 plan_idx 复用")}
        return {"status": "assigned", "goal_id": goal_id,
                "tasks": created, "reused": reused, "phase": "assigned",
                "next": "等待各 assignee 认领并交付"}
    finally:
        conn.close()


def request_verification(goal_id: int, agent_name: str = "") -> dict:
    """Move to ``verifying`` once every child has been delivered."""
    from memall.core.db import get_conn

    conn = get_conn()
    try:
        g = _get_goal(conn, goal_id)
        if not g:
            return {"status": "error", "error": f"goal #{goal_id} not found"}
        meta = g["meta"]
        if _phase_of(meta) == "verifying":
            # Idempotent (P3-2): the driver re-issues this prompt on every tick
            # until the verifier answers.  The old code accepted the repeat and
            # appended another phase_history entry each time, so a slow review
            # looked like it had been submitted N times and the audit trail
            # could not tell "submitted once" from "stuck in a loop".
            return {"status": "verifying", "goal_id": goal_id,
                    "verifier": (meta.get("verifier") or "").strip(),
                    "already": True,
                    "next": "已在验收中；这一步不会重复记录状态变化"}
        if _phase_of(meta) not in ("assigned", "executing"):
            return {"status": "error",
                    "error": f"cannot verify from phase '{_phase_of(meta)}'"}
        kids = _children(conn, goal_id)
        if not kids:
            return {"status": "error", "error": "goal has no task cards"}
        outstanding = [k for k in kids if k["status"] not in _CHILD_TERMINAL]
        if outstanding:
            return {"status": "blocked", "reason": "children still open",
                    "open": [{"id": k["id"], "status": k["status"]}
                             for k in outstanding]}
        verifier = (meta.get("verifier") or "").strip()
        if not verifier:
            # Option (a): the verifier is decided BEFORE the goal enters
            # review.  The old code advanced to ``verifying`` with an empty
            # verifier and the driver then produced an empty-agent delivery
            # that fallback.py silently dropped — the goal hung forever with
            # no escalation.  Refusing here keeps the "any non-producer may
            # verify" wording from inviting a grab-the-goal race.
            return {"status": "error",
                    "error": "verifier is required: request_verification rejects an empty verifier",
                    "goal_id": goal_id,
                    "next": "先指定验收人（须为非生产方），再请求验收"}
        meta["phase"] = "verifying"
        meta.setdefault("phase_history", []).append(
            {"phase": "verifying", "at": _now()})
        if not _save_meta(conn, goal_id, meta, g["raw"]):
            conn.rollback()
            return _conflict(goal_id)
        conn.commit()
        return {"status": "verifying", "goal_id": goal_id,
                "verifier": verifier,
                "next": f"验收人 {verifier} 需要验收"}
    finally:
        conn.close()


def verify_goal(goal_id: int, agent_name: str, passed: bool,
                verdict: str = "") -> dict:
    """Close the goal, or send it back. Producer may not verify (§五)."""
    from memall.core.db import get_conn

    conn = get_conn()
    try:
        g = _get_goal(conn, goal_id)
        if not g:
            return {"status": "error", "error": f"goal #{goal_id} not found"}
        meta = g["meta"]
        if _phase_of(meta) != "verifying":
            return {"status": "error",
                    "error": f"expected phase 'verifying', got '{_phase_of(meta)}'"}

        kids = _children(conn, goal_id)
        # Producer side is read through the single executor track
        # (assignee, else claimed_by) instead of ``claimed_by or assignee``:
        # for every card that was never claimed the old expression fell back
        # exactly the same way, so this only makes the priority explicit.
        producers = {k["executor"].lower() for k in kids if k["executor"]}
        producers.add((meta.get("owner") or "").lower())
        callers = (agent_name or "").strip().lower()
        if not callers:
            return {"status": "error", "error": "verifier identity is required"}
        if callers in producers:
            return {"status": "forbidden",
                    "reason": "生产者不得验收自己的交付（§五 独立验收）",
                    "producer_side": sorted(producers), "caller": agent_name}

        meta["verdict"] = {"passed": bool(passed), "by": agent_name,
                          "note": (verdict or "")[:_VERDICT_MAX], "at": _now()}
        meta["verified_by"] = agent_name
        if passed:
            meta["phase"] = "closed"
            meta["status"] = "done"
            meta.setdefault("phase_history", []).append(
                {"phase": "closed", "at": _now(), "by": agent_name})
            if not _save_meta(conn, goal_id, meta, g["raw"]):
                conn.rollback()
                return _conflict(goal_id)
            conn.execute("UPDATE memories SET arc_status = 'closed' WHERE id = ?",
                         (goal_id,))
            conn.commit()
            return {"status": "closed", "goal_id": goal_id,
                    "verified_by": agent_name,
                    "next": "目标已落地（可蒸馏进 L9/L10）"}
        # returned: children go back to active so work resumes
        restored = 0
        for k in kids:
            # Same terminal set as the pre-verification gate: a child that
            # counted as delivered there must be reopenable here, or a
            # rejected goal sees its "archived" work stay closed.
            if k["status"] in _CHILD_TERMINAL:
                row = conn.execute("SELECT metadata FROM memories WHERE id = ?",
                                   (k["id"],)).fetchone()
                km = _meta_of(row["metadata"])
                km["status"] = "active"
                km.pop("resolution", None)
                conn.execute(
                    "UPDATE memories SET metadata = ?, updated_at = ?, arc_status = NULL "
                    "WHERE id = ?",
                    (json.dumps(km, ensure_ascii=False), _now(), k["id"]))
                restored += 1
        meta["phase"] = "assigned"
        meta.setdefault("phase_history", []).append(
            {"phase": "assigned", "at": _now(), "returned_by": agent_name,
             "restored": restored})
        if not _save_meta(conn, goal_id, meta, g["raw"]):
            conn.rollback()
            return _conflict(goal_id)
        conn.commit()
        return {"status": "returned", "goal_id": goal_id,
                "restored_tasks": restored,
                "verdict": (verdict or "")[:_VERDICT_MAX],
                "next": "打回，相关任务已恢复为 active"}
    finally:
        conn.close()


# ── the driver ──────────────────────────────────────────────────────────

def next_action(conn, goal: dict) -> Optional[dict]:
    """The single next thing needed for this goal, and whose turn it is."""
    meta = goal["meta"]
    phase = _phase_of(meta)
    owner = (meta.get("owner") or "").strip()
    gid = goal["id"]

    if phase == "intake":
        return {"agent": owner, "goal_id": gid, "action": "decompose",
                "message": f"[目标 #{gid}] 待你拆解：{meta.get('objective', '')[:60]}",
                "instruction": (
                    f"调用 memall_write，参数：action=goal_decompose, goal_id={gid}, "
                    "children=[{title, detail, assignee, acceptance, depends_on_idx}]。"
                    "每个 child 必须有 acceptance（缺了会被拒绝）；"
                    "depends_on_idx 是本列表内的 0 基下标，只能引用更靠前的兄弟节点。"
                    "若某个 child 依赖前一个的产出，必须写进 depends_on_idx；"
                    "若确实全部并行，额外传 parallel_ok=true。")}
    if phase == "decomposed":
        return {"agent": owner, "goal_id": gid, "action": "deliberate",
                "message": f"[目标 #{gid}] 拆解已记录（{len(meta.get('plan') or [])} 项），请发起论证",
                "instruction": (
                    "先调用 memall_discussion action=create（participants 用当前在册 agent，"
                    "convergence_rule=majority）拿到 discussion_id；"
                    f"再调用 memall_write，参数：action=goal_deliberate, goal_id={gid}, "
                    "discussion_id=<上一步的 id>。")}
    if phase == "deliberating":
        did = int(meta.get("discussion_id") or 0)
        if not did:
            return {"agent": owner, "goal_id": gid, "action": "deliberate",
                    "message": f"[目标 #{gid}] 需要关联一个讨论"}
        drow = conn.execute("SELECT metadata FROM memories WHERE id = ?",
                            (did,)).fetchone()
        dmeta = _meta_of(drow["metadata"]) if drow else {}
        if _status_of(dmeta.get("status")) == "converged":
            return {"agent": owner, "goal_id": gid, "action": "finalize",
                    "message": f"[目标 #{gid}] 论证已收敛，请落任务卡",
                    "instruction": (
                        f"调用 memall_write，参数：action=goal_finalize, goal_id={gid}。"
                        "它会按已记录的 plan 生成任务卡（含 depends_on 与 acceptance）。")}
        return None  # discussion nudges are the discussion's own job
    if phase in ("assigned", "executing"):
        kids = _children(conn, gid)
        if not kids:
            # P3-7: an 'assigned' goal with no live children used to fall
            # through to ``return None``, so the driver said nothing and the
            # goal sat silent forever — no task board entry (there is no card)
            # and no goal prompt either.  Route the owner back to finalize;
            # it is idempotent now, so re-running only re-materialises what is
            # actually missing.
            return {"agent": owner, "goal_id": gid, "action": "finalize",
                    "message": f"[目标 #{gid}] 处于 assigned 但没有任何任务卡，需要重建",
                    "instruction": (
                        f"调用 memall_write，参数：action=goal_finalize, goal_id={gid} "
                        "重建任务卡（已存在的按 plan_idx 复用）。"
                        "若该目标已无意义，请让验收人打回或归档。")}
        if all(k["status"] in _CHILD_TERMINAL for k in kids):
            return {"agent": owner or "", "goal_id": gid,
                    "action": "request_verification",
                    "message": f"[目标 #{gid}] 子任务已全部交付，请提交验收",
                    "instruction": (
                        f"调用 memall_write，参数：action=goal_request_verification, "
                        f"goal_id={gid}。")}
        return None  # per-task nudges are the task board's job
    if phase == "verifying":
        verifier = (meta.get("verifier") or "").strip()
        return {"agent": verifier, "goal_id": gid, "action": "verify",
                "message": f"[目标 #{gid}] 等待验收",
                "instruction": (
                    f"调用 memall_read action=goal_status goal_id={gid} 复核子任务与交付，"
                    f"再调用 memall_write，参数：action=goal_verify, goal_id={gid}, "
                    "passed=true|false, verdict=<你的结论>。生产者不得验收。")}
    return None


def goal_tick(conn=None) -> dict:
    """Compute every goal's next action. Returns the prompts to deliver.

    Idempotent: running it twice with no state change produces the same list.
    The caller decides how to deliver (session note / notification bar).
    """
    from memall.core.db import get_conn as _get_conn

    own = conn is None
    conn = conn or _get_conn()
    try:
        rows = conn.execute(
            "SELECT id, subject, content, agent_name, metadata FROM memories "
            f"WHERE level = ? AND category = ? AND {_NOT_ARCHIVED} "
            "AND json_extract(metadata, '$.status') = 'active' "
            "ORDER BY id", (GOAL_LEVEL, GOAL_CATEGORY)).fetchall()
        actions = []
        for r in rows:
            goal = {"id": r["id"], "subject": r["subject"], "content": r["content"],
                    "creator": r["agent_name"], "meta": _meta_of(r["metadata"])}
            try:
                act = next_action(conn, goal)
            except Exception:
                logger.debug("next_action failed for goal %s", r["id"], exc_info=True)
                continue
            if act:
                act["phase"] = _phase_of(goal["meta"])
                actions.append(act)
        return {"goals": len(rows), "actions": actions}
    finally:
        if own:
            conn.close()
