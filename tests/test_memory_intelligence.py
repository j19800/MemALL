"""Result-level intelligence contracts: stable slot keys + category inference.

Both were measured broken before this change:
- every capture landed in category="general" (1/5 accuracy on obvious cases)
- slot_key encoded the whole sentence, so a topic restated was a brand-new
  slot and the self-evolution chain (UPDATE/SUPERSEDE/CONFLICT/REVIEW) could
  never fire.
"""
import json

from tests.test_helpers import init_temp_db, cleanup_temp_db


def _capture(content, **kw):
    from memall.core.thin_waist import capture
    return capture(content, **kw)


def _row(mid):
    from memall.core.db import get_conn
    c = get_conn()
    r = c.execute("SELECT category, metadata FROM memories WHERE id=?", (int(mid),)).fetchone()
    c.close()
    meta = json.loads(r["metadata"]) if isinstance(r["metadata"], str) else (r["metadata"] or {})
    return r["category"], meta


def test_category_inferred_when_caller_omits_it():
    """Obvious categories must be recognised instead of all becoming 'general'."""
    db, orig = init_temp_db()
    try:
        cases = [
            ("项目决定使用 PostgreSQL 作为主数据库，事务稳定且团队熟悉。", "decision"),
            ("用户偏好深色主题界面，字体 14px，习惯晚上做代码审查。", "preference"),
            ("修复了登录接口在 token 过期时返回 500 的 bug，已加回归测试。", "fix"),
            ("明天要完成 MCP 协议对接的联调工作，周五前交付。", "task"),
            ("反思：本周沟通成本偏高，需要提前同步设计评审。", "reflection"),
        ]
        for content, expected in cases:
            mid = _capture({"content": content, "level": "L4",
                            "agent_name": "intel", "project": "memall"})
            got, _ = _row(mid)
            assert got == expected, (content, got, expected)
        print("PASS: test_category_inferred_when_caller_omits_it")
    finally:
        cleanup_temp_db(db, orig)


def test_explicit_category_is_never_overwritten():
    """A caller-supplied category is a contract; keyword rules must not rewrite it.

    Dict input can distinguish "explicitly general" from "not supplied" via
    _pinned, which is the real MCP path.
    """
    db, orig = init_temp_db()
    try:
        content = "修复了登录接口 500 的 bug，已加回归测试用例覆盖。"
        # explicit general survives (would otherwise be guessed as 'fix')
        mid = _capture({"content": content, "level": "L4", "category": "general",
                        "agent_name": "intel2", "project": "memall"})
        assert _row(mid)[0] == "general"
        # explicit non-general survives
        mid2 = _capture({"content": content, "level": "L4", "category": "task",
                         "agent_name": "intel2", "project": "memall"})
        assert _row(mid2)[0] == "task"
        print("PASS: test_explicit_category_is_never_overwritten")
    finally:
        cleanup_temp_db(db, orig)


def test_slot_key_is_stable_topic_not_sentence():
    """Same topic restated -> SAME slot; different topics -> different slots."""
    db, orig = init_temp_db()
    try:
        a = _capture({"content": "数据库用 PostgreSQL，事务稳定可靠。", "level": "L4",
                      "category": "decision", "agent_name": "slot", "project": "memall"})
        b = _capture({"content": "数据库改为 MySQL，因为许可成本更低。", "level": "L4",
                      "category": "decision", "agent_name": "slot", "project": "memall"})
        c = _capture({"content": "部署改用 blue-green 灰度，回滚窗口 5 分钟。", "level": "L4",
                      "category": "decision", "agent_name": "slot", "project": "memall"})

        _, ma = _row(a)
        _, mb = _row(b)
        _, mc = _row(c)
        sa, sb, sc = ma.get("slot_key"), mb.get("slot_key"), mc.get("slot_key")

        assert sa and sb, (sa, sb)
        assert sa == sb, f"same topic must share a slot: {sa!r} vs {sb!r}"
        assert sc and sc != sa, f"different topic must not collide: {sc!r} == {sa!r}"

        # Slot keys must never encode mechanical labels or the whole sentence.
        for sk in (sa, sb, sc):
            assert "[L4" not in sk and "会话" not in sk, sk
            assert "老陈" not in sk, sk
            assert len(sk) <= 40, sk
        print("PASS: test_slot_key_is_stable_topic_not_sentence")
    finally:
        cleanup_temp_db(db, orig)


