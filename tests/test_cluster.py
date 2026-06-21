"""
Test Suite — Pipeline Cluster
==============================
Tests cluster_step() and internal clustering helpers.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from memall.core.nlp import compute_tfidf, cosine_sim


def test_cluster_step_empty_db():
    """Test: cluster_step returns zero when no memories exist."""
    from tests.test_helpers import init_temp_db, cleanup_temp_db
    from memall.pipeline.cluster import cluster_step

    db_path, patcher = init_temp_db()
    try:
        result = cluster_step(method="tfidf")
        assert isinstance(result, dict), f"Expected dict, got {type(result)}"
        assert result["clusters_created"] == 0, f"Expected 0, got {result['clusters_created']}"
        assert result["memories_clustered"] == 0, f"Expected 0, got {result['memories_clustered']}"
        print("  PASS test_cluster_step_empty_db")
    finally:
        cleanup_temp_db(db_path, patcher)


def test_cluster_step_with_short_content():
    """Test: cluster_step skips memories with short content."""
    from tests.test_helpers import init_temp_db, cleanup_temp_db, insert_memory
    from memall.pipeline.cluster import cluster_step
    from memall.core.db import get_conn

    db_path, patcher = init_temp_db()
    try:
        conn = get_conn()
        insert_memory(conn, "short")
        conn.close()

        result = cluster_step(method="tfidf")
        assert result["clusters_created"] == 0, f"Expected 0, got {result['clusters_created']}"
        print("  PASS test_cluster_step_with_short_content")
    finally:
        cleanup_temp_db(db_path, patcher)


def test_kmeans_pp_helpers():
    """Test: _kmeans_pp returns correct structure."""
    from memall.pipeline.cluster import _kmeans_pp

    docs = [
        {"hello": 0.5, "world": 0.3},
        {"hello": 0.4, "foo": 0.6},
        {"world": 0.2, "bar": 0.8},
    ]
    centroids, assignments = _kmeans_pp(docs, k=2)
    assert len(centroids) == 2, f"Expected 2 centroids, got {len(centroids)}"
    assert len(assignments) == 3, f"Expected 3 assignments, got {len(assignments)}"
    print("  PASS test_kmeans_pp_helpers")


def test_cluster_coherence():
    """Test: _cluster_coherence returns a valid score."""
    from memall.pipeline.cluster import _cluster_coherence

    tfidf_docs = [
        {"hello": 0.5, "world": 0.3},
        {"hello": 0.4, "world": 0.2},
        {"foo": 0.6, "bar": 0.4},
    ]
    score = _cluster_coherence(tfidf_docs, [0, 0, 1], [0, 1])
    assert isinstance(score, float), f"Expected float, got {type(score)}"
    assert score >= 0.0, f"Expected non-negative, got {score}"
    print("  PASS test_cluster_coherence")


def test_cluster_label():
    """Test: _cluster_label returns a string label."""
    from memall.pipeline.cluster import _cluster_label

    tfidf_docs = [{"hello": 0.5, "world": 0.3}]
    label = _cluster_label(tfidf_docs, ["hello world test"], 0, [0])
    assert isinstance(label, str), f"Expected string, got {type(label)}"
    assert len(label) > 0, "Expected non-empty label"
    print("  PASS test_cluster_label")


# ── Runner ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 60)
    print("MemALL Pipeline Cluster Tests")
    print("=" * 60)
    passed = 0
    failed = 0

    tests = [
        ("test_cluster_step_empty_db", test_cluster_step_empty_db),
        ("test_cluster_step_with_short_content", test_cluster_step_with_short_content),
        ("test_kmeans_pp_helpers", test_kmeans_pp_helpers),
        ("test_cluster_coherence", test_cluster_coherence),
        ("test_cluster_label", test_cluster_label),
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