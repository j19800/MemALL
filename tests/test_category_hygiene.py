"""Category hygiene gate — regression guard for the "field pollution" defect.

Historical bug: a distill.py slicing error turned two-char domain names into
single characters ("决策" -> "决"/"策", "[任务]" -> "["/"任"), writing 159
polluted rows that were then re-distilled 40+ times.  The gate must stop
anything structurally like that from ever landing again, while leaving
legitimate short tags (AI / QA / 决策) untouched.
"""

import pytest

from memall import config
from memall.core.thin_waist import _category_validity
from tests.test_helpers import init_temp_db, cleanup_temp_db

_BODY = "这是一条用于分类校验测试的正文字段，长度必须足够以通过写入质量门。"


def _captured_category(cat: str, **cfg):
    """capture() a memory with ``cat``, return the category actually stored."""
    from memall.core.thin_waist import capture
    from memall.core.db import get_conn

    db_path, patcher = init_temp_db()
    try:
        config.get_config()  # ensure the cached config object exists
        if cfg:
            config._config.setdefault("capture", {}).update(cfg)
        try:
            mid = capture(_BODY, agent_name="tester", category=cat)
        finally:
            config.reset_config()
        conn = get_conn()
        try:
            return conn.execute(
                "SELECT category FROM memories WHERE id = ?", (mid,)
            ).fetchone()["category"]
        finally:
            conn.close()
    finally:
        cleanup_temp_db(db_path, patcher)


# ── Structural check ────────────────────────────────────────────────────

@pytest.mark.parametrize("cat", ["", " ", "决", "L", "[", "#", "??", "{}", "a b", "决 策", "\n", "x\ty"])
def test_structurally_invalid_categories_rejected(cat):
    ok, why = _category_validity(cat, 2)
    assert not ok, f"{cat!r} should be rejected"
    assert why


@pytest.mark.parametrize("cat", ["general", "architecture", "decision", "AI", "QA", "决策", "会话总结", "problem"])
def test_legitimate_categories_accepted(cat):
    ok, why = _category_validity(cat, 2)
    assert ok, f"{cat!r} should be accepted, got: {why}"


def test_cjk_two_char_accepted_but_one_char_rejected():
    """The exact shape of the historical pollution."""
    assert _category_validity("决策", 2)[0] is True
    assert _category_validity("决", 2)[0] is False


# ── Entry-point behaviour ───────────────────────────────────────────────

def test_invalid_category_coerced_to_general_by_default():
    assert _captured_category("决") == "general"


def test_valid_category_preserved():
    assert _captured_category("architecture") == "architecture"


def test_reject_mode_raises():
    from memall.core.thin_waist import capture

    db_path, patcher = init_temp_db()
    try:
        config.get_config()
        config._config.setdefault("capture", {})["category_invalid_action"] = "reject"
        try:
            with pytest.raises(ValueError, match="invalid category"):
                capture(_BODY, agent_name="tester", category="[")
        finally:
            config.reset_config()
    finally:
        cleanup_temp_db(db_path, patcher)


def test_enum_restricts_known_vocabulary():
    assert _captured_category("somethingelse", allowed_categories=["architecture", "decision"]) == "general"


def test_gate_can_be_disabled():
    # Disabled gate must not silently rewrite — the caller's value is preserved.
    assert _captured_category("决", category_validation_enabled=False) == "决"


# ── Production-data guard: no polluted category may exist ───────────────

def test_no_polluted_categories_are_producible():
    """Every category value observed as pollution must fail the gate."""
    polluted = ["决", "策", "话", "会", "任", "在", "领", "域", "[", "]",
                " ", "  ", "#", "{", "}", "??", "L", "S", "3", "y"]
    for cat in polluted:
        ok, _ = _category_validity(cat, 2)
        assert not ok, f"polluted value {cat!r} would still pass the gate"
