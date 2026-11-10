"""ADR 0028 P4: long-term governance maintenance (opt-in, non-destructive).

Blueprint (ADR 0028 §2 P4) asks for periodic/async maintenance passes:
canonicalization, conflict review, expiry, low-utility archival, theme/profile
refresh, policy adaptation — plus an audit surface and append-only archive
anchors under ``ADR/archived_sessions/``.

Design rules (deliberate, and stricter than the blueprint's minimum):
  * **Default OFF**: ``maintenance.enabled`` gates everything. No pass runs
    unless an operator opts in.
  * **Non-destructive**: passes only ADD metadata markers / set
    ``memory_status='archived'``. Content is never rewritten; no row is
    physically deleted. Archived rows stay readable via
    ``retrieve(..., include_archived=True)`` (existing thin_waist capability).
  * **Auditable + reversible**: each action writes an append-only JSONL audit
    line including a ``before`` snapshot. ``rollback_from_audit`` restores
    the exact prior metadata/memory_status from that snapshot.
  * **Bounded**: ``max_actions_per_run`` caps churn per invocation.
  * **Best-effort**: any failure is logged and returned, never raised into
    the caller (scheduler/MCP).

This module reuses the existing ``memory_status='archived'`` + ``include_archived``
semantics instead of introducing a second archive store.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_MAINTENANCE_PIPELINE = "cognitive.loop.maintenance"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _cfg(key: str, default: Any = None):
    from memall.config import get_config
    return get_config(f"maintenance.{key}", default)


def _paths() -> tuple[Path, Path]:
    """(archive_dir, audit_log) resolved against the repo root (cwd)."""
    root = Path.cwd()
    arch = (root / str(_cfg("archive_dir", "ADR/archived_sessions"))).resolve()
    audit = (root / str(_cfg("audit_log", "ADR/archived_sessions/audit.jsonl"))).resolve()
    return arch, audit


def _append_audit(record: dict) -> None:
    """Append one audit line (append-only). Never raises."""
    try:
        _, audit = _paths()
        audit.parent.mkdir(parents=True, exist_ok=True)
        with audit.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
    except Exception as e:
        logger.debug("maintenance: audit append failed: %s", e)


def _snapshot_row(conn, mem_id: int) -> dict:
    """Read-only before-state snapshot for rollback/audit."""
    try:
        r = conn.execute(
            "SELECT id, metadata, memory_status, updated_at FROM memories WHERE id = ?",
            (mem_id,),
        ).fetchone()
        if not r:
            return {}
        return {
            "id": r["id"],
            "metadata": r["metadata"],
            "memory_status": r["memory_status"],
            "updated_at": r["updated_at"],
        }
    except Exception:
        return {}


def _policy_status_rules(action: str, meta: dict) -> dict:
    """ADR 0028 P4 acceptance: derive the rule-based updates for
    ``metadata.resolve.status`` and ``memory_status`` for one policy.

    Rules are deliberately conservative and non-destructive:
      * conflict_review -> resolve.status='REVIEW' (needs human review)
      * canonicalize / theme_profile / policy_adaptation -> resolve.status='REVIEW'
      * expire / low_utility_archive -> memory_status='archived' (never deleted)
    The prior values are preserved in the audit ``before`` snapshot, so
    ``rollback_from_audit`` restores them exactly.
    """
    rules = {"resolve_status": None, "memory_status": None}
    if action in ("conflict_review", "canonicalize", "theme_profile", "policy_adaptation"):
        rules["resolve_status"] = "REVIEW"
    if action in ("expire", "low_utility_archive"):
        rules["memory_status"] = "archived"
    return rules


def _mark(conn, mem_id: int, action: str, marker: dict,
          set_archived: bool = False, audit: bool = True) -> dict:
    """Apply ONE non-destructive action: add a metadata marker, apply the
    rule-based ``resolve.status`` / ``memory_status`` updates, with a
    before-snapshot audit line. The UPDATE only touches ``metadata`` /
    ``memory_status`` / ``updated_at`` — never content, never DELETE.
    """
    before = _snapshot_row(conn, mem_id) if _cfg("snapshot_before_write", True) else {}
    try:
        raw = before.get("metadata")
        meta = {}
        if isinstance(raw, dict):
            meta = dict(raw)
        elif isinstance(raw, str) and raw.strip().startswith("{"):
            try:
                meta = json.loads(raw) or {}
            except Exception:
                meta = {}
        markers = meta.get("cognitive_maintenance") or []
        markers.append({
            "action": action, "ts": _now(),
            "source_pipeline": str(_cfg("source_pipeline", _MAINTENANCE_PIPELINE)),
            **marker,
        })
        meta["cognitive_maintenance"] = markers[-10:]  # bounded

        # ADR 0028 P4: apply rule-based status updates (audited/rollbackable).
        rules = _policy_status_rules(action, meta)
        prev_resolve = meta.get("resolve")
        if not isinstance(prev_resolve, dict):
            prev_resolve = {}
        applied = {}
        if rules["resolve_status"]:
            prev_resolve = dict(prev_resolve)
            applied["resolve_status_before"] = prev_resolve.get("status")
            prev_resolve["status"] = rules["resolve_status"]
            resolved_meta = dict(meta)
            resolved_meta["resolve"] = prev_resolve
            meta = resolved_meta
            applied["resolve_status_after"] = rules["resolve_status"]
        new_memory_status = None
        if set_archived or rules["memory_status"] == "archived":
            new_memory_status = "archived"
            applied["memory_status_after"] = "archived"

        new_meta = json.dumps(meta, ensure_ascii=False)
        if new_memory_status:
            conn.execute(
                "UPDATE memories SET metadata=?, memory_status=?, updated_at=? WHERE id=?",
                (new_meta, new_memory_status, _now(), mem_id),
            )
        else:
            conn.execute(
                "UPDATE memories SET metadata=?, updated_at=? WHERE id=?",
                (new_meta, _now(), mem_id),
            )
        if audit:
            _append_audit({
                "ts": _now(), "action": action, "memory_id": mem_id,
                "source_pipeline": str(_cfg("source_pipeline", _MAINTENANCE_PIPELINE)),
                "marker": marker, "set_archived": set_archived,
                "rules_applied": applied, "before": before,
                "after": _snapshot_row(conn, mem_id),
            })
        return {"ok": True, "memory_id": mem_id, "action": action,
                "rules_applied": applied}
    except Exception as e:
        logger.debug("maintenance: mark failed for %s: %s", mem_id, e)
        return {"ok": False, "memory_id": mem_id, "action": action, "error": str(e)[:200]}


def _select_candidates(conn, action: str, limit: int) -> list[int]:
    """Read-only candidate selection, one policy per action."""
    try:
        if action == "conflict_review":
            # Parentheses matter: SQL AND binds tighter than OR. Without them
            # archived CONFLICT rows (which must be excluded) were re-selected.
            rows = conn.execute(
                "SELECT id FROM memories WHERE "
                "(COALESCE(memory_status,'') LIKE '%CONFLICT%' "
                "OR COALESCE(memory_status,'') LIKE '%REVIEW%') "
                "AND COALESCE(memory_status,'') != 'archived' ORDER BY updated_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        elif action == "expire":
            rows = conn.execute(
                "SELECT id FROM memories WHERE invalid_at IS NOT NULL AND invalid_at != '' "
                "AND invalid_at <= ? AND COALESCE(memory_status,'') != 'archived' "
                "ORDER BY updated_at DESC LIMIT ?",
                (_now(), limit),
            ).fetchall()
        elif action in ("canonicalize", "low_utility_archive", "theme_profile", "policy_adaptation"):
            # Conservative: only memories already explicitly tagged for review.
            rows = conn.execute(
                "SELECT id FROM memories WHERE COALESCE(memory_status,'') = 'REVIEW' "
                "AND COALESCE(memory_status,'') != 'archived' ORDER BY updated_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        else:
            rows = []
        return [int(r[0]) for r in rows]
    except Exception as e:
        logger.debug("maintenance: candidate select failed: %s", e)
        return []


def run_maintenance(actions: list[str] | None = None, dry_run: bool = False,
                    max_actions: int | None = None) -> dict:
    """Run one bounded maintenance pass. Config-gated, non-destructive.

    Args:
      actions: subset of {canonicalize, conflict_review, expire,
        low_utility_archive, theme_profile, policy_adaptation}. Defaults to
        the enabled subset from config.
      dry_run: if True, only report candidates (no writes, no audit lines).
      max_actions: override the per-run cap.
    Returns an audit-friendly summary dict.
    """
    if not bool(_cfg("enabled", False)):
        return {"ok": True, "ran": False, "reason": "maintenance.enabled=False (opt-in)",
                "actions": []}
    all_actions = ("canonicalize", "conflict_review", "expire",
                   "low_utility_archive", "theme_profile", "policy_adaptation")
    if actions:
        wanted = [a for a in actions if a in all_actions]
    else:
        wanted = [a for a in all_actions if bool(_cfg(a, False))]
    cap = int(max_actions or _cfg("max_actions_per_run", 50) or 50)
    cap = max(1, min(cap, 500))

    from memall.core.db import get_conn
    conn = get_conn()
    try:
        summary: dict[str, Any] = {
            "ok": True, "ran": True, "dry_run": bool(dry_run),
            "ts": _now(), "source_pipeline": str(_cfg("source_pipeline", _MAINTENANCE_PIPELINE)),
            "actions": [], "total_marked": 0, "snapshot_before_write": bool(_cfg("snapshot_before_write", True)),
        }
        budget = cap
        for action in wanted:
            if budget <= 0:
                break
            cands = _select_candidates(conn, action, budget)
            marked = 0
            for mid in cands:
                if budget <= 0:
                    break
                if dry_run:
                    marked += 1
                    budget -= 1
                    continue
                res = _mark(
                    conn, mid, action,
                    marker={"policy": action, "dry_run": False},
                    set_archived=(action in ("expire", "low_utility_archive")),
                )
                if res.get("ok"):
                    marked += 1
                    budget -= 1
            conn.commit()
            summary["actions"].append({
                "action": action, "candidates": len(cands), "marked": marked,
            })
            summary["total_marked"] += marked
        _append_audit({"ts": _now(), "action": "run_maintenance",
                       "dry_run": bool(dry_run), "summary": summary})
        return summary
    except Exception as e:
        logger.debug("maintenance: run failed: %s", e)
        return {"ok": False, "ran": True, "error": str(e)[:200]}
    finally:
        try:
            conn.close()
        except Exception:
            pass


def archive_session(session_id: str, payload: dict | None = None,
                    agent_name: str = "") -> dict:
    """Write an append-only archive anchor for a session into
    ``ADR/archived_sessions/``. Never deletes the source memories.
    """
    try:
        arch, _ = _paths()
        arch.mkdir(parents=True, exist_ok=True)
        safe = "".join(ch for ch in (session_id or "unknown") if ch.isalnum() or ch in "-_.")[:80] or "unknown"
        path = arch / f"session_{safe}.jsonl"
        rec = {
            "ts": _now(), "session_id": session_id, "agent_name": agent_name,
            "source_pipeline": str(_cfg("source_pipeline", _MAINTENANCE_PIPELINE)),
            "payload": payload or {},
            "note": "append-only anchor; source memories are NOT deleted (include_archived=True to read)",
        }
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
        _append_audit({"ts": _now(), "action": "archive_session",
                       "session_id": session_id, "path": str(path)})
        return {"ok": True, "path": str(path), "session_id": session_id}
    except Exception as e:
        logger.debug("maintenance: archive_session failed: %s", e)
        return {"ok": False, "error": str(e)[:200]}


def read_audit(limit: int = 50) -> dict:
    """Read the tail of the append-only maintenance audit log."""
    try:
        _, audit = _paths()
        if not audit.exists():
            return {"ok": True, "entries": [], "count": 0}
        lines = audit.read_text(encoding="utf-8").splitlines()[-max(1, int(limit)):]
        entries = []
        for ln in lines:
            try:
                entries.append(json.loads(ln))
            except Exception:
                continue
        return {"ok": True, "entries": entries, "count": len(entries)}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200], "entries": []}


def rollback_from_audit(entry: dict) -> dict:
    """Restore one memory's metadata/memory_status from an audit ``before``
    snapshot. Non-destructive reversal of a single maintenance action.
    """
    before = (entry or {}).get("before") or {}
    mid = before.get("id")
    if not mid or not _cfg("snapshot_before_write", True):
        return {"ok": False, "error": "no snapshot in audit entry"}
    from memall.core.db import get_conn
    conn = get_conn()
    try:
        conn.execute(
            "UPDATE memories SET metadata=?, memory_status=?, updated_at=? WHERE id=?",
            (before.get("metadata"), before.get("memory_status"),
             before.get("updated_at") or _now(), int(mid)),
        )
        conn.commit()
        _append_audit({"ts": _now(), "action": "rollback", "memory_id": mid,
                       "restored": before})
        return {"ok": True, "memory_id": mid, "restored": before}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}
    finally:
        try:
            conn.close()
        except Exception:
            pass


def maintenance_status() -> dict:
    """Read-only status/observability for the maintenance surface."""
    _, audit = _paths()
    return {
        "enabled": bool(_cfg("enabled", False)),
        "source_pipeline": str(_cfg("source_pipeline", _MAINTENANCE_PIPELINE)),
        "archive_dir": str(audit.parent),
        "audit_log_exists": audit.exists(),
        "snapshot_before_write": bool(_cfg("snapshot_before_write", True)),
        "policies": {a: bool(_cfg(a, False)) for a in (
            "canonicalize", "conflict_review", "expire",
            "low_utility_archive", "theme_profile", "policy_adaptation")},
    }
