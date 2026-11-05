"""
Pipeline step: discussion deliberation and resolution.

Lifecycle:
  1. create_discussion()     → capture an L5 memory with metadata
  2. confirm_discussion()    → capture P2 + edge, auto-converges immediately
  3. resolve_pending_deliberations() → pipeline sweep: converge any overlooked
  4. check_pending_discussions()     → agent queries + P2 reminder (dedup''d 1h)

Multi-agent negotiation model removed per #7769.  Single confirmation
converges the discussion.
"""

import hashlib
import json
import re
from memall.core.thin_waist import normalize_agent_name
import logging
import uuid
from datetime import datetime, timezone, timedelta

from memall.core.db import get_conn
from memall.core.status import pending_tasks
from memall.pipeline.util import _smart_subject
from memall.core.utils import now_iso, unwrap

logger = logging.getLogger(__name__)


def _insert_or_reuse_id(conn, cur, content_hash: str) -> int:
    """Row id of the just-inserted row, or the id of the row that already
    holds *content_hash*.

    All the inserts in this module are content-hash keyed, and
    ``memories.content_hash`` is UNIQUE, so a repeat (the same discussion
    created twice, the same agent restating the same position, a discussion
    converged twice) collides.  ``INSERT OR IGNORE`` turns that into
    ``rowcount == 0`` — and the pre-existing row is exactly what we want to
    reference, not an error to raise.
    """
    if cur.rowcount:
        return cur.lastrowid
    row = conn.execute(
        "SELECT id FROM memories WHERE content_hash = ?", (content_hash,)
    ).fetchone()
    return row["id"] if row else 0


def _short_id() -> str:
    return "dt_" + uuid.uuid4().hex[:8]


def unwrap_meta(meta: dict, key: str):
    """Unwrap a single metadata key; return default (empty list) if absent."""
    raw = meta.get(key)
    if raw is None:
        return (
            []
            if key in ("participants", "options", "open_questions", "action_items")
            else ""
        )
    return unwrap(raw)


# ── Discussion CRUD (L5 + edges) ──


