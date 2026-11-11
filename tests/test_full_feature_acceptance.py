"""Full-feature acceptance: quality scoring, vector search, telemetry wiring.

Each assertion pins a defect found by turning EVERY feature on and driving the
real MCP surface — the kind of gap that per-feature unit tests miss.
"""
import json

from tests.test_helpers import init_temp_db, cleanup_temp_db


def _cfg_on(paths):
    import memall.config as cfg
    cfg.get_config()
    for p in paths:
        parts = p.split(".")
        cur = cfg._config
        for k in parts[:-1]:
            cur = cur.setdefault(k, {})
        cur[parts[-1]] = True
    cfg._last_mtime_check = float("inf")


def test_normal_memories_are_accepted_not_flagged_review():
    """Ordinary, well-formed memories must clear the gate.

    Before: EVERY normal capture landed in `review` because three scoring
    dimensions were mis-scaled — specificity only counted digits/acronyms
    (Chinese specifics scored 1), completeness demanded a 210-character essay,
    and relevance was a pure length test. A gate that flags everything protects
    nothing.
    """
    db, orig = init_temp_db()
    try:
        from memall.core.models import MemoryInput
        from memall.core.thin_waist import capture

        cases = [
            "项目决定使用 PostgreSQL 16 作为主数据库，团队已有运维经验。",
            "我偏好深色主题，字号 14px，习惯晚上做代码审查。",
            "修复了登录接口 token 过期返回 500 的 bug，已加回归测试。",
            "明天下午三点前完成 MCP 协议联调，周五交付验收。",
            "反思：沟通成本偏高，应提前做设计评审再动手编码。",
            "数据库连接池耗尽导致服务雪崩，根因是慢查询堆积。",
        ]
        gates = []
        for text in cases:
            m = capture(MemoryInput(content=text, level="L4",
                                    agent_name="acc", project="p"))
            d = m.to_dict() if hasattr(m, "to_dict") else {}
            gates.append((d.get("quality") or {}).get("gate"))
        accepted = sum(1 for g in gates if g == "accepted")
        assert accepted >= 5, f"normal memories should pass the gate: {gates}"
        print("PASS: test_normal_memories_are_accepted_not_flagged_review")
    finally:
        cleanup_temp_db(db, orig)


def test_junk_is_still_rejected_after_relaxing_the_scales():
    """Loosening the scales must NOT let junk through — the discriminating power
    is the whole point of the gate."""
    db, orig = init_temp_db()
    try:
        from memall.core.models import MemoryInput
        from memall.core.thin_waist import capture

        junk = ["嗯那个然后就是那个。", "哈哈哈哈", " "]
        for text in junk:
            try:
                capture(MemoryInput(content=text, level="L4",
                                    agent_name="junk", project="p"))
            except ValueError:
                continue  # hard reject is also a pass
            raise AssertionError(f"junk must not be accepted verbatim: {text!r}")
        print("PASS: test_junk_is_still_rejected_after_relaxing_the_scales")
    finally:
        cleanup_temp_db(db, orig)


def test_vector_search_actually_returns_results():
    """The whole vector path was dead: sqlite-vec rejected `LIMIT ?` and the
    error was swallowed, so mode=vector returned 0 rows for EVERY query."""
    db, orig = init_temp_db()
    try:
        from memall.core.models import MemoryInput
        from memall.core.thin_waist import capture
        from memall.graph.embeddings import build_index
        from memall.search.vec0_provider import Vec0Provider

        texts = [
            "部署流程：先跑迁移脚本，再重启 worker，最后验证健康检查。",
            "反思：沟通成本偏高，应提前做设计评审再动手编码。",
            "数据库连接池耗尽导致服务雪崩，根因是慢查询堆积。",
        ]
        for t in texts:
            capture(MemoryInput(content=t, level="L4", agent_name="vec", project="p"))
        build_index(force=True)

        out = Vec0Provider().search("部署", top_k=3)
        assert not out.get("error"), out
        assert out.get("total", 0) >= 1, f"vector search returned nothing: {out}"
        print("PASS: test_vector_search_actually_returns_results")
    finally:
        cleanup_temp_db(db, orig)


def test_utility_telemetry_is_reachable_over_mcp():
    """The action was advertised in the schema but absent from _ACTION_MODELS,
    so every call was rejected with 'unknown action'."""
    db, orig = init_temp_db()
    try:
        from memall.mcp.adapter import handle_call
        raw = handle_call("memall_system", {"action": "utility_telemetry"})
        out = json.loads(raw)
        assert out.get("ok") is True, out
        tel = out.get("telemetry") or {}
        for key in ("calls", "applied", "ranking_disturbance",
                    "cold_start_share", "mean_latency_ms"):
            assert key in tel, (key, sorted(tel))
        print("PASS: test_utility_telemetry_is_reachable_over_mcp")
    finally:
        cleanup_temp_db(db, orig)


def test_problem_category_recognises_incident_language():
    """An incident rarely contains the literal word 问题."""
    db, orig = init_temp_db()
    try:
        from memall.core.db import get_conn
        from memall.core.models import MemoryInput
        from memall.core.thin_waist import capture

        for text in ("数据库连接池耗尽导致服务雪崩，根因是慢查询堆积。",
                     "Redis 缓存穿透导致数据库压力暴涨，已加布隆过滤器。",
                     "API 网关超时引发上游雪崩，根因是连接泄漏。"):
            mid = int(capture(MemoryInput(content=text, level="L4",
                                          agent_name="prob", project="p")))
            c = get_conn()
            cat = c.execute("SELECT category FROM memories WHERE id=?", (mid,)).fetchone()[0]
            c.close()
            assert cat == "problem", (text, cat)
        print("PASS: test_problem_category_recognises_incident_language")
    finally:
        cleanup_temp_db(db, orig)
