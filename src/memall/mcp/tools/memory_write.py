import json

# KB enrichment hook (non-blocking). Callers can set via set_kb_enrich_hook(fn).
# fn(meta_dict) -> None; runs in a daemon thread, failures are swallowed.
_kb_enrich_hook = None
_kb_enrich_calls = 0

def set_kb_enrich_hook(fn):
    """Register a non-blocking KB enrichment function. Pass None to clear."""
    global _kb_enrich_hook
    _kb_enrich_hook = fn

def _fire_kb_enrich(meta: dict) -> None:
    global _kb_enrich_calls
    fn = _kb_enrich_hook
    if not callable(fn):
        return
    _kb_enrich_calls += 1
    try:
        import threading
        t = threading.Thread(target=lambda: fn(meta), daemon=True)
        t.start()
    except Exception:
        pass


from memall.config import get_config
from memall.core.db import get_conn
from memall.core.thin_waist import (
    smart_store,
    store_batch,
    update,
    normalize_agent_name,
    capture,
    _is_human_readable_content,
    _normalize_content_for_human_readability,
)
from memall.core.models import MemoryInput


def handle_smart_store(arguments: dict) -> str:
    if not (arguments.get("content") or "").strip():
        return json.dumps({"error": "content is required (non-empty string)"}, ensure_ascii=False)
    # Project inference is centralized in capture(); smart_store → capture() will
    # populate it automatically. Pass the caller's project through unchanged.
    project = arguments.get("project", "")
    result = smart_store(
        content=arguments["content"],
        owner=arguments.get("owner", ""),
        agent_name=arguments.get("agent_name", ""),
        subject=arguments.get("subject", ""),
        project=project,
        category=arguments.get("category", "general"),
        level=arguments.get("level", "P2"),
        dedup_threshold=arguments.get("dedup_threshold", 0.85),
    )
    return json.dumps(result, ensure_ascii=False)


def handle_store_batch(arguments: dict) -> str:
    items = arguments.get("items") or []
    if not items:
        return json.dumps({"error": "items is required (non-empty array)"}, ensure_ascii=False)
    result = store_batch(items)
    return json.dumps(result, ensure_ascii=False)


def handle_batch_capture(arguments: dict) -> str:
    """Capture multiple memories in a single operation for improved efficiency."""
    items = arguments.get("items") or []
    if not items:
        return json.dumps({"error": "items is required (non-empty array)"}, ensure_ascii=False)
    # Process each item
    results = []
    errors = []
    skipped_unreadable = 0

    for i, item in enumerate(items):
        try:
            # Validate required fields
            if not item.get("content"):
                errors.append({"index": i, "error": "content is required"})
                continue

            content = item["content"]
            ok_r, reason_r = _is_human_readable_content(content)
            if not ok_r:
                nc, changed, nr = _normalize_content_for_human_readability(content)
                if changed and nc and len(nc.strip()) >= 12:
                    content = nc
                elif get_config("quality.batch_reject_unreadable", get_config("quality.auto_reject_unreadable", False)):
                    skipped_unreadable += 1
                    errors.append({"index": i, "error": f"unreadable content ({reason_r})"})
                    continue
                # else: Day1/2 behavior — allow, capture() will log/warn

            # Create memory input
            memory_input = MemoryInput(
                content=content,
                owner=item.get("owner", ""),
                agent_name=item.get("agent_name", ""),
                subject=item.get("subject", ""),
                project=item.get("project", ""),
                category=item.get("category", "general"),
                level=item.get("level", "P2"),
                tags=json.dumps(item.get("tags", [])),
                confidence=item.get("confidence", 0.5),
                visibility=item.get("visibility", "private"),
                stream=item.get("stream", "ledger"),
                thread_id=item.get("thread_id")
            )

            # Capture the memory
            memory_id = capture(memory_input)
            row = {"index": i, "memory_id": int(memory_id), "status": getattr(memory_id, "status", "ok")}
            if hasattr(memory_id, "to_dict"):
                q = memory_id.to_dict().get("quality")
                if q: row["quality"] = q
                for k in ("readability","ingest_mode","source_pipeline"):
                    v = memory_id.to_dict().get(k)
                    if v is not None: row[k] = v
            results.append(row)
        except Exception as e:
            errors.append({
                "index": i,
                "error": str(e)
            })

    # Return results
    response = {
        "processed": len(results),
        "errors": len(errors),
        "skipped_unreadable": skipped_unreadable,
        "results": results
    }

    if errors:
        response["errors_detail"] = errors

    return json.dumps(response, ensure_ascii=False)




