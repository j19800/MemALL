"""
Skill layer — quality-quantified skill registry (P0-②).
============================================================

A verified goal's outcome becomes a reusable playbook.  That playbook earns
trust only through **real outcomes** — a ``verify_goal`` pass (+w1, usage+1)
or a ``returned`` fail (-w2, demote).  No character-count heuristics: the old
4-gram gate punished good content for being short, which is the exact failure
this layer exists to retire (定稿 §3.1, 分歧 #6).

The layer has three surfaces:

* **Registry** (``skills`` table, migration 042): ``provisional`` → ``trusted``
  is a threshold, not a binary.  Both ``quality_score`` and ``usage_count``
  must clear their thresholds (``skills.thresholds``, default 0.6 / 3).

* **Distill entry** (``skill_distill``): explicit, not automatic.  ``verify_goal
  (passed=True)`` produces a session note pointing at the goal; the verifier
  or owner calls ``skill_distill(goal_id)`` to materialise a provisional row.
  Automatic落库 would repeat the character-gate mistake.

* **Outcome signal** (``record_outcome``): called by ``verify_goal`` after
  close/return.  A pass on a goal that has associated skills adds +w1 and
  bumps usage; a return demotes them back to provisional and records why.

This module is intentionally small and SQLite-only; it reads and writes the
``skills`` table directly (like ``agent_waker`` reads ``wake_claims``).
"""

from __future__ import annotations

import json
import logging
import hashlib
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger("memall.pipeline.skills")

SKILL_STATUSES = ("provisional", "trusted")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _cfg(key: str, default):
    try:
        from memall.config import get_config
        return get_config(f"skills.{key}", default)
    except Exception:
        return default


def thresholds() -> tuple:
    """Return (quality_threshold, usage_threshold) from config or defaults."""
    q = _cfg("thresholds.quality", 0.6)
    u = _cfg("thresholds.usage", 3)
    try:
        q = float(q)
    except (TypeError, ValueError):
        q = 0.6
    try:
        u = int(u)
    except (TypeError, ValueError):
        u = 3
    return q, u


def weights() -> tuple:
    """Return (pass_weight, fail_weight) from config or defaults."""
    w1 = _cfg("weights.pass", 1.0)
    w2 = _cfg("weights.fail", 2.0)
    try:
        w1 = float(w1)
    except (TypeError, ValueError):
        w1 = 1.0
    try:
        w2 = float(w2)
    except (TypeError, ValueError):
        w2 = 2.0
    return w1, w2


def _get_conn():
    from memall.core.db import get_conn
    return get_conn()


def _row_to_skill(row) -> dict:
    if row is None:
        return {}
    return {
        "id": row["id"],
        "name": row["name"],
        "description": row["description"],
        "domain": row["domain"],
        "status": row["status"],
        "quality_score": row["quality_score"],
        "usage_count": row["usage_count"],
        "source_memory_id": row["source_memory_id"],
        "frontmatter_hash": row["frontmatter_hash"],
        "version": row["version"],
        "last_demote_reason": row["last_demote_reason"],
    }


def get_skill(name: str) -> dict:
    conn = _get_conn()
    try:
        row = conn.execute("SELECT * FROM skills WHERE name = ?",
                           (name.strip(),)).fetchone()
        return _row_to_skill(row)
    finally:
        conn.close()