def create_discussion(
    title: str,
    background: str = "",
    options: list | None = None,
    open_questions: list | None = None,
    action_items: list | None = None,
    recommendation: str = "",
    creator: str = "system",
    participants: list | None = None,
    timeout_hours: int = 72,
    suggest: bool = False,
    convergence_rule: str = "unanimous",
) -> dict:
    """Create a discussion as an L5 memory (category=discussion).

    Required format (enforced by content builder):
      == 问题描述 ==   ← background field
      == 解决方案 ==   ← options list
      == 建议 ==       ← recommendation field

    Multi-agent: converge_discussion() checks participants before converging.
    If participants is empty or None, discussion converges on first response.
    """
    creator = normalize_agent_name(creator)
    participants_list = [normalize_agent_name(p) for p in (participants or [])]
    conn = get_conn()
    try:
        now = now_iso()
        uid = _short_id()
        subject = f"[讨论] {title}"[:200]

        # Build structured content: 问题描述 → 方案 → 建议
        parts = [f"[讨论] {title} [{uid}]"]
        if background:
            parts.append(f"\n\n== 问题描述（事实与数据）==\n{background}")
        if options:
            parts.append(
                f"\n\n== 解决方案 ==\n"
                + "\n".join(f"{i}. {o}" for i, o in enumerate(options, 1))
            )
        if recommendation:
            parts.append(f"\n\n== 建议 ==\n{recommendation}")
        content = "".join(parts)[:2000]
        if not content:
            content = f"[讨论] {title} [{uid}]"

        h = hashlib.sha256(content.encode("utf-8")).hexdigest()

        # 🧠 Capability-aware participant suggestion (rational routing).
        # Pure advisory — never auto-invites; only records a recommendation so the
        # creator / calling agent can decide. Skipped unless explicitly requested.
        suggested_participants: list = []
        if suggest and not participants_list:
            try:
                from memall.pipeline.agent_routing import suggest_participants

                suggested_participants = [
                    s["agent"]
                    for s in suggest_participants(
                        title=title,
                        background=background,
                        options=options,
                        k=3,
                        exclude=[creator],
                    )
                ]
            except Exception:
                logger.debug(
                    "create_discussion: participant suggestion failed", exc_info=True
                )

        meta = json.dumps(
            {
                "status": "active",
                # A discussion is an L5 CONTRACT object: respond_discussion()
                # looks it up as level='L5' AND category='discussion'.  The
                # keyword classifier could rewrite that level (a long review
                # body matches L6 markers), after which the lookup failed and
                # the 会审 could never converge — observed on #999964, which
                # turned into L6/conflict hours after creation while the
                # original #999948 stayed L5.  Pin both fields so the
                # capture-time pinning protects discussions exactly like task
                # cards.
                "pinned_fields": ["category", "level"],
                "participants": participants_list,
                # When the discussion may converge.  Read by _rule_satisfied();
                # "unanimous" (every participant must respond) is the default
                # and the historical behaviour.
                "convergence_rule": (convergence_rule or "unanimous").lower(),
                # The deadline lives in metadata: it was a parameter that went
                # only to the Lark notification, so nothing could ever read it
                # (a dead field, like convergence_rule used to be).  It is now
                # the escape hatch for the action-item gate below - past this
                # deadline a silent assignee can no longer freeze a discussion.
                "timeout_hours": int(timeout_hours),
                "suggested_participants": suggested_participants,
                "options": options or [],
                "open_questions": open_questions or [],
                "action_items": action_items or [],
                "conclusion": "",
                "converged_at": "",
                "convergence_reason": "",
            }
        )

        cur = conn.execute(
            """INSERT OR IGNORE INTO memories
               (content, content_hash, level, owner, agent_name, subject,
                category, project, summary, occurred_at, created_at, updated_at,
                supersedes, confidence, visibility, metadata, arc_status)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                content,
                h,
                "L5",
                creator,
                creator,
                subject,
                "discussion",
                "",
                "",
                now,
                now,
                now,
                "[]",
                0.5,
                "private",
                meta,
                "open",
            ),
        )
        memory_id = _insert_or_reuse_id(conn, cur, h)
        conn.commit()

        try:
            from memall.lark_notify import notify_discussion_created

            notify_discussion_created(
                title=title,
                memory_id=memory_id,
                creator=creator,
                participants=participants_list,
                options=options or [],
                timeout_hours=timeout_hours,
            )
        except Exception:
            logger.warning("convergence.py: silent error", exc_info=True)

        return {
            "discussion_id": memory_id,
            "memory_id": memory_id,
            "subject": subject,
            "title": title,
            "background": background,
            "status": "active",
            "participants": participants_list,
            "suggested_participants": suggested_participants,
            "action_items": action_items or [],
            "open_questions": open_questions or [],
            "recommendation": recommendation,
        }
    finally:
        conn.close()


def get_discussion(discussion_id: int) -> dict:
    """Fetch a single L5 discussion + its response edges."""
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT * FROM memories WHERE id=? AND level='L5' AND category='discussion'",
            (discussion_id,),
        ).fetchone()
        if not row:
            return {"error": "not found"}

        disc = dict(row)
        meta = json.loads(disc.get("metadata") or "{}")

        responses = conn.execute(
            "SELECT m.id, m.content, m.metadata, m.agent_name, m.created_at "
            "FROM memories m JOIN edges e ON e.target_id = m.id "
            "WHERE e.source_id=? AND e.relation_type='cites' "
            "ORDER BY m.created_at LIMIT 1000",
            (discussion_id,),
        ).fetchall()

        return {
            "discussion": {
                "id": disc["id"],
                "subject": disc["subject"],
                "content": disc["content"],
                "status": meta.get("status", "active"),
                "options": unwrap_meta(meta, "options"),
                "open_questions": unwrap_meta(meta, "open_questions"),
                "action_items": unwrap_meta(meta, "action_items"),
                "participants": unwrap_meta(meta, "participants"),
                "created_at": disc["created_at"],
                "conclusion": unwrap_meta(meta, "conclusion"),
                "converged_at": meta.get("converged_at", ""),
                # 🧠 Surface the intelligent consensus analysis so callers
                # (agents / dashboards) can see whether the group agreed,
                # who dissented, and the synthesized conclusion.
                "consensus": meta.get("consensus"),
                "conflict": meta.get("conflict"),
                "dissenters": meta.get("dissenters", []),
                "stance_summary": meta.get("stance_summary", {}),
                "synthesis": meta.get("synthesis", ""),
                "suggested_participants": meta.get("suggested_participants", []),
            },
            "responses": [dict(r) for r in responses],
        }
    finally:
        conn.close()


def list_active_discussions() -> list[dict]:
    """List all L5 discussions with status=active."""
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT id, subject, content, metadata, created_at FROM memories "
            "WHERE level='L5' AND category='discussion' "
            f"AND {pending_tasks()} "
            "ORDER BY created_at DESC LIMIT 1000",
        ).fetchall()

        results = []
        for r in rows:
            meta = json.loads(r["metadata"] or "{}")
            count = conn.execute(
                "SELECT COUNT(*) as c FROM edges "
                "WHERE source_id=? AND relation_type='cites'",
                (r["id"],),
            ).fetchone()["c"]
            resp_rows = conn.execute(
                "SELECT m.agent_name FROM memories m JOIN edges e ON e.target_id = m.id "
                "WHERE e.source_id=? AND e.relation_type='cites' "
                "GROUP BY m.agent_name",
                (r["id"],),
            ).fetchall()
            responded_agents = [rr["agent_name"] for rr in resp_rows]
            results.append(
                {
                    "memory_id": r["id"],
                    "subject": r["subject"],
                    "participants": unwrap_meta(meta, "participants"),
                    "responded_agents": responded_agents,
                    "action_items": unwrap_meta(meta, "action_items"),
                    "response_count": count,
                    "created_at": r["created_at"],
                }
            )
        return results
    finally:
        conn.close()


def list_all_discussions() -> list[dict]:
    """List ALL L5 discussions (active, converged, stale) for HTML dashboard."""
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT id, subject, content, metadata, summary, created_at FROM memories "
            "WHERE level='L5' AND category='discussion' "
            "ORDER BY created_at DESC LIMIT 1000",
        ).fetchall()

        results = []
        for r in rows:
            meta = json.loads(r["metadata"] or "{}")
            count = conn.execute(
                "SELECT COUNT(*) as c FROM edges "
                "WHERE source_id=? AND relation_type='cites'",
                (r["id"],),
            ).fetchone()["c"]
            resp_rows = conn.execute(
                "SELECT m.agent_name FROM memories m JOIN edges e ON e.target_id = m.id "
                "WHERE e.source_id=? AND e.relation_type='cites' "
                "GROUP BY m.agent_name",
                (r["id"],),
            ).fetchall()
            responded_agents = [rr["agent_name"] for rr in resp_rows]
            results.append(
                {
                    "memory_id": r["id"],
                    "subject": r["subject"],
                    "summary": r["summary"] or r["content"][:200]
                    if r["content"]
                    else "",
                    "status": meta.get("status", "active"),
                    "participants": unwrap_meta(meta, "participants"),
                    "responded_agents": responded_agents,
                    "response_count": count,
                    "created_at": r["created_at"],
                }
            )
        return results
    finally:
        conn.close()


# ── Amend (roster / rule correction) ──

def amend_discussion(discussion_id: int, participants=None,
                     convergence_rule: str = "", actor: str = "",
                     reason: str = "") -> dict:
    """Correct a discussion's roster or rule, then re-evaluate convergence.

    AGENTS.md §一.5 already states the rule this enforces: ``participants`` must
    name the agents who are actually on the roster, because a departed member
    makes the discussion un-convergeable by arithmetic — it will never answer.
    Measured: #1001746 was created with ``claude``/``marvis`` (both out of the
    roster since 2026-10-23) alongside the four live agents, so with
    ``majority`` it needed 4 of 6 while only 3 could ever speak.

    Until now the surface had create/respond/status only, so a wrong roster
    could not be corrected at all — the discussion was simply stuck.  This is
    that missing action.

    It records what changed (before/after rosters, rule, actor, reason) in
    ``metadata.roster_history`` so a later reader can see the correction rather
    than finding a discussion that simply "always had" four participants, and it
    re-evaluates immediately: correcting a roster often satisfies the rule with
    responses that were already on file.
    """
    from memall.core.db import get_conn

    if not discussion_id:
        return {"error": "discussion_id is required"}
    conn = get_conn()
    try:
        row = conn.execute("SELECT * FROM memories WHERE id = ?",
                           (int(discussion_id),)).fetchone()
        if not row:
            return {"error": f"discussion #{discussion_id} not found"}
        disc = dict(row)
        meta = json.loads(disc.get("metadata") or "{}")
        status = meta.get("status")

        before_p = list(unwrap_meta(meta, "participants") or [])
        before_r = (meta.get("convergence_rule") or "unanimous").strip().lower()

        # Validate the REQUEST before consulting the state: an unknown rule or an
        # empty roster is a malformed call whatever the discussion's status, and
        # reporting it as "not active" would hide the real mistake.
        new_p = before_p
        if participants is not None:
            cleaned = []
            for p in participants:
                name = str(p).strip()
                if name and name.lower() not in [c.lower() for c in cleaned]:
                    cleaned.append(name)
            if not cleaned:
                return {"error": "participants must contain at least one agent"}
            new_p = cleaned

        new_r = (convergence_rule or before_r).strip().lower()
        if new_r not in ("unanimous", "majority", "any"):
            return {"error": f"unknown convergence_rule '{new_r}' "
                             f"(expected unanimous | majority | any)"}

        if status != "active":
            return {"status": status, "discussion_id": int(discussion_id),
                    "warning": f"discussion is {status}; only active ones can be amended"}

        if new_p == before_p and new_r == before_r:
            return {"status": "unchanged", "discussion_id": int(discussion_id),
                    "participants": new_p, "convergence_rule": new_r}

        meta["participants"] = new_p
        meta["convergence_rule"] = new_r
        meta.setdefault("roster_history", []).append({
            "at": datetime.now(timezone.utc).isoformat(),
            "by": (actor or "").strip(), "reason": (reason or "")[:400],
            "participants_before": before_p, "participants_after": new_p,
            "rule_before": before_r, "rule_after": new_r,
        })
        conn.execute("UPDATE memories SET metadata = ?, updated_at = ? WHERE id = ?",
                     (json.dumps(meta, ensure_ascii=False),
                      datetime.now(timezone.utc).isoformat(), int(discussion_id)))
        conn.commit()

        disc["metadata"] = json.dumps(meta, ensure_ascii=False)
        recheck = {}
        try:
            recheck = _converge_single(conn, disc)
        except Exception as exc:
            logger.debug("amend: recheck failed for %s", discussion_id, exc_info=True)
            recheck = {"warning": f"recheck failed: {exc}"}
        return {"status": "amended", "discussion_id": int(discussion_id),
                "participants": new_p, "convergence_rule": new_r,
                "participants_before": before_p, "rule_before": before_r,
                "recheck": recheck}
    finally:
        conn.close()


# ── Confirm & converge ──


def _converge_single(conn, disc: dict) -> dict:
    """Inner: converge one active discussion.

    Called by confirm_discussion() and resolve_pending_deliberations().
    Expects *conn* with an open transaction.
    Only converges if all participants (if any) have responded.
    """
    meta = json.loads(disc.get("metadata") or "{}")
    if meta.get("status") != "active":
        return {"warning": f"already {meta.get('status')}"}

    responses = conn.execute(
        "SELECT m.* FROM memories m JOIN edges e ON e.target_id = m.id "
        "WHERE e.source_id = ? AND e.relation_type = 'cites' ORDER BY m.created_at LIMIT 1000",
        (disc["id"],),
    ).fetchall()

    if not responses:
        return {"warning": "no responses, can not converge"}

    # sqlite3.Row has no .get() — materialise to dicts before any dict-style
    # access.  The rows were read here but only converted further down
    # (line "converge_discussion(conn, disc, [dict(r) ...])"), so the
    # participant check above crashed with
    # "AttributeError: 'sqlite3.Row' object has no attribute 'get'".
    response_dicts = [dict(r) for r in responses]

    # Check if all participants have responded
    participants = unwrap_meta(meta, "participants") or []
    if participants and len(participants) > 1:
        responded_agents = set()
        for resp in response_dicts:
            rmeta = json.loads(resp.get("metadata") or "{}")
            # An observer stance (non-participant) is recorded but does not
            # count toward convergence.
            if rmeta.get("observer"):
                continue
            a = rmeta.get("agent_name", resp.get("agent_name", ""))
            if a:
                responded_agents.add(a)
        # B1: the rule sees the stances, not just the head count.  This must
        # be consulted even when nobody is missing — an all-reject roster that
        # fully responded used to fall straight through to converge_discussion
        # and emit an L4 decision plus L5 task cards for a proposal nobody
        # supports.
        agreeing, rejecting, _abst = _stance_tally(response_dicts)
        missing = [p for p in participants if p not in responded_agents]
        if not _rule_satisfied(meta, responded_agents, participants,
                               created_at=disc.get("created_at"),
                               agreeing=agreeing, rejecting=rejecting):
            if missing:
                return {
                    "warning": f"waiting for participants: {missing}",
                    "participants": participants,
                }
            return {
                "warning": "vetoed: recorded rejections block the rule",
                "rejecting": sorted(rejecting),
            }

    return converge_discussion(conn, disc, response_dicts, "confirmed")


def _pending_action_item_owners(meta: dict, responded: set,
                                agreeing: set) -> list:
    """Action-item assignees who are participants and have not spoken yet.

    A discussion must not close while someone it assigned work to has not
    given a stance.  Measured on #1000375: it converged 9 minutes after
    creation on 3/5 votes, while its own action_items named ``dsh`` as the
    prerequisite reviewer — the prerequisite was still being produced, so the
    reviewer could only deliver outside the thread and the converged
    conclusion never contained the defects that review found.

    Only assignees who are BOTH participants and not yet responders block
    convergence; an assignee who is not on the roster is somebody else's
    commitment and must not freeze the discussion.
    """
    items = meta.get("action_items") or []
    if not isinstance(items, list):
        return []
    roster = {p.strip().lower() for p in (meta.get("participants") or [])
              if isinstance(p, str)}
    answered = {a.strip().lower() for a in (set(responded) | set(agreeing))
                if isinstance(a, str)}
    pending = []
    for item in items:
        if not isinstance(item, dict):
            continue
        who = (item.get("assigned_to") or "").strip()
        if not who:
            continue
        low = who.lower()
        if low in roster and low not in answered:
            pending.append(who)
    return pending


def _discussion_deadline_passed(meta: dict, created_at) -> bool:
    """True once the discussion is older than its ``timeout_hours``.

    ``timeout_hours`` was stored at creation and read by nothing — a dead
    field — so there was no escape hatch for a discussion stalled on an
    unresponsive assignee.  It is the deadline now: past it, the
    action-item gate stops blocking (otherwise a single silent assignee could
    freeze a discussion forever, which is the same failure the ``majority``
    rule was introduced to fix).
    """
    try:
        from datetime import datetime, timedelta, timezone

        hours = int(meta.get("timeout_hours") or 72)
        if hours <= 0 or not created_at:
            return False
        started = created_at
        if isinstance(started, str):
            started = datetime.fromisoformat(started.replace("Z", "+00:00"))
        if started.tzinfo is None:
            started = started.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) >= started + timedelta(hours=hours)
    except (TypeError, ValueError, OverflowError):
        return False


def _stance_tally(response_dicts: list[dict]) -> tuple[set, set, set]:
    """Split counted responses into (agreeing, rejecting, abstaining).

    B1: convergence used to count only *who* answered, never *what* they
    answered, so a discussion where every participant rejected still produced
    an L4 decision and L5 task cards.  The rule now sees the stances too.

    Observer stances (non-participants, A1-1) are excluded here, so an
    outsider's ``reject`` can never veto a roster's decision.
    """
    agreeing: set = set()
    rejecting: set = set()
    abstaining: set = set()
    for resp in response_dicts:
        rmeta = json.loads(resp.get("metadata") or "{}")
        if rmeta.get("observer"):
            continue
        agent = rmeta.get("agent_name") or resp.get("agent_name") or ""
        if not agent:
            continue
        stance = (rmeta.get("stance") or "confirm").lower()
        if stance in ("reject", "disagree"):
            rejecting.add(agent)
        elif stance in ("abstain", "pass"):
            abstaining.add(agent)
        else:
            agreeing.add(agent)
    return agreeing, rejecting, abstaining


def _rule_satisfied(meta: dict, responded: set, participants: list,
                    created_at=None, agreeing: set | None = None,
                    rejecting: set | None = None) -> bool:
    """Is the discussion's convergence rule met by the responses so far?

    The rule lives in ``metadata.convergence_rule`` and used to be a dead
    field: nothing read it, so a discussion only converged when EVERY
    participant had responded.  That made a discussion with a stale roster
    (an agent that had left the project, or one that had not yet been
    invited) un-convergeable in principle — the missing agent was never
    coming.

    Rules:

    ``unanimous`` (default)
        Every participant must have responded.  This is the historical
        behaviour, kept as the default so an existing discussion converges
        exactly as it did before.
    ``majority``
        More than half of the participants must have responded.  A single
        unresponsive agent can no longer hold a discussion open forever.
    ``any``
        One response is enough.  For a discussion nobody will ever join.

    The rule only decides WHEN the discussion converges.  What it converges
    TO is unchanged: :func:`converge_discussion` still records the real
    stance distribution, and a discussion that converged with a dissent
    still reports ``consensus=False`` and lists its dissenters.

    Stance veto (B1).  When the caller supplies ``rejecting``, a veto is
    honoured instead of being out-counted:

    * ``unanimous`` — any reject means the group is not unanimous, so the
      discussion cannot close on a vote count alone.
    * ``majority`` — opponents must be strictly outnumbered by supporters;
      2 reject vs 2 confirm is not "more than half agreed".
    * ``any`` — deliberately NOT vetoed.  Its purpose is "close on one
      response" for rosters that will never answer; vetoing it would defeat
      the rule.  The dissent is still recorded (consensus=False).

    ``rejecting=None`` means the caller has no stance information and the
    historical count-only behaviour applies, so callers that cannot tally
    stances keep their previous semantics instead of silently tightening.
    """
    # Gate first: a discussion cannot close while an assignee it named as a
    # prerequisite has not spoken, UNLESS its own timeout_hours has elapsed.
    if not _discussion_deadline_passed(meta, created_at):
        pending_owners = _pending_action_item_owners(
            meta, responded, agreeing or set())
        if pending_owners:
            return False

    rule = (meta.get("convergence_rule") or "unanimous").lower()
    if rule not in ("unanimous", "majority", "any"):
        rule = "unanimous"

    # ---- B1: a veto is not a number to be out-voted ----
    if rejecting is not None:
        if rule == "unanimous" and rejecting:
            return False
        if rule == "majority" and len(rejecting) >= len(agreeing or set()):
            return False

    if rule == "any":
        return bool(responded)
    if rule == "majority":
        # More than half of the participants must have spoken.  Rounded up,
        # so 3 participants need 2 and 5 participants need 3.
        needed = len(participants) // 2 + 1
        return len(responded) >= needed
    return len(responded) >= len(participants)


def confirm_discussion(
    discussion_id: int,
    agent_name: str,
    stance: str = "confirm",
    note: str = "",
) -> dict:
    """Confirm a stance on a discussion.

    Creates a P2 response + edge (cites), then immediately converges
    the discussion — no multi-agent waiting.

    Returns decision_id + task_ids from converge_discussion.

    Retried on ``database is locked``: this function reads the discussion,
    commits the response, then reads again and WRITES the converged state on
    the same connection.  In WAL, a read snapshot that upgrades to a writer
    raises "database is locked" immediately and ``busy_timeout`` does not
    cover it.  Under a concurrent writer (a background pipeline thread while
    an agent answers) that is exactly the upgrade, so the stance was lost and
    the discussion silently stayed "active" — measured 1 in 8 guard runs.
    The retry is safe: the response insert is idempotent by content hash and
    the convergence write is guarded by the status check at the top.
    """
    import random
    import sqlite3
    import time

    from memall.config import get_config
    from memall.core.thin_waist import _is_lock_error

    attempts = max(1, int(get_config("convergence.lock_retries", 5) or 5))
    for attempt in range(attempts):
        try:
            return _confirm_discussion_once(discussion_id, agent_name, stance, note)
        except sqlite3.OperationalError as exc:
            if not _is_lock_error(exc) or attempt == attempts - 1:
                raise
            wait = min(0.05 * (2 ** attempt), 1.0) + random.uniform(0, 0.03)
            logger.warning(
                "confirm_discussion: %s (attempt %d/%d), retrying in %.2fs",
                exc, attempt + 1, attempts, wait,
            )
            time.sleep(wait)
    raise RuntimeError("confirm_discussion: retry loop exited")  # pragma: no cover


def _confirm_discussion_once(
    discussion_id: int,
    agent_name: str,
    stance: str = "confirm",
    note: str = "",
) -> dict:
    """One attempt of :func:`confirm_discussion` (see it for the retry policy)."""
    agent_name = normalize_agent_name(agent_name)
    conn = get_conn()
    try:
        now = now_iso()

        # Fetch the discussion
        row = conn.execute(
            "SELECT * FROM memories WHERE id=? AND level='L5' AND category='discussion'",
            (discussion_id,),
        ).fetchone()
        if not row:
            return {"error": f"discussion #{discussion_id} not found"}
        disc = dict(row)

        disc_meta = json.loads(disc.get("metadata") or "{}")
        if disc_meta.get("status") == "expired":
            # A late response revives a timed-out discussion: the deadline parks
            # zombies, it does not kill the topic. Flip back to active so the
            # normal convergence path applies to this response.
            disc_meta["status"] = "active"
            disc_meta.pop("expired_at", None)
            # converge_discussion() re-reads the status from *disc*, so the
            # revival has to be mirrored there or it would still see 'expired'
            # and refuse to converge.
            disc["metadata"] = json.dumps(disc_meta, ensure_ascii=False)
            conn.execute(
                "UPDATE memories SET metadata = ?, updated_at = ? WHERE id = ?",
                (disc["metadata"], now, discussion_id),
            )
        if disc_meta.get("status") != "active":
            return {
                "warning": f"discussion #{discussion_id} is already {disc_meta.get('status')}"
            }

        # Roster membership. A non-participant may still record a stance, but
        # it is flagged and does NOT count toward convergence: refusing it
        # outright discards useful input from a stakeholder, while counting it
        # would let an agent outside the roster decide the outcome (measured on
        # #1001746: two decommissioned agents voted).
        roster = unwrap_meta(disc_meta, "participants") or []
        roster_set = {p.strip().lower() for p in roster if isinstance(p, str)}
        is_participant = (not roster) or (agent_name.lower() in roster_set)

        # ── P2 response ──
        subject = f"[表态] 讨论#{discussion_id} | {agent_name}: {stance}"
        content = f"[表态] 讨论#{discussion_id} | {agent_name}: {stance}\n\n{note}"[
            :2000
        ]
        h = hashlib.sha256(content.encode("utf-8")).hexdigest()
        rmeta = json.dumps(
            {
                "stance": stance,
                "discussion_id": discussion_id,
                "agent_name": agent_name,
                "note": note[:500],
                # A response is a P2 CONTRACT object: convergence counts the
                # replies to decide whether a discussion may converge.  The
                # keyword classifier used to rewrite that level (a long note
                # matches L3/L6 markers) and the counting query filtered
                # level='P2', so a demoted reply silently stopped counting —
                # 11 of 30 live responses had already drifted (one to L1, one
                # to L6), which is why discussions never converged.  Pin both.
                "pinned_fields": ["category", "level"],
                # Observer stances are recorded for the deliberation record but
                # excluded from every convergence tally (see _converge_single and
                # the auto-converge below).
                "observer": not is_participant,
            }
        )
        cur = conn.execute(
            """INSERT OR IGNORE INTO memories
               (content, content_hash, level, owner, agent_name, subject,
                category, project, summary, occurred_at, created_at, updated_at,
                supersedes, confidence, visibility, metadata, arc_status)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                content,
                h,
                "P2",
                agent_name,
                agent_name,
                subject,
                "discussion_response",
                "",
                "",
                now,
                now,
                now,
                "[]",
                0.6,
                "private",
                rmeta,
                "open",
            ),
        )
        resp_id = _insert_or_reuse_id(conn, cur, h)

        # Edge: response cites discussion
        conn.execute(
            "INSERT OR IGNORE INTO edges (source_id, target_id, relation_type, created_at, valid_from) VALUES (?,?,?,?,?)",
            (discussion_id, resp_id, "cites", now, now),
        )

        conn.commit()

        # ── Auto-converge: check if all participants have responded ──
        participants = unwrap_meta(disc_meta, "participants") or []

        # Fetch ALL responses so far
        # Do NOT filter by level here.  Identity of a reply is the cites edge
        # plus category='discussion_response'; level is a mutable classifier
        # verdict and filtering on it made demoted replies invisible, so the
        # discussion waited forever for participants who had already answered.
        responses = conn.execute(
            "SELECT * FROM memories WHERE id IN ("
            "  SELECT target_id FROM edges WHERE source_id = ? AND relation_type = 'cites'"
            ") AND category = 'discussion_response' ORDER BY created_at ASC",
            (discussion_id,),
        ).fetchall()
        response_list = [dict(r) for r in responses]

        # Check if all participants have responded
        if participants and len(participants) > 1:
            responded_agents = set()
            for resp in response_list:
                rmeta = json.loads(resp.get("metadata") or "{}")
                # An observer stance (non-participant) is recorded but does not
                # count toward convergence.
                if rmeta.get("observer"):
                    continue
                a = rmeta.get("agent_name", resp.get("agent_name", ""))
                if a:
                    responded_agents.add(a)
            # B1: same stance-aware test as the sweep path, so both agree on
            # when to close AND on honouring a recorded veto.
            agreeing, rejecting, _abst = _stance_tally(response_list)
            missing = [p for p in participants if p not in responded_agents]
            if not _rule_satisfied(disc_meta, responded_agents,
                                   participants,
                                   created_at=disc.get("created_at"),
                                   agreeing=agreeing, rejecting=rejecting):
                # Rule not satisfied — do NOT converge
                conn.execute(
                    "UPDATE memories SET metadata = ?, updated_at = ? WHERE id = ?",
                    (json.dumps(disc_meta), now_iso(), discussion_id),
                )
                conn.commit()
                out = {
                    "status": "pending",
                    "discussion_id": discussion_id,
                    "response_id": resp_id,
                    "responded_agents": list(responded_agents),
                    "missing_agents": missing,
                }
                if not missing and rejecting:
                    out["vetoed_by"] = sorted(rejecting)
                return out

        # All participants (if any) have responded, or no participants listed → converge
        result = converge_discussion(
            conn, disc, response_list, f"Confirmed by {agent_name}"
        )
        result["response_id"] = resp_id
        return result

    finally:
        conn.close()