def test_slot_reuse_triggers_resolve_decisions():
    """Restating a slot must produce a real decision, not always NOOP/new_slot.

    This is the acceptance criterion for self-evolution: before the slot fix
    every statement was a new slot, so UPDATE/SUPERSEDE/CONFLICT/REVIEW were
    unreachable.
    """
    db, orig = init_temp_db()
    try:
        first = _capture({"content": "数据库用 PostgreSQL，事务稳定可靠。", "level": "L4",
                          "category": "decision", "agent_name": "evo", "project": "memall"})
        second = _capture({"content": "数据库改为 MySQL，因为许可成本更低。", "level": "L4",
                           "category": "decision", "agent_name": "evo", "project": "memall"})
        _, m1 = _row(first)
        _, m2 = _row(second)
        d1 = (m1.get("resolve") or {}).get("decision")
        d2 = (m2.get("resolve") or {}).get("decision")
        assert d1 == "NOOP", f"first statement opens the slot: {d1}"
        assert d2 in ("UPDATE", "SUPERSEDE", "MERGE", "REVIEW", "CONFLICT"), \
            f"restating a slot must decide something, got {d2}"
        print("PASS: test_slot_reuse_triggers_resolve_decisions")
    finally:
        cleanup_temp_db(db, orig)


def test_review_gated_memories_are_hidden_from_search_by_default():
    """The quality gate must FILTER, not merely label.

    Measured before: 5 of 6 memory_status=REVIEW rows surfaced in normal
    search, so sub-quality content competed with accepted memories.
    """
    db, orig = init_temp_db()
    try:
        from memall.core.db import get_conn
        from memall.core.models import MemoryInput
        from memall.core.thin_waist import capture, retrieve
        good = int(capture(MemoryInput(
            content="PostgreSQL 主库选型决策，事务稳定可靠，团队已确认。",
            level="L4", category="decision", agent_name="rv", project="p")))
        bad = int(capture(MemoryInput(
            content="PostgreSQL 待复核内容 a=1，b=2，c=3，d=4。",
            level="L4", category="general", agent_name="rv", project="p")))
        c = get_conn()
        c.execute("UPDATE memories SET memory_status='REVIEW' WHERE id=?", (bad,))
        c.commit()
        c.close()

        def ids(**kw):
            r = retrieve("PostgreSQL", viewer="rv", include_content=True, **kw) or []
            return [m.id for m in (r if isinstance(r, list) else [r])]

        default_ids = ids()
        assert good in default_ids, default_ids
        assert bad not in default_ids, f"REVIEW row must be hidden: {default_ids}"

        # Explicit opt-in still exposes pending items (human review workflow).
        assert bad in ids(include_review=True)
        print("PASS: test_review_gated_memories_are_hidden_from_search_by_default")
    finally:
        cleanup_temp_db(db, orig)


def test_session_start_injects_recent_decisions_after_capture():
    """A new session must surface what was just learned.

    Two defects measured before: (a) the injection query required level='L4'
    while captures default to P2, so it matched nothing; (b) auto_inject caches
    per agent for 300s and only the update path invalidated it, so a session
    started after fresh captures could inject a stale empty picture.
    """
    db, orig = init_temp_db()
    try:
        from memall.mcp.adapter import handle_call

        def sess():
            # Go through the MCP handler: cache invalidation lives there.
            raw = handle_call("memall_system", {
                "action": "session_start", "agent_name": "sess_agent", "auto_inject": True})
            return json.loads(raw)

        # Prime the cache with an empty picture, then confirm a capture
        # invalidates it (the original bug: only the MCP update path did).
        assert not (sess().get("l4_recent") or [])

        from memall.core.thin_waist import capture
        from memall.core.models import MemoryInput
        for content, cat in [
            ("项目决定使用 PostgreSQL 作为主数据库，事务稳定团队熟悉。", "decision"),
            ("用户偏好深色主题界面，字体使用 14px 字号。", "preference"),
            ("修复了登录接口 500 的 bug，已加回归测试覆盖。", "fix"),
        ]:
            capture(MemoryInput(content=content, level="P2", category=cat,
                               agent_name="sess_agent", project="p"))

        out = sess()
        recent = out.get("l4_recent") or []
        assert len(recent) >= 1, f"session must inject recent decisions, got {out.get('injection')}"
        blob = json.dumps(out.get("injection") or {}, ensure_ascii=False)
        assert "PostgreSQL" in blob or "深色主题" in blob, blob[:300]
        print("PASS: test_session_start_injects_recent_decisions_after_capture")
    finally:
        cleanup_temp_db(db, orig)


