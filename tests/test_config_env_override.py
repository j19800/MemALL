"""Regression tests for the MEMALL_* env-override resolution fix.

Root cause (defect): ``_apply_env_overrides`` collapsed *every* underscore to a
dot, so ``MEMALL_CAPTURE_THREAD_INFERENCE_WINDOW_MINUTES`` mapped to
``capture.thread.inference.window.minutes`` and never matched the real config
key ``capture.thread_inference_window_minutes`` (which carries an underscore
inside its segment).

Fix: resolve the literal dotted path first; if it does not exist in the config,
fall back to the *flattened* key (dots → underscores), so underscore-bearing
config keys are overridable from env.
"""
import pytest

from memall import config


def test_env_dotted_path_still_works(monkeypatch):
    """Existing contract: MEMALL_DB_PATH → db.path must keep working."""
    monkeypatch.setenv("MEMALL_DB_PATH", "/tmp/test.db")
    config.reset_config()
    try:
        assert config.get_config("db.path") == "/tmp/test.db"
    finally:
        config.reset_config()


def test_env_underscore_key_flattened_fallback(monkeypatch):
    """Underscore-bearing segment now resolves via the flattened key."""
    monkeypatch.setenv("MEMALL_CAPTURE_THREAD_INFERENCE_WINDOW_MINUTES", "30")
    config.reset_config()
    try:
        assert config.get_config("capture.thread_inference_window_minutes") == 30
    finally:
        config.reset_config()


def test_env_bool_coercion_on_underscore_key(monkeypatch):
    monkeypatch.setenv("MEMALL_CAPTURE_SEMANTIC_DEDUP_ENABLED", "true")
    config.reset_config()
    try:
        assert config.get_config("capture.semantic_dedup_enabled") is True
    finally:
        config.reset_config()


def test_env_nested_underscore_key(monkeypatch):
    monkeypatch.setenv("MEMALL_NLP_SENTENCE_TRANSFORMERS", "true")
    config.reset_config()
    try:
        assert config.get_config("nlp.sentence_transformers") is True
    finally:
        config.reset_config()


def test_env_unknown_key_creates_dotted_path(monkeypatch):
    """Unknown keys still get created as a dotted path (legacy behaviour)."""
    monkeypatch.setenv("MEMALL_CUSTOM_SECTION_FOO", "bar")
    config.reset_config()
    try:
        assert config.get_config("custom.section.foo") == "bar"
    finally:
        config.reset_config()