def respond_discussion(
    discussion_id: int,
    agent_name: str,
    stance: str,
    arguments: str = "",
    round_num: int = 1,
) -> dict:
    """Deprecated: use confirm_discussion() instead.

    This wrapper maps *arguments* to *note* and ignores *round_num*.
    """
    return confirm_discussion(
        discussion_id=discussion_id,
        agent_name=agent_name,
        stance=stance,
        note=arguments,
    )


# ── Pipeline step ──


def _analyze_stances(responses: list[dict]) -> dict:
    """Derive a structured consensus analysis from discussion responses.

    This is the intelligence layer that was removed in #7769: instead of just
    listing stances textually, we compute the agreement distribution, detect
    conflict, identify dissenters, and *synthesize* a conclusion from the actual
    deliberation (rather than relying on a pre-supplied, often-empty field).

    Returns::

        {
            "counts": {"confirm": n, "reject": n, "abstain": n, "other": n},
            "agents": [agent, ...],
            "consensus": bool,        # True only when there are supporters and NO reject
            "conflict": bool,         # True when both confirm and reject present
            "dissenters": [agent, ...],
            "synthesis": str,         # derived decision text from the deliberation
        }
    """
    counts = {"confirm": 0, "reject": 0, "abstain": 0, "other": 0}
    notes = {"confirm": [], "reject": [], "abstain": []}
    agents: list[str] = []
    dissenters: list[str] = []

    for resp in responses:
        rmeta = json.loads(resp.get("metadata") or "{}")
        agent = rmeta.get("agent_name") or resp.get("agent_name") or ""
        stance = (rmeta.get("stance") or "confirm").lower()
        note = (rmeta.get("note") or rmeta.get("arguments") or "")[:200]
        if rmeta.get("observer"):
            # Non-participant stance: recorded for the deliberation record,
            # but excluded from the consensus tally (旁听不算票).
            if stance in notes and note:
                notes[stance].append(f"{agent}（旁听）: {note}")
            continue
        if stance in counts:
            counts[stance] += 1
        else:
            stance = "other"
            counts["other"] += 1
        if isinstance(agent, str) and agent:
            agents.append(agent)
        if stance == "reject":
            dissenters.append(agent)
        if stance in notes and note:
            notes[stance].append(f"{agent}: {note}")

    has_confirm = counts["confirm"] > 0
    has_reject = counts["reject"] > 0
    consensus = has_confirm and not has_reject
    conflict = has_confirm and has_reject

    # Synthesize a conclusion from the actual deliberation.
    if conflict:
        pros = "；".join(notes["confirm"]) or "（支持方未附理由）"
        cons = "；".join(notes["reject"]) or "（反对方未附理由）"
        synthesis = (
            f"存在分歧，需后续复核。支持方认为：{pros}。"
            f"反对方（{', '.join(dissenters)}）认为：{cons}。"
        )
    elif consensus:
        synthesis = "各方一致通过。" + (
            "依据：" + "；".join(notes["confirm"]) if notes["confirm"] else "无附理由。"
        )
    elif counts["reject"] > 0:
        synthesis = "无支持意见，提案被否决。" + (
            "反对理由：" + "；".join(notes["reject"]) if notes["reject"] else ""
        )
    else:
        synthesis = "无明确立场，按默认通过处理。"

    return {
        "counts": counts,
        "agents": agents,
        "consensus": consensus,
        "conflict": conflict,
        "dissenters": dissenters,
        "synthesis": synthesis,
    }


