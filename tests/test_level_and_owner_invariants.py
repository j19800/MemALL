"""Regression tests for the level-enum and owner-invariant fixes (review F-01/F-02).

F-01 — the canonical level enum (P0/P1/P2 + L1..L11) was duplicated in four
places that disagreed, so callers could pass values the ``memories.level`` CHECK
constraint rejects, raising an uncaught ``sqlite3.IntegrityError``.

F-02 — ``agent_memory.add`` defaulted ``owner`` to the writing agent, silently
violating the "owner is ALWAYS a human" invariant enforced by ``capture``.
"""

import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from memall.agent_memory import add
from memall.core.levels import LEVEL_SQL_LIST, VALID_LEVELS, normalize_level
from memall.core.thin_waist import _pool_conn, capture, retrieve, smart_store, update

_CONTENT = "这是一条用于验证 level 枚举与 owner 不变式的回归测试记忆内容。"


def _row(mid):
    """Read the stored row via the public retrieve() seam (pool-backed)."""
    m = retrieve(mid)
    assert m is not None
    return {"level": m.level, "owner": m.owner, "creator": m.creator,
            "agent_name": m.agent_name}


# ── F-01: canonical enum ────────────────────────────────────────────────

def test_normalize_level_passthrough_and_case():
    assert normalize_level("P2") == "P2"
    assert normalize_level("l11") == "L11"
    assert normalize_level("  L5  ") == "L5"


def test_normalize_level_aliases_map_into_priority_band():
    assert normalize_level("critical") == "P0"
    assert normalize_level("HIGH") == "P1"
    assert normalize_level("medium") == "P2"
    assert normalize_level("low") == "P2"
    assert normalize_level("normal") == "P2"


@pytest.mark.parametrize("bad", ["P3", "P4", "P99", "L12", "L0", "archived", "", None, 123])
def test_normalize_level_never_returns_invalid_value(bad):
    assert normalize_level(bad) in VALID_LEVELS


def test_valid_levels_matches_db_check_constraint():
    """The canonical enum and the storage CHECK must stay in lockstep."""
    with _pool_conn() as conn:
        ddl = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='memories'"
        ).fetchone()[0]
    assert "CHECK (level IN (" in ddl
    for lv in VALID_LEVELS:
        assert f"'{lv}'" in ddl, f"{lv} missing from DB CHECK"
    assert LEVEL_SQL_LIST  # generated list is non-empty


def test_db_rejects_level_outside_enum_directly():
    """Guard rail: proves the CHECK is real, so app-level normalization matters."""
    with pytest.raises(sqlite3.IntegrityError):
        with _pool_conn() as conn:
            conn.execute(
                "INSERT INTO memories (content, content_hash, level, occurred_at, "
                "created_at, updated_at) VALUES (?,?,?,?,?,?)",
                ("x" * 60, "h_direct_invalid", "medium", "2026-01-01", "2026-01-01", "2026-01-01"),
            )


def test_add_accepts_out_of_range_level_without_integrity_error():
    mid = add(_CONTENT, agent="claude", level="P3")
    assert _row(mid)["level"] == "P2"  # clamped, not crashed


def test_capture_normalizes_word_level():
    mid = capture(_CONTENT, agent_name="claude", level="medium")
    assert _row(mid)["level"] == "P2"


def test_smart_store_normalizes_word_level():
    res = smart_store(_CONTENT, agent_name="claude", level="high")
    assert res["status"] == "new"
    assert _row(res["id"])["level"] == "P1"


def test_update_normalizes_level():
    mid = capture(_CONTENT, agent_name="claude", level="P2")
    assert update(mid, level="P3") is True
    assert _row(mid)["level"] == "P2"


# ── F-02: owner is always a human ───────────────────────────────────────

def test_add_without_owner_defaults_to_human_not_agent():
    mid = add(_CONTENT, agent="claude")
    row = _row(mid)
    assert row["owner"] != "claude"
    assert row["owner"] == "老陈"
    assert row["creator"] == "claude"


def test_add_explicit_owner_is_kept():
    mid = add(_CONTENT, agent="claude", owner="张三")
    assert _row(mid)["owner"] == "张三"


def test_capture_without_owner_defaults_to_human():
    mid = capture(_CONTENT, agent_name="claude")
    row = _row(mid)
    assert row["owner"] == "老陈"
    assert row["creator"] == "claude"


# ── F-01 follow-up: pipeline write paths that bypass capture() ──────────

def test_adaptive_ttl_step_degrades_without_invalid_level():
    """adaptive_ttl_step used to SET level='P3', which the CHECK rejects."""
    from memall.pipeline.adaptive_memory import adaptive_ttl_step

    stale = (datetime.now(timezone.utc) - timedelta(days=40)).isoformat()
    with _pool_conn() as conn:
        conn.execute(
            "INSERT INTO memories (content, content_hash, level, agent_name, "
            "category, occurred_at, created_at, updated_at, access_count) "
            "VALUES (?,?,?,?,?,?,?,?,0)",
            ("stale low value memory " * 3, "h_adaptive_stale", "P2",
             "claude", "general", stale, stale, stale),
        )
        mid = conn.execute(
            "SELECT id FROM memories WHERE content_hash='h_adaptive_stale'"
        ).fetchone()["id"]

    res = adaptive_ttl_step()  # must not raise IntegrityError
    assert res["status"] == "ok"
    assert res["degraded"] >= 1

    with _pool_conn() as conn:
        row = conn.execute(
            "SELECT level, memory_status FROM memories WHERE id=?", (mid,)
        ).fetchone()
    assert row["level"] == "P2"  # canonical; never an invalid P3/P4
    assert row["memory_status"] == "dormant"


def test_batch_restore_clamps_legacy_original_level():
    """Restoring legacy metadata original_level='P4' must not violate CHECK."""
    from memall.pipeline.ops import batch_restore

    legacy = json.dumps({"original_level": {"value": "P4"}})
    with _pool_conn() as conn:
        conn.execute(
            "INSERT INTO memories (content, content_hash, level, agent_name, "
            "category, occurred_at, created_at, updated_at, metadata, "
            "memory_status) VALUES (?,?,?,?,?,?,?,?,?,'archived')",
            ("archived legacy memory " * 3, "h_ops_legacy", "P2", "claude",
             "general", "2026-01-01", "2026-01-01", "2026-01-01", legacy),
        )
        mid = conn.execute(
            "SELECT id FROM memories WHERE content_hash='h_ops_legacy'"
        ).fetchone()["id"]

    res = batch_restore()  # must not raise IntegrityError
    assert res["restored"] >= 1

    with _pool_conn() as conn:
        row = conn.execute(
            "SELECT level, memory_status FROM memories WHERE id=?", (mid,)
        ).fetchone()
    assert row["level"] == "P2"  # "P4" clamped into the canonical enum
    assert row["memory_status"] is None


def test_import_data_level_map_clamps_legacy_p3_p4():
    from memall.cli.import_data import _normalize_level
    assert _normalize_level("P3") == "P2"
    assert _normalize_level("P4") == "P2"
    assert _normalize_level("P0") == "P0"
    assert _normalize_level("L11") == "L11"


def test_import_data_word_aliases_resolve_case_insensitively():
    """Word aliases were dead: input was upper-cased before a lowercase lookup."""
    from memall.cli.import_data import _normalize_level
    assert _normalize_level("session") == "L4"
    assert _normalize_level("Decision") == "L4"
    assert _normalize_level("reflection") == "L6"
    assert _normalize_level("lesson") == "L7"