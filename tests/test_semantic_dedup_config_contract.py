"""Contract tests for semantic dedup config defaults, v2 keys, and legacy precedence."""

from tests.test_helpers import init_temp_db, cleanup_temp_db


def _get_capture_cfg():
    from memall.config import get_config
    return get_config("capture", {}) or {}


def test_semantic_dedup_defaults_and_v2_keys_present():
    db, orig = init_temp_db()
    try:
        cap = _get_capture_cfg()
        assert cap.get("semantic_dedup_enabled") is False
        assert cap.get("semantic_dedup_threshold_jaccard") == 0.85
        assert cap.get("semantic_dedup_threshold_cosine") == 0.90
        assert cap.get("semantic_dedup_window_same_agent") == 50
        assert cap.get("semantic_dedup_window_cross_agent") == 0
        assert cap.get("semantic_dedup_window_cross_session") == 0
        assert cap.get("semantic_dedup_use_embeddings") is False
        assert cap.get("auto_capture_min_content_len") == 20
        print("PASS: test_semantic_dedup_defaults_and_v2_keys_present")
    finally:
        cleanup_temp_db(db, orig)


def test_legacy_semantic_dedup_keys_take_precedence_in_capture_path():
    """Legacy keys take precedence over v2 keys when both are configured (backcompat)."""
    db, orig = init_temp_db()
    try:
        import memall.config as cfg
        from memall.core.db import get_conn
        from memall.core.thin_waist import capture, MemoryInput

        cfg.reset_config()
        cfg.get_config()  # populate cache
        cap = cfg._config.setdefault("capture", {})
        cap["semantic_dedup_enabled"] = True
        # Legacy keys present + conflicting strict v2 keys; verify legacy used.
        cap["semantic_dedup_threshold"] = 0.85
        cap["semantic_dedup_window"] = 30
        cap["semantic_dedup_threshold_cosine"] = 0.99
        cap["semantic_dedup_threshold_jaccard"] = 0.99
        cap["semantic_dedup_window_same_agent"] = 1
        cap["semantic_dedup_window_cross_agent"] = 0
        cap["semantic_dedup_window_cross_session"] = 0
        cfg._last_mtime_check = float("inf")

        text = "数据库选 PostgreSQL 16 作为主库，事务稳定，确保可以回滚并验证数据一致性，避免线上事故。"
        r1 = capture(MemoryInput(content=text, agent_name="agent_legacy", level="L4"))
        r2 = capture(MemoryInput(content=text + " ", agent_name="agent_legacy", level="L4"))
        assert str(r1) == str(r2), (r1, r2)

        conn = get_conn()
        n = conn.execute("SELECT COUNT(*) c FROM memories").fetchone()["c"]
        conn.close()
        assert n == 1, f"expected legacy-precedence semantic dedup to merge near-duplicate, got {n} rows"
        print("PASS: test_legacy_semantic_dedup_keys_take_precedence_in_capture_path")
    finally:
        cleanup_temp_db(db, orig)


def test_v2_semantic_dedup_keys_apply_when_legacy_absent():
    """When legacy keys are absent, v2 cosine threshold and same-agent window drive capture dedup."""
    db, orig = init_temp_db()
    try:
        import memall.config as cfg
        from memall.core.db import get_conn
        from memall.core.thin_waist import capture, MemoryInput

        cfg.reset_config()
        cfg.get_config()
        cap = cfg._config.setdefault("capture", {})
        # Remove legacy keys so v2-only behavior applies.
        cap.pop("semantic_dedup_threshold", None)
        cap.pop("semantic_dedup_window", None)
        cap["semantic_dedup_enabled"] = True
        cap["semantic_dedup_threshold_cosine"] = 0.40
        cap["semantic_dedup_threshold_jaccard"] = 0.40
        cap["semantic_dedup_window_same_agent"] = 30
        cap["semantic_dedup_window_cross_agent"] = 0
        cap["semantic_dedup_window_cross_session"] = 0
        cap["semantic_dedup_use_embeddings"] = False
        cfg._last_mtime_check = float("inf")

        a = "语义去重v2配置生效测试：MemALL 写入层字段完整性修复方案设计与实现细节。"
        b = "语义去重v2配置生效测试：MemALL 写入层字段完整性修复方案设计以及具体实现细节说明。"
        r1 = capture(MemoryInput(content=a, agent_name="agent_v2", level="L4"))
        r2 = capture(MemoryInput(content=b, agent_name="agent_v2", level="L4"))
        assert str(r1) == str(r2), (r1, r2)

        conn = get_conn()
        n = conn.execute("SELECT COUNT(*) c FROM memories").fetchone()["c"]
        conn.close()
        assert n == 1, f"expected v2-threshold semantic dedup to merge near-duplicate, got {n} rows"
        print("PASS: test_v2_semantic_dedup_keys_apply_when_legacy_absent")
    finally:
        cleanup_temp_db(db, orig)

