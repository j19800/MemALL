"""
Test Suite — Federation Family
===============================
Tests family_init, family_invite, family_list.
"""

import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


def test_family_init():
    """Test: family_init creates a new family circle."""
    from memall.federation.family import family_init

    # Use temp path for family db
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        tmp_path = Path(tmp.name)

    with patch("memall.federation.family.get_family_db_path", return_value=tmp_path):
        result = family_init("test_circle", owner_name="test_owner")

    # Clean up
    tmp_path.unlink(missing_ok=True)
    for ext in ["-wal", "-shm"]:
        p = Path(str(tmp_path) + ext)
        p.unlink(missing_ok=True)

    assert isinstance(result, dict), f"Expected dict, got {type(result)}"
    assert result["status"] == "ok", f"Expected 'ok', got {result}"
    assert result["circle_name"] == "test_circle"
    assert result["owner"] == "test_owner"
    print("  PASS test_family_init")


def test_family_init_duplicate():
    """Test: family_init returns error for duplicate circle name."""
    from memall.federation.family import family_init

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        tmp_path = Path(tmp.name)

    with patch("memall.federation.family.get_family_db_path", return_value=tmp_path), \
         patch("memall.federation.family._FAMILY_DB_INITIALIZED", False):
        first = family_init("dup_circle", owner_name="owner1")
        assert first["status"] == "ok"

        second = family_init("dup_circle", owner_name="owner2")
        assert second["status"] == "error", f"Expected error, got {second}"

    tmp_path.unlink(missing_ok=True)
    for ext in ["-wal", "-shm"]:
        p = Path(str(tmp_path) + ext)
        p.unlink(missing_ok=True)

    print("  PASS test_family_init_duplicate")


def test_family_invite():
    """Test: family_invite adds a member."""
    from memall.federation.family import family_init, family_invite

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        tmp_path = Path(tmp.name)

    with patch("memall.federation.family.get_family_db_path", return_value=tmp_path), \
         patch("memall.federation.family._FAMILY_DB_INITIALIZED", False):
        family_init("test_circle", owner_name="admin")
        result = family_invite("test_circle", "new_member", role="member", invited_by="admin")

    tmp_path.unlink(missing_ok=True)
    for ext in ["-wal", "-shm"]:
        p = Path(str(tmp_path) + ext)
        p.unlink(missing_ok=True)

    assert result["status"] == "ok", f"Expected 'ok', got {result}"
    assert result["member"] == "new_member"
    print("  PASS test_family_invite")


def test_family_invite_nonexistent_circle():
    """Test: family_invite returns error for non-existent circle."""
    from memall.federation.family import family_invite

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        tmp_path = Path(tmp.name)

    with patch("memall.federation.family.get_family_db_path", return_value=tmp_path), \
         patch("memall.federation.family._FAMILY_DB_INITIALIZED", False):
        result = family_invite("nonexistent", "member1")

    tmp_path.unlink(missing_ok=True)
    for ext in ["-wal", "-shm"]:
        p = Path(str(tmp_path) + ext)
        p.unlink(missing_ok=True)

    assert result["status"] == "error", f"Expected error, got {result}"
    print("  PASS test_family_invite_nonexistent_circle")


def test_family_list():
    """Test: family_list returns members."""
    from memall.federation.family import family_init, family_invite, family_list

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        tmp_path = Path(tmp.name)

    with patch("memall.federation.family.get_family_db_path", return_value=tmp_path), \
         patch("memall.federation.family._FAMILY_DB_INITIALIZED", False):
        family_init("my_circle", owner_name="admin")
        family_invite("my_circle", "alice", role="member", invited_by="admin")
        family_invite("my_circle", "bob", role="admin", invited_by="admin")

        members = family_list("my_circle")

    tmp_path.unlink(missing_ok=True)
    for ext in ["-wal", "-shm"]:
        p = Path(str(tmp_path) + ext)
        p.unlink(missing_ok=True)

    assert isinstance(members, list), f"Expected list, got {type(members)}"
    assert len(members) == 3, f"Expected 3 members, got {len(members)}"
    names = [m["member"] for m in members]
    assert "admin" in names
    assert "alice" in names
    assert "bob" in names
    print("  PASS test_family_list")


def test_family_list_all():
    """Test: family_list returns all circles when no name given."""
    from memall.federation.family import family_init, family_list

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        tmp_path = Path(tmp.name)

    with patch("memall.federation.family.get_family_db_path", return_value=tmp_path), \
         patch("memall.federation.family._FAMILY_DB_INITIALIZED", False):
        family_init("circle_a", owner_name="admin")
        family_init("circle_b", owner_name="admin")

        all_members = family_list()

    tmp_path.unlink(missing_ok=True)
    for ext in ["-wal", "-shm"]:
        p = Path(str(tmp_path) + ext)
        p.unlink(missing_ok=True)

    assert isinstance(all_members, list)
    assert len(all_members) >= 2
    print("  PASS test_family_list_all")


# ── Runner ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 60)
    print("MemALL Federation Family Tests")
    print("=" * 60)
    passed = 0
    failed = 0

    tests = [
        ("test_family_init", test_family_init),
        ("test_family_init_duplicate", test_family_init_duplicate),
        ("test_family_invite", test_family_invite),
        ("test_family_invite_nonexistent_circle", test_family_invite_nonexistent_circle),
        ("test_family_list", test_family_list),
        ("test_family_list_all", test_family_list_all),
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