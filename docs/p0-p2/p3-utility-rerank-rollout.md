# P3 Utility Rerank — 冷启动/度量基线/灰度/Kill-Switch

> **文档状态**：现行（2026-11-10）。**默认 off，不改默认**。本文是「启用前必须满足」的策略文档，不构成启用动作。
> 关联：`src/memall/search/enhancements.py`（`_apply_utility_rerank` / `aggregate_interaction_utility`）、`src/memall/core/thin_waist.py`（`unified_search`/`enhanced_search` 传播）、`src/memall/config.py`（`search.utility_rerank`）、`tests/test_utility_rerank_p3.py`（10 passed）。

## 1. 当前状态（何时都不改默认）

| 项 | 值 |
|---|---|
| `search.utility_rerank.enabled` | **`False`**（默认） |
| 生效条件 | 调用方显式传 `utility_rerank=True` **且** config `enabled=True`（双门） |
| 证据门 | 每条记忆累计 `clicks+copies+dwells+impressions >= min_interactions`（默认 2） |
| 数学 | `utility_rerank_score = base * (1 + clamp(weight,0,0.5) * u)`，`u = utility/denom`，只做非负微调，**永不丢结果** |
| 回退 | 无证据 / 低证据 / 聚合失败 / 异常 → 原样返回（无 `utility_rerank_*` 字段、顺序不变） |
| 观测 | additive 字段 `utility_rerank_signal` / `utility_rerank_score`，以及 `enhanced_search` 的 `utility_rerank_signals` 映射 |

配置：
```yaml
search:
  utility_rerank:
    enabled: false
    weight: 0.12
    window_days: 30
    min_interactions: 2
```

## 2. 冷启动 / 低证据回退（已测试）

- **零证据**（新库/新 agent）：`aggregate_interaction_utility` 返回空 → `_apply_utility_rerank` 直接 `return results`，不加字段、不改顺序、不丢项。
- **低于阈值**：证据 `< min_interactions` 的行被 `continue` 跳过；若整体无合格行同样原样返回。
- **异常/聚合失败**：`agg.ok=False` 或抛异常 → 原样返回（best-effort）。
- **score-less 结果**：无 `rrf_score` 等基础分的条目以 `base=0.0` 计算，仍保留在结果集中。
- 覆盖：`test_cold_start_zero_evidence_falls_back_unchanged` / `test_low_evidence_below_min_interactions_falls_back_unchanged` / `test_signal_math_is_bounded_and_never_drops_items`。

## 3. 跨 Agent 隔离（已测试）

- `aggregate_interaction_utility` 的 SQL：`WHERE memory_id IS NOT NULL AND (? = '' OR agent_name = ?)`。传入具体 `agent_name` 时**只看该 agent 的交互行**。
- `apply_enhancements` 把 `agent_name=agent_name or viewer` 传入聚合，因此检索身份决定效用账本作用域。
- 覆盖：`test_cross_agent_interaction_isolation`（A 的 5 次点击不会给 B 的结果加任何信号；B 聚合返回空）。

> ⚠️ 已知限制：`agent_name=""` 时该条件退化为「不过滤」——即**跨所有 agent 聚合**。MCP/adapter 通常带身份，但直接调用 `apply_enhancements(..., agent_name="")` 时无隔离。灰度前需决定：空身份是「全局账本」还是「禁用 rerank」。（见 §6 负债）

## 4. 度量基线（灰度前必须采集，当前仅有契约测试）

启用前需在**影子模式**（config `enabled=True` 但只观测 `utility_rerank_signal/score`，不计入最终排序评估，或对比组）采集至少以下指标，形成书面基线：

| 指标 | 采集方式 | 验收门槛（建议，需评审） |
|---|---|---|
| 排序扰动率 | 开/关 rerank 的 top-k 顺序差异（Kendall tau / Jaccard@k） | 有扰动但可控；不得出现「全部重排」 |
| 采纳率 | `search_interactions` 中 click/copy/dwell 相对 impression 的比率 | 灰度组不低于对照组 |
| 冷启动占比 | 无合格证据的检索占比 | 高占比时 rerank 实质无效，需先积累数据 |
| 信号分布 | `utility_rerank_signal` 的 min/max/分位数 | 无极端离群（denom 已归一化，仍需观测） |
| 延迟 | `aggregate_interaction_utility` 额外 SQL 的 p50/p95 | 增量在检索 SLA 内 |
| 不丢结果 | 开/关的 `len(results)` 恒等 | **恒等**（已有单测，线上仍需埋点） |
| 跨 agent 串扰 | 随机抽查：某 agent 的 signal 是否由其自身交互驱动 | 0 串扰 |

> 当前实现**尚未**内置这些遥测埋点（只有 debug 字段）。这是启用前必须补的最小观测面。

## 5. 灰度与 Kill-Switch（流程，当前未执行）

1. **阶段 0（当前）**：`enabled=False`。所有传播/E2E/回退/隔离契约通过；仅登记策略。
2. **阶段 1 影子**：`enabled=True` 但生产不显式传 `utility_rerank=True`（双门仍关），或仅对内部探活请求传 flag；采集 §4 基线，不改用户可见排序结论。
3. **阶段 2 小流量**：按 `agent_name` / 项目白名单显式传 flag，对比组保持关闭；观察采纳率/延迟 ≥ 1 个完整周期。
4. **阶段 3 扩大**：逐级放量，每级有回退判据。
5. **阶段 4（可选）默认**：只有全部指标达标且评审通过，才考虑改 config 默认。**本次及近期一律不改默认**。

### Kill-Switch（任一触发即回退）
- **一级开关**：`search.utility_rerank.enabled=False`（配置热重载 ≤2s 生效，`get_config` 自动 stat 配置文件）。设回 False 后即便调用方传 `utility_rerank=True` 也不生效——`apply_enhancements` 的双门生效。这是首要、即时的回退手段。
- **调用侧**：停止传 `utility_rerank` flag（MCP/unified_search 调用方）。
- **代码侧**：`_apply_utility_rerank` 内任何异常都被 try/except 吞掉并原样返回，天然 fail-safe。
- **数据侧**：清空/归档 `search_interactions` 中的异常行不影响功能，只让证据归零 → 自动回退到「无信号」。
- 回退验证：开关关闭后跑 `tests/test_utility_rerank_p3.py` 的 default-off 用例，确认无 `utility_rerank_*` 字段、顺序不变。

## 6. 待办负债（启用前）

- [ ] §4 遥测埋点（排序扰动、采纳率、延迟、冷启动占比、串扰抽查）最小化实现。
- [ ] §3 空 `agent_name` 语义决策（全局账本 vs 禁用 rerank）并补契约。
- [ ] 书面基线报告 + 评审记录，作为改默认的准入证据。
- [ ] 灰度白名单机制（按 agent/project 显式放量的最小实现）。
- [ ] `weight` 上限 0.5 硬编码在代码里，是否上移到 config 校验（当前 config 值也钳制，双保险）。
