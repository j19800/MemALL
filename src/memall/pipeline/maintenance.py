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

import hashlib
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_MAINTENANCE_PIPELINE = "cognitive.loop.maintenance"

# memory_status values a rollback may restore. Anything else in an audit
# record is treated as tampered/unexpected and refused (defence in depth:
# the restore is never a blind write).
_ROLLBACKABLE_STATUSES = frozenset(
    {"", "None", None, "ACTIVE", "CONFLICT", "REVIEW", "archived", "superseded",
     "deleted", "pending", "resolved"}
)


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
        # ALWAYS read the current metadata, even when snapshotting is disabled.
        # With `snapshot_before_write=False` the old code took `before={}` as the
        # source and then WROTE a metadata document containing only the marker —
        # wiping resolve/slot_key/quality/user fields (real data loss, found by
        # independent review). Snapshotting only controls what the AUDIT keeps.
        cur_row = _snapshot_row(conn, mem_id)
        raw = cur_row.get("metadata") if cur_row else None
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
    # A caller may only NARROW the operator's authorised set, never widen it:
    # each requested action must also be enabled in config. Otherwise passing
    # actions=["expire"] would archive memories even though the operator
    # explicitly left maintenance.expire=False (config-gate bypass).
    permitted = [a for a in all_actions if bool(_cfg(a, False))]
    if actions:
        wanted = [a for a in actions if a in permitted]
        skipped = [a for a in actions if a not in permitted and a in all_actions]
    else:
        wanted = list(permitted)
        skipped = []
    cap = int(max_actions or _cfg("max_actions_per_run", 50) or 50)
    cap = max(1, min(cap, 500))

    from memall.core.db import get_conn
    conn = get_conn()
    try:
        summary: dict[str, Any] = {
            "ok": True, "ran": True, "dry_run": bool(dry_run),
            "ts": _now(), "source_pipeline": str(_cfg("source_pipeline", _MAINTENANCE_PIPELINE)),
            "actions": [], "skipped_unauthorised": skipped,
            "total_marked": 0, "snapshot_before_write": bool(_cfg("snapshot_before_write", True)),
        }
        if not wanted:
            # Nothing the operator authorised was requested: report and leave
            # without writing an audit line or touching any row.
            summary["ran"] = False
            summary["reason"] = "no action authorised by config"
            return summary
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


_AUDIT_TAIL_MAX_BYTES = 4_000_000  # ~4MB tail read; guards against OOM on a huge log


def _read_audit_tail_lines(max_lines: int):
    """Read only the last chunk of the audit log (bounded by bytes)."""
    _, audit = _paths()
    if not audit.exists():
        return []
    size = audit.stat().st_size
    with audit.open("rb") as f:
        if size > _AUDIT_TAIL_MAX_BYTES:
            f.seek(size - _AUDIT_TAIL_MAX_BYTES)
        buf = f.read()
    text = buf.decode("utf-8", "replace")
    lines = text.splitlines()
    if size > _AUDIT_TAIL_MAX_BYTES and lines:
        # First line may be a partial record from the mid-file seek; drop it.
        lines = lines[1:]
    return lines[-max(1, int(max_lines)):]


def _redact_audit_record(rec: dict) -> dict:
    """Project an audit record for an UNTRUSTED reader.

    The on-disk log keeps full before/after metadata snapshots so the server
    can verify and roll back. Returning those verbatim over MCP leaks other
    agents' private memory content and secrets that happen to live in
    metadata. Callers still get the decision trail they need to audit:
    identity, policy, which fields changed, and a non-reversible hash so a
    rollback can be requested by (ts, memory_id, action).
    """
    if not isinstance(rec, dict):
        return {}
    out = {
        "ts": rec.get("ts"),
        "action": rec.get("action"),
        "memory_id": rec.get("memory_id"),
        "session_id": rec.get("session_id"),
        "path": rec.get("path"),
        "source_pipeline": rec.get("source_pipeline"),
        "rules_applied": rec.get("rules_applied"),
        "marker": {k: v for k, v in (rec.get("marker") or {}).items()
                   if k in ("policy", "dry_run", "note")} if isinstance(rec.get("marker"), dict) else None,
        "set_archived": rec.get("set_archived"),
        "redacted": True,
    }
    for side in ("before", "after"):
        snap = rec.get(side)
        if isinstance(snap, dict):
            meta = snap.get("metadata")
            out[side] = {
                "id": snap.get("id"),
                "memory_status": snap.get("memory_status"),
                "updated_at": snap.get("updated_at"),
                # Fingerprint only: proves a snapshot exists and lets the
                # server match it, without disclosing memory content.
                "metadata_sha256": (hashlib.sha256(str(meta).encode("utf-8", "replace")).hexdigest()
                                    if meta is not None else None),
            }
    return {k: v for k, v in out.items() if v is not None}


