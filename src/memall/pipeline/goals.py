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
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger("memall.pipeline.goals")

GOAL_CATEGORY = "goal"
GOAL_LEVEL = "L5"

_PHASES = ("intake", "decomposed", "deliberating", "assigned",
           "executing", "verifying", "closed")

# A child in one of these states no longer blocks the goal from verifying.
_CHILD_TERMINAL = ("done", "resolved", "archived", "converged", "verified")

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
            "creator": row["agent_name"], "meta": _meta_of(row["metadata"])}


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
            "status": _status_of(m.get("status")),
            "acceptance": m.get("acceptance") or "",
            "depends_on": m.get("depends_on") or [],
            "delivery": m.get("delivery") or "",
        })
    return out


def _save_meta(conn, goal_id: int, meta: dict) -> None:
    conn.execute("UPDATE memories SET metadata = ?, updated_at = ? WHERE id = ?",
                 (json.dumps(meta, ensure_ascii=False), _now(), goal_id))


def _phase_of(meta: dict) -> str:
    p = meta.get("phase")
    return p if p in _PHASES else "intake"


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

    body = (f"[目标] {objective.strip()}\n\n"
            f"验收标准：{acceptance.strip() or '（未填写，验收前必须补齐）'}\n"
            f"编排人：{owner}\n"
            f"验收人：{verifier.strip() or '（待定，须为非生产方）'}")
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
                      **({"participants": participants} if participants else {}),
                  }, ensure_ascii=False))
    return {"status": "created", "goal_id": gid, "phase": "intake",
            "owner": owner,
            "next": f"编排人 {owner} 需要拆解这个目标"}


def decompose(goal_id: int, agent_name: str, children: list[dict]) -> dict:
    """Record the decomposition: child tasks + dependencies + proposed assignees.

    ``children`` = [{"title", "detail", "assignee", "acceptance", "depends_on_idx"}]
    where ``depends_on_idx`` refers to positions in this same list (0-based).
    Nothing is materialised yet — 论证 comes first, so the plan is a proposal.
    """
    from memall.core.db import get_conn

    if not children:
        return {"status": "error", "error": "children list is empty"}
    conn = get_conn()
    try:
        g = _get_goal(conn, goal_id)
        if not g:
            return {"status": "error", "error": f"goal #{goal_id} not found"}
        meta = g["meta"]
        if _phase_of(meta) not in ("intake", "decomposed"):
            return {"status": "error",
                    "error": f"goal is in phase '{_phase_of(meta)}', cannot decompose"}
        owner = (meta.get("owner") or "").strip()
        if agent_name and owner and agent_name.lower() != owner.lower():
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

        meta["plan"] = plan
        meta["decomposed_by"] = agent_name
        meta["phase"] = "decomposed"
        meta.setdefault("phase_history", []).append(
            {"phase": "decomposed", "at": _now(), "children": len(plan)})
        _save_meta(conn, goal_id, meta)
        conn.commit()
        return {"status": "decomposed", "goal_id": goal_id, "children": len(plan),
                "phase": "decomposed",
                "next": f"编排人 {owner} 需要发起论证（memall_discussion）"}
    finally:
        conn.close()


