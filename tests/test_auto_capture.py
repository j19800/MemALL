import json

from tests.test_helpers import init_temp_db, cleanup_temp_db


def _fresh_hook_state():
    # Reset in-process coarse dedup cache between tests.
    import memall.auto_capture as ac
    ac._recent_keys.clear()


def test_auto_capture_extracts_ask_and_persists():
    db, orig = init_temp_db()
    try:
        _fresh_hook_state()
        from memall.core.lifecycle import HookRegistry, HOOK_POST_TOOL_USE
        from memall.auto_capture import _auto_capture_hook  # ensure registered logic available
        from memall.core.db import get_conn

        args = {"action": "ask", "question": "为什么要用 PostgreSQL？", "mode": "stance"}
        result = json.dumps({"answer": "因为事务支持稳定、生态完整，团队也更熟悉运维。"}, ensure_ascii=False)
        _auto_capture_hook("memall_persona", args, result=result)

        conn = get_conn()
        rows = conn.execute(
            "SELECT id, level, category, subject, content, metadata FROM memories ORDER BY id DESC LIMIT 5"
        ).fetchall()
        conn.close()
        assert rows, "expected auto-captured memory"
        row = rows[0]
        assert row["level"] == "L4"
        assert row["category"] == "decision"
        assert "推演" in (row["subject"] or "") or "PostgreSQL" in (row["subject"] or "")
        assert "问：为什么要用 PostgreSQL？" in (row["content"] or "")
        assert "答：" in (row["content"] or "")
        meta = json.loads(row["metadata"] or "{}")
        assert meta.get("source") == "ask"
        print("PASS: test_auto_capture_extracts_ask_and_persists")
    finally:
        cleanup_temp_db(db, orig)


def test_auto_capture_skips_errors_and_short_content():
    db, orig = init_temp_db()
    try:
        _fresh_hook_state()
        from memall.auto_capture import _auto_capture_hook
        from memall.core.db import get_conn

        # error payload
        _auto_capture_hook("memall_persona", {"action": "ask", "question": "Q?"}, result=json.dumps({"error": "boom"}))
        # short extracted content
        _auto_capture_hook("memall_persona", {"action": "ask", "question": "hi"}, result=json.dumps({"answer": "ok"}))
        # non-target action
        _auto_capture_hook("memall_persona", {"action": "search", "question": "为什么要用 PostgreSQL？"}, result=json.dumps({"answer": "因为事务支持稳定、生态完整。"}))

        conn = get_conn()
        n = conn.execute("SELECT COUNT(*) c FROM memories").fetchone()["c"]
        conn.close()
        assert n == 0, f"expected no captures, got {n}"
        print("PASS: test_auto_capture_skips_errors_and_short_content")
    finally:
        cleanup_temp_db(db, orig)


def test_auto_capture_coarse_dedup_prevents_repeat_capture():
    db, orig = init_temp_db()
    try:
        _fresh_hook_state()
        from memall.auto_capture import _auto_capture_hook
        from memall.core.db import get_conn

        args = {"action": "ask", "question": "缓存策略怎么选？"}
        result = json.dumps({"answer": "优先使用读写分离缓存，并设置合理 TTL 与失效策略。"}, ensure_ascii=False)
        _auto_capture_hook("memall_persona", args, result=result)
        _auto_capture_hook("memall_persona", args, result=result)

        conn = get_conn()
        n = conn.execute("SELECT COUNT(*) c FROM memories").fetchone()["c"]
        conn.close()
        assert n == 1, f"expected coarse dedup to keep 1 capture, got {n}"
        print("PASS: test_auto_capture_coarse_dedup_prevents_repeat_capture")
    finally:
        cleanup_temp_db(db, orig)


