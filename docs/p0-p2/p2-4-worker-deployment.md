# P2-4 KB Enrich Durable Worker — 部署与运维说明

> **文档状态**：现行（2026-11-10）。ADR 0028 P2-4 持久化 worker 的配置、启停、观测与多进程语义。
> 关联：[p2-kb-boundary-hardening.md](p2-kb-boundary-hardening.md)、[TECH_DEBT_LEDGER.md](TECH_DEBT_LEDGER.md)、`commit 8aa68cc` 及其后续。

## 1. 行为摘要（先看这个）

- 默认 **`kb.enrich_worker.autostart = False`**：装好/重启后**不会**自动起后台线程。KB ingest 仍可用（enrich hook 为 daemon-thread best-effort、非阻塞）。
- 持久化队列只在 `kb.enrich_async=True` 且 `kb.enrich_worker.backend == "sqlite+queue"` 时生效。
- Gateway `start()` 对 `ensure_kb_enrich_worker_started()` 是 **best-effort try/except**：worker 启动失败绝不影响网关启动。
- Worker 为 **单线程/进程**（`_worker_lock` 保证每进程至多一个）；跨进程靠 `pipeline_jobs` 表的 CAS claim 去重，不靠进程内锁。

## 2. 配置项（`src/memall/config.py`）

```yaml
kb:
  enrich_async: true                # 总开关；false 时 hook/worker 都跳过
  enrich_worker:
    backend: sqlite+queue           # 否则 worker 直接退出（见 kb_enrich_worker_loop）
    autostart: false                # 默认 OFF —— 生产启用前务必评估（见 §5）
    poll_interval_seconds: 2        # 空闲轮询间隔
    max_attempts: 3                 # 单 job 最大尝试次数
    base_backoff_seconds: 5         # 首次失败退避
    max_backoff_seconds: 300        # 退避上限
    claim_ttl_seconds: 300          # claimed_at 租约；过期 running 可被回收
    batch_size: 10
```

## 3. 启用（生产）

1. 确认 `pipeline_jobs` 表已随 `init_db()` 创建（SCHEMA_SQL 内建；`_ensure_pipeline_jobs_schema` 会为老库补 `claimed_at`/`created_at` 列）。
2. 在部署配置里把 `kb.enrich_worker.autostart` 设为 `true`（其余保持默认或按负载调整）。
3. 启动 Gateway/进程：`MemAllGateway.start()` 会 best-effort 调 `ensure_kb_enrich_worker_started()`。
4. 也可显式调用：`from memall.pipeline.kb_enrich import ensure_kb_enrich_worker_started; ensure_kb_enrich_worker_started()`。

> ⚠️ **多实例部署**：多个进程可同时 autostart。CAS claim 保证同一 job 只会被一个进程领取（`WHERE status='queued'` 条件 UPDATE），但每个进程各自起一条轮询线程——请按实例数评估轮询/DB 压力。

## 4. 观测与运维（MCP `memall_system`）

| action | 作用 |
|---|---|
| `kb_enrich_stats` | 返回 hook 统计 + `pipeline` 队列指标（total/queued/running/ok/failed/queue_depth/last_error）+ `worker` 状态（started/alive/thread_name/stop_requested）。 |
| `kb_reprocess` | 有界一次性重处理失败/未完成任务；`trigger_mode=bounded_once`，`process_budget` 默认 50、钳制到 [1,500]；幂等（重复调用不复制行）。 |
| `kb_enrich_stop` | 协作式停止 autostarted worker；`timeout_seconds` 默认 5、上限 60；设置 stop event 并 `join(timeout)`，返回 `stop_requested/joined/still_alive`。幂等、无 worker 时安全返回。 |

Python 等价入口：`kb_enrich_stats()` / `kb_enrich_worker_status()` / `stop_kb_enrich_worker(timeout_seconds=...)` / `reprocess_kb_enrich({...})`。

**启停语义**：`stop_kb_enrich_worker()` 用 `threading.Event.wait()` 唤醒空闲轮询，循环条件 `while not _worker_stop.is_set()`，因此 stop 是**有界**的（≤ poll 间隔 + join timeout）。再次 `ensure_kb_enrich_worker_started()` 会 `clear()` stop event 并起新线程。

## 5. 启用前评估清单（默认 off 的理由）

- [ ] 队列积压监控：`kb_enrich_stats.pipeline.queue_depth` 是否持续增长。
- [ ] `running` 且 `claimed_at` 长期不推进 → 检查 `claim_ttl_seconds` 与 worker 存活。
- [ ] `recent_errors` / `last_error` 是否出现 `exceeded_max_attempts`。
- [ ] 多实例时轮询线程总数 = 实例数；DB 写锁竞争是否可接受（WAL + busy_timeout 已配置）。
- [ ] enrichment handler 当前是**轻量占位**（`kb_enrich_handler` 只做幂等去重/统计），接真实 LLM/embedding 前不要盲目开 autostart。
- [ ] 备份/迁移：`pipeline_jobs` 是持久表，纳入 DB 备份范围。

## 6. 不变量 / 测试覆盖

- KB = evidence，never user-state；enrichment 绝不阻塞 capture（`memory_write._fire_kb_enrich` 全程 try/except）。
- CAS 单赢家：同进程双连接 + **真多进程 spawn（4 进程）** 均有测试（`tests/test_kb_enrich_worker_p2.py`）。
- `reprocess` 有界一次、budget 钳制、重复调用不复制行；stats 的 `worker` 字段 additive/back-compat。
- 已知负债：worker 无「排空当前 job 后再停」的 drain 语义（stop 在下一轮循环边界退出，正在处理的 job 会完成当前步骤）；无跨进程 graceful drain 协调。见 `TECH_DEBT_LEDGER.md`。
