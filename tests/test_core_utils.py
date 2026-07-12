"""
Test Suite — Core Utils
========================
Tests unwrap, and other utility functions.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


def test_unwrap_basic():
    """unwrap should extract value from versioned envelope."""
    from memall.core.utils import unwrap
    result = unwrap({"value": "hello", "_meta": {"version": 1}})
    assert result == "hello", f"Expected 'hello', got {result}"
    print("  PASS test_unwrap_basic")


def test_unwrap_no_meta():
    """unwrap should pass through dict without _meta."""
    from memall.core.utils import unwrap
    result = unwrap({"value": "hello", "other": "world"})
    assert result == {"value": "hello", "other": "world"}
    print("  PASS test_unwrap_no_meta")


def test_unwrap_nested():
    """unwrap should handle nested _meta."""
    from memall.core.utils import unwrap
    result = unwrap({"value": {"nested": "data"}, "_meta": {"version": 2}})
    assert result == {"nested": "data"}
    print("  PASS test_unwrap_nested")


def test_unwrap_non_dict():
    """unwrap should pass through non-dict values."""
    from memall.core.utils import unwrap
    assert unwrap("string") == "string"
    assert unwrap(42) == 42
    assert unwrap(None) is None
    assert unwrap([1, 2, 3]) == [1, 2, 3]
    print("  PASS test_unwrap_non_dict")


def test_unwrap_value_only():
    """unwrap should NOT unwrap bare 'value' key without _meta."""
    from memall.core.utils import unwrap
    result = unwrap({"value": 42})
    # Without _meta, the dict is returned as-is
    assert result == {"value": 42}, f"Expected dict, got {result}"
    print("  PASS test_unwrap_value_only")


if __name__ == "__main__":
    print("=" * 60)
    print("Core Utils Tests")
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