def test_auto_capture_reflect_and_discussion_extractors():
    db, orig = init_temp_db()
    try:
        _fresh_hook_state()
        from memall.auto_capture import _auto_capture_hook
        from memall.core.db import get_conn

        _auto_capture_hook(
            "memall_system",
            {
                "action": "reflect",
                "memory_id": 42,
                "context": "这个教训很有价值，下次发布前必须先跑回归测试并补充回滚预案，避免同类线上事故重复发生。",
                "reflect_sub_action": "agree",
            },
            result=json.dumps({"ok": True}),
        )
        _auto_capture_hook(
            "memall_discussion",
            {
                "action": "respond",
                "discussion_id": 7,
                "stance": "agree",
                "arguments": "我支持先做灰度发布并观察核心指标，因为可以降低线上回滚风险，也更容易验证兼容性问题。",
                "agent_name": "reviewer",
            },
            result=json.dumps({"ok": True}),
        )

        conn = get_conn()
        rows = conn.execute("SELECT level, category, content, metadata, agent_name FROM memories ORDER BY id").fetchall()
        conn.close()
        assert len(rows) == 2, f"expected 2 captures, got {len(rows)}"
        r1, r2 = rows[0], rows[1]
        assert r1["category"] == "reflection"
        # level hint is L6; quality gate may downgrade to P2 in edge cases, so accept both.
        assert r1["level"] in ("L6", "P2")
        assert "反思 #42" in (r1["content"] or "")
        m1 = json.loads(r1["metadata"] or "{}")
        assert m1.get("source") == "reflect" and m1.get("target_memory_id") == 42
        assert r2["category"] == "discussion"
        assert r2["level"] in ("L4", "P2")
        assert "[支持]" in (r2["content"] or "")
        assert r2["agent_name"] == "reviewer"
        m2 = json.loads(r2["metadata"] or "{}")
        assert m2.get("source") == "discussion" and m2.get("discussion_id") == 7
        print("PASS: test_auto_capture_reflect_and_discussion_extractors")
    finally:
        cleanup_temp_db(db, orig)


def test_auto_capture_respects_configurable_min_length():
    db, orig = init_temp_db()
    try:
        _fresh_hook_state()
        import memall.config as cfg
        from memall.auto_capture import _auto_capture_hook
        from memall.core.db import get_conn

        # Raise threshold so a normal-length auto-capture is filtered.
        cfg.get_config()
        cfg._config.setdefault("capture", {})["auto_capture_min_content_len"] = 200
        cfg._last_mtime_check = float("inf")

        _auto_capture_hook(
            "memall_persona",
            {"action": "ask", "question": "为什么要用 PostgreSQL？"},
            result=json.dumps({"answer": "因为事务支持稳定、生态完整。"}),
        )
        conn = get_conn()
        n = conn.execute("SELECT COUNT(*) c FROM memories").fetchone()["c"]
        conn.close()
        assert n == 0, f"expected high min length to skip capture, got {n}"
        print("PASS: test_auto_capture_respects_configurable_min_length")
    finally:
        cleanup_temp_db(db, orig)

def test_auto_capture_can_be_disabled_by_config():
    db, orig = init_temp_db()
    try:
        _fresh_hook_state()
        import memall.config as cfg
        from memall.auto_capture import _auto_capture_hook
        from memall.core.db import get_conn

        cfg.reset_config()
        cfg.get_config()
        cfg._config.setdefault("capture", {})["auto_capture_enabled"] = False
        cfg._last_mtime_check = float("inf")

        _auto_capture_hook(
            "memall_persona",
            {"action": "ask", "question": "为什么要用 PostgreSQL？"},
            result=json.dumps({"answer": "因为事务支持稳定、生态完整，团队也更熟悉运维。"}),
        )
        conn = get_conn()
        n = conn.execute("SELECT COUNT(*) c FROM memories").fetchone()["c"]
        conn.close()
        assert n == 0, f"expected disabled auto-capture to skip, got {n}"
        print("PASS: test_auto_capture_can_be_disabled_by_config")
    finally:
        cleanup_temp_db(db, orig)


def test_auto_capture_records_decision_reasons_and_low_information_skip():
    db, orig = init_temp_db()
    try:
        _fresh_hook_state()
        import memall.auto_capture as ac
        import memall.config as cfg
        from memall.core.db import get_conn

        cfg.reset_config(); cfg.get_config(); cfg._last_mtime_check = float("inf")
        ac.clear_auto_capture_decisions()
        # short/low-signal answer -> low information gate (not min-length, not disabled)
        ac._auto_capture_hook(
            "memall_persona",
            {"action": "ask", "question": "缓存策略怎么选？", "mode": "stance"},
            result=json.dumps({"answer": "可以，完全没问题。"}),
        )
        conn = get_conn()
        n = conn.execute("SELECT COUNT(*) c FROM memories").fetchone()["c"]
        conn.close()
        assert n == 0
        d = ac.get_last_auto_capture_decision("memall_persona", "ask", "")
        assert d and d.get("reason") == "skipped_low_information", d
        print("PASS: test_auto_capture_records_decision_reasons_and_low_information_skip")
    finally:
        cleanup_temp_db(db, orig)


