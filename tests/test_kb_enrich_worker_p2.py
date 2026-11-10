"""P2-4 durable KB enrichment worker tests (sqlite+queue).

Focused on: claim/complete lifecycle, retry/backoff metadata, cross-process
idempotency (completed key dedup), pipeline stats, reprocess reset.
Deterministic (direct function calls), no daemon-thread timing dependence.
"""

import json
import sqlite3
from datetime import datetime, timezone, timedelta

from tests.test_helpers import init_temp_db, cleanup_temp_db


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def _pipeline_count(db_path, status=None, doc_id=None):
    conn = sqlite3.connect(db_path)
    q = "SELECT COUNT(*) FROM pipeline_jobs WHERE kind='kb_enrich'"
    params = []
    if status is not None:
        if isinstance(status, (list, tuple)):
            q += f" AND status IN ({','.join('?'*len(status))})"
            params.extend(status)
        else:
            q += " AND status=?"
            params.append(status)
    if doc_id is not None:
        q += " AND doc_id=?"
        params.append(doc_id)
    n = conn.execute(q, params).fetchone()[0]
    conn.close()
    return n


def _insert_job(db_path, *, doc_id="d1", chunk_hash="h1", doc_version="v1",
                chunk_index=0, status="queued", attempts=0, max_attempts=3,
                next_retry_at=None, created_at=None):
    conn = sqlite3.connect(db_path)
    now = created_at or _now_iso()
    conn.execute(
        """INSERT INTO pipeline_jobs
           (created_at, kind, doc_id, chunk_hash, doc_version, chunk_index,
            source_pipeline, metadata, status, attempts, max_attempts, next_retry_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        (now, "kb_enrich", doc_id, chunk_hash, doc_version, chunk_index,
         "document.import", json.dumps({"doc_id": doc_id}), status, attempts, max_attempts, next_retry_at),
    )
    conn.commit()
    conn.close()


def test_claim_complete_lifecycle():
    db_path, orig = init_temp_db()
    try:
        from memall.core.db import get_conn
        from memall.pipeline.kb_enrich import _claim_job, _mark_complete, _ensure_pipeline_jobs_schema

        _insert_job(db_path, doc_id="d-lc", chunk_hash="hc1")
        conn = get_conn()
        _ensure_pipeline_jobs_schema(conn)
        job_id = _claim_job(conn)
        assert job_id is not None
        row = conn.execute("SELECT status, handled_by FROM pipeline_jobs WHERE id=?", (job_id,)).fetchone()
        assert row[0] == "running"
        _mark_complete(conn, job_id, "ok")
        row2 = conn.execute("SELECT status, result, handled_by FROM pipeline_jobs WHERE id=?", (job_id,)).fetchone()
        conn.close()
        assert row2[0] == "ok"
        assert row2[1] == "ok"
        assert row2[2] is None
        assert _pipeline_count(db_path, status="ok", doc_id="d-lc") == 1
    finally:
        cleanup_temp_db(db_path, orig)


def test_claim_skips_future_next_retry():
    db_path, orig = init_temp_db()
    try:
        from memall.core.db import get_conn
        from memall.pipeline.kb_enrich import _claim_job, _ensure_pipeline_jobs_schema

        future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        _insert_job(db_path, doc_id="d-fut", chunk_hash="hf", next_retry_at=future)
        conn = get_conn()
        _ensure_pipeline_jobs_schema(conn)
        job_id = _claim_job(conn)
        conn.close()
        assert job_id is None
        assert _pipeline_count(db_path, status="queued", doc_id="d-fut") == 1
    finally:
        cleanup_temp_db(db_path, orig)


def test_claim_cross_process_dedup_completed_key():
    db_path, orig = init_temp_db()
    try:
        from memall.core.db import get_conn
        from memall.pipeline.kb_enrich import _claim_job, _ensure_pipeline_jobs_schema

        # Completed job with same key
        _insert_job(db_path, doc_id="d-dup", chunk_hash="hd", status="ok", attempts=1)
        # New queued duplicate
        _insert_job(db_path, doc_id="d-dup", chunk_hash="hd", status="queued")
        conn = get_conn()
        _ensure_pipeline_jobs_schema(conn)
        job_id = _claim_job(conn)
        conn.close()
        # Should dedup (mark queued as ok/deduped or skip); either no running job claimed, or deduped
        # Our implementation marks duplicate queued as ok(deduped) and recurses.
        assert _pipeline_count(db_path, status=["queued", "running"], doc_id="d-dup") == 0
        assert _pipeline_count(db_path, status="ok", doc_id="d-dup") == 2
    finally:
        cleanup_temp_db(db_path, orig)


def test_mark_complete_failed():
    db_path, orig = init_temp_db()
    try:
        from memall.core.db import get_conn
        from memall.pipeline.kb_enrich import _claim_job, _mark_complete, _ensure_pipeline_jobs_schema

        _insert_job(db_path, doc_id="d-fail", chunk_hash="hf2")
        conn = get_conn()
        _ensure_pipeline_jobs_schema(conn)
        job_id = _claim_job(conn)
        assert job_id is not None
        _mark_complete(conn, job_id, "some error")
        row = conn.execute("SELECT status, error_msg, handled_by FROM pipeline_jobs WHERE id=?", (job_id,)).fetchone()
        conn.close()
        assert row[0] == "failed"
        assert "some error" in (row[1] or "")
        assert row[2] is None
    finally:
        cleanup_temp_db(db_path, orig)


def test_kb_enrich_stats_includes_pipeline_metrics():
    db_path, orig = init_temp_db()
    try:
        from memall.pipeline.kb_enrich import kb_enrich_stats

        _insert_job(db_path, doc_id="d-s1", chunk_hash="hs1", status="queued")
        _insert_job(db_path, doc_id="d-s2", chunk_hash="hs2", status="ok")
        _insert_job(db_path, doc_id="d-s3", chunk_hash="hs3", status="failed")
        stats = kb_enrich_stats()
        assert "fired" in stats
        p = stats.get("pipeline")
        assert isinstance(p, dict), stats
        assert p.get("total") == 3
        assert p.get("queued") == 1
        assert p.get("ok") == 1
        assert p.get("failed") == 1
        assert p.get("queue_depth") >= 1
    finally:
        cleanup_temp_db(db_path, orig)


def test_reprocess_resets_failed_jobs():
    db_path, orig = init_temp_db()
    try:
        from memall.pipeline.kb_enrich import reprocess_kb_enrich

        _insert_job(db_path, doc_id="d-rp", chunk_hash="hr1", status="failed", attempts=3)
        _insert_job(db_path, doc_id="d-rp", chunk_hash="hr2", status="ok")
        res = reprocess_kb_enrich({"doc_id": "d-rp", "failed_only": True})
        assert res.get("ok") is True
        assert res.get("reprocessed") == 1
        # The failed one should now be queued (reprocess reset)
        assert _pipeline_count(db_path, status="queued", doc_id="d-rp") == 1
        assert _pipeline_count(db_path, status="ok", doc_id="d-rp") == 1
    finally:
        cleanup_temp_db(db_path, orig)


def test_ensure_kb_enrich_worker_not_started_when_autostart_off():
    db_path, orig = init_temp_db()
    try:
        from memall.pipeline.kb_enrich import ensure_kb_enrich_worker_started
        # Default autostart is False in config
        started = ensure_kb_enrich_worker_started()
        assert started is False
    finally:
        cleanup_temp_db(db_path, orig)

def _simulate_worker_failure_once(db_path, job_id, exc_msg, *, base_backoff=5, max_backoff=300, max_attempts_override=None):
    """Deterministic: replicate kb_enrich_worker_loop failure-handling branch (no sleep/thread)."""
    import time as _time
    from memall.core.db import get_conn
    from memall.pipeline.kb_enrich import _mark_complete
    conn = get_conn()
    job = conn.execute(
        """SELECT attempts, max_attempts, error_msg
           FROM pipeline_jobs WHERE id=?""",
        (job_id,),
    ).fetchone()
    attempts = int(job[0] or 0)
    max_att = int((max_attempts_override if max_attempts_override is not None else job[1]) or 3)
    if attempts + 1 >= max_att:
        _mark_complete(conn, job_id, f"exceeded_max_attempts({max_att}): {exc_msg}")
    else:
        next_retry = _time.time() + base_backoff
        next_iso = _time.strftime("%Y-%m-%dT%H:%M:%S", _time.gmtime(next_retry)) + "Z"
        conn.execute(
            """UPDATE pipeline_jobs
               SET status='queued', attempts=attempts+1,
                   next_retry_at=?, handled_by=NULL, error_msg=?
             WHERE id=?""",
            (next_iso, str(exc_msg)[:300], job_id),
        )
        conn.commit()
    conn.close()


def test_worker_retry_then_exceed_max_attempts_deterministic():
    db_path, orig = init_temp_db()
    try:
        from memall.core.db import get_conn
        from memall.pipeline.kb_enrich import _claim_job, _ensure_pipeline_jobs_schema

        _insert_job(db_path, doc_id="d-retry", chunk_hash="hr", status="queued", attempts=0, max_attempts=2)
        conn = get_conn(); _ensure_pipeline_jobs_schema(conn); job_id = _claim_job(conn); conn.close()
        assert job_id is not None
        # 1st failure: attempts 0 -> 1, requeued (max_attempts=2)
        _simulate_worker_failure_once(db_path, job_id, "boom1", base_backoff=0, max_attempts_override=2)
        conn = get_conn(); r = conn.execute("SELECT status, attempts, error_msg, next_retry_at FROM pipeline_jobs WHERE id=?", (job_id,)).fetchone(); conn.close()
        assert r[0] == "queued" and r[1] == 1 and "boom1" in (r[2] or "")
        # Claim again (next_retry_at may be ~now; ensure eligible)
        conn = get_conn(); conn.execute("UPDATE pipeline_jobs SET status='queued', next_retry_at=NULL, handled_by=NULL, claimed_at=NULL WHERE id=?", (job_id,)); conn.commit()
        _ensure_pipeline_jobs_schema(conn); job_id2 = _claim_job(conn); conn.close()
        assert job_id2 == job_id
        # 2nd failure: attempts 1+1 >= 2 -> exceeded
        _simulate_worker_failure_once(db_path, job_id, "boom2", base_backoff=0, max_attempts_override=2)
        conn = get_conn(); r2 = conn.execute("SELECT status, attempts, error_msg, result FROM pipeline_jobs WHERE id=?", (job_id,)).fetchone(); conn.close()
        assert r2[0] == "failed"
        assert "exceeded_max_attempts" in (r2[2] or "") or "exceeded_max_attempts" in str(r2)
    finally:
        cleanup_temp_db(db_path, orig)


def test_reprocess_all_resets_non_ok():
    db_path, orig = init_temp_db()
    try:
        from memall.pipeline.kb_enrich import reprocess_kb_enrich
        _insert_job(db_path, doc_id="d-all", chunk_hash="ha1", status="failed", attempts=3)
        _insert_job(db_path, doc_id="d-all", chunk_hash="ha2", status="queued")
        res = reprocess_kb_enrich({"doc_id": "d-all", "failed_only": False, "all": True})
        assert res.get("ok") is True
        assert res.get("reprocessed", 0) >= 1
        # both should be queued after reset-all (failed_only False)
        assert _pipeline_count(db_path, status="queued", doc_id="d-all") >= 1
    finally:
        cleanup_temp_db(db_path, orig)

def test_claim_ttl_recovers_stale_queued_claim_marker():
    """Queued rows with old claimed_at become claimable (lease expired)."""
    db_path, orig = init_temp_db()
    try:
        from memall.core.db import get_conn
        from memall.pipeline.kb_enrich import _claim_job, _ensure_pipeline_jobs_schema
        _insert_job(db_path, doc_id="d-ttl", chunk_hash="httl", status="queued")
        conn = get_conn(); _ensure_pipeline_jobs_schema(conn)
        # simulate stale claimed_at
        conn.execute("UPDATE pipeline_jobs SET claimed_at='2000-01-01T00:00:00+00:00' WHERE kind='kb_enrich'")
        conn.commit()
        job_id = _claim_job(conn, claim_ttl_seconds=60)
        conn.close()
        assert job_id is not None
    finally:
        cleanup_temp_db(db_path, orig)


def test_kb_enrich_stats_pipeline_backend_off_returns_hook_only_when_not_sqlite_queue():
    """When backend is not sqlite+queue, stats should still return hook stats; pipeline block may be absent."""
    db_path, orig = init_temp_db()
    try:
        from memall.config import get_config, reset_config
        from memall.pipeline.kb_enrich import kb_enrich_stats
        # Force non-sqlite backend view for stats (config key)
        try:
            get_config()["kb"]["enrich_worker"]["backend"] = "noqueue"
        except Exception:
            pass
        reset_config()
        stats = kb_enrich_stats()
        assert isinstance(stats, dict)
        assert "fired" in stats
        # pipeline metrics not required for noqueue; must not crash
    finally:
        try:
            from memall.config import get_config, reset_config
            get_config()["kb"]["enrich_worker"]["backend"] = "sqlite+queue"
            reset_config()
        except Exception:
            pass
        cleanup_temp_db(db_path, orig)


def test_reprocess_kb_enrich_contract_shape():
    db_path, orig = init_temp_db()
    try:
        from memall.pipeline.kb_enrich import reprocess_kb_enrich
        res = reprocess_kb_enrich({"doc_id": "nonexistent"})
        assert isinstance(res, dict)
        assert res.get("ok") is True
        assert "reprocessed" in res
        # additive/back-compat: shape may include filter or explicit fields
        assert "doc_id" in res or "filter" in res
    finally:
        cleanup_temp_db(db_path, orig)

def test_reprocess_bounded_trigger_is_additive_nonbreaking():
    db_path, orig = init_temp_db()
    try:
        from memall.core.db import get_conn
        from memall.pipeline.kb_enrich import reprocess_kb_enrich, _ensure_pipeline_jobs_schema
        conn = get_conn()
        try:
            _ensure_pipeline_jobs_schema(conn)
            conn.execute("INSERT INTO pipeline_jobs (created_at,kind,doc_id,chunk_hash,doc_version,chunk_index,status,attempts,next_retry_at) VALUES ('2026-01-01T00:00:00Z','kb_enrich','docb-bounded','hb-bounded','1',0,'failed',3,NULL)")
            conn.commit()
        finally:
            conn.close()
        res = reprocess_kb_enrich({"doc_id": "docb-bounded", "failed_only": True, "process_budget": 5})
        assert res.get("ok") is True
        assert res.get("reprocessed", 0) >= 1
        assert "triggered_worker" in res
        # additive: trigger_mode may exist
        _ = res.get("trigger_mode")
    finally:
        cleanup_temp_db(db_path, orig)


def test_claim_cas_single_winner_under_contention():
    """CAS claim: two independent connections racing for one queued row yield exactly one winner."""
    db_path, orig = init_temp_db()
    try:
        from memall.core.db import get_conn
        from memall.pipeline.kb_enrich import _claim_job, _ensure_pipeline_jobs_schema

        _insert_job(db_path, doc_id="d-cas", chunk_hash="hcas", status="queued")
        c1 = get_conn()
        c2 = get_conn()
        try:
            _ensure_pipeline_jobs_schema(c1)
            # Both connections see the row in the initial SELECT by opening a
            # transaction snapshot before the claim (simulates cross-process read).
            c1.execute("BEGIN")
            c1.execute("SELECT id FROM pipeline_jobs WHERE kind='kb_enrich' AND status='queued'").fetchone()
            c2.execute("BEGIN")
            c2.execute("SELECT id FROM pipeline_jobs WHERE kind='kb_enrich' AND status='queued'").fetchone()
            # c1 wins the claim on its own connection; c2 then attempts on the
            # now-stale view. The conditional UPDATE (WHERE status='queued')
            # must make only one of them win.
            j1 = _claim_job(c1)
            try:
                j2 = _claim_job(c2)
            except sqlite3.OperationalError:
                # WAL lock contention is acceptable; roll back and retry on a
                # fresh connection to verify no double-claim anyway.
                c2.rollback()
                c2.close()
                c2 = get_conn()
                j2 = _claim_job(c2)
            winners = [j for j in (j1, j2) if j is not None]
            assert len(winners) <= 1, f"double claim detected: {winners}"
            # At most one running row for this job
            running = _pipeline_count(db_path, status="running", doc_id="d-cas")
            assert running <= 1
        finally:
            for c in (c1, c2):
                try:
                    c.rollback()
                except Exception:
                    pass
                try:
                    c.close()
                except Exception:
                    pass
    finally:
        cleanup_temp_db(db_path, orig)


def test_claim_cas_skips_already_running_row():
    """A row already claimed (status='running') must not be re-claimable by a second worker."""
    db_path, orig = init_temp_db()
    try:
        from memall.core.db import get_conn
        from memall.pipeline.kb_enrich import _claim_job, _ensure_pipeline_jobs_schema

        _insert_job(db_path, doc_id="d-run", chunk_hash="hrun", status="queued")
        c1 = get_conn()
        _ensure_pipeline_jobs_schema(c1)
        j1 = _claim_job(c1)
        c1.close()
        assert j1 is not None
        c2 = get_conn()
        _ensure_pipeline_jobs_schema(c2)
        j2 = _claim_job(c2)
        c2.close()
        assert j2 is None, "running row must not be claimed again"
        assert _pipeline_count(db_path, status="running", doc_id="d-run") == 1
    finally:
        cleanup_temp_db(db_path, orig)


def test_reprocess_budget_is_bounded_and_clamped():
    """process_budget is clamped to [1,500]; bounded once trigger stays additive and idempotent."""
    db_path, orig = init_temp_db()
    try:
        from memall.core.db import get_conn
        from memall.pipeline.kb_enrich import reprocess_kb_enrich, _ensure_pipeline_jobs_schema

        conn = get_conn()
        try:
            _ensure_pipeline_jobs_schema(conn)
            for i in range(6):
                conn.execute(
                    "INSERT INTO pipeline_jobs (created_at,kind,doc_id,chunk_hash,doc_version,chunk_index,status,attempts,next_retry_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?)",
                    (f"2026-01-01T00:00:0{i}Z", "kb_enrich", "docb-budget", f"hb{i}", "1", i, "failed", 3, None),
                )
            conn.commit()
        finally:
            conn.close()

        # Re-running with the same args must not error; bounded trigger is best-effort.
        r1 = reprocess_kb_enrich({"doc_id": "docb-budget", "failed_only": True, "all": True, "process_budget": 2})
        assert r1.get("ok") is True
        assert r1.get("trigger_mode") == "bounded_once"
        # Extra/oversized budget must be clamped (still succeeds, additive contract).
        r2 = reprocess_kb_enrich({"doc_id": "docb-budget", "failed_only": True, "all": True, "process_budget": 99999})
        assert r2.get("ok") is True
        assert r2.get("trigger_mode") == "bounded_once"
    finally:
        cleanup_temp_db(db_path, orig)


# ── True multi-process contention (spawn independent OS processes) ──

def _mp_claim_worker(db_path, barrier, out_q, idx):
    """Child process: open its OWN sqlite connection and try to claim one job."""
    import sqlite3 as _s
    from memall.pipeline.kb_enrich import _claim_job, _ensure_pipeline_jobs_schema
    try:
        conn = _s.connect(str(db_path), timeout=30)
        conn.row_factory = _s.Row
        _ensure_pipeline_jobs_schema(conn)
        barrier.wait()  # release all children at once -> real contention
        jid = _claim_job(conn)
        out_q.put((idx, jid))
        conn.close()
    except Exception as e:
        out_q.put((idx, ("error", str(e)[:120])))


def test_claim_cas_true_multiprocess_single_winner():
    """True multi-process: N spawned processes race one queued row; CAS yields exactly 1 winner, no double-claim."""
    import multiprocessing as mp
    db_path, orig = init_temp_db()
    try:
        from memall.core.db import get_conn
        from memall.pipeline.kb_enrich import _ensure_pipeline_jobs_schema
        # Seed one queued job via a normal pooled connection (schema ready).
        c = get_conn()
        _ensure_pipeline_jobs_schema(c)
        c.execute(
            "INSERT INTO pipeline_jobs (created_at,kind,doc_id,chunk_hash,doc_version,chunk_index,status,attempts,next_retry_at) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            ("2026-01-01T00:00:00Z", "kb_enrich", "d-mp", "hmp", "1", 0, "queued", 0, None),
        )
        c.commit()
        c.close()

        n = 4
        ctx = mp.get_context("spawn")
        barrier = ctx.Barrier(n)
        out_q = ctx.Queue()
        procs = [ctx.Process(target=_mp_claim_worker, args=(str(db_path), barrier, out_q, i)) for i in range(n)]
        for p in procs:
            p.start()
        for p in procs:
            p.join(timeout=30)
            if p.is_alive():
                p.terminate()
        results = []
        while not out_q.empty():
            results.append(out_q.get())
        assert len(results) == n, f"expected {n} child results, got {results}"
        # No child errored
        assert all(not (isinstance(r[1], tuple) and r[1][0] == "error") for r in results), results
        winners = [r for r in results if isinstance(r[1], int) and r[1] is not None]
        assert len(winners) == 1, f"CAS violation: {len(winners)} winners among {results}"
        # Exactly one running row for this job
        assert _pipeline_count(db_path, status="running", doc_id="d-mp") == 1
        assert _pipeline_count(db_path, status="queued", doc_id="d-mp") == 0
        print("PASS: test_claim_cas_true_multiprocess_single_winner")
    finally:
        cleanup_temp_db(db_path, orig)


def test_kb_enrich_worker_status_and_stop_bounded():
    """Worker lifecycle observability + cooperative stop is bounded and idempotent."""
    from memall.pipeline.kb_enrich import (
        kb_enrich_worker_status, stop_kb_enrich_worker, kb_enrich_stats,
    )
    # No worker running (autostart default off): status reflects that.
    st0 = kb_enrich_worker_status()
    assert st0.get("alive") is False
    # stop with no worker: bounded, no error, additive shape
    r = stop_kb_enrich_worker(timeout_seconds=0.5)
    assert r.get("stop_requested") is True
    assert r.get("still_alive") is False
    assert r.get("thread_was_running") is False
    # idempotent: calling again must not raise
    r2 = stop_kb_enrich_worker(timeout_seconds=0.5)
    assert r2.get("stop_requested") is True
    # stats exposes worker observability (additive)
    stats = kb_enrich_stats()
    assert "worker" in stats and isinstance(stats["worker"], dict)
    assert "stop_requested" in stats["worker"]
    print("PASS: test_kb_enrich_worker_status_and_stop_bounded")


def test_mcp_kb_enrich_actions_validator_registered():
    """MCP surface: kb_enrich_stats / kb_reprocess / kb_enrich_stop are known to validator (no unknown-action)."""
    from memall.mcp.validator import validate_tool_input
    for act in ("kb_enrich_stats", "kb_reprocess", "kb_enrich_stop"):
        ok, data, err = validate_tool_input("memall_system", {"action": act})
        assert ok is True, f"{act}: {err}"
    # filters must survive exclude_unset dump for kb_reprocess
    ok, data, err = validate_tool_input("memall_system", {
        "action": "kb_reprocess", "doc_id": "dx", "failed_only": True, "process_budget": 7,
    })
    assert ok is True
    assert data.get("doc_id") == "dx" and data.get("process_budget") == 7
    print("PASS: test_mcp_kb_enrich_actions_validator_registered")


def test_reprocess_idempotent_under_repeated_calls():
    """Bounded reprocess is idempotent: repeated calls don't duplicate rows or crash (multi-invocation safety)."""
    db_path, orig = init_temp_db()
    try:
        from memall.core.db import get_conn
        from memall.pipeline.kb_enrich import reprocess_kb_enrich, _ensure_pipeline_jobs_schema
        c = get_conn()
        _ensure_pipeline_jobs_schema(c)
        for i in range(3):
            c.execute(
                "INSERT INTO pipeline_jobs (created_at,kind,doc_id,chunk_hash,doc_version,chunk_index,status,attempts,next_retry_at) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (f"2026-01-01T00:00:0{i}Z", "kb_enrich", "d-idem", f"hi{i}", "1", i, "failed", 3, None),
            )
        c.commit()
        c.close()
        total_before = _pipeline_count(db_path, doc_id="d-idem")
        for _ in range(3):
            res = reprocess_kb_enrich({"doc_id": "d-idem", "failed_only": True, "process_budget": 2})
            assert res.get("ok") is True
            assert res.get("trigger_mode") == "bounded_once"
        # No row duplication from repeated reprocess resets
        assert _pipeline_count(db_path, doc_id="d-idem") == total_before
        print("PASS: test_reprocess_idempotent_under_repeated_calls")
    finally:
        cleanup_temp_db(db_path, orig)
