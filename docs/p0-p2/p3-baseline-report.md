# ADR 0028 P3 灰度基线报告

> **文档状态**：现行（2026-11-11）。目的：为「是否可以把 utility 重排从默认关改为灰度开启」提供**可复现的数据**，而不是凭感觉。
> 对应技术债条目：「Telemetry + ranking-quality baselines before any gray rollout」。

## 1. 观测手段

`memall_system action=utility_telemetry`（只读，进程内计数器，无存储、无 PII）：

| 指标 | 含义 | 为什么重要 |
|---|---|---|
| `ranking_disturbance` | 每个条目平均绝对位次移动 | 0 = 开关没起作用（无意义）；过大 = 排序被打乱（危险） |
| `max_displacement` | 单条最大位次移动 | 捕捉个别条目的剧烈跳动 |
| `cold_start_share` | 无交互证据的调用占比 | 冷启动时开关应当**完全不起作用** |
| `applied_share` | 真正发生重排的调用占比 | 衡量实际影响面 |
| `mean_latency_ms` | 重排平均耗时 | 灰度前的性能底线 |

## 2. 基线测量（2026-11-11，本地临时库）

构造 3 条同主题记忆，给第 2、3 条灌入点击证据（6 次 / 2 次），查询 `PostgreSQL 主库选型`：

| 状态 | 结果顺序 | 召回集合 | 说明 |
|---|---|---|---|
| **OFF**（默认） | `[1, 2]` | {1, 2} | 基线 |
| **ON**（仅改 config，**不传任何 flag**） | `[2, 1]` | {1, 2} | 高 utility 的 2 跃居第一 |
| **关回 OFF** | `[1, 2]` | {1, 2} | 完全恢复基线，无粘滞 |

对应遥测：`calls=1, applied=1, ranking_disturbance=1.0, max_displacement=1, cold_start_share=0.0`

**冷启动对照**（无任何交互证据）：`cold_start_share=1.0, applied=0` —— 开关正确地什么也没做。这是最重要的安全性质：**新部署 / 新用户不会被历史证据影响**。

## 3. 已确认的安全性质

| 性质 | 状态 | 证据 |
|---|---|---|
| 默认关闭 | ✅ | `quality.utility.tracking_enabled` 默认 `False` |
| 仅 config 生效（无需 per-call flag） | ✅ | 上表 ON 行：未传任何 flag |
| 召回集合不变（只重排不增删） | ✅ | OFF/ON 集合均为 {1,2} |
| 冷启动无影响 | ✅ | `applied=0`，`cold_start_share=1.0` |
| 关掉后完全恢复 | ✅ | 顺序回到 `[1,2]` |
| 跨 agent 不串扰 | ✅ | 空 `agent_name` 默认 `deny`（无证据），需显式设 `global` 才跨 agent |
| 有回归测试 | ✅ | `test_p3_ordering_actually_changes_with_utility` + 变异验证 |

## 4. 灰度建议

**前置条件已满足**：遥测可用、基线可复现、召回不变、冷启动安全、跨 agent 隔离、有测试守护。

建议的灰度路径（**仍未执行，需人工决策**）：
1. 选一个低风险 agent 白名单，开启 `quality.utility.tracking_enabled`
2. 观察 `utility_telemetry`：`ranking_disturbance` 应保持在个位数，`cold_start_share` 按预期
3. 若 `max_displacement` 出现异常大值，回滚只需把开关设回 `False`（已验证可完全恢复）
4. 收集足够样本后再评估是否扩大范围

## 5. 仍然不建议做的事

- **不要**改 `quality.utility.tracking_enabled` 的默认值 —— 需要真实流量数据支撑。
- **不要**在没有 `agent_name` 的调用路径上依赖 utility（默认 deny，不会给证据）。
- 跨 agent 全局账本（`empty_agent_scope="global"`）仅限明确需要统一账本的部署。
