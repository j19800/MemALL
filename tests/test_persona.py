"""Tests for pipeline/persona.py — cognitive persona (color profile + prototype).

Covers the full public surface: feature extraction, color mapping, prototype
resolution, single/dual persona generation, persistence, and the batch step.
"""

from memall.core.thin_waist import capture
from memall.pipeline.persona import (
    extract_features,
    features_to_colors,
    colors_to_prototype,
    generate_persona,
    generate_dual_persona,
    save_persona,
    persona_step,
    _time_entropy,
)

AGENT = "persona_test_agent"

_TOPICS = [
    "结构化决策：我们决定采用方案 A，并给出明确结论与可执行步骤。",
    "技术选型：最终方案定为 FastAPI，因为异步支持好，团队熟悉度高。",
    "部署策略：必须本地优先，保证数据安全可控，这是确定的结论。",
    "复盘教训：上次直接改生产库很危险，下次先在测试环境验证。",
    "架构反思：模块边界不清导致耦合，需要拆分并明确接口契约。",
    "偏好记录：我倾向用 Python 做后端，工具链成熟且生态完整。",
]


def _seed(agent: str = AGENT) -> None:
    for i, text in enumerate(_TOPICS):
        capture(text, agent_name=agent, level="L1" if i % 2 == 0 else "L7")


def test_extract_features_no_memories():
    feats = extract_features("persona_agent_without_memories")
    assert "error" in feats
    assert feats["sample_size"] == 0


def test_extract_features_with_memories():
    _seed()
    feats = extract_features(AGENT)
    assert "error" not in feats
    assert feats["sample_size"] == len(_TOPICS)
    for key in (
        "certainty_score", "decision_ratio", "identity_signal",
        "domain_breadth", "capture_regularity",
    ):
        assert key in feats


def test_features_to_colors_sums_to_one():
    _seed()
    colors = features_to_colors(extract_features(AGENT))
    assert set(colors) == {"white", "blue", "black", "red", "green"}
    assert abs(sum(colors.values()) - 1.0) < 0.01


def test_colors_to_prototype_known_pair():
    proto = colors_to_prototype(
        {"white": 0.6, "blue": 0.2, "black": 0.1, "red": 0.05, "green": 0.05}
    )
    assert proto["en"] == "Arbiter"
    assert proto["cn"] == "裁决者"
    assert proto["primary_color"]["name"] == "白"
    assert proto["secondary_color"]["name"] == "蓝"


def test_colors_to_prototype_single_color():
    proto = colors_to_prototype(
        {"white": 0.9, "blue": 0.02, "black": 0.03, "red": 0.03, "green": 0.02}
    )
    assert proto["cn"] == "定锚"
    assert proto["secondary_color"] is None


def test_generate_persona_shape():
    _seed()
    profile = generate_persona(AGENT)
    assert "error" not in profile
    assert profile["time_range"] == "all"
    assert "features" in profile and "prototype" in profile
    assert "interpretation" in profile and "generated_at" in profile


def test_generate_dual_persona_delta():
    _seed()
    dual = generate_dual_persona(AGENT, dynamic_days=7)
    assert dual["agent_name"] == AGENT
    assert "static" in dual and "dynamic" in dual
    assert "prototype_shift" in dual["delta"]
    assert dual["delta"]["activity_ratio"] > 0


def test_save_persona_persists_profile_json():
    _seed()
    result = save_persona(AGENT)
    assert "error" not in result
    from memall.core.db import get_conn
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT profile_json FROM identities WHERE LOWER(agent_name) = LOWER(?)",
            (AGENT,),
        ).fetchone()
        assert row is not None
        assert row["profile_json"]
    finally:
        conn.close()


def test_persona_step_processes_seeded_agent():
    _seed()
    out = persona_step()
    assert out["agents_processed"] >= 1
    assert AGENT in out["agents"]


def test_time_entropy_edge_cases():
    assert _time_entropy([]) == 1.0
    assert _time_entropy([1]) == 1.0