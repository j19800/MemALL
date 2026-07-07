"""
Phase 14: Memory Operations Enhancement
========================================
Core atomic memory operations: merge, split, tag, batch, deduplicate.
All operations use transactions for atomicity.
"""

import hashlib
import json
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Any, Sequence

from memall.core.db import get_conn
from memall.core.nlp import compute_tfidf, cosine_sim


# ══════════════════════════════════════════════════════════════════
# Internal helpers
# ══════════════════════════════════════════════════════════════════

def _ensure_tags_column(conn) -> None:
    """Ensure memories table has a `tags` TEXT column (JSON array format)."""
    cur = conn.execute("PRAGMA table_info(memories)")
    cols = [r["name"] for r in cur.fetchall()]
    if "tags" not in cols:
        conn.execute(
            "ALTER TABLE memories ADD COLUMN tags TEXT NOT NULL DEFAULT '[]'"
        )
        conn.commit()


def _parse_tags(raw: Optional[str]) -> List[str]:
    """Parse tags field from DB (JSON array string) into a Python list."""
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, list):
            return parsed
    except (json.JSONDecodeError, TypeError):
        logger.warning("ops.py: silent error", exc_info=True)
    return []


def _tags_to_json(tags: List[str]) -> str:
    """Serialize tags list to JSON string, ensuring uniqueness."""
    return json.dumps(sorted(set(t.strip() for t in tags if t.strip())), ensure_ascii=False)


