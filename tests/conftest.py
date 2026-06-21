"""
Pytest configuration — redirect DB_PATH to a temporary database
for every test to prevent pollution of the production database.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


@pytest.fixture(autouse=True)
def _test_db(monkeypatch, tmp_path):
    """Redirect DB_PATH to a temporary database for test isolation.

    Every test gets a fresh empty database with schema + migrations applied.
    The global connection pool is also reset so it picks up the temp DB.
    """
    db_dir = tmp_path / ".memall"
    db_dir.mkdir(parents=True)
    db_path = db_dir / "data.db"

    monkeypatch.setattr("memall.core.db.DB_PATH", db_path)
    monkeypatch.setattr("memall.core.db._global_pool", None)

    from memall.core.db import init_db
    init_db(migrate=True)
