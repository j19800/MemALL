"""
Test Suite — Config (Phase 16+)
===============================
Tests config merge, environment override, dot-path access, and YAML fallback.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from memall.config import (
    merge_config,
    get_config,
    reset_config,
    _get_dot_path,
    _set_dot_path,
)


def test_merge_config():
    """Test deep merge of two config dicts."""
    base = {"a": 1, "b": {"x": 10, "y": 20}, "c": [1, 2]}
    override = {"b": {"y": 99, "z": 30}, "c": [3]}

    merged = merge_config(base, override)

    assert merged["a"] == 1, "top-level key not preserved"
    assert merged["b"]["x"] == 10, "nested key lost"
    assert merged["b"]["y"] == 99, "nested key not overridden"
    assert merged["b"]["z"] == 30, "new nested key missing"
    assert merged["c"] == [3], "list not overridden"
    print("  PASS test_merge_config")


def test_dot_path():
    """Test dot-path get and set."""
    d = {"db": {"path": "/tmp/test.db", "timeout": 30}}

    val = _get_dot_path(d, "db.path")
    assert val == "/tmp/test.db", f"Expected /tmp/test.db, got {val}"

    val2 = _get_dot_path(d, "db.timeout")
    assert val2 == 30, f"Expected 30, got {val2}"

    missing = _get_dot_path(d, "db.nonexistent", default=42)
    assert missing == 42, "default not returned for missing key"

    _set_dot_path(d, "db.path", "/new/path.db")
    assert d["db"]["path"] == "/new/path.db", "set_dot_path failed"

    _set_dot_path(d, "logging.level", "DEBUG")
    assert d["logging"]["level"] == "DEBUG", "nested creation failed"

    print("  PASS test_dot_path")


def test_get_config_default():
    """Test that get_config returns defaults without any config files."""
    # Clear any env var contamination from other tests
    for k in list(os.environ.keys()):
        if k.startswith("MEMALL_"):
            del os.environ[k]
    reset_config()

    db_path = get_config("db.path")
    assert db_path is not None, "db.path should have a default"
    assert ".memall" in str(db_path), f"db.path should contain .memall: {db_path}"

    gateway_port = get_config("gateway.port")
    assert gateway_port == 9919, f"Expected default port 9919, got {gateway_port}"

    print("  PASS test_get_config_default")


def test_env_override():
    """Test that MEMALL_* env vars override config."""
    reset_config()

    os.environ["MEMALL_GATEWAY_PORT"] = "12345"
    os.environ["MEMALL_DB_PATH"] = "/tmp/test_override.db"

    try:
        port = get_config("gateway.port")
        assert port == 12345, f"Env override failed: expected 12345, got {port}"

        db_path = get_config("db.path")
        assert db_path == "/tmp/test_override.db", f"Expected /tmp/test_override.db, got {db_path}"

        print("  PASS test_env_override")
    finally:
        del os.environ["MEMALL_GATEWAY_PORT"]
        del os.environ["MEMALL_DB_PATH"]
        reset_config()


def test_full_config():
    """Test that the full config dict is well-structured."""
    reset_config()
    cfg = get_config()

    assert "db" in cfg, "Missing 'db' section"
    assert "gateway" in cfg, "Missing 'gateway' section"
    assert "forget" in cfg, "Missing 'forget' section"

    print("  PASS test_full_config")


if __name__ == "__main__":
    print("=" * 60)
    print("MemALL Config Tests")
    print("=" * 60)
    passed = 0
    failed = 0

    tests = [
        ("test_merge_config", test_merge_config),
        ("test_dot_path", test_dot_path),
        ("test_get_config_default", test_get_config_default),
        ("test_env_override", test_env_override),
        ("test_full_config", test_full_config),
    ]

    for name, func in tests:
        try:
            func()
            passed += 1
        except Exception as e:
            print(f"  FAIL {name}: {e}")
            failed += 1

    print(f"\nResults: {passed} passed, {failed} failed")
    if failed:
        sys.exit(1)