def _ensure_ops_log(conn) -> None:
    """Ensure ops_log table exists (idempotent)."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS ops_log (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            op_type     TEXT NOT NULL,
            target_ids  TEXT NOT NULL,
            before_snapshot TEXT NOT NULL,
            op_metadata TEXT NOT NULL DEFAULT '{}',
            executed_at TEXT NOT NULL,
            rolled_back_at TEXT
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ops_log_type ON ops_log(op_type)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ops_log_rolled ON ops_log(rolled_back_at)")
    conn.commit()


def _snapshot_memories(conn, ids: Sequence[int]) -> Dict[int, dict]:
    """Dump essential columns for a list of memory IDs (for ops_log undo).

    Returns {mem_id: {level, content, metadata, tags}}.
    """
    if not ids:
        return {}
    placeholders = ",".join("?" * len(ids))
    rows = conn.execute(
        f"SELECT id, level, content, metadata, tags FROM memories WHERE id IN ({placeholders})",
        list(ids),
    ).fetchall()
    return {
        r["id"]: {
            "level": r["level"],
            "content": r["content"],
            "metadata": r["metadata"],
            "tags": r["tags"],
        }
        for r in rows
    }


def _record_ops_entry(
    conn,
    op_type: str,
    target_ids: list,
    before_snapshot: dict,
    op_metadata: dict,
) -> None:
    """Record a batch operation in ops_log for undo support (idempotent)."""
    _ensure_ops_log(conn)
    conn.execute(
        "INSERT INTO ops_log (op_type, target_ids, before_snapshot, op_metadata, "
        "executed_at) VALUES (?,?,?,?,?)",
        (
            op_type,
            json.dumps(target_ids, ensure_ascii=False),
            json.dumps(before_snapshot, default=str, ensure_ascii=False),
            json.dumps(op_metadata, ensure_ascii=False),
            datetime.now(timezone.utc).isoformat(),
        ),
    )


def _merge_memories_conn(
    conn,
    source_id: int,
    target_id: int,
    separator: str = "\n---\n",
) -> dict:
    """Internal merge reusing an existing connection.

    Caller is responsible for ``BEGIN IMMEDIATE`` / ``commit()`` /
    ``rollback()``.  This avoids the connection churn of
    :func:`merge_memories` when batching many merges (e.g. dedup).

    In addition to content merge, this function:
      * Merges source metadata into target (prefixed keys).
      * Prefers longer subject/summary (loses less information).
      * Increments ``access_count`` on the target.
    """
    if source_id == target_id:
        raise ValueError("source and target must be different memories")

    src = conn.execute(
        "SELECT id, content, subject, summary, metadata FROM memories WHERE id = ?",
        (source_id,),
    ).fetchone()
    tgt = conn.execute(
        "SELECT id, content, subject, summary, metadata FROM memories WHERE id = ?",
        (target_id,),
    ).fetchone()
    if not src:
        raise ValueError(f"source memory {source_id} not found")
    if not tgt:
        raise ValueError(f"target memory {target_id} not found")

    now_iso = datetime.now(timezone.utc).isoformat()

    # ── Merge content ──
    merged_content = tgt["content"] + separator + src["content"]

    # ── Merge metadata (prefix source keys to preserve traceability) ──
    try:
        tgt_meta = json.loads(tgt["metadata"]) if tgt["metadata"] else {}
    except (json.JSONDecodeError, TypeError):
        tgt_meta = {}
    try:
        src_meta = json.loads(src["metadata"]) if src["metadata"] else {}
    except (json.JSONDecodeError, TypeError):
        src_meta = {}
    for k, v in src_meta.items():
        merged_key = f"merge_src_{source_id}_{k}"
        tgt_meta[merged_key] = v

    # ── Merge subject (prefer longer) ──
    src_subj = (src["subject"] or "").strip()
    tgt_subj = (tgt["subject"] or "").strip()
    if src_subj and (not tgt_subj or len(src_subj) > len(tgt_subj)):
        merged_subject = src_subj
        tgt_meta[f"merge_src_{source_id}_original_subject"] = {
            "value": tgt_subj, "_meta": {"version": 1, "written_at": now_iso}
        }
    else:
        merged_subject = tgt_subj

    # ── Merge summary (prefer longer) ──
    src_sum = (src["summary"] or "").strip()
    tgt_sum = (tgt["summary"] or "").strip()
    if src_sum and (not tgt_sum or len(src_sum) > len(tgt_sum)):
        merged_summary = src_sum
    else:
        merged_summary = tgt_sum

    # ── Update target ──
    conn.execute(
        "UPDATE memories SET content=?, subject=?, summary=?, metadata=?, "
        "updated_at=?, access_count = access_count + 1 WHERE id=?",
        (
            merged_content,
            merged_subject,
            merged_summary,
            json.dumps(tgt_meta, ensure_ascii=False),
            now_iso,
            target_id,
        ),
    )

    # ── Redirect edges — source_id → target_id ──
    before_in = conn.execute(
        "SELECT COUNT(*) AS c FROM edges WHERE target_id = ?", (source_id,)
    ).fetchone()["c"]
    before_out = conn.execute(
        "SELECT COUNT(*) AS c FROM edges WHERE source_id = ?", (source_id,)
    ).fetchone()["c"]

    conn.execute("UPDATE edges SET target_id = ? WHERE target_id = ?",
                 (target_id, source_id))
    conn.execute("UPDATE edges SET source_id = ? WHERE source_id = ?",
                 (target_id, source_id))
    conn.execute("DELETE FROM edges WHERE source_id = ? AND target_id = ?",
                 (target_id, target_id))

    edges_redirected = before_in + before_out

    # ── Delete source memory ──
    conn.execute("DELETE FROM memories WHERE id = ?", (source_id,))

    return {
        "merged_into": target_id,
        "deleted_source": source_id,
        "edges_redirected": edges_redirected,
        "separator": separator,
        "subject_merged": merged_subject,
    }


# ══════════════════════════════════════════════════════════════════
# 1. Memory Merge
# ══════════════════════════════════════════════════════════════════

def merge_memories(
    source_id: int,
    target_id: int,
    separator: str = "\n---\n",
) -> Dict[str, Any]:
    """Merge source memory into target memory atomically.

    Wraps :func:`_merge_memories_conn` with its own connection and
    ``BEGIN IMMEDIATE``.

    Beyond content concatenation:
      * Source metadata is merged into target (prefixed keys).
      * Longer subject/summary is preferred.
      * ``access_count`` is incremented on the target.

    Args:
        source_id: ID of the memory to merge from (will be deleted).
        target_id: ID of the memory to merge into (will be kept).
        separator: String used to join the two contents. Default ``"\\n---\\n"``.

    Returns:
        dict: {merged_into, deleted_source, edges_redirected, separator, subject_merged}
    """
    conn = get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        result = _merge_memories_conn(conn, source_id, target_id, separator)
        conn.commit()
        return result
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ══════════════════════════════════════════════════════════════════
# 2. Memory Split
# ══════════════════════════════════════════════════════════════════

def split_memory(memory_id: int, delimiter: str = "\n\n") -> Dict[str, Any]:
    """Split a memory into multiple memories by the given delimiter.

    - Each non-empty segment becomes a new independent memory.
    - New memories inherit agent_name, category, and level.
    - All edges (inbound and outbound) are replicated for each new memory.
    - Original memory is archived (level='archived'), NOT deleted.

    Args:
        memory_id: ID of the memory to split.
        delimiter: String delimiter to split content by. Default ``\\n\\n``.

    Returns:
        dict: {original_id, split_count, new_ids}
    """
    conn = get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")

        # ── Read original ──
        orig = conn.execute(
            "SELECT id, content, agent_name, category, level, owner, subject, "
            "project, summary, confidence, visibility, metadata "
            "FROM memories WHERE id = ?",
            (memory_id,),
        ).fetchone()
        if not orig:
            raise ValueError(f"memory {memory_id} not found")

        content = orig["content"] or ""
        segments = [s.strip() for s in content.split(delimiter) if s.strip()]

        if len(segments) < 2:
            conn.rollback()
            return {
                "original_id": memory_id,
                "split_count": 0,
                "new_ids": [],
                "note": "content has only one segment after splitting, nothing to do",
            }

        # ── Read edges ──
        in_edges = conn.execute(
            "SELECT source_id, relation_type, weight, metadata FROM edges WHERE target_id = ?",
            (memory_id,),
        ).fetchall()
        out_edges = conn.execute(
            "SELECT target_id, relation_type, weight, metadata FROM edges WHERE source_id = ?",
            (memory_id,),
        ).fetchall()

        now = datetime.now(timezone.utc).isoformat()
        new_ids: List[int] = []

        for seg in segments:
            cur = conn.execute(
                """INSERT INTO memories
                   (content, content_hash, level, owner, agent_name, subject,
                    project, category, summary, occurred_at, created_at, updated_at,
                    supersedes, confidence, visibility, metadata)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    seg,
                    hashlib.sha256(seg.encode("utf-8")).hexdigest(),
                    orig["level"],
                    orig["owner"],
                    orig["agent_name"],
                    orig["subject"],
                    orig["project"],
                    orig["category"],
                    orig["summary"],
                    now,
                    now,
                    now,
                    None,
                    orig["confidence"],
                    orig["visibility"],
                    orig["metadata"],
                ),
            )
            new_id = cur.lastrowid
            new_ids.append(new_id)

            # Replicate in-edges: other → new_id
            for ie in in_edges:
                if ie["source_id"] != new_id:  # skip self
                    conn.execute(
                        "INSERT INTO edges (source_id, target_id, relation_type, weight, created_at, metadata) "
                        "VALUES (?,?,?,?,?,?)",
                        (ie["source_id"], new_id, ie["relation_type"],
                         ie["weight"], now, ie["metadata"]),
                    )

            # Replicate out-edges: new_id → other
            for oe in out_edges:
                if oe["target_id"] != new_id:
                    conn.execute(
                        "INSERT INTO edges (source_id, target_id, relation_type, weight, created_at, metadata) "
                        "VALUES (?,?,?,?,?,?)",
                        (new_id, oe["target_id"], oe["relation_type"],
                         oe["weight"], now, oe["metadata"]),
                    )

        # ── Archive original ──
        conn.execute(
            "UPDATE memories SET level = 'archived', updated_at = ? WHERE id = ?",
            (now, memory_id),
        )

        conn.commit()
        return {
            "original_id": memory_id,
            "split_count": len(new_ids),
            "new_ids": new_ids,
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ══════════════════════════════════════════════════════════════════
# 3. Memory Tagging
# ══════════════════════════════════════════════════════════════════

def tag_memory(
    memory_id: int,
    tags: List[str],
    mode: str = "add",
) -> Dict[str, Any]:
    """Add, set, or remove tags on a single memory.

    Tags are stored as a JSON array in the ``tags`` column.
    Column is auto-created if missing.

    Args:
        memory_id: Target memory ID.
        tags: List of tag strings.
        mode: ``"add"`` (default) appends, ``"set"`` overwrites,
              ``"remove"`` deletes specified tags.

    Returns:
        dict: {memory_id, tags, mode}
    """
    if mode not in ("add", "set", "remove"):
        raise ValueError(f"mode must be 'add', 'set', or 'remove', got '{mode}'")

    conn = get_conn()
    try:
        _ensure_tags_column(conn)

        row = conn.execute(
            "SELECT id, tags FROM memories WHERE id = ?", (memory_id,)
        ).fetchone()
        if not row:
            raise ValueError(f"memory {memory_id} not found")

        current = _parse_tags(row["tags"])

        if mode == "set":
            new_tags = [t.strip() for t in tags if t.strip()]
        elif mode == "add":
            new_tags = sorted(set(current + [t.strip() for t in tags if t.strip()]))
        else:  # remove
            remove_set = {t.strip() for t in tags if t.strip()}
            new_tags = [t for t in current if t not in remove_set]

        tags_json = json.dumps(new_tags, ensure_ascii=False)
        conn.execute(
            "UPDATE memories SET tags = ?, updated_at = ? WHERE id = ?",
            (tags_json, datetime.now(timezone.utc).isoformat(), memory_id),
        )
        conn.commit()

        return {"memory_id": memory_id, "tags": new_tags, "mode": mode}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ══════════════════════════════════════════════════════════════════
# 4. Batch Operations
# ══════════════════════════════════════════════════════════════════

def batch_tag(
    agent_name: Optional[str] = None,
    category: Optional[str] = None,
    tags: Optional[List[str]] = None,
    mode: str = "add",
    level: Optional[str] = None,
    tags_include: Optional[List[str]] = None,
    before: Optional[str] = None,
    after: Optional[str] = None,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """Batch-add tags to memories matching a multi-criteria filter.

    All filter parameters are optional; omitting all returns the whole
    table.  At least one filter should be supplied in practice.

    Args:
        agent_name: Optional agent filter.
        category: Optional category filter.
        tags: Tags to apply (default ``[]``).
        mode: ``"add"`` (default), ``"set"``, or ``"remove"``.
        level: Optional level filter (P0/P1/P2/L1-L10/archived).
        tags_include: Optional list — match memories containing ALL of these tags.
        before: Optional ISO date — match memories with ``occurred_at < before``.
        after: Optional ISO date — match memories with ``occurred_at > after``.
        dry_run: When ``True``, return preview without writing.

    Returns:
        dict: {matched, updated, dry_run, preview}
    """
    if mode not in ("add", "set", "remove"):
        raise ValueError(f"mode must be 'add', 'set', or 'remove', got '{mode}'")
    if tags is None:
        tags = []

    where_clauses: List[str] = []
    params: List[Any] = []
    if agent_name is not None:
        where_clauses.append("agent_name = ?")
        params.append(agent_name)
    if category is not None:
        where_clauses.append("category = ?")
        params.append(category)
    if level is not None:
        where_clauses.append("level = ?")
        params.append(level)
    if before is not None:
        where_clauses.append("occurred_at < ?")
        params.append(before)
    if after is not None:
        where_clauses.append("occurred_at > ?")
        params.append(after)
    # tags_include is enforced post-fetch in Python (no clean SQL with JSON)

    where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

    conn = get_conn()
    try:
        _ensure_tags_column(conn)
        sql = f"SELECT id, tags FROM memories {where_sql}"
        rows = conn.execute(sql, params).fetchall()

        # tags_include filter (all of these must be in the row's tags)
        if tags_include:
            inc_set = {t.strip() for t in tags_include if t.strip()}
            rows = [
                r for r in rows
                if inc_set.issubset(set(_parse_tags(r["tags"])))
            ]

        matched = len(rows)
        preview = [r["id"] for r in rows[:10]]

        if dry_run or matched == 0:
            return {
                "matched": matched,
                "updated": 0,
                "dry_run": dry_run,
                "preview": preview,
            }

        conn.execute("BEGIN IMMEDIATE")
        updated = 0
        tag_set = [t.strip() for t in tags if t.strip()]

        # Snapshot BEFORE changes (for undo)
        all_ids = [r["id"] for r in rows]
        snapshot = _snapshot_memories(conn, all_ids)

        for row in rows:
            current = _parse_tags(row["tags"])

            if mode == "set":
                new_tags = tag_set
            elif mode == "add":
                new_tags = sorted(set(current + tag_set))
            else:  # remove
                remove_set = set(tag_set)
                new_tags = [t for t in current if t not in remove_set]

            if new_tags != current:
                conn.execute(
                    "UPDATE memories SET tags = ?, updated_at = ? WHERE id = ?",
                    (json.dumps(new_tags, ensure_ascii=False),
                     datetime.now(timezone.utc).isoformat(),
                     row["id"]),
                )
                updated += 1

        if updated:
            _record_ops_entry(
                conn, "batch_tag", all_ids, snapshot,
                {"mode": mode, "tags": tags, "dry_run": False},
            )

        conn.commit()
        return {
            "matched": matched,
            "updated": updated,
            "dry_run": False,
            "preview": preview,
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def batch_archive(
    agent_name: Optional[str] = None,
    days: int = 30,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """Archive all memories older than *days*.

    For each archived memory, the original ``level`` is preserved in
    ``metadata.original_level`` so it can be restored later (see
    :func:`batch_restore`).

    Args:
        agent_name: Optional agent filter. ``None`` means global.
        days: Age threshold in days. Default 30.
        dry_run: When ``True``, return preview without writing.

    Returns:
        dict: {matched, archived, dry_run, preview}
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    now_iso = datetime.now(timezone.utc).isoformat()

    conn = get_conn()
    try:
        if agent_name is not None:
            rows = conn.execute(
                "SELECT id, level, metadata FROM memories "
                "WHERE agent_name = ? AND occurred_at < ? AND level != 'archived'",
                (agent_name, cutoff),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, level, metadata FROM memories "
                "WHERE occurred_at < ? AND level != 'archived'",
                (cutoff,),
            ).fetchall()

        matched = len(rows)
        preview = [row["id"] for row in rows[:10]]

        if dry_run or matched == 0:
            return {
                "matched": matched,
                "archived": 0,
                "dry_run": dry_run,
                "preview": preview,
            }

        conn.execute("BEGIN IMMEDIATE")

        # Snapshot BEFORE changes (correct for undo)
        all_ids = [r["id"] for r in rows]
        snapshot = _snapshot_memories(conn, all_ids)

        archived = 0
        for row in rows:
            try:
                meta = json.loads(row["metadata"]) if row["metadata"] else {}
            except (json.JSONDecodeError, TypeError):
                meta = {}
            meta["original_level"] = {
                "value": row["level"],
                "_meta": {"version": 1, "written_at": now_iso},
            }
            conn.execute(
                "UPDATE memories SET level = 'archived', metadata = ?, updated_at = ? "
                "WHERE id = ?",
                (json.dumps(meta, ensure_ascii=False), now_iso, row["id"]),
            )
            archived += 1

        _record_ops_entry(
            conn, "batch_archive", all_ids, snapshot,
            {"days": days, "cutoff": cutoff},
        )
        conn.commit()

        return {
            "matched": matched,
            "archived": archived,
            "dry_run": False,
            "preview": preview,
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def batch_restore(
    agent_name: Optional[str] = None,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """Restore all archived memories.

    Restores the original ``level`` from ``metadata.original_level`` if
    present; otherwise falls back to ``"P2"`` (legacy archives).

    Args:
        agent_name: Optional agent filter. ``None`` means global.
        dry_run: When ``True``, return preview without writing.

    Returns:
        dict: {matched, restored, dry_run, preview, fallback_p2}
    """
    now_iso = datetime.now(timezone.utc).isoformat()

    conn = get_conn()
    try:
        if agent_name is not None:
            rows = conn.execute(
                "SELECT id, metadata FROM memories "
                "WHERE agent_name = ? AND level = 'archived'",
                (agent_name,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, metadata FROM memories WHERE level = 'archived'",
            ).fetchall()

        matched = len(rows)
        preview = [row["id"] for row in rows[:10]]

        if dry_run or matched == 0:
            return {
                "matched": matched,
                "restored": 0,
                "fallback_p2": 0,
                "dry_run": dry_run,
                "preview": preview,
            }

        conn.execute("BEGIN IMMEDIATE")

        # Snapshot BEFORE changes (correct for undo)
        all_ids = [r["id"] for r in rows]
        snapshot = _snapshot_memories(conn, all_ids)

        restored = 0
        fallback_p2 = 0
        for row in rows:
            try:
                meta = json.loads(row["metadata"]) if row["metadata"] else {}
            except (json.JSONDecodeError, TypeError):
                meta = {}

            orig = meta.pop("original_level", None)
            if isinstance(orig, dict):
                target_level = orig.get("value", "P2")
            else:
                target_level = "P2"
                fallback_p2 += 1

            conn.execute(
                "UPDATE memories SET level = ?, metadata = ?, updated_at = ? "
                "WHERE id = ?",
                (
                    target_level,
                    json.dumps(meta, ensure_ascii=False),
                    now_iso,
                    row["id"],
                ),
            )
            restored += 1

        _record_ops_entry(
            conn, "batch_restore", all_ids, snapshot,
            {"fallback_p2": fallback_p2},
        )
        conn.commit()

        return {
            "matched": matched,
            "restored": restored,
            "fallback_p2": fallback_p2,
            "dry_run": False,
            "preview": preview,
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ══════════════════════════════════════════════════════════════════
# 5. Memory Deduplication
# ══════════════════════════════════════════════════════════════════

def deduplicate(
    agent_name: Optional[str] = None,
    threshold: float = 0.9,
    max_pairs: int = 5000,
    max_memories: int = 10000,
    length_ratio_max: float = 5.0,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """Find near-duplicate memories and merge shorter into longer.

    Performance optimizations vs naive O(n²):
      * **length_ratio_max** — skip pairs whose length ratio exceeds the
        limit (default 5x).  Most true duplicates live within the same
        length bucket.
      * **max_memories** — bail out cleanly if the candidate set is too
        large (caller can re-run with a narrower filter).
      * **max_pairs** — stop comparing once N candidate pairs are found.

    Uses TF-IDF + cosine similarity from ``core/nlp.py``.  Merging calls
    :func:`merge_memories` for each pair, which appends content and
    redirects edges.

    Args:
        agent_name: Optional agent filter — only deduplicate within this agent.
        threshold: Cosine similarity threshold (0.0–1.0). Default 0.9.
        max_pairs: Maximum candidate pairs to evaluate. Default 5000.
        max_memories: Maximum memories to scan. Default 10000.
        length_ratio_max: Skip pairs whose length ratio > this. Default 5.0.
        dry_run: When ``True``, return preview without writing.

    Returns:
        dict: {duplicates_found, merged, pairs, dry_run, scanned, truncated}
    """
    if not (0.0 <= threshold <= 1.0):
        raise ValueError(f"threshold must be 0.0–1.0, got {threshold}")
    if max_pairs < 1:
        raise ValueError(f"max_pairs must be >= 1, got {max_pairs}")
    if max_memories < 2:
        raise ValueError(f"max_memories must be >= 2, got {max_memories}")
    if not (1.0 <= length_ratio_max):
        raise ValueError(f"length_ratio_max must be >= 1.0, got {length_ratio_max}")

    conn = get_conn()
    try:
        if agent_name:
            rows = conn.execute(
                "SELECT id, content FROM memories "
                "WHERE agent_name = ? AND level != 'archived'"
                " AND LENGTH(TRIM(content)) > 10 "
                "ORDER BY id LIMIT ?",
                (agent_name, max_memories),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, content FROM memories "
                "WHERE level != 'archived' AND LENGTH(TRIM(content)) > 10 "
                "ORDER BY id LIMIT ?",
                (max_memories,),
            ).fetchall()
    finally:
        conn.close()

    scanned = len(rows)
    if scanned < 2:
        return {
            "duplicates_found": 0, "merged": 0, "pairs": [],
            "dry_run": dry_run, "scanned": scanned, "truncated": False,
        }

    # ── TF-IDF for all documents ──
    ids = [r["id"] for r in rows]
    contents = [r["content"] or "" for r in rows]
    lengths = [len(c) for c in contents]
    tfidf_docs = compute_tfidf(contents)

    # ── Pairwise comparison with length-ratio blocking ──
    pairs: List[Dict[str, Any]] = []
    removed: set = set()
    truncated = False
    comparisons = 0

    for i in range(scanned):
        if ids[i] in removed:
            continue
        for j in range(i + 1, scanned):
            if ids[j] in removed:
                continue
            # Length-ratio blocking — major speedup on skewed corpora
            li, lj = lengths[i], lengths[j]
            if li == 0 or lj == 0:
                continue
            ratio = max(li, lj) / min(li, lj)
            if ratio > length_ratio_max:
                continue
            comparisons += 1
            if comparisons > max_pairs:
                truncated = True
                break
            sim = cosine_sim(tfidf_docs[i], tfidf_docs[j])
            if sim >= threshold:
                if li >= lj:
                    kept, removed_id = ids[i], ids[j]
                else:
                    kept, removed_id = ids[j], ids[i]
                removed.add(removed_id)
                pairs.append({
                    "kept": kept,
                    "removed": removed_id,
                    "similarity": round(sim, 4),
                })
        if truncated:
            break

    duplicates_found = len(pairs)
    if dry_run or duplicates_found == 0:
        return {
            "duplicates_found": duplicates_found,
            "merged": 0,
            "pairs": pairs[:20] if dry_run else [],
            "dry_run": dry_run,
            "scanned": scanned,
            "truncated": truncated,
            "comparisons": comparisons,
        }

    # ── Execute merges using shared connection ──
    merged = 0
    errors: list = []
    merge_conn = get_conn()
    try:
        merge_conn.execute("BEGIN IMMEDIATE")
        for pair in pairs:
            try:
                _merge_memories_conn(
                    merge_conn, pair["removed"], pair["kept"],
                )
                merged += 1
            except Exception as e:
                errors.append({
                    "kept": pair["kept"],
                    "removed": pair["removed"],
                    "error": str(e)[:200],
                })
        if errors:
            logging.getLogger("memall.ops").warning(
                "deduplicate: %d / %d merges failed", len(errors), len(pairs)
            )
        merge_conn.commit()
    except Exception:
        merge_conn.rollback()
        raise
    finally:
        merge_conn.close()

    return {
        "duplicates_found": duplicates_found,
        "merged": merged,
        "pairs": pairs,
        "dry_run": False,
        "scanned": scanned,
        "truncated": truncated,
        "comparisons": comparisons,
        "errors": errors,
    }


# ══════════════════════════════════════════════════════════════════
# 6. Undo
# ══════════════════════════════════════════════════════════════════

def undo(op_id: int) -> Dict[str, Any]:
    """Undo a previously logged batch operation.

    Reads the ``before_snapshot`` stored in ``ops_log`` and restores each
    affected memory to its original state.  Fails if the operation was
    already undone.

    Args:
        op_id: ID in the ops_log table (returned by batch operations).

    Returns:
        dict: {undone, op_id, op_type, note}
    """
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT * FROM ops_log WHERE id = ?", (op_id,)
        ).fetchone()
        if not row:
            raise ValueError(f"op {op_id} not found in ops_log")
        if row["rolled_back_at"]:
            raise ValueError(
                f"op {op_id} was already undone at {row['rolled_back_at']}"
            )

        snapshot = json.loads(row["before_snapshot"])
        now_iso = datetime.now(timezone.utc).isoformat()
        conn.execute("BEGIN IMMEDIATE")
        restored = 0
        for mem_id_str, cols in snapshot.items():
            mem_id = int(mem_id_str)
            conn.execute(
                "UPDATE memories SET level=?, content=?, metadata=?, tags=?, "
                "updated_at=? WHERE id=?",
                (
                    cols["level"],
                    cols["content"],
                    cols["metadata"],
                    cols["tags"],
                    now_iso,
                    mem_id,
                ),
            )
            restored += 1
        conn.execute(
            "UPDATE ops_log SET rolled_back_at = ? WHERE id = ?",
            (now_iso, op_id),
        )
        conn.commit()
        return {
            "undone": restored,
            "op_id": op_id,
            "op_type": row["op_type"],
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
