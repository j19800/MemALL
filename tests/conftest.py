"""
Pytest configuration — redirect DB_PATH to a temporary database
for every test to prevent pollution of the production database.
"""

import os
import sqlite3
import sys
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

# Standalone runnable scripts that match pytest's default ``*_test.py`` pattern
# but are not suites. They run heavy work at module top level, so they must be
# excluded before import — otherwise plain ``pytest tests/`` executes them.
collect_ignore = ["smoke_test.py", "stress_test.py", "quality_test.py"]


@pytest.fixture(autouse=True)
def _reset_session_identity():
    """Clear the process-global session identity around every test.

    ``memall.mcp.session_identity`` keeps the caller's identity in module-level
    state (a ContextVar plus a ``_handshake_agent`` fallback) because the MCP
    surface needs it across a whole connection.  A test that binds one and
    forgets to clear it therefore leaks into EVERY later test in the same
    process: ``test_task_lifecycle`` binds "testagent" and never resets, after
    which ``test_mcp_adapter``'s calls silently carry that identity instead of
    the one they pass, and its reads come back empty.

    That is why the adapter tests passed alone and failed in a batch.  Resetting
    here — before and after each test — makes identity leakage impossible
    regardless of what any individual test does.
    """
    try:
        from memall.mcp import session_identity as _si

        _si.reset()
        yield
        _si.reset()
    except Exception:
        # Identity module missing/changed: nothing to isolate.
        yield


@pytest.fixture(autouse=True)
def _test_db(monkeypatch, tmp_path):
    """Redirect DB_PATH to a temporary database for test isolation.

    Every test gets a fresh empty database with schema + migrations applied.
    The global connection pool is also reset so it picks up the temp DB.
    """
    db_dir = tmp_path / ".memall"
    db_dir.mkdir(parents=True)
    db_path = db_dir / "data.db"

    from memall.core import db as core_db

    # Release the previous test's pool.  Its connections otherwise linger, and
    # background daemon threads (capture post-process, pipeline worker) that
    # read the module-global DB_PATH at run time can contend with this test's
    # freshly created database.
    if core_db._global_pool is not None:
        try:
            core_db._global_pool.close_all()
        except Exception:
            pass

    monkeypatch.setattr("memall.core.db.DB_PATH", db_path)
    monkeypatch.setattr("memall.core.db._global_pool", None)

    # Isolate security policy too.  A test must not depend on whatever
    # ``security.disabled_agents`` the ambient deployment happens to carry:
    # entry names such as ``gateway_test`` are permanently denylisted on the
    # dev machine, and the suite then failed for a reason unrelated to the
    # code under test.
    #
    # The isolation must survive config RESETS inside tests.  The first
    # implementation cleared the live dict once here — but a test that calls
    # ``config.reset_config()`` itself (semantic-dedup, thread-inference,
    # category-hygiene fixtures all do) wiped the gate and the next
    # ``get_config()`` reloaded the developer's real config file, restoring
    # the denylist.  capture(agent_name="sem_test") then raised
    # "agent is disabled" — three test files failed for the deployment's
    # ambient config, not for the code under test.  Wrapping reset_config
    # re-sanitises after every reload, so no test can observe the ambient
    # denylist regardless of what it does with the config module.
    try:
        import memall.config as mc

        def _sanitize_live_config():
            if mc._config is not None:
                sec = mc._config.setdefault("security", {})
                sec["disabled_agents"] = []
                sec["disabled_projects"] = []
                mc._last_mtime_check = float("inf")

        _sanitize_live_config()

        _orig_reset = mc.reset_config

        def _reset_keep_isolation():
            _orig_reset()
            mc.get_config()  # force the disk reload now, inside the test
            _sanitize_live_config()

        monkeypatch.setattr(mc, "reset_config", _reset_keep_isolation)
    except Exception:
        pass

    from memall.core.db import init_db

    # A leftover background thread may briefly hold a lock on the DB_PATH it
    # read at run time.  Retry the transient lock instead of failing the fixture
    # (a failure here would also leave a half-initialized schema).
    for attempt in range(5):
        try:
            init_db(migrate=True)
            return
        except sqlite3.OperationalError as exc:
            if "locked" not in str(exc).lower() or attempt == 4:
                raise
            time.sleep(0.2 * (attempt + 1))