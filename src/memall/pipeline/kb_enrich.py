"""KB async enrichment worker (P2-4).

Registers a non-blocking hook for KB ingest. Failures are swallowed; hook is
config-gated. Enrichment is best-effort and never blocks capture.

P2-4 真异步队列骨架（当前）：daemon-thread hook + in-process idempotency/统计。
后续可替换为持久化队列/后台 worker（幂等 key: doc_id+chunk_hash+doc_version）。
"""
from __future__ import annotations

import logging
import threading
import sqlite3
from typing import Any

log = logging.getLogger(__name__)

_LOCK = threading.Lock()
_SEEN_KEYS: set[str] = set()
_MAX_SEEN = 4096
_STATS: dict[str, Any] = {
    "fired": 0,
    "ok": 0,
    "failed": 0,
    "skipped": 0,
    "deduped": 0,
    "last_error": "",
}


def kb_enrich_stats() -> dict[str, Any]:
    """Return hook stats plus optional durable pipeline_jobs queue metrics.

    Pipeline metrics are best-effort; errors are swallowed and omitted.
    """
    result: dict[str, Any] = {**_STATS, "seen_keys": len(_SEEN_KEYS)}
    # Observability: autostarted worker state (additive; read-only). Defined
    # below in the worker-lifecycle section; guarded for partial import states.
    try:
        result["worker"] = kb_enrich_worker_status()
    except Exception:
        pass
    try:
        from memall.config import get_config as _gc
        if str(_gc("kb.enrich_worker.backend", "sqlite+queue")).strip() != "sqlite+queue":
            return result
        from memall.core.db import get_conn
        conn = get_conn()
        try:
            _ensure_pipeline_jobs_schema(conn)
            rows = conn.execute(
                """SELECT
                      COUNT(*) AS total,
                      SUM(CASE WHEN status='queued' THEN 1 ELSE 0 END) AS queued,
                      SUM(CASE WHEN status='running' THEN 1 ELSE 0 END) AS running,
                      SUM(CASE WHEN status IN ('ok','completed') THEN 1 ELSE 0 END) AS ok,
                      SUM(CASE WHEN status='failed' THEN 1 ELSE 0 END) AS failed,
                      SUM(CASE WHEN status='running' AND claimed_at IS NOT NULL THEN 1 ELSE 0 END) AS stale_running,
                      MAX(CASE WHEN status IN ('ok','completed') THEN completed_at END) AS last_completed_at,
                      MAX(CASE WHEN status='failed' THEN error_msg END) AS last_error
                   FROM pipeline_jobs
                   WHERE kind='kb_enrich'
                   LIMIT 1"""
            ).fetchone()
            if rows:
                total = int(rows[0] or 0)
                queued = int(rows[1] or 0)
                running = int(rows[2] or 0)
                ok = int(rows[3] or 0)
                failed = int(rows[4] or 0)
                result["pipeline"] = {
                    "total": total,
                    "queued": queued,
                    "running": running,
                    "ok": ok,
                    "failed": failed,
                    "completed": ok,
                    "queue_depth": queued + running,
                    "last_completed_at": rows[6] if len(rows) > 6 else None,
                    "last_error": rows[7] if len(rows) > 7 else None,
                }
                if failed:
                    # Include up to 3 recent failed errors
                    recent = conn.execute(
                        """SELECT error_msg FROM pipeline_jobs
                           WHERE kind='kb_enrich' AND status='failed'
                           ORDER BY completed_at DESC, id DESC
                           LIMIT 3"""
                    ).fetchall()
                    result["pipeline"]["recent_errors"] = [r[0] for r in recent if r[0]]
                # Actionable health signal: raw counters require a human to
                # interpret them, so a backlog or a stuck lease went unnoticed.
                # Surface a verdict plus the thresholds that produced it.
                try:
                    from memall.config import get_config as _gc
                    _max_depth = int(_gc("kb.enrich_worker.alert_queue_depth", 100) or 100)
                    _max_stale = int(_gc("kb.enrich_worker.alert_stale_running", 1) or 1)
                    _alerts = []
                    if result["pipeline"]["queue_depth"] > _max_depth:
                        _alerts.append({
                            "code": "queue_backlog",
                            "detail": f"queue_depth={result['pipeline']['queue_depth']} > {_max_depth}",
                        })
                    _stale = int(rows[5] or 0) if len(rows) > 5 else 0
                    if _stale > _max_stale:
                        _alerts.append({
                            "code": "stale_running",
                            "detail": f"{_stale} job(s) held past claim_ttl",
                        })
                    if int(rows[4] or 0) and not (result["pipeline"].get("last_completed_at")):
                        _alerts.append({
                            "code": "failures_without_success",
                            "detail": f"failed={rows[4]} and never completed",
                        })
                    result["pipeline"]["health"] = {
                        "ok": not _alerts,
                        "alerts": _alerts,
                        "thresholds": {
                            "alert_queue_depth": _max_depth,
                            "alert_stale_running": _max_stale,
                        },
                    }
                except Exception:
                    pass
        finally:
            conn.close()
    except Exception:
        pass
    return result