def read_audit(limit: int = 50, redact: bool = True) -> dict:
    """Read the tail of the append-only maintenance audit log.

    `redact=True` (default) returns the untrusted-reader projection. Pass
    `redact=False` only from trusted, in-process maintenance code that needs
    the full snapshot — never wire it to a network surface.
    """
    try:
        lines = _read_audit_tail_lines(max(1, int(limit)))
        entries = []
        for ln in lines:
            try:
                rec = json.loads(ln)
            except Exception:
                continue
            if not isinstance(rec, dict):
                continue
            entries.append(_redact_audit_record(rec) if redact else rec)
        return {"ok": True, "entries": entries, "count": len(entries),
                "redacted": bool(redact)}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200], "entries": []}


def _iter_audit_entries_desc(max_lines: int = 20000):
    """Yield FULL audit records newest-first, from a bounded byte tail.

    Trusted in-process use only (rollback verification); never returned over
    MCP. A record outside the scanned tail is simply not found, which makes
    the rollback refuse rather than act on unverifiable state.
    """
    try:
        lines = _read_audit_tail_lines(max_lines)
        for ln in reversed(lines):
            try:
                rec = json.loads(ln)
            except Exception:
                continue
            if isinstance(rec, dict):
                yield rec
    except Exception as e:
        logger.debug("maintenance: audit scan failed: %s", e)


def _find_audit_record(ts: str, memory_id: int, action: str) -> dict:
    """Locate ONE maintenance record in the server-side audit log.

    The audit log — not caller-supplied data — is the only trusted source for
    a rollback snapshot (security fix: passing an arbitrary `entry` dict made
    rollback_from_audit an arbitrary-write primitive on any memory row).
    """
    if not ts or memory_id is None or not action:
        return {}
    for rec in _iter_audit_entries_desc():
        if (str(rec.get("ts") or "") == str(ts)
                and rec.get("memory_id") == memory_id
                and rec.get("action") == action):
            return rec
    return {}


