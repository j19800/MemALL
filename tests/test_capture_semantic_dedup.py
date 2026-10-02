"""Regression tests for the (config-gated) semantic de-duplication at capture.

Semantic de-dup is OFF by default to avoid falsely merging legitimately distinct
memories. These tests enable it explicitly and isolate the config so other tests
are unaffected.
"""

import pytest
from memall.core.thin_waist import capture, retrieve
from memall import config


@pytest.fixture
def _enable_semantic_dedup():
    # NOTE: the config env override collapses underscores to dots
    # (MEMALL_CAPTURE_SEMANTIC_DEDUP_ENABLED -> capture.semantic.dedup.enabled,
    # which never matches capture.semantic_dedup_enabled), so we patch the
    # config dict directly rather than via env vars.
    config.reset_config()
    config.get_config()  # populate internal cache with defaults
    config._config["capture"]["semantic_dedup_enabled"] = True
    config._config["capture"]["semantic_dedup_threshold"] = 0.4
    config._config["capture"]["semantic_dedup_window"] = 30
    yield
    # Restore default config for subsequent tests.
    config.reset_config()


def test_semantic_dedup_merges_near_duplicate(_enable_semantic_dedup):
    a = ("语义去重测试记忆内容关于 MemALL 的写入层字段完整性修复方案设计与实现细节。")
    b = ("语义去重测试记忆内容关于 MemALL 的写入层字段完整性修复方案设计以及具体实现细节说明。")
    c = ("今天天气晴朗适合外出散步和运动锻炼身体保持健康生活方式。")

    mid_a = capture(a, agent_name="sem_test")
    mid_b = capture(b, agent_name="sem_test")
    mid_c = capture(c, agent_name="sem_test")

    # b is a near-duplicate of a -> should reuse a's id.
    assert mid_b == mid_a
    # c is genuinely different -> new id.
    assert mid_c != mid_a


def test_semantic_dedup_disabled_by_default_keeps_distinct():
    a = ("默认关闭语义去重时两条措辞相近但实质不同的记忆应得到不同 ID 用于验证。")
    b = ("默认关闭语义去重时两条措辞相近但实质不同的记忆应得到不同 ID 用于校验。")
    mid_a = capture(a, agent_name="sem_test")
    mid_b = capture(b, agent_name="sem_test")
    assert mid_a != mid_b
