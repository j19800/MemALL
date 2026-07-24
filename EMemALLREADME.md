# MemALL — 本地优先的 AI Agent 记忆系统

[![Python](https://img.shields.io/badge/python-3.12+-blue.svg)]()
[![License](https://img.shields.io/badge/license-MIT-green.svg)]()
[![Tests](https://img.shields.io/badge/tests-327_passing-brightgreen.svg)]()
[![Coverage](https://img.shields.io/badge/coverage-48%25-yellow.svg)]()

---

**MemALL** 是一个开源、本地优先的 AI Agent 记忆系统。提供完整的记忆生命周期管理、可插拔记忆策略、实体提取和知识图谱，以及多 Agent 共享能力。

## 核心特性

### 22 级记忆分层
从 `P0`（临时）到 `L11`（领域智能），覆盖 11 个认知层级，每级有独立的 TTL、衰减速率和权重。

### 完整记忆生命周期
```
capture -> classify -> enrich -> reflect -> distill -> integrate -> decay -> forget
```
自动分类、蒸馏、反思、冲突检测、低价值记忆自动遗忘。

### 可插拔记忆策略
| 策略 | 说明 |
|------|------|
| BufferStrategy | 滑动窗口，保留最近 N 条 |
| SummaryStrategy | 自动触发 L9 摘要 |
| EntityStrategy | 实体提取 + 实体检索 |
| KGStrategy | 知识图谱三元组 + 图遍历 |

### 实体提取 + 知识图谱
自动提取人物、技术、工具、语言等实体，中英文 SPO 三元组提取，知识图谱图遍历检索。

### 多 Agent 共享
软引用共享（不复制数据），信任级别过滤（private -> public），广播/查询/取消共享。

### MCP 协议支持
Streamable HTTP（推荐）、STDIO 子进程模式、7 个工具、30 个子操作。

## 快速开始

```bash
git clone https://github.com/j19800/MemALL.git
cd MemALL
pip install -r requirements.txt
python -m memall.gateway
# 打开 http://localhost:9920
```

## 性能

| 操作 | 延迟 |
|------|------|
| 写入记忆 (capture) | ~5ms |
| 检索 (retrieve) | ~2ms |
| 上下文组装 (build_context) | ~15ms |
| 混合搜索 (hybrid_search) | ~50ms |

## 架构

```
CLI / MCP / HTTP Gateway
        |
   策略层 (Buffer / Summary / Entity / KG)
        |
   核心层 (capture / retrieve / build_context)
        |
   管线层 (classify -> distill -> decay)
        |
   存储层 (SQLite / FTS5 / vec0 / entities)
```

## 测试

```bash
python -m pytest tests/ --tb=short --ignore=tests/test_link.py
```

## License

MIT
