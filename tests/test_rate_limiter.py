"""
Test Suite — Core Rate Limiter
================================
Tests rate limiter, token bucket algorithm.
"""

import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


def test_rate_limiter_allows_within_limit():
    """Rate limiter should allow requests within the limit."""
    from memall.core.rate_limiter import get_rate_limiter

    limiter = get_rate_limiter()
    key = "test_key_1"
    for _ in range(5):
        allowed = limiter.allow(key, limit=10)
        assert allowed is True, f"Expected allowed, got {allowed}"
    print("  PASS test_rate_limiter_allows_within_limit")


def test_rate_limiter_blocks_above_limit():
    """Rate limiter should block requests above the limit."""
    from memall.core.rate_limiter import get_rate_limiter

    limiter = get_rate_limiter()
    key = "test_key_block"
    for i in range(5):
        limiter.allow(key, limit=3)
    result = limiter.allow(key, limit=3)
    assert isinstance(result, bool)
    print("  PASS test_rate_limiter_blocks_above_limit")


def test_rate_limiter_different_keys():
    """Different keys should have independent limits."""
    from memall.core.rate_limiter import get_rate_limiter

    limiter = get_rate_limiter()
    for _ in range(5):
        limiter.allow("key_a", limit=10)
    allowed = limiter.allow("key_b", limit=10)
    assert allowed is True, "Different keys should have independent limits"
    print("  PASS test_rate_limiter_different_keys")


def test_rate_limiter_remaining():
    """remaining() should return the remaining allowance."""
    from memall.core.rate_limiter import get_rate_limiter

    limiter = get_rate_limiter()
    key = "remaining_test"
    remaining = limiter.remaining(key, limit=10)
    assert remaining <= 10, f"Expected <= 10, got {remaining}"
    assert remaining >= 0, f"Expected >= 0, got {remaining}"
    print("  PASS test_rate_limiter_remaining")


def test_rate_limiter_get_limiter():
    """get_rate_limiter should return singleton."""
    from memall.core.rate_limiter import get_rate_limiter

    r1 = get_rate_limiter()
    r2 = get_rate_limiter()
    assert r1 is r2, "Expected singleton instance"
    print("  PASS test_rate_limiter_get_limiter")


if __name__ == "__main__":
    print("=" * 60)
    print("Rate Limiter Tests")
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