def list_skills(agent: str = "", status: str = "") -> list[dict]:
    conn = _get_conn()
    try:
        sql = "SELECT * FROM skills"
        params: list = []
        clauses = []
        if agent:
            clauses.append("domain = ?")
            params.append(agent.strip())
        if status:
            clauses.append("status = ?")
            params.append(status.strip())
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY id"
        return [_row_to_skill(r) for r in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()


def skill_distill(goal_id: int, name: str, description: str, domain: str,
                  agent_name: str = "", source_memory_id: Optional[int] = None) -> dict:
    """Materialise a provisional skill from a verified goal's outcome.

    Explicit, not automatic: ``verify_goal`` produces a session note pointing
    at the goal; the verifier or owner calls this to固化一条 provisional row.
    Upsert by name (UNIQUE constraint) — re-distilling the same name updates
    description/domain and bumps version, rather than creating a duplicate.
    """
    name = (name or "").strip()
    if not name:
        return {"status": "error", "error": "skill name is required"}
    description = (description or "").strip()
    if not description:
        return {"status": "error", "error": "skill description is required"}
    domain = (domain or "general").strip()
    src = source_memory_id if source_memory_id is not None else goal_id
    now = _now()

    conn = _get_conn()
    try:
        conn.execute("BEGIN")
        try:
            existing = conn.execute("SELECT id, version FROM skills WHERE name = ?",
                                    (name,)).fetchone()
            if existing:
                sid = existing["id"]
                version = existing["version"] + 1
                conn.execute(
                    "UPDATE skills SET description=?, domain=?, version=?, "
                    "source_memory_id=?, updated_at=? WHERE id=?",
                    (description, domain, version, src, now, sid))
                status = "updated"
            else:
                cur = conn.execute(
                    "INSERT INTO skills (name, description, domain, status, "
                    "quality_score, usage_count, source_memory_id, frontmatter_hash, "
                    "version, created_at, updated_at, memory_status) "
                    "VALUES (?,?,?,?,0,0,?,NULL,1,?,?,NULL)",
                    (name, description, domain, "provisional", src, now, now))
                sid = cur.lastrowid
                version = 1
                status = "created"
            conn.execute("COMMIT")
            return {"status": status, "skill_id": sid, "name": name,
                    "version": version, "goal_id": goal_id,
                    "source_memory_id": src}
        except Exception:
            conn.execute("ROLLBACK")
            raise
    finally:
        conn.close()


def record_outcome(goal_id: int, passed: bool) -> dict:
    """Apply a quality signal to all skills sourced from *goal_id*.

    Called by ``verify_goal`` after close (passed=True) or return (passed=False).
    A pass adds +w1 to quality_score and +1 to usage_count, then promotes any
    skill that clears both thresholds.  A return demotes every associated
    skill back to provisional and records ``last_demote_reason``.

    If no skills are associated with the goal, this is a no-op (the goal
    simply had nothing distillable yet).
    """
    w1, w2 = weights()
    q_th, u_th = thresholds()
    conn = _get_conn()
    try:
        conn.execute("BEGIN")
        try:
            rows = conn.execute(
                "SELECT * FROM skills WHERE source_memory_id = ?",
                (goal_id,)).fetchall()
            affected = 0
            promoted = 0
            demoted = 0
            for row in rows:
                sid = row["id"]
                if passed:
                    new_score = row["quality_score"] + w1
                    new_usage = row["usage_count"] + 1
                    conn.execute(
                        "UPDATE skills SET quality_score=?, usage_count=?, "
                        "updated_at=? WHERE id=?",
                        (new_score, new_usage, _now(), sid))
                    affected += 1
                    if (new_score >= q_th and new_usage >= u_th
                            and row["status"] == "provisional"):
                        conn.execute(
                            "UPDATE skills SET status='trusted', updated_at=? "
                            "WHERE id=?", (_now(), sid))
                        promoted += 1
                else:
                    conn.execute(
                        "UPDATE skills SET status='provisional', "
                        "quality_score=0, last_demote_reason=?, updated_at=? "
                        "WHERE id=?",
                        ("goal #%d returned (verify fail)" % goal_id, _now(), sid))
                    demoted += 1
                    affected += 1
            conn.execute("COMMIT")
            return {"status": "signaled", "goal_id": goal_id,
                    "passed": bool(passed), "affected": affected,
                    "promoted": promoted, "demoted": demoted}
        except Exception:
            conn.execute("ROLLBACK")
            raise
    finally:
        conn.close()


def frontmatter_hash(name: str, description: str, version: int) -> str:
    """Deterministic hash of the frontmatter trio, used for idempotent export."""
    payload = f"{name}\n{description}\n{version}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def render_skill_md(skill: dict) -> str:
    """Render a skill row as a minimal SKILL.md (frontmatter + body).

    Frontmatter follows the Multica minimal model (name/description/version/
    source_memory_id/frontmatter_hash) — borrowing the paradigm, not the code.
    """
    name = skill.get("name", "")
    description = skill.get("description", "")
    version = skill.get("version", 1)
    src = skill.get("source_memory_id")
    fhash = skill.get("frontmatter_hash") or frontmatter_hash(name, description, version)
    lines = [
        "---",
        f"name: {name}",
        f"description: {description}",
        f"version: {version}",
        f"source_memory_id: {src if src is not None else ''}",
        f"frontmatter_hash: {fhash}",
        "---",
        "",
        description,
        "",
    ]
    return "\n".join(lines)
