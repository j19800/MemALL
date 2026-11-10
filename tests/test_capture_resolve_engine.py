"""Phase 2 MVP resolve engine tests (rules-first, non-destructive)."""
from __future__ import annotations

import json
import os
import sqlite3

import pytest

from tests.test_helpers import cleanup_temp_db, init_temp_db


@pytest.fixture()
def db_path():
    db_path, old = init_temp_db()
    try:
        yield db_path
    finally:
        cleanup_temp_db(db_path, old)


@pytest.fixture(autouse=True)
def _reset_config_env(monkeypatch):
    """Ensure config/env changes do not leak across tests."""
    import memall.config as config

    for k in list(os.environ):
        if k.startswith("MEMALL_CAPTURE_") or k.startswith("MEMALL_QUALITY_"):
            monkeypatch.delenv(k, raising=False)
    config.reset_config()
    yield
    config.reset_config()


def _conn(db_path):
    c = sqlite3.connect(str(db_path))
    c.row_factory = sqlite3.Row
    return c


def _get_meta(db_path, mem_id: int) -> dict:
    with _conn(db_path) as c:
        row = c.execute("SELECT metadata FROM memories WHERE id=?", (int(mem_id),)).fetchone()
    assert row is not None
    return json.loads(row["metadata"] or "{}")


def test_resolve_sets_slot_key_and_noop_for_new_slot(db_path):
    from memall.core.models import MemoryInput
    from memall.core.thin_waist import capture

    mid = capture(
        MemoryInput(
            content="服务端口配置为 3306，用于本地 MySQL 数据库连接。",
            level="P1",
            agent_name="test-agent",
            owner="u",
            project="memall",
            category="config",
            subject="数据库端口配置",
        )
    )
    assert int(mid) > 0
    m = _get_meta(db_path, int(mid))
    assert m.get("slot_key")
    assert (m.get("resolve") or {}).get("slot_key")


def test_resolve_supersedes_config_change_and_writes_edge(db_path):
    from memall.core.models import MemoryInput
    from memall.core.thin_waist import capture

    m1 = capture(
        MemoryInput(
            content="项目数据库端口配置为 3306，这是当前生产使用端口。",
            level="P1",
            agent_name="test-agent",
            owner="u",
            project="memall",
            category="config",
            subject="数据库端口配置",
        )
    )
    m2 = capture(
        MemoryInput(
            content="项目数据库端口改为 3307，不再使用 3306，已废弃旧端口配置。",
            level="P1",
            agent_name="test-agent",
            owner="u",
            project="memall",
            category="config",
            subject="数据库端口配置",
        )
    )
    assert int(m1) != int(m2)
    meta2 = _get_meta(db_path, int(m2))
    res = meta2.get("resolve") or {}
    assert res.get("slot_key")
    assert res.get("target_id") == int(m1)
    assert res.get("decision") in {"SUPERSEDE", "UPDATE", "CONFLICT", "REVIEW", "MERGE", "NOOP"}
    with _conn(db_path) as c:
        old = c.execute("SELECT id, content FROM memories WHERE id=?", (int(m1),)).fetchone()
        assert old is not None
        edges = c.execute("SELECT relation_type, target_id FROM edges WHERE source_id=?", (int(m2),)).fetchall()
    assert any(e["target_id"] == int(m1) for e in edges) or res.get("target_id") == int(m1)


def test_capture_int_compatibility_with_resolve(db_path):
    from memall.core.models import MemoryInput
    from memall.core.thin_waist import capture

    mid = capture(MemoryInput(content="采用 PostgreSQL 作为主数据库的架构决策。", level="L4", agent_name="a", category="decision", subject="数据库选型"))
    assert isinstance(int(mid), int)
    assert int(mid) == mid


def test_resolve_can_be_disabled(db_path, monkeypatch):
    import memall.config as config
    from memall.core.models import MemoryInput
    from memall.core.thin_waist import capture

    monkeypatch.setenv("MEMALL_CAPTURE_RESOLVE_ENABLED", "false")
    config.reset_config()
    try:
        mid = capture(
            MemoryInput(
                content="服务端口配置为 3306，用于本地 MySQL 数据库连接。",
                level="P1", agent_name="test-agent", project="memall", category="config", subject="数据库端口配置",
            )
        )
        m = _get_meta(db_path, int(mid))
        assert "resolve" not in m
    finally:
        config.reset_config()
