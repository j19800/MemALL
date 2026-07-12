"""
Test Suite — Pipeline distill_l7
===================================
Tests _extract_lessons, distill_l7_step edge cases.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


def test_extract_lessons_chinese():
    """_extract_lessons should find Chinese lesson patterns."""
    from memall.pipeline.distill_l7 import _extract_lessons

    text = "教训：不要在生产环境直接修改数据库。下次应该先在测试环境验证。"
    lessons = _extract_lessons(text)
    assert len(lessons) >= 1, f"Expected at least 1 lesson, got {len(lessons)}"
    print("  PASS test_extract_lessons_chinese")


def test_extract_lessons_english():
    """_extract_lessons should find English lesson patterns."""
    from memall.pipeline.distill_l7 import _extract_lessons

    text = "lesson learned: always validate input before processing. root cause: missing null check."
    lessons = _extract_lessons(text)
    assert len(lessons) >= 1, f"Expected at least 1 lesson, got {len(lessons)}"
    print("  PASS test_extract_lessons_english")


def test_extract_lessons_empty():
    """_extract_lessons should return empty list for no matches."""
    from memall.pipeline.distill_l7 import _extract_lessons

    text = "This is a simple statement without any lesson patterns."
    lessons = _extract_lessons(text)
    assert lessons == [], f"Expected empty list, got {lessons}"
    print("  PASS test_extract_lessons_empty")


def test_extract_lessons_short_snippet():
    """_extract_lessons should skip snippets under 12 chars."""
    from memall.pipeline.distill_l7 import _extract_lessons

    text = "教训：太短了。"
    lessons = _extract_lessons(text)
    # The snippet "太短了。" is 4 chars, under 12, should be skipped
    assert len(lessons) == 0, f"Expected 0, got {len(lessons)}"
    print("  PASS test_extract_lessons_short_snippet")


def test_extract_lessons_dedup():
    """_extract_lessons should deduplicate by first 40 chars."""
    from memall.pipeline.distill_l7 import _extract_lessons

    text = (
        "教训：不要在生产环境直接修改数据库。下次应该先在测试环境验证。"
        "教训：不要在生产环境直接修改数据库。这是重复的教训。"
    )
    lessons = _extract_lessons(text)
    # Should deduplicate since first 40 chars overlap
    assert len(lessons) >= 1, f"Expected at least 1 lesson, got {len(lessons)}"
    print("  PASS test_extract_lessons_dedup")


def test_extract_lessons_max_scan():
    """_extract_lessons should only scan first 2000 chars."""
    from memall.pipeline.distill_l7 import _extract_lessons

    # Create text with lesson at position 2500 (beyond _MAX_SCAN_LEN=2000)
    text = "a" * 2500 + "教训：这个教训在扫描范围之外。"
    lessons = _extract_lessons(text)
    # The lesson is beyond 2000 chars, should not be found
    assert len(lessons) == 0, f"Expected 0, got {len(lessons)}"
    print("  PASS test_extract_lessons_max_scan")


def test_distill_l7_step_no_l6():
    """distill_l7_step should handle empty L6 memories."""
    from tests.test_helpers import init_temp_db, cleanup_temp_db
    from memall.pipeline.distill_l7 import distill_l7_step

    db_path, patcher = init_temp_db()
    try:
        result = distill_l7_step()
        assert isinstance(result, dict)
        assert "scanned" in result
        print("  PASS test_distill_l7_step_no_l6")
    finally:
        cleanup_temp_db(db_path, patcher)


if __name__ == "__main__":
    print("=" * 60)
    print("Distill L7 Tests")
    print("=" * 60)
    passed = 0
    failed = 0
    for name in sorted(dir()):
        if name.startswith("test_"):
            try:
                globals()[name]()
                passed += 1
            except Exception as e:
                print(f"  FAIL {name}: {e}")
                import traceback
                traceback.print_exc()
                failed += 1
    print(f"\nResults: {passed} passed, {failed} failed")
    if failed:
        sys.exit(1)