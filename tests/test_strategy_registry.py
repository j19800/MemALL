"""
Test Suite — Strategy Registry
===============================
Tests get_strategy, register, get_registered_strategies, clear_cache.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


def test_get_strategy_default():
    from memall.strategy.registry import get_strategy, clear_cache
    clear_cache()
    s = get_strategy("default_test")
    assert s is not None
    from memall.strategy.buffer import BufferStrategy
    assert isinstance(s, BufferStrategy), f"Expected BufferStrategy, got {type(s)}"
    print("  PASS test_get_strategy_default")


def test_get_strategy_by_name():
    from memall.strategy.registry import get_strategy, clear_cache
    clear_cache()
    s = get_strategy("test_agent", "buffer")
    from memall.strategy.buffer import BufferStrategy
    assert isinstance(s, BufferStrategy)
    print("  PASS test_get_strategy_by_name")


def test_get_strategy_cached():
    from memall.strategy.registry import get_strategy, clear_cache
    clear_cache()
    s1 = get_strategy("cached_test", "buffer")
    s2 = get_strategy("cached_test", "buffer")
    assert s1 is s2, "Expected same cached instance"
    print("  PASS test_get_strategy_cached")


def test_get_registered_strategies():
    from memall.strategy.registry import get_registered_strategies
    names = get_registered_strategies()
    assert "buffer" in names
    assert "summary" in names
    assert "entity" in names
    assert "kg" in names
    print("  PASS test_get_registered_strategies")


def test_clear_cache():
    from memall.strategy.registry import get_strategy, clear_cache
    clear_cache()
    s1 = get_strategy("clear_test", "buffer")
    clear_cache()
    s2 = get_strategy("clear_test", "buffer")
    assert s1 is not s2, "Expected different instance after cache clear"
    print("  PASS test_clear_cache")


def test_fallback_to_buffer():
    from memall.strategy.registry import get_strategy, clear_cache
    clear_cache()
    s = get_strategy("unknown_test", "nonexistent_strategy")
    from memall.strategy.buffer import BufferStrategy
    assert isinstance(s, BufferStrategy), "Expected fallback to BufferStrategy"
    print("  PASS test_fallback_to_buffer")


if __name__ == "__main__":
    print("=" * 60)
    print("Strategy Registry Tests")
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