def _enabled() -> bool:
    try:
        from memall.config import get_config
        return bool(get_config("kb.enrich_async", True))
    except Exception:
        return True


def _idempotency_key(meta: dict) -> str:
    if not isinstance(meta, dict):
        return ""
    parts = [
        str(meta.get("doc_id") or ""),
        str(meta.get("chunk_hash") or ""),
        str(meta.get("doc_version") or ""),
        str(meta.get("chunk_index") or ""),
    ]
    if not any(parts):
        return ""
    return "|".join(parts)


def _mark_seen(key: str) -> bool:
    """Return True if first time seen (should process)."""
    if not key:
        return True
    with _LOCK:
        if key in _SEEN_KEYS:
            return False
        if len(_SEEN_KEYS) >= _MAX_SEEN:
            # bounded memory: drop oldest-ish by clearing half (simple, deterministic enough)
            for k in list(_SEEN_KEYS)[: _MAX_SEEN // 2]:
                _SEEN_KEYS.discard(k)
        _SEEN_KEYS.add(key)
        return True


def kb_enrich_handler(meta: dict) -> None:
    """Best-effort KB enrichment. Runs in daemon thread via memory_write hook."""
    with _LOCK:
        _STATS["fired"] += 1
    if not _enabled():
        with _LOCK:
            _STATS["skipped"] += 1
        return
    try:
        if not isinstance(meta, dict):
            with _LOCK:
                _STATS["skipped"] += 1
            return
        key = _idempotency_key(meta)
        if not _mark_seen(key):
            with _LOCK:
                _STATS["deduped"] += 1
            return
        # Lightweight, non-destructive enrichment placeholder.
        # Heavy LLM/entity/embedding enrichment should be added behind explicit
        # config and a durable queue/worker with retry limits.
        _ = meta.get("doc_id"), meta.get("chunk_hash"), meta.get("source_pipeline")
        with _LOCK:
            _STATS["ok"] += 1
            _STATS["last_error"] = ""
    except Exception as e:
        with _LOCK:
            _STATS["failed"] += 1
            _STATS["last_error"] = str(e)[:300]
        log.debug("kb_enrich_handler failed: %s", e)


def register_default_kb_enrich_hook() -> bool:
    try:
        from memall.mcp.tools.memory_write import set_kb_enrich_hook
        set_kb_enrich_hook(kb_enrich_handler)
        return True
    except Exception:
        return False

# ── Durable pipeline job worker (P2-4) ──

def _now_iso() -> str:
    """Current UTC ISO-8601 timestamp."""
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def _claim_job(conn, claim_ttl_seconds: int = 300) -> int | None:
    """Atomically claim a queued kb_enrich job.
    Respects next_retry_at TTL, claimed_at lease, cross-process dedup.
    Returns job id if claimed, None if none eligible.
    """
    from datetime import datetime, timezone, timedelta
    now_dt = datetime.now(timezone.utc)
    now = now_dt.isoformat()
    ttl_cutoff = (now_dt - timedelta(seconds=int(claim_ttl_seconds or 300))).isoformat()
    row = conn.execute(
        """SELECT id, doc_id, chunk_hash, doc_version, chunk_index FROM pipeline_jobs
           WHERE kind='kb_enrich' AND status='queued'
             AND (next_retry_at IS NULL OR next_retry_at <= ?)
             AND (claimed_at IS NULL OR claimed_at <= ?)
           ORDER BY created_at ASC
           LIMIT 1""",
        (now, ttl_cutoff),
    ).fetchone()
    if row is None:
        return None
    job_id, doc_id, chunk_hash, doc_version, chunk_index = row
    # Cross-process idempotency: skip if same key already completed
    if any(v is not None for v in (doc_id, chunk_hash, doc_version, chunk_index)):
        done = conn.execute(
            """SELECT 1 FROM pipeline_jobs
               WHERE kind='kb_enrich'
                 AND (doc_id = ? OR (doc_id IS NULL AND ? IS NULL))
                 AND (chunk_hash = ? OR (chunk_hash IS NULL AND ? IS NULL))
                 AND (doc_version = ? OR (doc_version IS NULL AND ? IS NULL))
                 AND (chunk_index = ? OR (chunk_index IS NULL AND ? IS NULL))
                 AND status IN ('ok','completed')
                 AND id != ?
               LIMIT 1""",
            (doc_id, doc_id, chunk_hash, chunk_hash, doc_version, doc_version, chunk_index, chunk_index, job_id),
        ).fetchone()
        if done:
            # CAS: only dedup the row if it is still queued (another process may
            # have claimed/completed it between our SELECT and this UPDATE).
            cur = conn.execute(
                """UPDATE pipeline_jobs
                      SET status='ok', result='deduped', handled_by=NULL, completed_at=?
                    WHERE id=? AND status='queued'""",
                (now, job_id),
            )
            conn.commit()
            if cur.rowcount != 1:
                # Row changed underneath us (claimed/completed elsewhere); retry.
                return _claim_job(conn, claim_ttl_seconds)
            return _claim_job(conn, claim_ttl_seconds)
    # CAS claim: the conditional WHERE keeps two processes from claiming the
    # same row. Only the worker whose UPDATE matches (rowcount==1) owns the job.
    cur = conn.execute(
        """UPDATE pipeline_jobs
           SET status='running', handled_by=?, claimed_at=?
         WHERE id=? AND status='queued'
           AND (next_retry_at IS NULL OR next_retry_at <= ?)
           AND (claimed_at IS NULL OR claimed_at <= ?)""",
        ('__worker__', now, job_id, now, ttl_cutoff),
    )
    conn.commit()
    if cur.rowcount != 1:
        # Lost the race (another process claimed it first); try the next job.
        return _claim_job(conn, claim_ttl_seconds)
    return job_id


def _mark_complete(conn, job_id: int, result: str = "ok") -> None:
    """Mark job as ok or failed. Always clears handled_by."""
    now = _now_iso()
    if result == "ok" or not result:
        conn.execute(
            """UPDATE pipeline_jobs
               SET status='ok', result=?, handled_by=NULL, completed_at=?
             WHERE id=?""",
            ("ok", now, job_id),
        )
    else:
        conn.execute(
            """UPDATE pipeline_jobs
               SET status='failed', error_msg=?, handled_by=NULL, completed_at=?
             WHERE id=?""",
            (result, now, job_id),
        )
    conn.commit()


def _ensure_pipeline_jobs_schema(conn) -> None:
    """Best-effort: add missing columns for existing pipeline_jobs DBs."""
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(pipeline_jobs)").fetchall()}
        changed = False
        if "claimed_at" not in cols:
            conn.execute("ALTER TABLE pipeline_jobs ADD COLUMN claimed_at TEXT DEFAULT NULL")
            changed = True
        if "created_at" not in cols:
            conn.execute("ALTER TABLE pipeline_jobs ADD COLUMN created_at TEXT DEFAULT NULL")
            changed = True
        if changed:
            conn.commit()
    except Exception:
        pass


def kb_enrich_worker_loop(poll_interval_seconds: int = 2,
                          max_attempts: int = 3,
                          base_backoff_seconds: int = 5,
                          max_backoff_seconds: int = 300,
                          claim_ttl_seconds: int = 300) -> None:
    """Background worker: poll pipeline_jobs and process kb_enrich jobs.
    Exponential backoff; cross-process idempotent claim; best-effort.
    """
    import time as _time
    import json as _json
    from memall.core.db import get_conn
    from memall.config import get_config as _gc

    cfg_backend = _gc("kb.enrich_worker.backend", "sqlite+queue")
    if str(cfg_backend).strip() != "sqlite+queue":
        log.info("kb_enrich worker: backend not sqlite+queue — exiting")
        return

    backoff = base_backoff_seconds
    # Cooperative shutdown: loop condition + Event.wait() instead of sleep()
    # so stop_kb_enrich_worker() wakes an idle worker immediately (bounded).
    while not _worker_stop.is_set():
        conn = None
        try:
            conn = get_conn()
            _ensure_pipeline_jobs_schema(conn)
            job_id = _claim_job(conn, claim_ttl_seconds)
            if job_id is not None:
                job = conn.execute(
                    """SELECT doc_id, chunk_hash, doc_version, chunk_index,
                              source_pipeline, metadata, attempts, max_attempts, error_msg
                       FROM pipeline_jobs WHERE id=?""",
                    (job_id,),
                ).fetchone()
                if job:
                    meta = {
                        "doc_id": job[0],
                        "chunk_hash": job[1],
                        "doc_version": job[2],
                        "chunk_index": job[3],
                        "source_pipeline": job[4],
                    }
                    meta_raw = job[5]
                    if isinstance(meta_raw, str):
                        try:
                            parsed = _json.loads(meta_raw)
                            if isinstance(parsed, dict):
                                meta.update(parsed)
                        except Exception:
                            pass
                    try:
                        kb_enrich_handler(meta)
                        _mark_complete(conn, job_id, "ok")
                        backoff = base_backoff_seconds
                    except Exception as e:
                        attempts = int(job[6] or 0)
                        max_att = int(job[7] or max_attempts)
                        if attempts + 1 >= max_att:
                            _mark_complete(conn, job_id, f"exceeded_max_attempts({max_att}): {e}")
                        else:
                            next_retry = _time.time() + backoff
                            next_iso = _time.strftime("%Y-%m-%dT%H:%M:%S", _time.gmtime(next_retry)) + "Z"
                            conn.execute(
                                """UPDATE pipeline_jobs
                                   SET status='queued', attempts=attempts+1,
                                       next_retry_at=?, handled_by=NULL, error_msg=?
                                 WHERE id=?""",
                                (next_iso, str(e)[:300], job_id),
                            )
                            conn.commit()
                            backoff = min(max_backoff_seconds, max(base_backoff_seconds, backoff * 2))
            else:
                _worker_stop.wait(max(0.05, float(poll_interval_seconds)))
        except Exception as e:
            log.debug("kb_enrich worker loop error: %s", e)
            _worker_stop.wait(max(0.05, float(poll_interval_seconds)))
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass
    log.info("kb_enrich worker: stop requested — exiting loop")


def start_kb_enrich_worker(**kwargs) -> threading.Thread:
    """Start the kb_enrich worker loop in a daemon thread."""
    t = threading.Thread(
        target=kb_enrich_worker_loop,
        daemon=True,
        name="kb-enrich-worker",
        kwargs=kwargs,
    )
    t.start()
    return t


def reprocess_kb_enrich(args: dict | None = None) -> dict:
    """Reprocess pipeline_jobs: retry failed jobs, or process all uncompleted jobs.

    args keys (all optional):
      doc_id: str | None
      failed_only: bool  (default True)
      all: bool          (default False)
    Returns summary dict.
    """
    import json
    from memall.core.db import get_conn

    args = args or {}
    doc_id = args.get("doc_id")
    failed_only = args.get("failed_only", True)
    all_flag = args.get("all", False)

    conn = None
    try:
        conn = get_conn()
        _ensure_pipeline_jobs_schema(conn)

        # Build WHERE
        if all_flag:
            where = "kind='kb_enrich' AND status IN ('queued','running','failed')"
        elif failed_only:
            where = "kind='kb_enrich' AND status='failed'"
        else:
            where = "kind='kb_enrich' AND status IN ('queued','failed')"

        if doc_id:
            where += " AND doc_id=?"

        rows = conn.execute(
            f"""SELECT id, doc_id, chunk_hash, doc_version, chunk_index,
                       source_pipeline, metadata, max_attempts
                 FROM pipeline_jobs WHERE {where}
                 ORDER BY created_at ASC""",
            [doc_id] if doc_id else [],
        ).fetchall()

        processed = 0
        ok = 0
        failed = 0
        for row in rows:
            # Reset job to queued so the normal claim loop can pick it up
            conn.execute(
                """UPDATE pipeline_jobs
                   SET status='queued', attempts=0, next_retry_at=?, handled_by=NULL
                 WHERE id=?""",
                (_now_iso(), row[0]),
            )
            processed += 1

        conn.commit()

        # Trigger a bounded one-shot pass in a background thread (best-effort).
        # Does NOT start the infinite worker loop; processes up to a small budget.
        import threading as _t
        import time as _time

        def _run_once():
            _time.sleep(0.2)
            budget = max(1, min(int(args.get("process_budget", 50) or 50), 500))
            processed_pass = 0
            try:
                conn2 = get_conn()
                try:
                    _ensure_pipeline_jobs_schema(conn2)
                    for _ in range(budget):
                        job_id = _claim_job(conn2, claim_ttl_seconds=300)
                        if job_id is None:
                            break
                        job = conn2.execute(
                            """SELECT doc_id,chunk_hash,doc_version,chunk_index,
                                      source_pipeline,metadata,attempts,max_attempts,error_msg
                               FROM pipeline_jobs WHERE id=?""",
                            (job_id,),
                        ).fetchone()
                        if not job:
                            break
                        meta = {
                            "doc_id": job[0], "chunk_hash": job[1], "doc_version": job[2],
                            "chunk_index": job[3], "source_pipeline": job[4],
                        }
                        meta_raw = job[5]
                        if isinstance(meta_raw, str):
                            try:
                                import json as _json
                                parsed = _json.loads(meta_raw)
                                if isinstance(parsed, dict):
                                    meta.update(parsed)
                            except Exception:
                                pass
                        try:
                            kb_enrich_handler(meta)
                            _mark_complete(conn2, job_id, "ok")
                            processed_pass += 1
                        except Exception as e:
                            attempts = int(job[6] or 0)
                            max_att = int(job[7] or 3)
                            if attempts + 1 >= max_att:
                                _mark_complete(conn2, job_id, f"exceeded_max_attempts({max_att}): {e}")
                            else:
                                next_iso = _now_iso()
                                conn2.execute(
                                    """UPDATE pipeline_jobs
                                       SET status='queued', attempts=attempts+1,
                                           next_retry_at=?, handled_by=NULL, error_msg=?
                                     WHERE id=?""",
                                    (next_iso, str(e)[:300], job_id),
                                )
                                conn2.commit()
                                break
                finally:
                    try: conn2.close()
                    except Exception: pass
            except Exception:
                pass

        t = _t.Thread(target=_run_once, daemon=True, name="kb-reprocess-once")
        t.start()

        return {
            "ok": True,
            "reprocessed": processed,
            "doc_id": doc_id,
            "failed_only": failed_only,
            "all": all_flag,
            "triggered_worker": True, "trigger_mode": "bounded_once",
        }
    except Exception as e:
        return {"ok": False, "error": str(e)[:300]}
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass

# ── Worker lifecycle (single-thread-per-process, config-gated) ──

_worker_lock = threading.Lock()
_worker_thread: threading.Thread | None = None
_worker_started: bool = False
_worker_stop = threading.Event()   # cooperative shutdown signal for the loop


def stop_kb_enrich_worker(timeout_seconds: float = 5.0) -> dict:
    """Cooperatively stop the autostarted worker (bounded, non-blocking-ish).

    Sets the stop event, wakes the loop from its poll sleep, and joins with a
    bounded timeout. Safe to call even when no worker is running. Returns a
    small observability dict (additive; never raises).
    """
    global _worker_thread, _worker_started
    _worker_stop.set()
    with _worker_lock:
        t = _worker_thread
    joined = False
    alive = False
    if t is not None:
        try:
            t.join(timeout=max(0.0, float(timeout_seconds)))
            joined = True
            alive = t.is_alive()
        except Exception:
            alive = t.is_alive() if t is not None else False
        if not alive:
            with _worker_lock:
                if _worker_thread is t:
                    _worker_thread = None
                    _worker_started = False
    return {
        "stop_requested": True,
        "thread_was_running": t is not None,
        "joined": joined,
        "still_alive": alive,
        "timeout_seconds": float(timeout_seconds),
    }


def kb_enrich_worker_status() -> dict:
    """Observability snapshot for the autostarted worker (read-only, additive)."""
    with _worker_lock:
        t = _worker_thread
    return {
        "started": bool(_worker_started),
        "alive": bool(t is not None and t.is_alive()),
        "thread_name": t.name if t is not None else None,
        "stop_requested": _worker_stop.is_set(),
    }


def ensure_kb_enrich_worker_started(**overrides) -> bool:
    """Start the durable worker once per process if config allows it.

    Returns True if a worker thread is running (started now or previously).
    Safe to call repeatedly; config-gated and non-blocking.
    """
    global _worker_thread, _worker_started
    try:
        from memall.config import get_config as _gc
        if not bool(_gc("kb.enrich_async", True)):
            return False
        backend = str(_gc("kb.enrich_worker.backend", "sqlite+queue")).strip()
        if backend != "sqlite+queue":
            return False
        if not bool(_gc("kb.enrich_worker.autostart", False)):
            return False
    except Exception:
        return False
    with _worker_lock:
        if _worker_thread is not None and _worker_thread.is_alive():
            return True
        # Clear a previous cooperative stop before (re)starting a fresh loop.
        _worker_stop.clear()
        kwargs = {
            "poll_interval_seconds": int(overrides.get("poll_interval_seconds", 2) or 2),
            "max_attempts": int(overrides.get("max_attempts", 3) or 3),
            "base_backoff_seconds": int(overrides.get("base_backoff_seconds", 5) or 5),
            "max_backoff_seconds": int(overrides.get("max_backoff_seconds", 300) or 300),
            "claim_ttl_seconds": int(overrides.get("claim_ttl_seconds", 300) or 300),
        }
        try:
            from memall.config import get_config as _gc
            for k in ("poll_interval_seconds", "max_attempts", "base_backoff_seconds", "max_backoff_seconds", "claim_ttl_seconds"):
                v = _gc(f"kb.enrich_worker.{k}", None)
                if v is not None and k not in overrides:
                    kwargs[k] = int(v)
        except Exception:
            pass
        _worker_thread = start_kb_enrich_worker(**kwargs)
        _worker_started = True
        return True
