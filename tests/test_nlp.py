"""
Test Suite — Core NLP
======================
Tests tokenize, compute_tfidf, cosine_sim.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from memall.core.nlp import tokenize, compute_tfidf, cosine_sim


def test_tokenize_basic():
    """Test: tokenize returns filtered tokens."""
    tokens = tokenize("Hello World 你好世界")
    assert len(tokens) > 0, "Expected non-empty tokens"
    # Check stopwords are removed
    assert "the" not in tokens, "Stopword 'the' should be removed"
    assert "的" not in tokens, "Stopword '的' should be removed"
    print("  PASS test_tokenize_basic")


def test_tokenize_short_tokens():
    """Test: tokenize removes single-character tokens."""
    tokens = tokenize("a b c hello world")
    assert "a" not in tokens, "Single char 'a' should be removed"
    assert "hello" in tokens, "'hello' should be present"
    print("  PASS test_tokenize_short_tokens")


def test_compute_tfidf():
    """Test: compute_tfidf returns correct structure."""
    docs = [
        "the cat sat on the mat",
        "the dog sat on the log",
        "cats and dogs are pets",
    ]
    tfidf_docs = compute_tfidf(docs)
    assert len(tfidf_docs) == 3, f"Expected 3 doc vectors, got {len(tfidf_docs)}"
    for tfidf in tfidf_docs:
        assert isinstance(tfidf, dict), f"Expected dict, got {type(tfidf)}"
        assert len(tfidf) > 0, "Expected non-empty TF-IDF vector"
    print("  PASS test_compute_tfidf")


def test_cosine_sim_identical():
    """Test: cosine_sim returns approximately 1.0 for identical vectors."""
    v = {"hello": 0.5, "world": 0.3}
    sim = cosine_sim(v, v)
    assert abs(sim - 1.0) < 1e-10, f"Expected ~1.0, got {sim}"
    print("  PASS test_cosine_sim_identical")


def test_cosine_sim_orthogonal():
    """Test: cosine_sim returns 0.0 for disjoint vectors."""
    sim = cosine_sim({"hello": 0.5}, {"world": 0.3})
    assert sim == 0.0, f"Expected 0.0, got {sim}"
    print("  PASS test_cosine_sim_orthogonal")


def test_cosine_sim_partial():
    """Test: cosine_sim returns value in (0, 1) for overlapping vectors."""
    sim = cosine_sim({"hello": 0.5, "world": 0.3}, {"hello": 0.4, "foo": 0.6})
    assert 0 < sim < 1, f"Expected 0 < sim < 1, got {sim}"
    print("  PASS test_cosine_sim_partial")


def test_cosine_sim_empty():
    """Test: cosine_sim handles empty dicts gracefully."""
    assert cosine_sim({}, {"a": 1.0}) == 0.0
    assert cosine_sim({"a": 1.0}, {}) == 0.0
    print("  PASS test_cosine_sim_empty")


# ── Runner ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 60)
    print("MemALL Core NLP Tests")
    print("=" * 60)
    passed = 0
    failed = 0

    tests = [
        ("test_tokenize_basic", test_tokenize_basic),
        ("test_tokenize_short_tokens", test_tokenize_short_tokens),
        ("test_compute_tfidf", test_compute_tfidf),
        ("test_cosine_sim_identical", test_cosine_sim_identical),
        ("test_cosine_sim_orthogonal", test_cosine_sim_orthogonal),
        ("test_cosine_sim_partial", test_cosine_sim_partial),
        ("test_cosine_sim_empty", test_cosine_sim_empty),
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