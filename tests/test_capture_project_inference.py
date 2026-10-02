"""Regression tests for the project-inference defect fix.

These pin the *public seam* ``capture`` / ``smart_store`` / ``store_batch`` /
``add`` so the project field is always populated regardless of which write path
was used — the root-cause fix for the ~77% empty-project data-quality issue.
"""

from memall.core.thin_waist import capture, smart_store, store_batch, retrieve
from memall.agent_memory import add


def _project_of(mid):
    m = retrieve(mid)
    return m.project if m is not None else None


def test_capture_infers_project_from_agent():
    mid = capture(
        "MemALL 需要在 capture 入口统一 project 推断，因为这样能确保写入层的字段完整性。",
        agent_name="workbuddy",
    )
    assert _project_of(mid) == "memall"


def test_capture_infers_project_from_content():
    mid = capture(
        "今天发布了抖音短视频带货视频，数据表现不错。",
        agent_name="some_custom_agent",
    )
    assert _project_of(mid) == "douyin-daily"


def test_capture_keeps_explicit_project():
    mid = capture(
        "这条记忆属于一个明确指定的项目。",
        agent_name="workbuddy",
        project="my_explicit_project",
    )
    assert _project_of(mid) == "my_explicit_project"


def test_smart_store_infers_project():
    res = smart_store(
        "smart_store 也应通过 capture 统一推断出 project 字段。",
        agent_name="claude",
    )
    assert res["status"] == "new"
    assert _project_of(res["id"]) == "memall"


def test_store_batch_infers_project():
    res = store_batch([
        {"content": "批量写入的第一条记忆需要带 project。", "agent_name": "marvis"},
        {"content": "批量写入的第二条记忆也需要带 project。", "agent_name": "marvis"},
    ])
    assert res["count"] == 2
    for mid in res["ids"]:
        assert _project_of(mid) == "memall"


def test_agent_memory_add_infers_project():
    mid = add("通过 agent_memory.add 写入也应推断 project。", agent="opencode")
    assert _project_of(mid) == "memall"
