# ADR-0002：所有写入收敛到 `thin_waist.capture()`

- **状态**：Accepted（2026-09-26）
- **决策者**：老陈
- **相关**：`src/memall/core/thin_waist.py`、`src/memall/core/project_infer.py`、`pipeline/_backfill_thread.py`、`pipeline/_backfill_project.py`

## 背景

诊断发现写入路径存在"护栏只在外层、入口不设防"的结构性缺陷：

- **`project` 77% 为空**：推断逻辑零散分布在 `mcp/tools/capture.py`、`mcp/tools/memory_write.py`、`agent_memory.py`、`mcp/tools/__init__.py`（`quick` 的硬编码 `kw_map`）共 **4 处漂移实现**；唯一公共入口 `capture()` 反而**不推断**。
- **`thread_id` 覆盖率 0%**：全库无任何写入时赋值逻辑，导致 `traverse(thread_aware=True)` 永久失效。
- **标签丢失**：`agent_memory.add()` 向 `MemoryInput` 传 `tags`，而 `MemoryInput` 无该字段 → SDK 主入口**每次调用必抛 TypeError**；DB 自迁移 014 起就有 `tags` 列，却无人落库。

## 决策

1. `thin_waist.capture()` 是**唯一写入漏斗**，所有路径（MCP capture / smart_store / store_batch / agent_memory.add / 原始调用）必须经它。
2. 数据质量护栏在 `capture()` 内强制：
   - `project` 缺失 → 经 `core/project_infer.infer_project()` 推断（**唯一真源**，删除其余 4 处实现）；
   - `thread_id` 缺失 → 挂到同 `(agent_name, project, 时间窗)` 的最早记忆（root 自身保持 NULL）；
   - `tags` → 随 INSERT 落库。
3. 新增强制能力一律**配置门控并默认保守**（如语义去重默认关闭），避免误伤真实记忆。
4. 历史数据用 `_backfill_thread` / `_backfill_project` 补齐，动生产库前**先备份 + dry-run**。

## 后果

- **正**：`project` 空值从 77% 降到 ~0；`thread_id` 覆盖率 0% → 82%（生产实测 thread_aware 召回 1 → 496 节点）；`add()` 恢复可用。
- **负**：老调用方"显式传空 project"的语义被覆盖（现在会推断出值）——这正是期望行为。
- **验证**：`tests/test_project_infer.py`、`tests/test_capture_project_inference.py`、`tests/test_capture_thread_inference.py`。