def attach_discussion(goal_id: int, agent_name: str, discussion_id: int) -> dict:
    """Link the deliberation to the goal and move to ``deliberating``."""
    from memall.core.db import get_conn

    conn = get_conn()
    try:
        g = _get_goal(conn, goal_id)
        if not g:
            return {"status": "error", "error": f"goal #{goal_id} not found"}
        meta = g["meta"]
        if _phase_of(meta) != "decomposed":
            return {"status": "error",
                    "error": f"expected phase 'decomposed', got '{_phase_of(meta)}'"}
        owner = (meta.get("owner") or "").strip()
        if agent_name and owner and agent_name.lower() != owner.lower():
            return {"status": "forbidden", "reason": "only the owner may open deliberation",
                    "owner": owner}
        meta["discussion_id"] = int(discussion_id)
        meta["phase"] = "deliberating"
        meta.setdefault("phase_history", []).append(
            {"phase": "deliberating", "at": _now(), "discussion_id": int(discussion_id)})
        _save_meta(conn, goal_id, meta)
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
        g = _get_goal(conn, goal_id)
        if not g:
            return {"status": "error", "error": f"goal #{goal_id} not found"}
        meta = g["meta"]
        if _phase_of(meta) != "deliberating":
            return {"status": "error",
                    "error": f"expected phase 'deliberating', got '{_phase_of(meta)}'"}
        owner = (meta.get("owner") or "").strip()
        if agent_name and owner and agent_name.lower() != owner.lower():
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

        idx_to_id: dict[int, int] = {}
        created = []
        for item in plan:
            deps = [idx_to_id[i] for i in item.get("depends_on_idx") or []
                    if i in idx_to_id]
            acc = item.get("acceptance") or meta.get("acceptance") or ""
            assignee = item.get("assignee") or owner
            body = (f"{item.get('detail') or item['title']}\n\n"
                    f"验收标准：{acc}\n"
                    f"所属目标：#{goal_id} {meta.get('objective', '')}")
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
            if isinstance(tid, int):
                idx_to_id[item["idx"]] = tid
                created.append({"idx": item["idx"], "task_id": tid,
                                "assignee": assignee, "depends_on": deps})

        meta["task_ids"] = [c["task_id"] for c in created]
        meta["phase"] = "assigned"
        meta.setdefault("phase_history", []).append(
            {"phase": "assigned", "at": _now(), "tasks": len(created)})
        _save_meta(conn, goal_id, meta)
        conn.commit()
        return {"status": "assigned", "goal_id": goal_id,
                "tasks": created, "phase": "assigned",
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
        if _phase_of(meta) not in ("assigned", "executing", "verifying"):
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
        if verifier and agent_name and verifier.lower() == agent_name.lower():
            pass  # verifier pulling the goal into review is exactly right
        meta["phase"] = "verifying"
        meta.setdefault("phase_history", []).append(
            {"phase": "verifying", "at": _now()})
        _save_meta(conn, goal_id, meta)
        conn.commit()
        return {"status": "verifying", "goal_id": goal_id,
                "verifier": verifier or "（未指定，任何非生产方均可验收）",
                "next": f"验收人 {verifier or '(非生产方)'} 需要验收"}
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
        producers = {(k["claimed_by"] or k["assignee"] or "").lower() for k in kids}
        producers.discard("")
        producers.add((meta.get("owner") or "").lower())
        callers = (agent_name or "").strip().lower()
        if not callers:
            return {"status": "error", "error": "verifier identity is required"}
        if callers in producers:
            return {"status": "forbidden",
                    "reason": "生产者不得验收自己的交付（§五 独立验收）",
                    "producer_side": sorted(producers), "caller": agent_name}

        meta["verdict"] = {"passed": bool(passed), "by": agent_name,
                          "note": (verdict or "")[:2000], "at": _now()}
        meta["verified_by"] = agent_name
        if passed:
            meta["phase"] = "closed"
            meta["status"] = "done"
            meta.setdefault("phase_history", []).append(
                {"phase": "closed", "at": _now(), "by": agent_name})
            _save_meta(conn, goal_id, meta)
            conn.execute("UPDATE memories SET arc_status = 'closed' WHERE id = ?",
                         (goal_id,))
            conn.commit()
            return {"status": "closed", "goal_id": goal_id,
                    "verified_by": agent_name,
                    "next": "目标已落地（可蒸馏进 L9/L10）"}
        # returned: children go back to active so work resumes
        restored = 0
        for k in kids:
            if k["status"] in ("done", "resolved", "verified", "converged"):
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
        _save_meta(conn, goal_id, meta)
        conn.commit()
        return {"status": "returned", "goal_id": goal_id,
                "restored_tasks": restored, "verdict": verdict[:300],
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
                    "depends_on_idx 是本列表内的 0 基下标。")}
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
        if kids and all(k["status"] in _CHILD_TERMINAL for k in kids):
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
