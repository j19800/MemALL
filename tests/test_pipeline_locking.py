"""
Test Suite — Pipeline 写锁持有（回归护栏）
==========================================
缺陷：``_run_step`` 会把共享的 ``pipeline_conn`` 传给接受 ``conn`` 参数的 step
（如 ``entity_extraction_step``）。step 写入后不提交，于是该连接在整个
pipeline 生命周期内持有写事务；后续每个自建连接的 step 都会撞上
"database is locked"（busy_timeout 5s），表现为每步 5~11 秒并最终失败。

修复：step 是最小工作单元 —— 成功后 ``conn.commit()``，失败后 ``conn.rollback()``，
确保写锁在 step 边界释放。
"""

import os
import sqlite3
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


def test_step_releases_write_transaction_on_shared_conn():
    """step 跑完后，共享连接不得残留未提交事务，其它连接必须能立即写。"""
    from tests.test_helpers import init_temp_db, cleanup_temp_db, insert_memory
    from memall.core.db import get_conn
    from memall.pipeline.pipeline import _run_step
    from memall.pipeline.entity_pipeline import entity_extraction_step

    db_path, patcher = init_temp_db()
    try:
        conn = get_conn()
        insert_memory(conn, "MemALL 与 老陈 讨论架构决策，涉及 SQLite 与 知识图谱。",
                      agent_name="lock_agent", category="architecture", level="L3")
        conn.commit()
        conn.close()

        shared = get_conn()
        try:
            entry = _run_step("entity_extraction", entity_extraction_step, {},
                              conn=shared)
            assert entry.get("status") == "ok", f"step failed: {entry}"
            assert not shared.in_transaction, \
                "shared connection still holds an open write transaction"

            # 另一个连接必须能立刻写入（否则后续 step 会被锁 5~11 秒）
            other = get_conn()
            try:
                other.execute("UPDATE memories SET access_count = access_count + 1")
                other.commit()
            finally:
                other.close()
        finally:
            shared.close()
    finally:
        cleanup_temp_db(db_path, patcher)


def test_failed_step_rolls_back_shared_conn():
    """step 抛异常时，共享连接上的半成品事务必须回滚（不得继续持锁）。"""
    from tests.test_helpers import init_temp_db, cleanup_temp_db
    from memall.core.db import get_conn
    from memall.pipeline.pipeline import _run_step

    db_path, patcher = init_temp_db()
    try:
        shared = get_conn()
        try:

            def _boom(conn=None):
                conn.execute("UPDATE memories SET access_count = 1")
                raise RuntimeError("step blew up mid-write")

            entry = _run_step("boom", _boom, {}, conn=shared)
            assert entry.get("status") == "failed", f"expected failure entry: {entry}"
            assert not shared.in_transaction, \
                "failed step left the shared connection inside a transaction"

            other = get_conn()
            try:
                other.execute("UPDATE memories SET access_count = 0")
                other.commit()
            finally:
                other.close()
        finally:
            shared.close()
    finally:
        cleanup_temp_db(db_path, patcher)