def test_auto_capture_persistent_dedup_across_restart_and_reports_reason():
    db, orig = init_temp_db()
    try:
        _fresh_hook_state()
        import memall.auto_capture as ac
        import memall.config as cfg
        from memall.core.db import get_conn

        cfg.reset_config(); cfg.get_config(); cfg._last_mtime_check = float("inf")
        ac.clear_auto_capture_decisions()
        args = {"action": "ask", "question": "为什么要做幂等设计？"}
        result = json.dumps({"answer": "因为重试和网络抖动会导致重复请求，幂等能保证副作用只发生一次并提升系统可靠性。"}, ensure_ascii=False)
        ac._auto_capture_hook("memall_persona", args, result=result)

        # Simulate process restart: clear in-process state but keep DB persistent dedup table.
        ac._recent_keys.clear()
        ac.clear_auto_capture_decisions()
        ac._auto_capture_hook("memall_persona", args, result=result)
        ac._recent_keys.clear()
        ac.clear_auto_capture_decisions()
        ac._auto_capture_hook("memall_persona", args, result=result)

        conn = get_conn()
        n = conn.execute("SELECT COUNT(*) c FROM memories").fetchone()["c"]
        rows = conn.execute("SELECT hit_count FROM auto_capture_recent").fetchall()
        conn.close()
        assert n == 1, f"expected persistent dedup to keep 1 capture across restarts, got {n}"
        assert rows and int(rows[0]["hit_count"] or 0) >= 3
        d = ac.get_last_auto_capture_decision("memall_persona", "ask", "")
        assert d and d.get("reason") == "skipped_dedup_persistent", d
        dedup = d.get("dedup") or {}
        assert dedup.get("persistent_hit") is True
        assert int(dedup.get("hit_count") or 0) >= 3
        print("PASS: test_auto_capture_persistent_dedup_across_restart_and_reports_reason")
    finally:
        cleanup_temp_db(db, orig)


def test_auto_capture_info_increment_gate_can_be_disabled_for_compat():
    db, orig = init_temp_db()
    try:
        _fresh_hook_state()
        import memall.auto_capture as ac
        import memall.config as cfg
        from memall.core.db import get_conn

        cfg.reset_config(); cfg.get_config()
        cfg._config.setdefault("capture", {})["auto_capture_info_increment_enabled"] = False
        cfg._last_mtime_check = float("inf")
        ac.clear_auto_capture_decisions()
        ac._auto_capture_hook(
            "memall_persona",
            {"action": "ask", "question": "缓存策略怎么选？", "mode": "stance"},
            result=json.dumps({"answer": "可以结合业务读写比例选择缓存策略，并明确失效与一致性方案。"}),
        )
        conn = get_conn()
        n = conn.execute("SELECT COUNT(*) c FROM memories").fetchone()["c"]
        conn.close()
        assert n == 1, f"expected capture when info increment gate disabled, got {n}"
        d = ac.get_last_auto_capture_decision("memall_persona", "ask", "")
        assert d and d.get("reason") == "captured", d
        print("PASS: test_auto_capture_info_increment_gate_can_be_disabled_for_compat")
    finally:
        cleanup_temp_db(db, orig)


def test_auto_capture_extractors_config_restriction_narrows_whitelist():
    """TD-AUTO-SRC-01: capture.auto_capture_extractors may narrow the whitelist;
    default (absent) keeps all three built-in sources enabled."""
    db, orig = init_temp_db()
    try:
        _fresh_hook_state()
        import memall.auto_capture as ac
        import memall.config as cfg
        from memall.core.db import get_conn

        cfg.reset_config(); cfg.get_config(); cfg._last_mtime_check = float("inf")
        # Default: none set -> all built-ins active.
        assert ac._extractor_allowed(("memall_persona", "ask"))
        assert ac._extractor_allowed(("memall_system", "reflect"))

        cfg._config.setdefault("capture", {})["auto_capture_extractors"] = ["memall_persona.ask"]

        args_ask = {"action": "ask", "question": "为什么要做幂等设计？"}
        res_ask = json.dumps({"answer": "因为重试和网络抖动会导致重复请求，幂等保证副作用只发生一次并提升可靠性。"}, ensure_ascii=False)
        # reflect with a real memory_id: would capture if not restricted.
        args_ref = {"action": "reflect", "memory_id": 42, "context": "本次故障根因是缓存击穿，需要引入互斥重建策略来加固系统。"}

        ac._auto_capture_hook("memall_persona", args_ask, result=res_ask)
        ac._auto_capture_hook("memall_system", args_ref, result=json.dumps({"ok": True}))

        conn = get_conn()
        n = conn.execute("SELECT COUNT(*) c FROM memories").fetchone()["c"]
        conn.close()
        assert n == 1, f"restricted whitelist should allow only ask captures, got {n}"
        d = ac.get_last_auto_capture_decision("memall_system", "reflect", "")
        assert d and d.get("reason") == "skipped_disabled", d
        # Sanity: removing the restriction re-enables reflect capture.
        cfg._config["capture"]["auto_capture_extractors"] = None
        ac._recent_keys.clear()
        ac._auto_capture_hook("memall_system", args_ref, result=json.dumps({"ok": True}))
        conn = get_conn()
        n2 = conn.execute("SELECT COUNT(*) c FROM memories").fetchone()["c"]
        conn.close()
        assert n2 == 2, f"unrestricted reflect should capture, got {n2}"
        print("PASS: test_auto_capture_extractors_config_restriction_narrows_whitelist")
    finally:
        cleanup_temp_db(db, orig)


