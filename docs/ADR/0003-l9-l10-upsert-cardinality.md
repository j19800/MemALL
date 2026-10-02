# ADR-0003：L9/L10 管线合成层采用 upsert 基数约定

- **状态**：Accepted（2026-09-26）
- **决策者**：老陈
- **相关**：`src/memall/pipeline/distill.py`、`src/memall/pipeline/integrate.py`、`config.distill.upsert_enabled`

## 背景

生产库（4253 条记忆）出现大规模近重复（相似度 0.99）。排查后确认**不是用户重复写入**，而是管线合成层自身的两个缺陷：

### 缺陷 A：L9 蒸馏只 INSERT、不 upsert
`distill_step()` 用 `INSERT OR IGNORE` 且仅以**完整 content_hash** 去重。但 L9 头部嵌入了会变的来源计数：

```
[L9 蒸馏] SOLO 在 correction 领域共 10 条 → 11 条 → 12 条
```

hash 每次都不同 → 每个 pipeline 周期新增一条 L9 → 无上限累积。生产实测：**688 条冗余 L9**，单组最高 58 条。

### 缺陷 B：分组键被内层循环遮蔽（P0）
`distill.py` 内层去重循环写 `key = s[:40]`，**覆盖了外层分组键** `key = (agent_name, category)`。于是：

- 头部被写成 `[L9 蒸馏] S 在 y 领域`（取的是句子片段的首字符）；
- 每条 L9 的 `agent_name` 被错记为 `system`、`category` 变成单字符垃圾值；
- 下游 L10 整合因此拿到错误的领域归属。

### 缺陷 C：L10 整合同型累积 + 守卫失效
`integrate_step()` 的去重只比对"最近 5 条 `level='L10'`"的行，但生产里早期 L10 已被分类器重定级为 `L6`，守卫查不到 → 每轮继续追加（"来源：2 条 → 4 条 → 6 条 → 8 条"）。

## 决策

1. **基数约定（upsert contract）**：
   - **L9**：每个 `(agent_name, category)` 只保留**一条**蒸馏记忆；
   - **L10**：每个 `agent_name` 只保留**一条**跨领域整合记忆。
2. 存在则 **UPDATE**（内容/hash/subject/时间），不存在才 INSERT；hash 未变则跳过。
3. 检测方式必须**与 level 无关**（L10 按内容前缀 `[L10 整合]%` 匹配），因为分类器会重定级。
4. 由 `config.distill.upsert_enabled`（默认 `True`）门控，可一键回退到旧的追加行为。
5. 历史冗余用 `dedupe_l9()` 归档（`level='archived'`，**可逆、不删数据**），保留每组最新一条。

## 后果

- **正**：生产库 L9 828 → **100 条有效**（归档 728、冗余组 0）；近重复对 572 → **142**（降 75%）；L9 归属恢复正确。
- **正**：从根因止血，后续 pipeline 周期不再增长。
- **负**：L9 只保留每个领域的最新一条摘要，丢失历史版本演进轨迹——可接受，因为蒸馏本身是可重算的派生物。
- **遗留**：剩余 142 对近重复待下一个周期验证是否被 L10 upsert 消除；若仍存在需另立 ADR 分析。
- **验证**：`tests/test_distill_upsert.py`（8 例，含"分组键不得被遮蔽"的回归断言）。