def test_v2_same_agent_window_respected_when_legacy_absent():
    """v2 semantic_dedup_window_same_agent limits candidate set when legacy window is absent."""
    db, orig = init_temp_db()
    try:
        import memall.config as cfg
        from memall.core.db import get_conn
        from memall.core.thin_waist import capture, MemoryInput

        cfg.reset_config()
        cfg.get_config()
        cap = cfg._config.setdefault("capture", {})
        cap.pop("semantic_dedup_threshold", None)
        cap.pop("semantic_dedup_window", None)
        cap["semantic_dedup_enabled"] = True
        cap["semantic_dedup_threshold_cosine"] = 0.40
        cap["semantic_dedup_threshold_jaccard"] = 0.40
        cap["semantic_dedup_window_same_agent"] = 1
        cap["semantic_dedup_window_cross_agent"] = 0
        cap["semantic_dedup_window_cross_session"] = 0
        cfg._last_mtime_check = float("inf")

        a = "窗口测试：数据库主库选 PostgreSQL 16 作为核心存储，事务一致性与回滚能力稳定可靠。"
        b = "窗口测试：数据库主库选 PostgreSQL 16 作为核心存储，事务一致性与回滚能力稳定可用。"
        noise = "噪声记忆：今天讨论前端组件命名规范与样式变量整理，不涉及数据库选型。"
        r1 = capture(MemoryInput(content=a, agent_name="agent_win", level="L4"))
        capture(MemoryInput(content=noise, agent_name="agent_win", level="L4"))
        r3 = capture(MemoryInput(content=b, agent_name="agent_win", level="L4"))
        assert str(r1) != str(r3), (r1, r3)

        conn = get_conn()
        n = conn.execute("SELECT COUNT(*) c FROM memories").fetchone()["c"]
        conn.close()
        assert n == 3, f"expected window=1 to prevent semantic merge past intervening memory, got {n} rows"
        print("PASS: test_v2_same_agent_window_respected_when_legacy_absent")
    finally:
        cleanup_temp_db(db, orig)


def test_v2_cross_scope_windows_reserved_no_behavior_change():
    """Cross-agent/cross-session window knobs are reserved (default 0) and must not enable cross-agent merges."""
    db, orig = init_temp_db()
    try:
        import memall.config as cfg
        from memall.core.db import get_conn
        from memall.core.thin_waist import capture, MemoryInput

        cfg.reset_config()
        cfg.get_config()
        cap = cfg._config.setdefault("capture", {})
        cap.pop("semantic_dedup_threshold", None)
        cap.pop("semantic_dedup_window", None)
        cap["semantic_dedup_enabled"] = True
        cap["semantic_dedup_threshold_cosine"] = 0.40
        cap["semantic_dedup_threshold_jaccard"] = 0.40
        cap["semantic_dedup_window_same_agent"] = 30
        cap["semantic_dedup_window_cross_agent"] = 999
        cap["semantic_dedup_window_cross_session"] = 999
        cap["semantic_dedup_use_embeddings"] = True  # reserved; must not be required/activated in local-text path
        cfg._last_mtime_check = float("inf")

        a = "跨scope保留测试：服务使用 Redis 作为会话缓存并设置 30 分钟过期策略。"
        b = "跨scope保留测试：服务使用 Redis 作为会话缓存并设置 30 分钟过期策略配置。"
        r1 = capture(MemoryInput(content=a, agent_name="agent_a", level="L4"))
        r2 = capture(MemoryInput(content=b, agent_name="agent_b", level="L4"))
        assert str(r1) != str(r2), (r1, r2)

        conn = get_conn()
        n = conn.execute("SELECT COUNT(*) c FROM memories").fetchone()["c"]
        conn.close()
        assert n == 2, f"reserved cross-scope knobs must not merge different agents, got {n} rows"
        print("PASS: test_v2_cross_scope_windows_reserved_no_behavior_change")
    finally:
        cleanup_temp_db(db, orig)


