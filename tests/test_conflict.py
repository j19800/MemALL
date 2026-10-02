"""Tests for federation/conflict.py — cross-agent contradiction detection.

The module operates on a dedicated ``family.db`` (separate from the main
``data.db``), so every test redirects that path to a temp file and resets the
module-level migration guards.
"""

import sqlite3

import pytest

import memall.federation.conflict as conflict
import memall.federation.family as family

_CONTENT_A = "部署方案讨论：经过评估我们决定采用本地部署，保证数据安全可控。"
_CONTENT_B = "部署方案讨论：经过评估我们决定放弃本地部署，改为云端方案更省成本。"


@pytest.fixture
def family_env(tmp_path, monkeypatch):
    """Point family.db at a temp file and reset the once-per-process guards."""
    path = tmp_path / "family.db"
    monkeypatch.setattr(family, "get_family_db_path", lambda: path)
    monkeypatch.setattr(conflict, "get_family_db_path", lambda: path)
    monkeypatch.setattr(family, "_FAMILY_DB_INITIALIZED", False)
    monkeypatch.setattr(conflict, "_CONFLICT_DB_MIGRATED", False)
    family.init_family_db(force=True)
    return path


def _add_shared(path, original_id: int, agent: str, content: str) -> int:
    conn = sqlite3.connect(str(path))
    try:
        cur = conn.execute(
            "INSERT INTO shared_memories "
            "(original_id, source_agent, source_db, content, published_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (original_id, agent, "test.db", content, "2026-10-02T00:00:00+00:00"),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def test_detect_contradiction_pure():
    assert conflict._detect_contradiction("我们采用本地方案", "我们放弃本地方案") is True
    assert conflict._detect_contradiction("今天天气很好", "明天也是晴天") is False


def test_detect_conflicts_needs_two_memories(family_env):
    _add_shared(family_env, 1, "agent_a", _CONTENT_A)
    out = conflict.detect_conflicts(mode="keyword")
    assert out["conflicts_detected"] == 0
    assert out["total_memories"] == 1


def test_detect_conflicts_finds_keyword_contradiction(family_env):
    _add_shared(family_env, 1, "agent_a", _CONTENT_A)
    _add_shared(family_env, 2, "agent_b", _CONTENT_B)
    out = conflict.detect_conflicts(mode="keyword")
    assert out["conflicts_detected"] >= 1

    listed = conflict.list_conflicts(status="open")
    assert len(listed) >= 1
    assert listed[0]["status"] == "open"
    assert listed[0]["conflict_type"] == "keyword"


def test_resolve_conflict_sets_winner_and_loser(family_env):
    a_id = _add_shared(family_env, 1, "agent_a", _CONTENT_A)
    _add_shared(family_env, 2, "agent_b", _CONTENT_B)
    conflict.detect_conflicts(mode="keyword")

    listed = conflict.list_conflicts(status="open")
    cid = listed[0]["id"]
    pair = (listed[0]["memory_id_a"], listed[0]["memory_id_b"])

    result = conflict.resolve_conflict(cid, pair[0])
    assert result["resolved"] is True
    assert result["winner"] == pair[0]
    assert result["loser"] == pair[1]
    assert a_id in pair

    # Resolving twice is rejected.
    again = conflict.resolve_conflict(cid, pair[0])
    assert "error" in again


def test_resolve_conflict_rejects_outside_winner(family_env):
    _add_shared(family_env, 1, "agent_a", _CONTENT_A)
    _add_shared(family_env, 2, "agent_b", _CONTENT_B)
    conflict.detect_conflicts(mode="keyword")
    cid = conflict.list_conflicts(status="open")[0]["id"]

    result = conflict.resolve_conflict(cid, 999999)
    assert "error" in result


def test_auto_resolve_clears_open_conflicts(family_env):
    _add_shared(family_env, 1, "agent_a", _CONTENT_A)
    _add_shared(family_env, 2, "agent_b", _CONTENT_B)
    conflict.detect_conflicts(mode="keyword")

    out = conflict.auto_resolve()
    assert out["total_processed"] >= 1
    assert out["auto_resolved"] >= 1
    assert conflict.list_conflicts(status="open") == []