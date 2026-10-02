"""Unit tests for the canonical ``infer_project`` (single source of truth)."""

from memall.core.project_infer import infer_project


def test_agent_map_precedence():
    assert infer_project(agent_name="workbuddy") == "memall"
    assert infer_project(agent_name="douyin-daily") == "douyin-daily"
    assert infer_project(agent_name="marvis") == "memall"
    assert infer_project(agent_name="opencode") == "memall"
    assert infer_project(agent_name="claude") == "memall"
    # Substring match (e.g. "workbuddy-2") still maps.
    assert infer_project(agent_name="workbuddy-assistant") == "memall"


def test_content_heuristics():
    assert infer_project(content="今天发了抖音短视频带货") == "douyin-daily"
    assert infer_project(content="agent hub 路由配置") == "memall-agent-hub"
    assert infer_project(content="desktop electron 打包") == "memall-desktop"
    assert infer_project(content="股票交易分析模型") == "tradingagents"


def test_default_when_no_signal():
    assert infer_project(agent_name="unknown_agent", content="随便写点东西") == "memall"
    assert infer_project() == "memall"