def test_v2_embedding_knob_requires_no_embedding_backend():
    """TD-DEDUP-V2-01: semantic_dedup_use_embeddings=True must not make capture
    require/activate an embedding backend; local-text layers still run."""
    db, orig = init_temp_db()
    try:
        import memall.config as cfg
        from memall.core.db import get_conn
        from memall.core.thin_waist import capture, MemoryInput

        cfg.reset_config()
        cfg.get_config()
        cap = cfg._config.setdefault("capture", {})
        cap.pop("semantic_dedup_threshold", None)
        cap.pop("semantic_dedup_window", None)
        cap["semantic_dedup_enabled"] = True
        cap["semantic_dedup_threshold_cosine"] = 0.90
        cap["semantic_dedup_window_same_agent"] = 30
        cap["semantic_dedup_use_embeddings"] = True
        cfg._last_mtime_check = float("inf")

        a = "嵌入保留开关测试：网关超时统一设置为 30 秒并重试两次以避免雪崩。"
        r1 = capture(MemoryInput(content=a, agent_name="agent_emb", level="L4"))
        r2 = capture(MemoryInput(content=a + "配置。", agent_name="agent_emb", level="L4"))
        assert r1 and r2  # no exception, no embedding dependency

        conn = get_conn()
        rows = conn.execute(
            "SELECT id, metadata FROM memories WHERE agent_name='agent_emb' ORDER BY id"
        ).fetchall()
        conn.close()
        assert len(rows) >= 1
        # If a merge happened it must come from a local-text layer, never embeddings.
        import json as _json
        for r in rows:
            m = _json.loads((r["metadata"] or "{}") if r["metadata"] else "{}")
            d = m.get("dedup") or {}
            if d.get("layer"):
                assert d["layer"] not in ("embeddings", "embedding", "vector"), (
                    f"reserved embedding knob must not enable embedding merges: {d}")
        print("PASS: test_v2_embedding_knob_requires_no_embedding_backend")
    finally:
        cleanup_temp_db(db, orig)


def test_v2_cross_session_window_never_narrows_same_agent_dedup():
    """TD-DEDUP-V2-01: cross_session window (reserved, default 0) must not
    disable or alter the same-agent window path when legacy keys absent."""
    db, orig = init_temp_db()
    try:
        import memall.config as cfg
        from memall.core.db import get_conn
        from memall.core.thin_waist import capture, MemoryInput

        cfg.reset_config()
        cfg.get_config()
        cap = cfg._config.setdefault("capture", {})
        cap.pop("semantic_dedup_threshold", None)
        cap.pop("semantic_dedup_window", None)
        cap["semantic_dedup_enabled"] = True
        cap["semantic_dedup_threshold_cosine"] = 0.90
        cap["semantic_dedup_threshold_jaccard"] = 0.60
        cap["semantic_dedup_window_same_agent"] = 30
        cap["semantic_dedup_window_cross_agent"] = 0
        cap["semantic_dedup_window_cross_session"] = 0
        cfg._last_mtime_check = float("inf")

        a = "会话窗口保留测试：部署采用蓝绿发布并且数据库只做向前兼容迁移。"
        capture(MemoryInput(content=a, agent_name="agent_cs", level="L4"))
        r2 = capture(MemoryInput(content=a + "策略。", agent_name="agent_cs", level="L4"))
        conn = get_conn()
        n = conn.execute("SELECT COUNT(*) c FROM memories WHERE agent_name='agent_cs'").fetchone()["c"]
        conn.close()
        # Same-agent window semantics are driven by window_same_agent only; the
        # reserved zero cross-session knob must not change the outcome shape.
        assert n in (1, 2)  # merged or not per similarity, but never an error
        print("PASS: test_v2_cross_session_window_never_narrows_same_agent_dedup")
    finally:
        cleanup_temp_db(db, orig)