def converge_discussion(conn, disc: dict, responses: list[dict], reason: str) -> dict:
    """Mark discussion converged, create L4 decision + L5 tasks.

    All writes happen on *conn* in a single transaction.
    Caller is responsible for commit.

    ``responses`` is normalised to dicts here: callers reach this function
    from several paths (MCP respond, capture-triggered convergence, the
    background resolver) and some of them hand over ``sqlite3.Row`` objects,
    which have no ``.get()``.  Normalising at the boundary means no caller
    can trip over it.
    """
    responses = [dict(r) for r in (responses or [])]
    meta = json.loads(disc.get("metadata") or "{}")
    if meta.get("status") != "active":
        return {"warning": "already " + str(meta.get("status"))}

    now = now_iso()
    action_items = unwrap_meta(meta, "action_items")
    title = re.sub(r"^(\[\?\?\] |\[讨论\] )", "", disc.get("subject", ""))

    # Collect participant names (used as fallback assignees for string action_items)
    participants = unwrap_meta(meta, "participants") or []

    # Aggregate latest stances from responses (simplified: just the confirming agent)
    latest: dict[str, dict] = {}
    for resp in responses:
        rmeta = json.loads(resp.get("metadata") or "{}")
        agent = rmeta.get("agent_name", resp.get("agent_name", ""))
        if isinstance(agent, str) and agent:
            latest[agent] = rmeta

    # 🧠 Intelligent consensus analysis (re-introduced lightweight deliberation)
    analysis = _analyze_stances(responses)
    # 🧠 Derive the conclusion from deliberation when no explicit one was set.
    preset_conclusion = unwrap_meta(meta, "conclusion")
    conclusion = preset_conclusion if preset_conclusion else analysis["synthesis"]

    # ?? 1. Update discussion L5 ??
    meta["status"] = "converged"
    meta["converged_at"] = now
    meta["convergence_reason"] = reason
    meta["consensus"] = analysis["consensus"]
    meta["conflict"] = analysis["conflict"]
    meta["stance_summary"] = analysis["counts"]
    meta["dissenters"] = analysis["dissenters"]
    meta["synthesis"] = analysis["synthesis"]
    if not preset_conclusion:
        meta["conclusion"] = conclusion
    conn.execute(
        "UPDATE memories SET metadata = ?, updated_at = ? WHERE id = ?",
        (json.dumps(meta), now, disc["id"]),
    )

    # ?? 2. Create L4 decision ??
    stances_lines = []
    for agent, s in latest.items():
        stance = s.get("stance", "confirm")
        args = (s.get("arguments") or s.get("note") or "")[:200]
        stances_lines.append(f"  {agent}: {stance} - {args}")

    consensus_label = (
        "一致通过"
        if analysis["consensus"]
        else ("存在分歧" if analysis["conflict"] else "默认通过")
    )
    dissent_line = (
        f"\n\n## 分歧方\n" + "\n".join(f"  - {a}" for a in analysis["dissenters"])
        if analysis["dissenters"]
        else ""
    )
    l4_content = (
        f"# [L4 会话] {title}\n\n"
        f"## 结论\n{conclusion}\n\n"
        f"## 共识分析\n状态：{consensus_label}；"
        f"支持 {analysis['counts']['confirm']} / 反对 {analysis['counts']['reject']} / "
        f"弃权 {analysis['counts']['abstain']} / 其他 {analysis['counts']['other']}。"
        f"{dissent_line}\n\n"
        f"## 各方立场\n" + "\n".join(stances_lines) + "\n\n"
        f"## 收敛原因\n{reason}"
    )[:2000]
    l4_hash = hashlib.sha256(l4_content.encode("utf-8")).hexdigest()
    l4_meta = json.dumps(
        {
            "source_discussion": disc["id"],
            "final_stances": latest,
            "convergence_reason": reason,
            "converged_at": now,
            "consensus": analysis["consensus"],
            "conflict": analysis["conflict"],
            "stance_summary": analysis["counts"],
            "dissenters": analysis["dissenters"],
            "synthesis": analysis["synthesis"],
        }
    )
    # The L4 decision is keyed by the hash of its own content, so the same
    # discussion converged twice (or two discussions with identical content)
    # hits the UNIQUE index on memories.content_hash.  A raw INSERT aborts
    # the whole transaction, which rolls back the "status = converged" update
    # above — the discussion stays active and the next pipeline run collides
    # again, forever.  Make the insert idempotent instead: if the hash is
    # already there, reuse that row and re-point the edge at it.
    holder = conn.execute(
        "SELECT id FROM memories WHERE content_hash = ?", (l4_hash,)
    ).fetchone()
    if holder is not None:
        l4_id = holder["id"]
    else:
        cur = conn.execute(
            """INSERT OR IGNORE INTO memories
               (content, content_hash, level, owner, agent_name, subject,
                category, summary, occurred_at, created_at, updated_at,
                supersedes, confidence, visibility, metadata, arc_status)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                l4_content,
                l4_hash,
                "L4",
                "system",
                "system",
                f"[L4 会话] {title}",
                "decision",
                "",
                now,
                now,
                now,
                "[]",
                0.7,
                "private",
                l4_meta,
                "open",
            ),
        )
        if cur.rowcount == 0:
            # Another writer took the hash between the check and the insert.
            l4_id = conn.execute(
                "SELECT id FROM memories WHERE content_hash = ?", (l4_hash,)
            ).fetchone()["id"]
        else:
            l4_id = cur.lastrowid

    # edge: discussion --refines--> decision
    conn.execute(
        "INSERT OR IGNORE INTO edges (source_id, target_id, relation_type, created_at, valid_from) VALUES (?,?,?,?,?)",
        (disc["id"], l4_id, "refines", now, now),
    )

    # ?? 3. Archive response P2 memories ??
    resp_ids = [r["id"] for r in responses if r.get("id")]
    if resp_ids:
        ph = ",".join("?" * len(resp_ids))
        conn.execute(
            f"UPDATE memories SET arc_status='closed', updated_at=? WHERE id IN ({ph})",
            (now, *resp_ids),
        )

    # Create L5 tasks per action_items (may be strings or dicts)
    task_ids = []
    for i, item in enumerate(action_items):
        if isinstance(item, str):
            desc = item
            # A plain-string action item carries NO assignee information.
            # Index round-robin used to invent one (participants[i % len]),
            # which silently reassigned work whenever the roster was amended
            # and produced cards nobody owned deliberately. Leave it empty —
            # the fallback below attributes it to the discussion creator, who
            # can re-delegate knowingly.
            assigned_to = ""
        else:
            assigned_to = item.get("assigned_to", "")
            desc = item.get("description", "")
        if not assigned_to and participants:
            # an action item that names nobody is a decision-level commitment
            # of the creator, not an arbitrary slice of the roster
            assigned_to = disc.get("agent_name") or ""
        assigned_to = normalize_agent_name(assigned_to)
        task_subject = f"[任务] {title} — {_smart_subject(desc)}"[:200]
        task_content = f"[任务] {title} | {desc}"[:2000]
        task_hash = hashlib.sha256(task_content.encode("utf-8")).hexdigest()
        task_meta = json.dumps(
            {
                "status": "active",
                "assignee": assigned_to,
                "source_discussion": disc["id"],
                "source_decision": l4_id,
            }
        )
        # INSERT OR IGNORE, like every other content-hash keyed insert in this
        # module: _insert_or_reuse_id() below is written to turn a duplicate
        # into "reuse the existing row", but a plain INSERT raises
        # UNIQUE constraint failed: memories.content_hash before it is ever
        # reached.  Consequence: converging a discussion whose action item
        # text repeats (the same discussion converged twice, or two
        # discussions with the same title) crashed the whole convergence.
        cur = conn.execute(
            """INSERT OR IGNORE INTO memories
               (content, content_hash, level, owner, agent_name, subject,
                category, project, summary, occurred_at, created_at, updated_at,
                supersedes, confidence, visibility, metadata, arc_status)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                task_content,
                task_hash,
                "L5",
                assigned_to,
                assigned_to,
                task_subject,
                "task",
                disc.get("project", ""),
                "",
                now,
                now,
                now,
                "[]",
                0.6,
                "private",
                task_meta,
                "open",
            ),
        )
        tid = _insert_or_reuse_id(conn, cur, task_hash)
        task_ids.append(tid)

        # edge: decision --refines--> task
        conn.execute(
            "INSERT OR IGNORE INTO edges (source_id, target_id, relation_type, created_at, valid_from) VALUES (?,?,?,?,?)",
            (l4_id, tid, "refines", now, now),
        )

    conn.commit()

    try:
        from memall.lark_notify import notify_discussion_converged

        notify_discussion_converged(
            discussion_id=disc["id"],
            title=title[:80],
            reason=reason,
            participants=list(latest.keys()),
            stances={a: latest.get(a, {}).get("stance", "confirm") for a in latest},
            task_count=len(task_ids),
        )
    except Exception:
        logger.warning("convergence.py: silent error", exc_info=True)

    return {
        "status": "converged",
        "discussion_id": disc["id"],
        "decision_id": l4_id,
        "task_ids": task_ids,
        "tasks_created": len(task_ids),
    }


