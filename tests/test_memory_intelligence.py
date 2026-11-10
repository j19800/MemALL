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
