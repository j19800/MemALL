"""Semantic dedup must catch re-wordings WITHOUT ever merging a reversal.

Measured: TF-IDF cosine scored a re-worded decision at 0.29 and a near-identical
sentence at 0.34, so the 0.85/0.92 thresholds could never fire — the feature was
dead weight. Character shingles fixed recall but introduced a FALSE MERGE:
"数据库使用 PostgreSQL 作为主库" and "数据库不使用 PostgreSQL 作为主库" differ
by one character and scored 0.95, silently destroying a decision reversal.
"""
import json

from tests.test_helpers import init_temp_db, cleanup_temp_db


def _enable_dedup(threshold=0.92, window=30):
    import memall.config as cfg
    cfg.reset_config()
    cfg.get_config()
    cfg._config.setdefault("capture", {}).update({
        "semantic_dedup_enabled": True,
        "semantic_dedup_threshold": threshold,
        "semantic_dedup_window": window,
    })
    cfg._last_mtime_check = float("inf")


def _capture(content, agent="dd", category="decision"):
    from memall.core.models import MemoryInput
    from memall.core.thin_waist import capture
    return int(capture(MemoryInput(content=content, level="L4",
                                   category=category, agent_name=agent, project="pay")))


def test_reworded_near_duplicate_is_merged():
    db, orig = init_temp_db()
    try:
        _enable_dedup()
        a = _capture("支付服务连接池耗尽导致雪崩，根因是慢查询。")
        b = _capture("支付服务连接池耗尽导致雪崩，根因是慢查询未加索引。")
        assert a == b, f"near-identical wording should merge: {a} vs {b}"
        print("PASS: test_reworded_near_duplicate_is_merged")
    finally:
        from memall.config import reset_config
        reset_config()
        cleanup_temp_db(db, orig)


def test_negation_is_never_merged():
    """One-character reversal must survive as a separate memory."""
    db, orig = init_temp_db()
    try:
        _enable_dedup()
        cases = [
            ("数据库使用 PostgreSQL 作为主库。", "数据库不使用 PostgreSQL 作为主库。"),
            ("这个方案可行，我们决定采用它。", "这个方案不可行，我们决定放弃它。"),
        ]
        for a_text, b_text in cases:
            a = _capture(a_text)
            b = _capture(b_text)
            assert a != b, f"FALSE MERGE of a reversal: {a_text!r} vs {b_text!r}"
        print("PASS: test_negation_is_never_merged")
    finally:
        from memall.config import reset_config
        reset_config()
        cleanup_temp_db(db, orig)


def test_distinct_facts_are_never_merged():
    db, orig = init_temp_db()
    try:
        _enable_dedup()
        cases = [
            ("数据库连接池设为 20 个连接数量。", "数据库连接池设为 50 个连接数量。"),
            ("明天下午三点开会讨论技术方案。", "后天上午十点开会讨论技术方案。"),
            ("alice 偏好深色主题界面设计。", "bob 偏好深色主题界面设计。"),
        ]
        for a_text, b_text in cases:
            a = _capture(a_text, category="general")
            b = _capture(b_text, category="general")
            assert a != b, f"distinct facts merged: {a_text!r} vs {b_text!r}"
        print("PASS: test_distinct_facts_are_never_merged")
    finally:
        from memall.config import reset_config
        reset_config()
        cleanup_temp_db(db, orig)


def test_exact_duplicate_still_dedups():
    """The safety work must not break the original guarantee."""
    db, orig = init_temp_db()
    try:
        _enable_dedup()
        a = _capture("数据库选 PostgreSQL 16 作为主库，事务稳定。")
        b = _capture("数据库选 PostgreSQL 16 作为主库，事务稳定。")
        assert a == b, (a, b)
        print("PASS: test_exact_duplicate_still_dedups")
    finally:
        from memall.config import reset_config
        reset_config()
        cleanup_temp_db(db, orig)


def test_differing_time_or_quantity_is_never_merged():
    """A differing number/date IS the information, not noise.

    Measured false merges at the 0.72 shingle bar: "…一周前确认" vs
    "…一个月前确认" scored 0.76 and collapsed three timeline entries into one,
    destroying the very ordering the temporal model exists to provide.
    """
    db, orig = init_temp_db()
    try:
        _enable_dedup()
        cases = [
            ("数据库选 PostgreSQL 16 作为主库，一周前确认。",
             "数据库选 PostgreSQL 16 作为主库，一个月前确认。"),
            ("数据库选 PostgreSQL 16 作为主库，半年前确认。",
             "数据库选 PostgreSQL 16 作为主库，一年前确认。"),
            ("数据库连接池设为 20 个连接。", "数据库连接池设为 50 个连接。"),
        ]
        for a_text, b_text in cases:
            a = _capture(a_text, category="general")
            b = _capture(b_text, category="general")
            assert a != b, f"quantities/dates differ but merged: {a_text!r} vs {b_text!r}"
        print("PASS: test_differing_time_or_quantity_is_never_merged")
    finally:
        from memall.config import reset_config
        reset_config()
        cleanup_temp_db(db, orig)


def test_timeline_accepts_an_explicit_identity():
    """TimelineInput lacked agent_name/viewer, so the validator stripped them and
    an explicit filter was silently ignored."""
    db, orig = init_temp_db()
    try:
        from memall.mcp.validator import validate_tool_input
        ok, data, err = validate_tool_input("memall_read", {
            "action": "timeline", "hours": 24, "agent_name": "who",
            "viewer": "who"})
        assert ok, err
        assert data.get("agent_name") == "who", data
        assert data.get("viewer") == "who", data
        print("PASS: test_timeline_accepts_an_explicit_identity")
    finally:
        cleanup_temp_db(db, orig)