def resolve_pending_deliberations() -> dict:
    """Sweep active L5 discussions, converge any with at least one response.

    Formerly called convergence_step (kept as alias below).
    This catches any discussions that were created but missed the
    auto-converge in confirm_discussion().
    """
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM memories WHERE level='L5' AND category='discussion' "
            f"AND {pending_tasks()}",
        ).fetchall()

        result: dict = {
            "checked": len(rows),
            "converged": 0,
            "tasks_created": 0,
            "expired": 0,
        }

        for disc in rows:
            dd = dict(disc)
            meta = json.loads(dd.get("metadata") or "{}")
            if meta.get("status") != "active":
                continue

            # Check if any response edges exist
            has_responses = conn.execute(
                "SELECT 1 FROM edges WHERE source_id=? AND relation_type='cites' LIMIT 1",
                (disc["id"],),
            ).fetchone()
            if not has_responses:
                # A discussion nobody ever answered used to stay 'active'
                # forever — no timeout ever closed it, so the active-topic
                # list filled with zombie cards. Past its own deadline, park
                # it as 'expired' so it stops pretending to be in flight; a
                # late response revives it (see _confirm_discussion_once).
                if _discussion_deadline_passed(meta, dd.get("created_at")):
                    meta["status"] = "expired"
                    meta["expired_at"] = now_iso()
                    conn.execute(
                        "UPDATE memories SET metadata = ?, updated_at = ? WHERE id = ?",
                        (json.dumps(meta, ensure_ascii=False), now_iso(), disc["id"]),
                    )
                    # The converge path commits inside converge_discussion(); this
                    # branch must commit too, or the parked status is rolled back
                    # when the connection closes and the zombie stays 'active'.
                    conn.commit()
                    result["expired"] += 1
                continue

            cr = _converge_single(conn, dd)
            if cr.get("task_ids"):
                result["tasks_created"] += len(cr["task_ids"])
            result["converged"] += 1

        # status distribution
        stats = conn.execute(
            "SELECT json_extract(metadata, '$.status') as st, COUNT(*) as cnt "
            "FROM memories WHERE level='L5' AND category='discussion' GROUP BY st",
        ).fetchall()
        result["status_counts"] = {r["st"]: r["cnt"] for r in stats}

        return result
    finally:
        conn.close()


