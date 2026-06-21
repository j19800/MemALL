"""
Test Suite — Pipeline Security
===============================
Tests audit_sensitive(), PermissionManager (set/get/check).
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from memall.pipeline.security import (
    _redact_email,
    _redact_phone,
    _redact_ip,
    _redact_idcard,
    _is_valid_ip,
)


def test_redact_email():
    """Test: _redact_email obscures local part."""
    assert _redact_email("user@example.com") == "u***@example.com"
    assert _redact_email("a@b.com") == "a***@b.com"
    print("  PASS test_redact_email")


def test_redact_phone():
    """Test: _redact_phone obscures middle digits."""
    assert _redact_phone("13812341234") == "138****1234"
    assert _redact_phone("12345") == "123****"
    print("  PASS test_redact_phone")


def test_redact_ip():
    """Test: _redact_ip obscures last two octets."""
    assert _redact_ip("192.168.1.1") == "192.168.***.***"
    print("  PASS test_redact_ip")


def test_redact_idcard():
    """Test: _redact_idcard obscures middle digits."""
    result = _redact_idcard("320102199001011234")
    assert len(result) == 18, f"Expected 18 chars, got {len(result)}"
    assert result.startswith("3201"), f"Should start with 3201, got {result[:4]}"
    assert result.endswith("1234"), f"Should end with 1234, got {result[-4:]}"
    print("  PASS test_redact_idcard")


def test_is_valid_ip():
    """Test: _is_valid_ip validates octets."""
    assert _is_valid_ip("192.168.1.1") is True
    assert _is_valid_ip("999.999.999.999") is False
    assert _is_valid_ip("not.an.ip") is False
    print("  PASS test_is_valid_ip")


def test_audit_sensitive_empty_db():
    """Test: audit_sensitive returns no findings on empty DB."""
    from tests.test_helpers import init_temp_db, cleanup_temp_db
    from memall.pipeline.security import audit_sensitive

    db_path, patcher = init_temp_db()
    try:
        result = audit_sensitive()
        assert isinstance(result, dict), f"Expected dict, got {type(result)}"
        assert result["findings"] == 0, f"Expected 0 findings, got {result['findings']}"
        assert result["risk_level"] in ("low", "medium", "high", "none")
        print("  PASS test_audit_sensitive_empty_db")
    finally:
        cleanup_temp_db(db_path, patcher)


def test_audit_sensitive_finds_email():
    """Test: audit_sensitive detects email addresses."""
    from tests.test_helpers import init_temp_db, cleanup_temp_db, insert_memory
    from memall.pipeline.security import audit_sensitive
    from memall.core.db import get_conn

    db_path, patcher = init_temp_db()
    try:
        conn = get_conn()
        insert_memory(conn, "contact me at john.doe@example.com for more info")
        conn.close()

        result = audit_sensitive()
        assert result["findings"] >= 1, f"Expected at least 1 finding, got {result}"
        assert result["by_type"]["email"] >= 1, f"Expected email finding"
        print("  PASS test_audit_sensitive_finds_email")
    finally:
        cleanup_temp_db(db_path, patcher)


def test_set_get_permission():
    """Test: set_permission and get_permission round-trip."""
    from tests.test_helpers import init_temp_db, cleanup_temp_db
    from memall.pipeline.security import set_permission, get_permission

    db_path, patcher = init_temp_db()
    try:
        result = set_permission("test_agent", "public")
        assert result["status"] == "ok", f"set failed: {result}"

        result = get_permission("test_agent")
        assert result["level"] == "public", f"Expected 'public', got '{result['level']}'"
        print("  PASS test_set_get_permission")
    finally:
        cleanup_temp_db(db_path, patcher)


def test_get_permission_default():
    """Test: get_permission returns 'private' for unknown agents."""
    from tests.test_helpers import init_temp_db, cleanup_temp_db
    from memall.pipeline.security import get_permission

    db_path, patcher = init_temp_db()
    try:
        result = get_permission("nonexistent_agent")
        assert result["level"] == "private", f"Expected 'private', got '{result['level']}'"
        print("  PASS test_get_permission_default")
    finally:
        cleanup_temp_db(db_path, patcher)


def test_set_permission_invalid_level():
    """Test: set_permission rejects invalid levels."""
    from tests.test_helpers import init_temp_db, cleanup_temp_db
    from memall.pipeline.security import set_permission

    db_path, patcher = init_temp_db()
    try:
        result = set_permission("test_agent", "invalid")
        assert "error" in result, f"Expected error for invalid level: {result}"
        print("  PASS test_set_permission_invalid_level")
    finally:
        cleanup_temp_db(db_path, patcher)


# ── Runner ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 60)
    print("MemALL Pipeline Security Tests")
    print("=" * 60)
    passed = 0
    failed = 0

    tests = [
        ("test_redact_email", test_redact_email),
        ("test_redact_phone", test_redact_phone),
        ("test_redact_ip", test_redact_ip),
        ("test_redact_idcard", test_redact_idcard),
        ("test_is_valid_ip", test_is_valid_ip),
        ("test_audit_sensitive_empty_db", test_audit_sensitive_empty_db),
        ("test_audit_sensitive_finds_email", test_audit_sensitive_finds_email),
        ("test_set_get_permission", test_set_get_permission),
        ("test_get_permission_default", test_get_permission_default),
        ("test_set_permission_invalid_level", test_set_permission_invalid_level),
    ]

    for name, func in tests:
        try:
            func()
            passed += 1
        except Exception as e:
            print(f"  FAIL {name}: {e}")
            import traceback
            traceback.print_exc()
            failed += 1

    print(f"\nResults: {passed} passed, {failed} failed")
    if failed:
        sys.exit(1)