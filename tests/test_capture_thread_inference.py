"""Regression tests for the thread_id (thread-aware) defect fix.

``thread_id`` was never assigned on the write path, leaving
``traverse(..., thread_aware=True)`` permanently dead. ``capture()`` now links a
new memory to the root of its (agent, project, time-window) thread so the feature
actually works — including for historical data via backfill.
"""

from memall.core.thin_waist import capture, retrieve, traverse


def _thread_of(mid):
    m = retrieve(mid)
    return m.thread_id if m is not None else None


def test_thread_inference_links_child_to_root():
    m1 = capture(
        "Thread 测试根记忆内容足够长以便通过质量门并且代表一次会话的开始。",
        agent_name="thr_test",
        project="memall",
    )
    m2 = capture(
        "Thread 测试子记忆内容也足够长以便通过质量门并且属于同一会话。",
        agent_name="thr_test",
        project="memall",
    )
    assert _thread_of(m1) is None           # the earliest memory is the root
    assert _thread_of(m2) == m1             # child points at the root


def test_thread_inference_skips_different_project():
    m1 = capture(
        "不同项目根记忆内容足够长以便通过质量门第一。",
        agent_name="thr_test",
        project="memall",
    )
    m2 = capture(
        "不同项目子记忆内容足够长以便通过质量门第二。",
        agent_name="thr_test",
        project="douyin-daily",
    )
    # Same agent but different project -> no thread link.
    assert _thread_of(m2) is None


def test_thread_aware_traverse_finds_root_and_siblings():
    m1 = capture(
        "遍历测试根记忆内容足够长以便通过质量门用于验证 thread_aware 检索。",
        agent_name="thr_test",
        project="memall",
    )
    m2 = capture(
        "遍历测试子记忆内容足够长以便通过质量门用于验证 thread_aware 检索。",
        agent_name="thr_test",
        project="memall",
    )
    res = traverse(m2, depth=1, thread_aware=True)
    node_ids = {n["id"] for n in res["nodes"]}
    assert m1 in node_ids
    assert m2 in node_ids


def test_thread_inference_disabled_by_zero_window():
    # NOTE: the config env override collapses underscores to dots
    # (MEMALL_CAPTURE_THREAD_INFERENCE_WINDOW_MINUTES -> capture.thread...
    # which never matches the dotted key capture.thread_inference_window_minutes),
    # so we patch the config dict directly instead of via env vars.
    from memall import config
    config.reset_config()
    config.get_config()  # populate internal cache with defaults
    config._config["capture"]["thread_inference_window_minutes"] = 0
    try:
        m1 = capture(
            "窗口关闭测试根记忆内容足够长以便通过质量门。",
            agent_name="thr_test",
            project="memall",
        )
        m2 = capture(
            "窗口关闭测试子记忆内容足够长以便通过质量门。",
            agent_name="thr_test",
            project="memall",
        )
        assert _thread_of(m2) is None
    finally:
        config.reset_config()