def test_auto_capture_extractor_registration_extension_point():
    """TD-AUTO-SRC-01: register/unregister are the explicit extension points."""
    db, orig = init_temp_db()
    try:
        _fresh_hook_state()
        import memall.auto_capture as ac
        from memall.core.db import get_conn

        def _extract_custom(arguments, result):
            ans = (result or {}).get("summary") or ""
            if not ans:
                return "", {}
            return str(ans), {
                "subject": "自定义简报",
                "category": "general",
                "level": "L4",
                "metadata": {"source": "custom", "target_memory_id": arguments.get("target_memory_id")},
            }

        prev = ac.register_auto_capture_extractor("memall_persona", "brief", _extract_custom)
        try:
            assert ("memall_persona", "brief") in ac.auto_capture_extractor_whitelist()
            assert prev is None
            ac._auto_capture_hook(
                "memall_persona", {"action": "brief", "target_memory_id": 7},
                result=json.dumps({"summary": "自定义来源：季度目标达成率 85%，主要缺口在留存指标。"}, ensure_ascii=False))
            conn = get_conn()
            row = conn.execute("SELECT content, metadata FROM memories ORDER BY id DESC LIMIT 1").fetchone()
            conn.close()
            assert row and "季度目标达成率" in row["content"]
            assert "custom" in (row["metadata"] or "")
        finally:
            assert ac.unregister_auto_capture_extractor("memall_persona", "brief")
            assert ("memall_persona", "brief") not in ac.auto_capture_extractor_whitelist()
        print("PASS: test_auto_capture_extractor_registration_extension_point")
    finally:
        cleanup_temp_db(db, orig)


def test_auto_capture_persistent_dedup_config_tuning():
    """TD hardening: persistent window/min-hits/retention/max-rows are config-tunable;
    defaults preserve the existing contract (300s, 2 hits, window*12 retention)."""
    from memall.auto_capture import _persistent_dedup_settings, _PERSISTENT_DEDUP_WINDOW_SECONDS, _PERSISTENT_DEDUP_MIN_HITS
    import memall.config as cfg

    db, orig = init_temp_db()
    try:
        cfg.reset_config(); cfg.get_config(); cfg._last_mtime_check = float("inf")

        # Defaults -> exact legacy contract.
        w, h, r = _persistent_dedup_settings()
        assert w == _PERSISTENT_DEDUP_WINDOW_SECONDS == 300
        assert h == _PERSISTENT_DEDUP_MIN_HITS == 2
        assert r == max(300 * 12, 3600)

        cap = cfg._config.setdefault("capture", {})
        cap["auto_capture_persistent_window_seconds"] = 60
        cap["auto_capture_persistent_min_hits"] = 3
        cap["auto_capture_retention_seconds"] = 900
        w, h, r = _persistent_dedup_settings()
        assert (w, h, r) == (60, 3, 900)

        # Retention can never undercut the window.
        cap["auto_capture_retention_seconds"] = 5
        w, h, r = _persistent_dedup_settings()
        assert r >= w

        # Absurd values clamped.
        cap["auto_capture_persistent_window_seconds"] = 10**9
        cap["auto_capture_persistent_min_hits"] = -4
        w, h, r = _persistent_dedup_settings()
        assert w == 86_400 and h == 1

        print("PASS: test_auto_capture_persistent_dedup_config_tuning")
    finally:
        cleanup_temp_db(db, orig)
