"""
Skill export — render trusted skills to host SKILL.md files (P0-② 分发面).
=======================================================================

The registry (``skills`` table) is the source of truth; this module is the
distribution surface.  ``export_trusted_skills`` renders a given agent's
``trusted`` rows to ``<target_dir>/<agent>-<domain>/SKILL.md``, frontmatter
only (name / description / version / source_memory_id / frontmatter_hash).

Two properties are load-bearing:

* **Idempotent**: the frontmatter hash (sha256 of name+description+version)
  is compared before writing.  Unchanged content is not rewritten, so the
  host directory and the ``skills-lock.json`` row count stay stable across
  repeated runs.

* **Fail-closed**: if the target directory cannot be created, the export
  aborts without writing a partial file.  A half-written SKILL.md is worse
  than no file — the host would load a truncated skill.

The lock file records ``hash ↔ source_memory_id ↔ exported_at`` so drift is
traceable: if a skill changes upstream, its hash changes, and the lock row
shows the old export.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger("memall.pipeline.skill_export")

LOCK_FILENAME = "skills-lock.json"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _get_conn():
    from memall.core.db import get_conn
    return get_conn()


def export_trusted_skills(agent: str, target_dir: str,
                          domain: Optional[str] = None) -> dict:
    """Render *agent*'s trusted skills to ``<target_dir>/<agent>-<domain>/SKILL.md``.

    Returns a summary dict: ``exported`` (count written), ``skipped``
    (count unchanged), ``errors`` (list of {skill, error}).
    """
    from memall.pipeline.skills import list_skills, render_skill_md, frontmatter_hash

    agent = (agent or "").strip()
    if not agent:
        return {"status": "error", "error": "agent is required"}
    if not target_dir:
        return {"status": "error", "error": "target_dir is required"}

    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM skills WHERE status='trusted' AND domain=?",
            (agent,)).fetchall()
    finally:
        conn.close()

    if not rows:
        # Nothing trusted for this agent — still create the lock file so the
        # target directory exists and the next run has a stable base.
        try:
            os.makedirs(target_dir, exist_ok=True)
            _write_lock(target_dir, [])
        except Exception as e:
            return {"status": "error", "error": f"cannot create target_dir: {e}"}
        return {"status": "ok", "agent": agent, "exported": 0,
                "skipped": 0, "errors": [], "note": "no trusted skills"}

    if not os.path.isdir(target_dir):
        try:
            os.makedirs(target_dir, exist_ok=True)
        except Exception as e:
            return {"status": "error",
                    "error": f"cannot create target_dir {target_dir!r}: {e}"}

    lock = _read_lock(target_dir)
    exported = 0
    skipped = 0
    errors = []
    lock_entries = []

    for row in rows:
        skill = {
            "id": row["id"],
            "name": row["name"],
            "description": row["description"],
            "domain": row["domain"],
            "version": row["version"],
            "source_memory_id": row["source_memory_id"],
            "frontmatter_hash": row["frontmatter_hash"],
        }
        fhash = skill["frontmatter_hash"] or frontmatter_hash(
            skill["name"], skill["description"], skill["version"])
        skill["frontmatter_hash"] = fhash

        safe_domain = (skill["domain"] or "general").replace("/", "_")
        subdir = os.path.join(target_dir, f"{agent}-{safe_domain}")
        try:
            os.makedirs(subdir, exist_ok=True)
        except Exception as e:
            errors.append({"skill": skill["name"], "error": str(e)})
            continue

        md_path = os.path.join(subdir, "SKILL.md")
        body = render_skill_md(skill)

        # Idempotency: skip if content and hash match what's already on disk.
        if os.path.isfile(md_path):
            try:
                with open(md_path, "r", encoding="utf-8") as f:
                    existing = f.read()
                if existing == body:
                    skipped += 1
                    lock_entries.append({
                        "skill_id": skill["id"],
                        "name": skill["name"],
                        "frontmatter_hash": fhash,
                        "source_memory_id": skill["source_memory_id"],
                        "exported_at": lock.get(skill["name"], {}).get("exported_at", _now()),
                    })
                    continue
            except Exception:
                pass  # file unreadable — rewrite it

        try:
            with open(md_path, "w", encoding="utf-8", newline="\n") as f:
                f.write(body)
            exported += 1
            lock_entries.append({
                "skill_id": skill["id"],
                "name": skill["name"],
                "frontmatter_hash": fhash,
                "source_memory_id": skill["source_memory_id"],
                "exported_at": _now(),
            })
        except Exception as e:
            errors.append({"skill": skill["name"], "error": str(e)})

    _write_lock(target_dir, lock_entries)
    return {"status": "ok", "agent": agent, "target_dir": target_dir,
            "exported": exported, "skipped": skipped, "errors": errors}


def _read_lock(target_dir: str) -> dict:
    path = os.path.join(target_dir, LOCK_FILENAME)
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _write_lock(target_dir: str, entries: list) -> None:
    path = os.path.join(target_dir, LOCK_FILENAME)
    by_name = {e["name"]: e for e in entries}
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(by_name, f, ensure_ascii=False, indent=2)