# ── Backward-compat aliases ──


def convergence_step() -> dict:
    """Deprecated: use resolve_pending_deliberations() instead."""
    return resolve_pending_deliberations()


# ── Pending check for session injection ──


def check_pending_discussions(agent_name: str) -> list[dict]:
    """Check active L5 discussions that have NOT yet received any response.

    Captures a P2 reminder memory (dedup''d: skips if one exists within 1h
    for the same discussion+agent pair).
    """
    agent_name = normalize_agent_name(agent_name)
    conn = get_conn()
    try:
        # Only fetch discussions where this agent is a participant
        rows = conn.execute(
            "SELECT DISTINCT m.* FROM memories m "
            "JOIN json_each(json_extract(m.metadata, '$.participants')) AS p "
            "WHERE m.level='L5' AND m.category='discussion' "
            f"AND {pending_tasks('m')} "
            "AND LOWER(p.value) = LOWER(?)",
            (agent_name,),
        ).fetchall()

        pending = []
        for disc in rows:
            dd = dict(disc)

            # Has THIS agent responded?  The old check asked "does the
            # discussion have ANY response" and then skipped the reminder for
            # EVERY participant — so the first agent to reply muted the rest.
            # That is why #999964 sat with one reply out of five participants.
            # The notification bar in server.py already asks the per-agent
            # question; this now matches it.
            my_response = conn.execute(
                "SELECT 1 FROM edges e JOIN memories r ON e.target_id = r.id "
                "WHERE e.source_id = ? AND e.relation_type = 'cites' "
                "AND r.category = 'discussion_response' "
                "AND LOWER(r.agent_name) = LOWER(?) LIMIT 1",
                (disc["id"], agent_name),
            ).fetchone()
            if my_response:
                continue

            # Dedup: recent P2 reminder for same discussion+agent
            now = datetime.now(timezone.utc)
            one_hour_ago = (now - timedelta(hours=1)).isoformat()
            existing = conn.execute(
                "SELECT id FROM memories WHERE agent_name = ? AND level = 'P2' "
                "AND category = 'discussion_pending' "
                "AND json_extract(metadata, '$.discussion_id') = ? AND created_at > ?",
                (agent_name, str(disc["id"]), one_hour_ago),
            ).fetchone()
            if existing:
                continue

            meta = json.loads(dd.get("metadata") or "{}")

            # Capture P2 reminder
            content = (f"[待回应讨论] {dd['subject']} — 提醒: {agent_name} 尚未确认")[
                :2000
            ]
            h = hashlib.sha256(content.encode("utf-8")).hexdigest()
            rem_meta = json.dumps(
                {
                    "discussion_id": disc["id"],
                    "title": dd["subject"],
                    "reminder_for": agent_name,
                }
            )
            cur = conn.execute(
                """INSERT OR IGNORE INTO memories
                   (content, content_hash, level, owner, agent_name, subject,
                    category, summary, occurred_at, created_at, updated_at,
                    supersedes, confidence, visibility, metadata, arc_status)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    content,
                    h,
                    "P2",
                    agent_name,
                    agent_name,
                    f"待回应: {dd['subject']}",
                    "discussion_pending",
                    "",
                    now.isoformat(),
                    now.isoformat(),
                    now.isoformat(),
                    # supersedes is NOT NULL DEFAULT '[]'.  Passing None here
                    # raised a constraint violation, and because the INSERT is
                    # OR IGNORE the failure was SILENT: no reminder row was
                    # written, yet the caller was told
                    # reminder_captured=True.  Same defect class as the one
                    # that broke notify_pending_tasks (which raised instead).
                    "[]",
                    0.5,
                    "private",
                    rem_meta,
                    "open",
                ),
            )
            conn.commit()
            captured = cur.rowcount > 0

            pending.append(
                {
                    "discussion_id": disc["id"],
                    "subject": dd["subject"],
                    # Report what actually happened instead of assuming.
                    "reminder_captured": captured,
                }
            )

        return pending
    finally:
        conn.close()
