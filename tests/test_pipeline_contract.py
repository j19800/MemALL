"""
Test Suite — Pipeline 幂等/可重入契约与崩溃恢复（F-05 回归护栏）
================================================================

背景：pipeline 在**每个 step 后提交**（见 ``_run_step``），这样长任务不会在
整个运行期间持有 SQLite 写锁。代价是崩溃会留下"部分推进"的数据库，因此
崩溃安全依赖一条明确契约：**每个 step 都必须可重入**——在已部分处理的库上
重跑必须收敛而非产生重复/损坏。

本测试覆盖：
1. 每个注册的 step 都声明了幂等类别（新增 step 不声明会失败）。
2. 声明类别都在允许集合内。
3. 崩溃遗留的 ``running`` 行会被 ``recover_stale_pipeline_runs`` 归为
   ``interrupted``，不会永久污染健康度。
4. 新建 run 记录 contract_version / pid / host。
5. step entry 记录其契约类别。
"""

import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


def test_every_registered_step_declares_a_contract():
    """注册到 _PIPELINE_STEPS 的每个 step 都必须声明幂等类别。"""
    from memall.pipeline.pipeline import (
        _PIPELINE_STEPS,
        validate_step_contracts,
        STEP_IDEMPOTENCY,
    )

    missing = validate_step_contracts()
    assert missing == [], f"steps missing an idempotency contract: {missing}"

    registered = {name for name, _, _, _ in _PIPELINE_STEPS}
    assert registered <= set(STEP_IDEMPOTENCY), "declared contract for unknown step"


def test_declared_classes_are_valid():
    """每个声明类别必须在允许集合内（避免拼写错误静默失效）。"""
    from memall.pipeline.pipeline import STEP_IDEMPOTENCY, _IDEMPOTENCY_CLASSES

    invalid = {
        name: cls for name, cls in STEP_IDEMPOTENCY.items()
        if cls not in _IDEMPOTENCY_CLASSES
    }
    assert invalid == {}, f"invalid idempotency classes: {invalid}"


def test_stale_running_run_is_recovered():
    """崩溃遗留的 running 行（早于阈值）应被标记为 interrupted。"""
    from tests.test_helpers import init_temp_db, cleanup_temp_db
    from memall.core.db import get_conn
    from memall.pipeline.pipeline import recover_stale_pipeline_runs

    db_path, patcher = init_temp_db()
    try:
        conn = get_conn()
        old = (datetime.now(timezone.utc) - timedelta(hours=5)).isoformat()
        conn.execute(
            "INSERT INTO pipeline_runs (started_at, status) VALUES (?, 'running')",
            (old,),
        )
        # A fresh run must NOT be touched.
        fresh = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "INSERT INTO pipeline_runs (started_at, status) VALUES (?, 'running')",
            (fresh,),
        )
        conn.commit()
        conn.close()

        n = recover_stale_pipeline_runs(max_age_seconds=7200)
        assert n == 1, f"expected 1 recovered run, got {n}"

        conn = get_conn()
        rows = conn.execute(
            "SELECT status, interrupted_at, error FROM pipeline_runs ORDER BY id"
        ).fetchall()
        conn.close()

        assert rows[0]["status"] == "interrupted"
        assert rows[0]["interrupted_at"], "interrupted_at must be stamped"
        assert "interrupted" in (rows[0]["error"] or "")
        assert rows[1]["status"] == "running", "fresh run must stay running"
    finally:
        cleanup_temp_db(db_path, patcher)


def test_completed_run_is_never_recovered():
    """已完成的 run 即使很旧也不得被改写。"""
    from tests.test_helpers import init_temp_db, cleanup_temp_db
    from memall.core.db import get_conn
    from memall.pipeline.pipeline import recover_stale_pipeline_runs

    db_path, patcher = init_temp_db()
    try:
        conn = get_conn()
        old = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
        conn.execute(
            "INSERT INTO pipeline_runs (started_at, status) VALUES (?, 'completed')",
            (old,),
        )
        conn.commit()
        conn.close()

        assert recover_stale_pipeline_runs(max_age_seconds=7200) == 0

        conn = get_conn()
        status = conn.execute("SELECT status FROM pipeline_runs").fetchone()["status"]
        conn.close()
        assert status == "completed"
    finally:
        cleanup_temp_db(db_path, patcher)


def test_new_run_records_contract_and_owner():
    """新建 run 必须记录 contract_version / pid / host。"""
    from tests.test_helpers import init_temp_db, cleanup_temp_db
    from memall.core.db import get_conn
    from memall.pipeline.pipeline import (
        _create_pipeline_run,
        PIPELINE_CONTRACT_VERSION,
    )

    db_path, patcher = init_temp_db()
    try:
        run_id = _create_pipeline_run()
        conn = get_conn()
        row = conn.execute(
            "SELECT contract_version, pid, host, status FROM pipeline_runs WHERE id = ?",
            (run_id,),
        ).fetchone()
        conn.close()

        assert row["status"] == "running"
        assert row["contract_version"] == PIPELINE_CONTRACT_VERSION
        assert row["pid"] == os.getpid()
        assert row["host"]
    finally:
        cleanup_temp_db(db_path, patcher)


def test_step_entry_records_contract_class():
    """step entry 必须带上其幂等类别，供 pipeline_runs.steps 审计。"""
    from tests.test_helpers import init_temp_db, cleanup_temp_db
    from memall.core.db import get_conn
    from memall.pipeline.pipeline import _run_step

    db_path, patcher = init_temp_db()
    try:
        shared = get_conn()
        try:
            entry = _run_step("noop", lambda: 0, {}, conn=shared)
            assert entry["status"] == "ok"
            assert entry["contract"] == "recompute"  # default for unregistered name

            entry2 = _run_step("link", lambda: 0, {}, conn=shared)
            assert entry2["contract"] == "recompute"

            entry3 = _run_step("distill", lambda: 0, {}, conn=shared)
            assert entry3["contract"] == "dedup"
        finally:
            shared.close()
    finally:
        cleanup_temp_db(db_path, patcher)