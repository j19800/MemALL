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


def test_session_injection_carries_actual_memory_content():
    """Injection must contain WHAT was decided, not just how many items exist.

    Measured before: a database holding 14 memories injected 65 characters —
    every section was a counter ("待决(14)") and no memory text appeared at all.
    """
    db, orig = init_temp_db()
    try:
        from memall.core.models import MemoryInput
        from memall.core.thin_waist import capture
        from memall.pipeline.session import session_start

        capture(MemoryInput(content="数据库改用 MySQL 8.0，因为 DBA 团队只熟悉 MySQL。",
                            level="L4", category="decision", agent_name="inj", project="p"))
        capture(MemoryInput(content="下周要完成支付服务的熔断降级改造。",
                            level="L4", category="task", agent_name="inj", project="p"))

        out = session_start(agent_name="inj", auto_inject=True)
        fmt = out.get("injection_formatted") or ""
        assert len(fmt) > 120, f"injection too thin: {len(fmt)} chars: {fmt!r}"
        assert "MySQL" in fmt or "熔断" in fmt, f"no memory content injected: {fmt!r}"
        assert "[RECENT]" in fmt, fmt
        print("PASS: test_session_injection_carries_actual_memory_content")
    finally:
        cleanup_temp_db(db, orig)


def test_slot_kind_matches_category():
    """Slot namespace must reflect what the memory IS.

    A preference and a deployment procedure both used to land in
    slot:decision:* merely because they contained a decision-ish verb, so
    unrelated categories collided inside one namespace.
    """
    db, orig = init_temp_db()
    try:
        from memall.core.thin_waist import _slot_key_for_memory
        from memall.core.models import MemoryInput

        cases = [
            ("我偏好用 vim 开发，代码风格遵循 PEP8。", "preference"),
            ("部署流程：先跑 alembic 迁移，再滚动重启。", "procedure"),
            ("教训：上线前必须压测，这次没有压测就上了。", "reflection"),
        ]
        for text, kind in cases:
            mi = MemoryInput(content=text, level="L4", agent_name="x", project="pay")
            slot = _slot_key_for_memory(mi, text)
            assert slot and f":{kind}:" in slot, (text, slot)
        # A real decision still lands in the decision namespace.
        mi = MemoryInput(content="数据库选 PostgreSQL 16，主从部署。",
                         level="L4", agent_name="x", project="pay")
        assert ":decision:" in (_slot_key_for_memory(mi, mi.content) or "")
        print("PASS: test_slot_kind_matches_category")
    finally:
        cleanup_temp_db(db, orig)


def test_engineering_decisions_are_classified_as_decisions():
    """"数据库选 PostgreSQL" is unmistakably a decision but matched no keyword,
    so it was stored as `general` while the slot logic simultaneously called it
    slot:decision:* — two rule sets disagreeing about one sentence."""
    db, orig = init_temp_db()
    try:
        from memall.core.db import get_conn
        from memall.core.models import MemoryInput
        from memall.core.thin_waist import capture

        for text in ("数据库选 PostgreSQL 16，主从部署，读写分离。",
                     "缓存用 Redis 集群模式，配三个哨兵节点。",
                     "API 网关选 Kong，因为它支持插件热加载。"):
            mid = int(capture(MemoryInput(content=text, level="L4",
                                          agent_name="dec", project="pay")))
            c = get_conn()
            cat = c.execute("SELECT category FROM memories WHERE id=?", (mid,)).fetchone()[0]
            c.close()
            assert cat == "decision", (text, cat)
        print("PASS: test_engineering_decisions_are_classified_as_decisions")
    finally:
        cleanup_temp_db(db, orig)


def test_tasks_and_incidents_get_slots():
    """Work items change over time too; without a slot they can never be
    recognised as the same item."""
    db, orig = init_temp_db()
    try:
        from memall.core.thin_waist import _slot_key_for_memory
        from memall.core.models import MemoryInput

        cases = [
            ("下周要完成支付服务的熔断降级改造。", ":task:"),
            ("支付服务连接池耗尽导致雪崩，根因是慢查询。", ":problem:"),
        ]
        for text, marker in cases:
            mi = MemoryInput(content=text, level="L4", agent_name="x", project="pay")
            slot = _slot_key_for_memory(mi, text)
            assert slot and marker in slot, (text, slot)
        print("PASS: test_tasks_and_incidents_get_slots")
    finally:
        cleanup_temp_db(db, orig)


def test_read_path_does_not_scan_the_whole_table():
    """A search must not pay for a full-table regex pass.

    session_start maintenance ran identity_step on EVERY read, and identity_step
    read every non-archived memory and ran ten regexes over each: at 5000
    memories that was ~85% of a 0.69s search (10k _extract_matches calls, 70k
    finditer calls per query). The scan is now bounded to the newest N rows.
    """
    db, orig = init_temp_db()
    try:
        import memall.config as cfg
        from memall.core.db import get_conn
        from memall.core.models import MemoryInput
        from memall.core.thin_waist import capture
        from memall.pipeline import identity

        # Seed more rows than the scan limit so the bound is observable.
        cfg.reset_config()          # do not inherit a previous test's mutations
        cfg.get_config()
        cfg._config.setdefault("session", {})["identity_scan_limit"] = 50
        cfg._last_mtime_check = float("inf")

        for i in range(120):
            capture(MemoryInput(
                content=f"身份扫描测试记忆第{i}条 内容足够长通过门禁校验流程。",
                level="L4", agent_name="ids", project="p"))

        seen = {"n": 0}
        orig_extract = identity._extract_matches

        def counting(text, patterns):
            seen["n"] += 1
            return orig_extract(text, patterns)

        identity._extract_matches = counting
        try:
            out = identity.identity_step()
        finally:
            identity._extract_matches = orig_extract

        assert out.get("scanned", 0) <= 50, f"scan was not bounded: {out}"
        # identity_step runs two pattern groups per row (L1 and L7), so the
        # extractor is called at most 2x the row limit.
        assert seen["n"] <= 100, f"extractor ran {seen['n']} times for a 50-row limit"
        # And critically: it must NOT be proportional to the 120 seeded rows.
        assert seen["n"] < 120, f"extractor scaled with the table, not the limit: {seen['n']}"
        print("PASS: test_read_path_does_not_scan_the_whole_table")
    finally:
        from memall.config import reset_config
        reset_config()
        cleanup_temp_db(db, orig)