def handle_kb_ingest(arguments: dict) -> str:
    """memall_write action=kb_ingest / document_import — ingest document into Knowledge Base.

    KB entries are distinct from user-state memories:
    - category=knowledge (default) and source_pipeline=document.import
    - Strong provenance (doc_id, path, title, section, offset, hash, updated_at)
    - Hash/FTS dedup+update/supersede by doc version (conservative, non-destructive)
    - KB entries NOT conflated with user preferences/continuity state
    """
    import hashlib
    from datetime import datetime, timezone

    content = arguments.get("content") or ""
    if not content.strip():
        return json.dumps({"error": "content is required (non-empty string)"}, ensure_ascii=False)

    doc_id = str(arguments.get("doc_id") or "").strip()
    title = (arguments.get("title") or (doc_id[:80] if doc_id else "knowledge document")).strip()
    path = str(arguments.get("path") or "").strip()
    project = str(arguments.get("project") or "").strip()
    category = arguments.get("category", "knowledge") or "knowledge"
    chunk_size = max(200, int(arguments.get("chunk_size", 1200) or 1200))
    chunk_overlap = min(max(0, int(arguments.get("chunk_overlap", 120) or 120)), max(0, chunk_size - 1))
    doc_version = str(arguments.get("doc_version") or "").strip()
    updated_at = str(arguments.get("updated_at") or "").strip()
    source_pipeline = arguments.get("source_pipeline", "document.import") or "document.import"
    metadata = arguments.get("metadata", "{}")

    doc_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
    now = datetime.now(timezone.utc).isoformat()

    # Deterministic chunking (KB provenance): overlapping character windows.
    # Merge a tiny trailing chunk into the previous chunk to avoid unreadable fragments.
    chunks = []
    start_i = 0
    n = len(content)
    while start_i < n:
        end_i = min(start_i + chunk_size, n)
        chunk = content[start_i:end_i]
        if chunk.strip():
            chunks.append({"chunk_index": len(chunks), "content": chunk, "offset": start_i, "length": len(chunk)})
        if end_i >= n:
            break
        nxt = end_i - chunk_overlap
        if nxt <= start_i:
            nxt = end_i
        start_i = nxt
    if len(chunks) >= 2 and len(chunks[-1]["content"].strip()) < max(80, chunk_size // 8):
        prev = chunks[-2]
        merged = content[prev["offset"]: chunks[-1]["offset"] + chunks[-1]["length"]]
        chunks[-2] = {"chunk_index": prev["chunk_index"], "content": merged, "offset": prev["offset"], "length": len(merged)}
        chunks.pop()
        for i, c in enumerate(chunks):
            c["chunk_index"] = i

    from memall.core.db import get_conn
    from memall.core.thin_waist import capture, update as mem_update
    from memall.core.models import MemoryInput

    conn = get_conn()
    results = []
    errors = []
    deduped = 0
    superseded = 0

    def _find_existing(chunk_index: int):
        if not doc_id:
            return None
        # Active KB chunk for same doc+chunk_index
        rows = conn.execute(
            "SELECT id, metadata, memory_status FROM memories WHERE category='knowledge' ORDER BY id DESC LIMIT 500"
        ).fetchall()
        for r in rows:
            try:
                md = json.loads(r["metadata"] or "{}") if isinstance(r["metadata"], str) else (r["metadata"] or {})
            except Exception:
                md = {}
            if str(md.get("doc_id") or "") != doc_id:
                continue
            if int(md.get("chunk_index", -1)) != int(chunk_index):
                continue
            if str(r["memory_status"] or "") in ("archived", "superseded", "deleted"):
                continue
            return {"id": r["id"], "metadata": md, "memory_status": r["memory_status"]}

    for chunk_info in chunks:
        chunk = chunk_info["content"]
        offset = chunk_info["offset"]
        chunk_hash = hashlib.sha256(chunk.encode("utf-8")).hexdigest()

        existing = _find_existing(chunk_info["chunk_index"])
        if existing:
            em = existing.get("metadata") or {}
            if em.get("chunk_hash") == chunk_hash and (not doc_version or em.get("doc_version") == doc_version):
                deduped += 1
                results.append({
                    "chunk_index": chunk_info["chunk_index"],
                    "memory_id": existing["id"],
                    "status": "deduped",
                    "offset": offset,
                })
                continue
            # New version/content for same doc chunk: create new row and supersede old (non-destructive)
            try:
                mem_update(existing["id"], memory_status="superseded", invalid_at=now)
                superseded += 1
            except Exception as e:
                errors.append({"chunk_index": chunk_info["chunk_index"], "warn": f"failed to supersede {existing['id']}: {e}"})

        ingest_metadata = {
            "doc_id": doc_id,
            "doc_version": doc_version,
            "doc_hash": doc_hash,
            "chunk_hash": chunk_hash,
            "chunk_index": chunk_info["chunk_index"],
            "offset": offset,
            "total_chunks": len(chunks),
            "source_pipeline": source_pipeline,
            "category": "knowledge",
            "provenance": {"path": path, "title": title, "updated_at": updated_at},
            "kb_kind": "document_chunk",
            "kb_user_state": False,
        }
        if updated_at:
            ingest_metadata["updated_at"] = updated_at
        if existing and existing.get("id"):
            ingest_metadata["supersedes_memory_id"] = existing["id"]
        try:
            extra_meta = json.loads(metadata) if isinstance(metadata, str) else metadata
            if isinstance(extra_meta, dict):
                ingest_metadata.update(extra_meta)
        except Exception:
            pass

        memory_input = MemoryInput(
            content=chunk,
            owner=arguments.get("owner", "") or "",
            agent_name=arguments.get("agent_name", "kb_ingest") or "kb_ingest",
            subject=(title or doc_id or "knowledge")[:120],
            project=project,
            category=category,
            level=arguments.get("level", "P2") or "P2",
            metadata=json.dumps(ingest_metadata, ensure_ascii=False),
            tags=[t for t in [f"kb:{doc_id}" if doc_id else None, f"kb-chunk:{chunk_info['chunk_index']}", f"offset:{offset}", "source:document.import", f"docver:{doc_version}" if doc_version else None] if t] + (arguments.get("tags") or []),
            # valid_from/invalid_at not in MemoryInput ctor; KB validity handled via metadata/supersede status
        )

        try:
            mid = capture(memory_input)
            row_result = {
                "chunk_index": chunk_info["chunk_index"],
                "memory_id": int(mid),
                "content_preview": chunk[:120],
                "offset": offset,
                "status": getattr(mid, "status", "ok"),
            }
            if existing and existing.get("id"):
                row_result["supersedes"] = existing["id"]
            if hasattr(mid, "to_dict"):
                d = mid.to_dict()
                for k in ("quality", "readability", "ingest_mode", "source_pipeline"):
                    if d.get(k) is not None:
                        row_result[k] = d[k]
            results.append(row_result)
        except Exception as e:
            errors.append({"chunk_index": chunk_info["chunk_index"], "error": str(e)})

    conn.close()

    out = {
        "doc_id": doc_id,
        "title": title,
        "doc_hash": doc_hash,
        "doc_version": doc_version,
        "kb_chunks": len(chunks),
        "created_or_updated": len([r for r in results if r.get("status") != "deduped"]),
        "deduped": deduped,
        "superseded": superseded,
        "results": results,
        "errors": errors,
        "source_pipeline": source_pipeline,
        "category": category,
    }
    return json.dumps(out, ensure_ascii=False)


def _is_supervisor(agent: str) -> bool:
    """True if ``agent`` is listed in ``security.supervisor_agents``.

    Delegates to ``memall.core.task_identity`` — this was a third copy of the
    same list lookup (P2-4); a policy change here would otherwise have applied
    to the task gate and not to the ownership gate.
    """
    from memall.core.task_identity import is_supervisor
    return is_supervisor(agent)


def handle_update(arguments: dict) -> str:
    mem_id = arguments.get("memory_id")
    if not mem_id:
        return json.dumps({"error": "memory_id is required"}, ensure_ascii=False)
    fields = {k: v for k, v in arguments.items()
              if k not in ("action", "memory_id", "agent_name", "confirm", "dry_run", "sub_action")
              and v is not None}
    if not fields:
        return json.dumps({"error": "no updatable fields provided"}, ensure_ascii=False)

    # ── Cross-agent ownership enforcement (on by default) ───────────────
    # Without this gate any agent can overwrite another agent's memory *and*
    # reassign ``agent_name`` to itself — i.e. silently take ownership of it.
    # That is a privilege-boundary violation, so the gate is on by default;
    # supervisor/orchestrator agents that legitimately curate others' memories
    # are exempted explicitly via ``security.supervisor_agents``.
    if get_config("security.enforce_agent_ownership", True):
        caller = arguments.get("agent_name")
        if caller is None:
            # No identity supplied: unauthenticated, NOT trusted internal.
            # Reject rather than silently bypass the ownership gate (R3-5).
            return json.dumps({
                "error": "agent_name is required: an empty identity is rejected",
                "memory_id": mem_id,
            }, ensure_ascii=False)
        elif not str(caller).strip():
            # An explicitly empty identity cannot be attributed to any agent,
            # so the ownership gate has nothing to compare against — refuse
            # rather than silently skip it (the old ``if caller and ...``
            # short-circuited on "" and let an anonymous caller overwrite
            # another agent's memory).
            return json.dumps({
                "error": "agent_name is required: an empty identity is rejected",
                "memory_id": mem_id,
            }, ensure_ascii=False)
        else:
            caller = str(caller)
        if caller and not _is_supervisor(caller):
            conn = get_conn()
            try:
                row = conn.execute(
                    "SELECT agent_name FROM memories WHERE id = ?", (mem_id,)
                ).fetchone()
            finally:
                conn.close()
            if row and row["agent_name"]:
                owner = row["agent_name"]
                if owner != normalize_agent_name(caller):
                    return json.dumps({
                        "error": "ownership violation",
                        "message": f"memory {mem_id} belongs to agent '{owner}', "
                                   f"caller is '{caller}'. Add the caller to "
                                   f"security.supervisor_agents if it is a "
                                   f"supervisor, or set "
                                   f"security.enforce_agent_ownership=false.",
                        "memory_id": mem_id,
                    }, ensure_ascii=False)

    # ── Pinned classification fields (R3-9) ─────────────────────────────
    # ``level`` and ``category`` are contracts with downstream consumers, not
    # free-form labels: notify_pending_tasks() selects category='task', the
    # goal driver selects category='goal', L10 reports are selected by
    # level='L10'.  The pipeline classifiers already honour
    # memall.core.pins.is_field_pinned, but this USER path did not — so a
    # caller could rewrite its own row from category='decision' to
    # category='goal' and thereby mint a goal card that bypasses the
    # double-proof delete guard, or reclassify a task out of the reminder
    # chain.  The same contract now applies here.
    if any(f in fields for f in ("level", "category")):
        from memall.core.pins import CONTRACT_CATEGORIES

        conn = get_conn()
        try:
            row = conn.execute(
                "SELECT category, level, metadata FROM memories WHERE id = ?", (mem_id,)
            ).fetchone()
        finally:
            conn.close()
        if row is not None:
            current_cat = row["category"] or ""
            new_cat = fields.get("category")
            protected = current_cat in CONTRACT_CATEGORIES or (
                isinstance(new_cat, str) and new_cat in CONTRACT_CATEGORIES)
            if protected:
                return json.dumps({
                    "error": "pinned field",
                    "message": (
                        f"memory {mem_id} has category '{current_cat}': level and "
                        "category are contracts with downstream consumers and "
                        "cannot be rewritten through update(). Create a new row "
                        "instead, or use the pipeline's own transition."
                    ),
                    "memory_id": mem_id,
                    "fields": sorted(f for f in ("level", "category") if f in fields),
                }, ensure_ascii=False)

    # Ownership is not a casual field: ``agent_name`` identifies the *caller*
    # (used by the gate above) and must never be written through this path,
    # otherwise any agent could re-assign a memory to itself.  ``confirm``/
    # ``dry_run`` are control flags, not row columns.
    fields = {k: v for k, v in arguments.items()
              if k not in ("memory_id", "agent_name", "confirm", "dry_run")
              and v is not None}
    ok = update(mem_id, **fields)
    if ok and "metadata" in fields:
        # A task card that just changed state must not stay in the agent's
        # cached view (auto_inject caches per agent for 300s).  The cache-hit
        # path already refreshes the task list, but dropping the whole entry
        # also rebuilds the counts shown in the injected context.
        try:
            from memall.mcp.federation_tools import invalidate_inject_cache
            conn = get_conn()
            try:
                row = conn.execute(
                    "SELECT agent_name FROM memories WHERE id = ?", (mem_id,)).fetchone()
            finally:
                conn.close()
            if row and row["agent_name"]:
                invalidate_inject_cache(row["agent_name"])
        except Exception:
            pass
    return json.dumps({"memory_id": mem_id, "updated": ok, "fields": list(fields.keys())})
