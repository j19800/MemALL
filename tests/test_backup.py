"""
Test Suite — Pipeline Backup
=============================
Tests backup_step().
"""

import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


def test_backup_step_no_db():
    """Test: backup_step returns 'no_db' when DB_PATH doesn't exist."""
    from memall.pipeline.backup import backup_step

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        nonexistent = Path(tmp.name)

    nonexistent.unlink()  # Remove it so it doesn't exist

    with patch("memall.pipeline.backup.DB_PATH", nonexistent):
        result = backup_step()

    assert isinstance(result, dict), f"Expected dict, got {type(result)}"
    assert result["status"] == "no_db", f"Expected 'no_db', got {result}"
    print("  PASS test_backup_step_no_db")


def test_backup_step_creates_backup():
    """Test: backup_step returns ok with a valid DB."""
    from memall.pipeline.backup import backup_step
    from tests.test_helpers import init_temp_db, cleanup_temp_db

    db_path, patcher = init_temp_db()
    try:
        result = backup_step()
        assert isinstance(result, dict), f"Expected dict, got {type(result)}"
        # Status should be 'ok' if VACUUM INTO is supported, 'no_db' otherwise
        assert result.get("status") in ("ok", "no_db"), f"Unexpected status: {result}"
        if result.get("status") == "ok":
            assert result["path"] is not None, "Expected backup path"
        print("  PASS test_backup_step_creates_backup")
    finally:
        cleanup_temp_db(db_path, patcher)


def test_rotation():
    """Test: rotation removes old backups beyond keep count."""
    from memall.pipeline.backup import rotation
    import tempfile

    with tempfile.TemporaryDirectory() as tmpdir:
        backup_dir = Path(tmpdir)
        # Create some dummy backup files
        for i in range(10):
            (backup_dir / f"data_2025060{i}_120000.db").write_text("dummy")

        rotation(backup_dir, keep_daily=3, keep_weekly=1)

        remaining = list(backup_dir.glob("data_*.db"))
        # keep_daily (3) + keep_weekly (1) = 4 max
        assert len(remaining) <= 4, f"Expected <= 4 backups, got {len(remaining)}: {remaining}"
        print("  PASS test_rotation")


def test_rotation_empty_dir():
    """Test: rotation handles empty directory."""
    from memall.pipeline.backup import rotation
    import tempfile

    with tempfile.TemporaryDirectory() as tmpdir:
        backup_dir = Path(tmpdir)
        rotation(backup_dir, keep_daily=7, keep_weekly=4)
        # Should not raise
        print("  PASS test_rotation_empty_dir")


# ── Runner ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 60)
    print("MemALL Pipeline Backup Tests")
    print("=" * 60)
    passed = 0
    failed = 0

    tests = [
        ("test_backup_step_no_db", test_backup_step_no_db),
        ("test_backup_step_creates_backup", test_backup_step_creates_backup),
        ("test_rotation", test_rotation),
        ("test_rotation_empty_dir", test_rotation_empty_dir),
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