def rollback_from_audit(entry: dict) -> dict:
    """Reverse ONE maintenance action using the SERVER-SIDE audit record.

    Trust model (hardened): the caller supplies only *identifiers* — ts,
    memory_id, action. The restore snapshot is read from the append-only
    audit log, so a caller cannot smuggle arbitrary metadata. The audit
    entry's `before` is applied only when the row's current state still
    matches the log's `after` (compare-and-set); otherwise the rollback is
    refused rather than silently clobbering later legitimate writes.
    """
    entry = entry or {}
    ts = entry.get("ts")
    mem_id = entry.get("memory_id")
    action = entry.get("action")
    # Back-compat: an entry taken verbatim from read_audit() already carries
    # ts/memory_id/action. A hand-crafted `before` payload is IGNORED.
    if not (ts and mem_id is not None and action):
        return {"ok": False, "error": "rollback requires ts+memory_id+action from an audit record"}
    if action == "rollback":
        return {"ok": False, "error": "refusing to roll back a rollback record"}

    rec = _find_audit_record(ts, mem_id, action)
    if not rec:
        return {"ok": False, "error": "no matching audit record (unverifiable request refused)"}
    before = rec.get("before") or {}
    after = rec.get("after") or {}
    mid = before.get("id") if before.get("id") is not None else mem_id
    try:
        mid = int(mid)
    except (TypeError, ValueError):
        return {"ok": False, "error": "invalid memory id in audit record"}
    if not before.get("metadata"):
        return {"ok": False, "error": "no before-snapshot in audit record (snapshot_before_write off?)"}

    # Validate the snapshot we are about to write (never blind-write).
    raw_meta = before.get("metadata")
    try:
        json.loads(raw_meta) if isinstance(raw_meta, str) else dict(raw_meta or {})
    except Exception as e:
        return {"ok": False, "error": f"audit snapshot metadata unreadable: {str(e)[:120]}"}
    restore_status = before.get("memory_status")
    if restore_status is not None and str(restore_status) not in _ROLLBACKABLE_STATUSES:
        return {"ok": False, "error": f"unexpected memory_status in audit record: {restore_status!r}"}

    from memall.core.db import get_conn
    conn = get_conn()
    try:
        cur = conn.execute(
            "SELECT metadata, memory_status, updated_at FROM memories WHERE id = ?", (mid,)
        ).fetchone()
        if not cur:
            return {"ok": False, "error": "memory row no longer exists"}

        # CAS: guard only the fields THIS maintenance action actually wrote
        # (resolve.status + the cognitive_maintenance marker), not the whole
        # metadata document. Rationale (found by real MCP-protocol E2E):
        # background pipeline hooks keep enriching `metadata` after a
        # maintenance pass (ingest_mode, pinned_fields, quality, ...), so a
        # whole-document equality check permanently refused every legitimate
        # rollback. Guarding the action's own fields still refuses a real
        # divergence (someone re-resolving or re-marking the row) while
        # tolerating unrelated enrichment.
        def _norm_status(v):
            return "" if v is None else str(v)

        want_status = after.get("memory_status")
        have_status = cur["memory_status"] if hasattr(cur, "keys") else cur[1]
        if want_status is not None and _norm_status(have_status) != _norm_status(want_status):
            return {"ok": False, "error": "state diverged from audit 'after'; rollback refused",
                    "memory_id": mid}

        def _norm_meta(v):
            if v is None:
                return {}
            if isinstance(v, dict):
                return v
            try:
                parsed = json.loads(v)
                return parsed if isinstance(parsed, dict) else {}
            except Exception:
                return {}

        def _guarded_view(meta: dict) -> dict:
            """Only the fields a maintenance pass owns."""
            res = meta.get("resolve")
            res = res if isinstance(res, dict) else {}
            marks = meta.get("cognitive_maintenance")
            marks = marks if isinstance(marks, list) else []
            return {
                "resolve_status": res.get("status"),
                "marker_actions": [m.get("action") if isinstance(m, dict) else m for m in marks],
                "marker_count": len(marks),
            }

        want_meta = after.get("metadata")
        if want_meta is not None:
            have_raw = cur["metadata"] if hasattr(cur, "keys") else cur[0]
            if _guarded_view(_norm_meta(have_raw)) != _guarded_view(_norm_meta(want_meta)):
                return {"ok": False, "error": "state diverged from audit 'after'; rollback refused",
                        "memory_id": mid}

        conn.execute(
            "UPDATE memories SET metadata=?, memory_status=?, updated_at=? WHERE id=?",
            (raw_meta, restore_status, before.get("updated_at") or _now(), mid),
        )
        conn.commit()
        _append_audit({"ts": _now(), "action": "rollback", "memory_id": mid,
                       "reverses": {"ts": ts, "policy_action": action},
                       "restored_from": "server-side-audit-log"})
        return {"ok": True, "memory_id": mid, "restored": before,
                "source": "server-side-audit-log"}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}
    finally:
        try:
            conn.close()
        except Exception:
            pass


def maintenance_status() -> dict:
    """Read-only status/observability for the maintenance surface."""
    arch, audit = _paths()
    return {
        "enabled": bool(_cfg("enabled", False)),
        "source_pipeline": str(_cfg("source_pipeline", _MAINTENANCE_PIPELINE)),
        # Report the REAL archive directory (archive_session writes here). The
        # previous version returned `audit.parent`, which is a different config
        # key — status showed a path nothing was ever written to.
        "archive_dir": str(arch),
        "audit_log": str(audit),
        "audit_log_exists": audit.exists(),
        "snapshot_before_write": bool(_cfg("snapshot_before_write", True)),
        "policies": {a: bool(_cfg(a, False)) for a in (
            "canonicalize", "conflict_review", "expire",
            "low_utility_archive", "theme_profile", "policy_adaptation")},
    }
