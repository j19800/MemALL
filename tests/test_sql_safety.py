"""
Test Suite — SQL 注入面回归护栏（2026-10-01）
============================================
锁定 MemALL 中“动态 SQL 标识符”相关的不变量。这些位置无法使用 ``?`` 占位符
（SQLite 不支持参数化表名/列名/排序字段），只能靠白名单或字面量保证安全。
本套件防止未来改动把外部输入接进这些位置。

覆盖：
1. ``thin_waist._ALLOWED_UPDATE_FIELDS`` 白名单项必须是合法标识符。
2. ``thin_waist.update`` 对非白名单字段（含注入串）静默忽略，数据库不受损。
3. ``gateway._import_memories`` 的 INSERT 列名来自字面量字典，调用方多传的键被忽略。
"""

import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def test_update_field_whitelist_are_identifiers():
    """白名单里的字段名必须都是合法标识符（否则会被拼进 SET 子句）。"""
    from memall.core.thin_waist import _ALLOWED_UPDATE_FIELDS

    for f in _ALLOWED_UPDATE_FIELDS:
        assert _IDENT.match(f), f"非标识符字段名进入白名单: {f!r}"


def test_update_ignores_injected_field_name():
    """把注入串当作字段名传入 update，必须被白名单挡掉且不破坏数据库。"""
    from tests.test_helpers import init_temp_db, cleanup_temp_db, insert_memory
    from memall.core.thin_waist import update
    from memall.core.db import get_conn

    db_path, original = init_temp_db()
    try:
        conn = get_conn()
        mid = insert_memory(
            conn,
            "用于 SQL 注入回归测试的记忆正文，长度足够以通过写入质量门。",
            agent_name="inject_guard_agent",
        )
        conn.close()

        evil_key = "content = 'x'; DROP TABLE memories;--"
        update(mid, **{evil_key: "boom", "level": "L9"})

        conn = get_conn()
        assert conn.execute("SELECT count(*) FROM memories").fetchone()[0] >= 1, "memories 表被破坏"
        row = conn.execute("SELECT level FROM memories WHERE id = ?", (mid,)).fetchone()
        assert row is not None, "目标记忆被删除"
        assert row["level"] == "L9", f"白名单字段未生效: {row['level']}"
        conn.close()
    finally:
        cleanup_temp_db(db_path, original)


def test_import_memories_ignores_unknown_keys():
    """导入路径的 INSERT 列名是字面量；调用方多传的键不得成为列名。"""
    from tests.test_helpers import init_temp_db, cleanup_temp_db
    from memall.gateway import _import_memories
    from memall.core.db import get_conn

    db_path, original = init_temp_db()
    try:
        conn = get_conn()
        payload = [{
            "id": 9001,
            "content": "导入回归测试记忆正文，长度足够以通过写入质量门。",
            "level": "P2",
            "category": "general",
            "x) VALUES (1); DROP TABLE memories;--": "boom",
        }]
        imported, _mapping = _import_memories(conn, payload, "import_guard_agent")
        assert imported == 1, f"应导入 1 条，实际 {imported}"

        cols = {r["name"] for r in conn.execute("PRAGMA table_info(memories)")}
        assert not any("DROP" in c.upper() for c in cols), f"注入列名进入了表结构: {cols}"
        assert conn.execute("SELECT count(*) FROM memories").fetchone()[0] == 1
        conn.close()
    finally:
        cleanup_temp_db(db_path, original)