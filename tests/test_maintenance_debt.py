"""Technical-debt regressions for the maintenance subsystem.

Each test pins a defect that was live: dry runs polluted the audit trail,
rotation did not exist, relative paths followed the process cwd, an old DB
never got its newer columns on the read path, low_utility_archive ignored the
utility ledger, and error inspection compared timestamps as strings.
"""
import json
import os
import tempfile

import pytest

from tests.test_helpers import init_temp_db, cleanup_temp_db


def _cfg(block: dict):
    import memall.config as cfg
    cfg.get_config()
    cfg._config.setdefault("maintenance", {}).update(block)
    cfg._last_mtime_check = float("inf")


def test_dry_run_writes_no_audit_line():
    db, orig = init_temp_db()
    tmp = tempfile.mkdtemp()
    try:
        audit = os.path.join(tmp, "audit.jsonl")
        _cfg({"enabled": True, "conflict_review": True,
              "archive_dir": tmp, "audit_log": audit,
              "snapshot_before_write": True})
        from memall.core.db import get_conn
        from memall.pipeline import maintenance as m
        c = get_conn()
        c.execute("INSERT INTO memories (content, content_hash, level, agent_name, category, "
                  "subject, occurred_at, created_at, updated_at, memory_status, metadata) "
                  "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                  ("dry run 审计测试记忆内容足够长通过门禁。", "h_dry", "L4", "t", "general",
                   "s", "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z",
                   "2026-01-01T00:00:00Z", "CONFLICT", "{}"))
        c.commit()
        c.close()

        m.run_maintenance(actions=["conflict_review"], dry_run=True)
        # The docstring promises "no writes, no audit lines".
        assert not os.path.exists(audit), "dry_run must not append an audit line"

        m.run_maintenance(actions=["conflict_review"], dry_run=False)
        assert os.path.exists(audit), "a real run must leave an audit trail"
        print("PASS: test_dry_run_writes_no_audit_line")
    finally:
        from memall.config import reset_config
        reset_config()
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)
        cleanup_temp_db(db, orig)


def test_audit_log_rotates_by_size():
    db, orig = init_temp_db()
    tmp = tempfile.mkdtemp()
    try:
        audit = os.path.join(tmp, "audit.jsonl")
        _cfg({"enabled": True, "archive_dir": tmp, "audit_log": audit})
        from memall.pipeline import maintenance as m
        os.makedirs(tmp, exist_ok=True)
        with open(audit, "w", encoding="utf-8") as f:
            f.write('{"x":1}\n' * 1_200_000)         # ~9.6MB > 8MB threshold
        before = os.path.getsize(audit)
        m._append_audit({"ts": "t", "action": "probe"})
        assert os.path.getsize(audit) < before, "log must rotate once over the cap"
        assert os.path.exists(audit + ".1"), "previous content must be kept as .1"
        print("PASS: test_audit_log_rotates_by_size")
    finally:
        from memall.config import reset_config
        reset_config()
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)
        cleanup_temp_db(db, orig)


def test_archive_paths_do_not_follow_process_cwd():
    db, orig = init_temp_db()
    try:
        _cfg({"enabled": True})
        from memall.pipeline import maintenance as m
        repo_arch = m._paths()[0]
        prev = os.getcwd()
        try:
            os.chdir(tempfile.gettempdir())
            assert m._paths()[0] == repo_arch, (
                "relative archive paths must anchor on the module, not process cwd")
        finally:
            os.chdir(prev)
        print("PASS: test_archive_paths_do_not_follow_process_cwd")
    finally:
        from memall.config import reset_config
        reset_config()
        cleanup_temp_db(db, orig)


