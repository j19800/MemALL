"""Sync protocol — portable bundle export/import.

Extracted from ``gateway.py`` (Phase 15).  Kept import-compatible by
re-exporting from ``memall.gateway``.
"""

import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from memall.core.db import get_conn
from memall.core.levels import normalize_level
from memall.core.thin_waist import normalize_agent_name

logger = logging.getLogger(__name__)

_PROJECT_DIR = Path.home() / ".memall"


# ── Identity merge ──

def export_bundle(agent_name: str, fmt: str = "json") -> Dict[str, Any]:
    """Export all data for an agent as a portable bundle.

    The bundle includes memories, edges, identity record, tags, and a
    timestamp.  Also writes the bundle to a temp JSON file under
    ``~/.memall/exports/`` and includes the file path in the return dict.

    Args:
        agent_name: Agent to export.
        fmt: Output format (only ``"json"`` supported currently).

    Returns:
        dict with keys: version, exported_at, agent_name, memories,
        edges, identity, file_path.
    """
    if fmt != "json":
        raise ValueError(f"unsupported format '{fmt}', only 'json' is supported")

    conn = get_conn()
    try:
        # ── Memories ──
        mem_rows = conn.execute(
            "SELECT * FROM memories WHERE agent_name = ? ORDER BY id LIMIT 1000",
            (agent_name,),
        ).fetchall()
        memories = [dict(r) for r in mem_rows]

        # ── Edges (all edges involving this agent's memories) ──
        mem_ids = [r["id"] for r in mem_rows]
        edges = []
        if mem_ids:
            placeholders = ",".join("?" for _ in mem_ids)
            edge_rows = conn.execute(
                f"SELECT * FROM edges WHERE source_id IN ({placeholders}) OR target_id IN ({placeholders})",
                mem_ids + mem_ids,
            ).fetchall()
            edges = [dict(r) for r in edge_rows]

        # ── Identity ──
        ident = conn.execute(
            "SELECT * FROM identities WHERE agent_name = ?", (agent_name,)
        ).fetchone()
        identity = dict(ident) if ident else {}

        bundle = {
            "version": "1.0",
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "agent_name": agent_name,
            "memories": memories,
            "edges": edges,
            "identity": identity,
        }
    finally:
        conn.close()

    # ── Write to file ──
    # Sanitize agent_name to prevent path traversal
    safe_name = re.sub(r'[^a-zA-Z0-9_\-\.]+', '_', agent_name) if agent_name else "unknown"
    export_dir = _PROJECT_DIR / "exports"
    export_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    file_path = export_dir / f"bundle_{safe_name}_{ts}.json"
    file_path.write_text(json.dumps(bundle, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    bundle["file_path"] = str(file_path)
    return bundle


def _import_identity(conn, identity: dict, agent_name: str) -> bool:
    """Import/update an identity record. Returns True if updated/inserted."""
    agent_name = normalize_agent_name(agent_name)
    if not identity or not agent_name:
        return False
    existing = conn.execute(
        "SELECT id FROM identities WHERE agent_name = ?", (agent_name,)
    ).fetchone()
    if existing:
        conn.execute(
            "UPDATE identities SET description = ?, agent_type = ?, "
            "last_heartbeat = ?, metadata = ? WHERE agent_name = ?",
            (
                identity.get("description", ""),
                identity.get("agent_type", "ai"),
                identity.get("last_heartbeat", datetime.now(timezone.utc).isoformat()),
                identity.get("metadata", None),
                agent_name,
            ),
        )
    else:
        conn.execute(
            "INSERT INTO identities (agent_name, agent_type, description, "
            "last_heartbeat, metadata) VALUES (?,?,?,?,?)",
            (
                agent_name,
                identity.get("agent_type", "ai"),
                identity.get("description", ""),
                identity.get("last_heartbeat", datetime.now(timezone.utc).isoformat()),
                identity.get("metadata", None),
            ),
        )
    return True


def _import_memories(conn, memories: list, agent_name: str) -> tuple:
    """Import memories with dedup by content_hash.

    Returns (imported_count, old_id_to_new: dict).
    """
    agent_name = normalize_agent_name(agent_name)
    existing_hashes = {
        r["content_hash"]
        for r in conn.execute(
            "SELECT content_hash FROM memories WHERE content_hash IS NOT NULL AND content_hash != ''"
        ).fetchall()
    }
    imported = 0
    old_id_to_new: Dict[int, int] = {}

    for m in memories:
        h = m.get("content_hash", "")
        if h and h in existing_hashes:
            row = conn.execute(
                "SELECT id FROM memories WHERE content_hash = ?",
                (h,),
            ).fetchone()
            if row:
                old_id_to_new[m["id"]] = row["id"]
            continue

        fields = {
            "content": m.get("content", ""),
            "content_hash": h,
            "level": normalize_level(m.get("level", "P2")),
            "owner": m.get("owner", ""),
            "agent_name": agent_name,
            "subject": m.get("subject", ""),
            "project": m.get("project", ""),
            "category": m.get("category", "general"),
            "summary": m.get("summary", ""),
            "occurred_at": m.get("occurred_at", datetime.now(timezone.utc).isoformat()),
            "created_at": m.get("created_at", datetime.now(timezone.utc).isoformat()),
            "updated_at": m.get("updated_at", datetime.now(timezone.utc).isoformat()),
            "supersedes": m.get("supersedes") or "[]",
            "confidence": m.get("confidence", 1.0),
            "visibility": m.get("visibility", "private"),
            "metadata": json.dumps(m.get("metadata", {})) if isinstance(m.get("metadata"), dict) else (m.get("metadata") or "{}"),
        }

        cur = conn.execute("PRAGMA table_info(memories)")
        cols = {r["name"] for r in cur.fetchall()}
        if "tags" in cols:
            fields["tags"] = m.get("tags", "[]")

        columns = list(fields.keys())
        placeholders = ",".join("?" for _ in columns)
        values = [fields[c] for c in columns]

        cur = conn.execute(
            f"INSERT INTO memories ({','.join(columns)}) VALUES ({placeholders})",
            values,
        )
        old_id_to_new[m["id"]] = cur.lastrowid
        existing_hashes.add(h)
        imported += 1

    return imported, old_id_to_new


def _import_edges(conn, edges_in: list, old_id_to_new: dict) -> int:
    """Import edges with dedup and ID remapping. Returns imported count."""
    existing_edge_keys = {
        (r["source_id"], r["target_id"], r["relation_type"])
        for r in conn.execute("SELECT source_id, target_id, relation_type FROM edges").fetchall()
    }
    imported = 0
    for e in edges_in:
        src = old_id_to_new.get(e.get("source_id"))
        tgt = old_id_to_new.get(e.get("target_id"))
        if src is None or tgt is None or src == tgt:
            continue
        key = (src, tgt, e.get("relation_type", "refines"))
        if key in existing_edge_keys:
            continue
        conn.execute(
            "INSERT INTO edges (source_id, target_id, relation_type, weight, created_at, metadata, valid_from) "
            "VALUES (?,?,?,?,?,?,?)",
            (
                src, tgt,
                e.get("relation_type", "refines"),
                e.get("weight", 1.0),
                e.get("created_at", datetime.now(timezone.utc).isoformat()),
                e.get("metadata", "{}"),
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        existing_edge_keys.add(key)
        imported += 1
    return imported


def _path_within_any(path: Path, roots: List[Path]) -> bool:
    """True if ``path`` is a root itself or lies underneath one of ``roots``.

    Compares whole path *components*, so a sibling directory that merely shares
    a string prefix (``~/.memall/exports_evil/`` vs ``~/.memall/exports/``)
    cannot pass.  Windows comparison is case-insensitive.
    """
    def _parts(p: Path):
        return tuple(
            part.casefold() if os.name == "nt" else part
            for part in Path(p).parts
        )

    target = _parts(path)
    for root in roots:
        base = _parts(root)
        if target[:len(base)] == base:
            return True
    return False


def import_bundle(bundle_or_path) -> Dict[str, Any]:
    """Import a full bundle (memories + edges + identity).

    Deduplicates memories by ``content_hash`` and edges by
    ``(source_content_hash, target_content_hash, relation_type)``.
    Identity records are updated if the agent already exists, otherwise
    inserted.

    Args:
        bundle_or_path: A dict bundle or a path (str/Path) to a bundle JSON file.

    Returns:
        dict: {imported_memories, imported_edges, identity_updated}
    """
    # ── Resolve input ──
    if isinstance(bundle_or_path, dict):
        bundle = bundle_or_path
    else:
        path = Path(bundle_or_path)
        if not path.exists():
            raise FileNotFoundError(f"bundle file not found: {path}")
        # Security: restrict import to the exports directory (plus any dirs the
        # operator explicitly allows via ``security.import_allowed_dirs``).
        # Comparison is done on whole path *components* (``<dir><sep>`` prefix),
        # not a bare string prefix — a bare prefix lets a sibling directory such
        # as ``~/.memall/exports_evil/`` pass the check.
        resolved = path.resolve()
        allowed_dirs = [(_PROJECT_DIR / "exports").resolve()]
        try:
            from memall.config import get_config as _get_config
            extra = _get_config("security.import_allowed_dirs", []) or []
            if isinstance(extra, str):
                extra = [extra]
            for d in extra:
                allowed_dirs.append(Path(d).expanduser().resolve())
        except Exception:
            logger.debug("gateway_sync: security.import_allowed_dirs unreadable, using default", exc_info=True)
        if not _path_within_any(resolved, allowed_dirs):
            raise PermissionError(
                f"Import rejected: {resolved} is outside allowed directories "
                f"{[str(d) for d in allowed_dirs]}"
            )
        bundle = json.loads(path.read_text(encoding="utf-8"))

    memories = bundle.get("memories", [])
    edges_in = bundle.get("edges", [])
    identity = bundle.get("identity", {})
    agent_name = bundle.get("agent_name", identity.get("agent_name", ""))

    conn = get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        identity_updated = _import_identity(conn, identity, agent_name)
        imported_memories, old_id_to_new = _import_memories(conn, memories, agent_name)
        imported_edges = _import_edges(conn, edges_in, old_id_to_new)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    return {
        "imported_memories": imported_memories,
        "imported_edges": imported_edges,
        "identity_updated": identity_updated,
    }