def test_structured_json_is_not_disguised_as_prose():
    """A bag of scalars must stay unreadable instead of being re-encoded.

    The normaliser used to turn {"a":1,"b":2,...} into "a=1，b=2，c=3", which
    then PASSED the readability check and was stored as if it were a sentence.
    A payload with real natural-language fields must still convert.
    """
    db, orig = init_temp_db()
    try:
        from memall.core.thin_waist import (
            _normalize_content_for_human_readability,
            _is_human_readable_content,
        )

        scalars = '{"a":1,"b":2,"c":3,"d":4,"e":5,"f":6}'
        out, changed, reason = _normalize_content_for_human_readability(scalars)
        assert changed is False, (out, reason)
        assert reason == "json_structured_scalars", reason
        assert _is_human_readable_content(out)[0] is False

        prose = '{"title":"数据库选型","message":"决定使用 PostgreSQL 作为主库"}'
        out2, changed2, reason2 = _normalize_content_for_human_readability(prose)
        assert changed2 is True and reason2 == "json_to_natural", (out2, reason2)
        assert "PostgreSQL" in out2
        print("PASS: test_structured_json_is_not_disguised_as_prose")
    finally:
        cleanup_temp_db(db, orig)


def test_subject_truncates_on_meaningful_boundary():
    """Long subjects must not sever words or swallow closing punctuation.

    Pure-function test: _make_subject needs no database, and opening a temp DB
    here let the background pipeline race the assertion.
    """
    from memall.core.thin_waist import _make_subject

    long_text = ("生产环境 API 网关地址是 https://api.example.com/v2，"
                 "限流 1000 QPS，需要白名单校验。")
    s = _make_subject(long_text, "general", "L4", "tester", "老陈")
    assert len(s) <= 60, s
    assert s.endswith("…"), s                       # explicit ellipsis, not a silent cut
    assert not s.endswith(("，", ",", "、")), s       # never leave a dangling comma
    assert _make_subject("短标题", "general", "L4", "t", "o").endswith("短标题")
    print("PASS: test_subject_truncates_on_meaningful_boundary")


def test_include_review_survives_the_mcp_search_whitelist():
    """`include_review` must work end-to-end through MCP search, not just retrieve().

    Two separate holes made it dead over MCP: the field was undeclared in
    UnifiedSearchInput (validator stripped it) and unified_search's fts5/direct
    path builds an explicit retrieve_args whitelist that omitted it.
    """
    db, orig = init_temp_db()
    try:
        from memall.core.db import get_conn
        from memall.mcp.adapter import handle_call
        import json as _json

        c = get_conn()
        try:
            c.execute("INSERT OR REPLACE INTO identities (agent_name, agent_type, trusted_by) "
                      "VALUES (?,?,?)", ("rv_viewer", "human", ""))
            c.execute(
                "INSERT INTO memories (content, content_hash, level, agent_name, category, "
                "project, subject, occurred_at, created_at, updated_at, memory_status, "
                "visibility, metadata) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                ("PostgreSQL review pending content stable verified.", "h_rev_whitelist",
                 "L4", "rv_viewer", "decision", "p", "s",
                 "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z",
                 "2026-01-01T00:00:00Z", "REVIEW", "public", "{}"))
            c.commit()
            rid = int(c.execute("SELECT id FROM memories WHERE content_hash='h_rev_whitelist'"
                                ).fetchone()[0])
        finally:
            c.close()

        def ids(extra=None):
            args = {"action": "search", "query": "PostgreSQL", "mode": "fts5",
                    "agent_name": "rv_viewer"}
            args.update(extra or {})
            out = _json.loads(handle_call("memall_read", args))
            return [r.get("memory_id") for r in (out.get("results") or [])]

        assert rid not in ids(), "REVIEW row must be hidden by default over MCP"
        assert rid in ids({"include_review": True}), "include_review must work over MCP"
        print("PASS: test_include_review_survives_the_mcp_search_whitelist")
    finally:
        cleanup_temp_db(db, orig)
