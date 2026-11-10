# ADR 0028 P0–P2 改造文档索引

> **文档状态**：现行（commit `92863b6` 基线；2026-11-10 持续推进，工作区含未提交改动）  
> 最新扩展 scoped regression（9 文件）**70 passed**（详见 [PRECOMMIT_SCOPED_REPORT_2026-11-10.md](PRECOMMIT_SCOPED_REPORT_2026-11-10.md)）。  
> 范围：ADR 0028 Memory Cognitive Loop 的 P0/P1/P2 实现、验收、提交清单与技术债务；并延伸记录 P2-4 CAS 加固与 P3 默认关闭效用重排（本轮新增 `tests/test_utility_rerank_p3.py`）。

## 文档清单

| 文档 | 用途 |
|---|---|
| [TRANSFORMATION_DOC.md](TRANSFORMATION_DOC.md) | P0–P2 改造总览、MCP 契约扩展、验收记录、后续路线 |
| [p0-resolve-exposure.md](p0-resolve-exposure.md) | P0：测试基线 + resolve/validity 增量暴露 |
| [p1-continuity-contract.md](p1-continuity-contract.md) | P1：Continuity Pack、session/recap KB 排除、MCP 契约 |
| [p2-kb-boundary-hardening.md](p2-kb-boundary-hardening.md) | P2：`kb_policy` 权威、KB 边界硬化、异步 enrich 骨架 |
| [p2-4-worker-deployment.md](p2-4-worker-deployment.md) | P2-4 durable worker 部署/启停/观测/多进程语义（autostart 默认 off） |
| [p3-utility-rerank-rollout.md](p3-utility-rerank-rollout.md) | P3 冷启动回退、跨 agent 隔离、度量基线/灰度/kill-switch（默认 off 不改） |
| [p4-deferred-design.md](p4-deferred-design.md) | P4 设计前置（历史：P4 曾 deferred；**本轮已实现**，见下对照表与 maintenance 模块） |
| [blueprint-completion-matrix.md](blueprint-completion-matrix.md) | **ADR 0028 §2 逐条字面完成度对照表**（P0–P4 全条目 + 已知偏差登记） |
| [COMMIT_CHECKLIST.md](COMMIT_CHECKLIST.md) | 提交范围、验收命令、排除项、阶段确认 |
| [TECH_DEBT_LEDGER.md](TECH_DEBT_LEDGER.md) | P2-4 真异步队列、P2-3 收敛、P1 边界测试、P3 保守闭环债务 |
| [PRECOMMIT_SCOPED_REPORT_2026-11-10.md](PRECOMMIT_SCOPED_REPORT_2026-11-10.md) | 各轮 scoped regression 实际结果（最新见文末「按蓝图补齐」轮次） |
| [CHANGELOG_TRACKING.md](CHANGELOG_TRACKING.md) | 每轮改动逐条可追溯记录 |
| `tests/test_blueprint_completion.py`（代码） | 蓝图补齐契约（P1/P3/P4，12 passed） |
| `tests/test_utility_rerank_p3.py`（代码） | P3 默认关闭效用重排 E2E 传播契约（10 passed） |
| `tests/test_kb_enrich_worker_p2.py`（代码） | P2-4 durable worker/CAS 契约（20 passed） |

## 关联
- 主计划：[ADR/0028-memory-cognitive-loop-implementation-plan.md](../../ADR/0028-memory-cognitive-loop-implementation-plan.md)
- 验收基线 commit：`92863b6 feat(memall): P2 KB boundary hardening, async enrich hook, P1 continuity contracts`（本地，未推送）

## 不变量（P0–P2）
- **向后兼容/增量字段**：老消费者不可见新字段时不报错；MCP 响应形状保持兼容。
- **KB 是 evidence，never user-state**：`continuity_pack` / `recap` / `session_start` 注入均排除 KB 证据。
- **`json_extract(metadata,'$.source_pipeline')` 契约保持**；`kb_policy` 为 KB provenance/排除权威。
- **异步 hook 非阻塞/容错/可配置**：`kb.enrich_async`（默认 true）。
- **`sqlite3.Row` 安全访问**；`thin_waist.py` 显式正向 `source_pipeline` 过滤（~意图保留）不改为排除语义。

