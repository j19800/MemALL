# P4 延迟项 — 设计前置（仅登记，不实现）

> **文档状态**：现行（2026-11-10）。ADR 0028 P4 保持 **deferred**：本轮只记录设计前置与依赖，不展开实现。
> 关联：[ADR/0028-memory-cognitive-loop-implementation-plan.md](../../ADR/0028-memory-cognitive-loop-implementation-plan.md)（P4 段）、[TECH_DEBT_LEDGER.md](TECH_DEBT_LEDGER.md)。

## 准入条件（P4 启动前必须全部满足）

1. P0–P3 稳定且全量回归绿、默认配置行为不漂移。
2. P3 效用重排已完成 §度量基线 + 灰度评审（见 [p3-utility-rerank-rollout.md](p3-utility-rerank-rollout.md)）——因为 P4 的多项策略依赖「效用/冲突」信号可信。
3. P2-4 worker 的 enrichment handler 已从**轻量占位**替换为真实管道（或明确决定保持占位）。
4. 有回滚/审计/非破坏性归档的验收标准（见下）。

## 延迟项清单与前置设计

| 项 | 目标 | 前置依赖 / 关键设计问题 | 风险 |
|---|---|---|---|
| Canonicalization（规范化） | 把等价表述收敛到 canonical slot | `slot_key`/`resolve` 语义稳定；需要「谁有权改写」规则 | 误合并不同事实 → 不可逆污染，必须 non-destructive + 可回滚 |
| Conflict review（冲突复核） | REVIEW/CONFLICT 的人/策略裁决闭环 | P1 `conflicts` 桶 + `resolve_status` 状态机；需定义裁决写入格式与幂等键 | 自动裁决可能覆盖用户状态；KB evidence 不得参与 |
| Expiry（有效性过期） | 基于 `invalid_at`/低效用做过期 | P1 temporal validity 已读；需区分「过期不注入」与「物理删除」 | 删除不可逆；优先注入层过滤，归档代替删除 |
| Low-utility 归档 | 长期低效用记忆归档 | P3 `utility_score` 可信 + 度量基线；需阈值/冷却期/恢复路径 | 阈值拍脑袋 → 误归档；须先影子统计 |
| Theme profile（主题画像） | 按主题聚合 agent 偏好 | 现有 L9/L10 蒸馏产物；需定义画像更新频率与过期 | 画像漂移、过时主题污染注入 |
| Policy adaptation（策略自适应） | 按效用/冲突率调整 resolve 阈值 | 全部上述信号 + 明确的「阈值边界」与变更审计 | 自适应正反馈失控；必须限幅、可冻结 |
| Audit（审计） | 所有自进化动作可追溯 | 统一事件 schema（actor/action/before/after/reason/ts）+ 查询面 | 审计缺失时无法归因/回滚 |
| Non-destructive archival | 归档≠删除 | `memory_status`/归档表 + 恢复 API + 备份策略 | 与现有 `forget`/archive 语义冲突需先对齐 |

## 不变量（P4 任何实现都不得破坏）

- **KB = evidence，never user-state**：canonicalization / conflict review / 注入 / 归档决策都不得把 KB chunk 当作用户偏好/决策。
- **non-destructive first**：默认只做「标记/过滤/归档」，不物理删除；任何删除需显式、可审计、可恢复。
- **默认 off / 双门**：新策略与 P3 一样，调用 flag + config 双门，默认关闭。
- **back-compat**：新增字段 additive；老消费者看不见不报错。
- **生产者不得验收**：P4 的策略效果由独立验证者/基线报告判定（沿用 ADR 现有治理约定）。

## 本轮结论

P4 **不展开实现**，仅以本文 + 债务账本登记上述前置。待准入条件满足后再按 ADR 0028 单独排期。
