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