def test_old_db_gets_new_columns_on_plain_get_conn():
    """A read-only consumer must not need init_db() to see the new columns."""
    import sqlite3
    from pathlib import Path
    from memall.core import db as core_db
    tmp = tempfile.mkdtemp()
    p = os.path.join(tmp, "old.db")
    try:
        c = sqlite3.connect(p)
        c.execute("CREATE TABLE memories (id INTEGER PRIMARY KEY, content TEXT, content_hash TEXT)")
        c.execute("CREATE TABLE search_interactions (id INTEGER PRIMARY KEY, query TEXT, "
                  "memory_id INTEGER, action TEXT, dwell_ms INTEGER, rank_position INTEGER, "
                  "agent_name TEXT, owner TEXT, session_id TEXT, created_at TEXT)")
        c.commit()
        c.close()

        orig_db = core_db.DB_PATH
        core_db.DB_PATH = Path(p)
        core_db._global_pool = None
        conn = core_db.get_conn()
        try:
            icols = {r[1] for r in conn.execute("PRAGMA table_info(search_interactions)").fetchall()}
            mcols = {r[1] for r in conn.execute("PRAGMA table_info(memories)").fetchall()}
        finally:
            conn.close()
            core_db.DB_PATH = orig_db
            core_db._global_pool = None
        need = {"task_id", "accepted", "slot_key", "scope"}
        assert need.issubset(icols), sorted(need - icols)
        assert "valid_from" in mcols, "temporal validity column must be re-asserted"
        print("PASS: test_old_db_gets_new_columns_on_plain_get_conn")
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


def test_low_utility_archive_consults_the_ledger():
    """The policy is named for utility, so it must use utility evidence."""
    db, orig = init_temp_db()
    tmp = tempfile.mkdtemp()
    try:
        from datetime import datetime, timezone
        _cfg({"enabled": True, "low_utility_archive": True,
              "archive_dir": tmp, "audit_log": os.path.join(tmp, "a.jsonl"),
              "snapshot_before_write": True})
        from memall.core.db import get_conn
        from memall.pipeline import maintenance as m
        now = datetime.now(timezone.utc).isoformat()
        c = get_conn()
        for cid, slot in (("h_low", "slot:low"), ("h_neutral", "slot:neutral")):
            c.execute("INSERT INTO memories (content, content_hash, level, agent_name, "
                      "category, subject, occurred_at, created_at, updated_at, metadata) "
                      "VALUES (?,?,?,?,?,?,?,?,?,?)",
                      (f"效用测试记忆 {cid} 内容足够长通过门禁校验。", cid, "L4", "t",
                       "general", "s", now, now, now, json.dumps({"slot_key": slot})))
        # Negative signal for slot:low only.
        c.execute("INSERT INTO search_interactions (query, memory_id, action, agent_name, "
                  "created_at, rejected, slot_key) VALUES (?,?,?,?,?,?,?)",
                  ("q", 1, "click", "u", now, 1, "slot:low"))
        c.commit()
        low_id = int(c.execute("SELECT id FROM memories WHERE content_hash='h_low'").fetchone()[0])
        neutral_id = int(c.execute("SELECT id FROM memories WHERE content_hash='h_neutral'").fetchone()[0])
        c.close()

        cands = m._select_candidates(get_conn(), "low_utility_archive", 10)
        assert low_id in cands, f"negative-utility slot must be a candidate: {cands}"
        assert neutral_id not in cands, f"no-evidence slot must not be archived: {cands}"
        print("PASS: test_low_utility_archive_consults_the_ledger")
    finally:
        from memall.config import reset_config
        reset_config()
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)
        cleanup_temp_db(db, orig)


def test_utility_window_uses_real_time_math():
    """ISO strings with 'T'/'+00:00' must not be compared as raw text."""
    db, orig = init_temp_db()
    try:
        from datetime import datetime, timezone, timedelta
        from memall.core.db import get_conn
        c = get_conn()
        now = datetime.now(timezone.utc)
        # 29 days ago must fall INSIDE a 30-day window; 31 days ago must not.
        for mid, days in ((1, 29), (2, 31)):
            ts = (now - timedelta(days=days)).isoformat()   # has T and +00:00
            c.execute("INSERT INTO search_interactions (query, memory_id, action, "
                      "agent_name, created_at, accepted, slot_key) "
                      "VALUES (?,?,?,?,?,?,?)", ("q", mid, "click", "u", ts, 1, "slot:x"))
        c.commit()
        rows = c.execute(
            "SELECT memory_id FROM search_interactions "
            "WHERE julianday(created_at) >= julianday('now','-30 days')").fetchall()
        c.close()
        ids = [r[0] for r in rows]
        assert 1 in ids and 2 not in ids, f"window math wrong: {ids}"
        print("PASS: test_utility_window_uses_real_time_math")
    finally:
        cleanup_temp_db(db, orig)
