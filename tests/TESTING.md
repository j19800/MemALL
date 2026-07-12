# MemALL 测试指南

## 快速开始

```bash
# 安装测试依赖
pip install pytest coverage

# 运行全部测试（不含 e2e 和 link）
python -m pytest tests/ --tb=short --ignore=tests/test_link.py --ignore=tests/test_e2e.py --ignore=tests/smoke_test.py

# 运行特定模块
python -m pytest tests/test_thin_waist.py -v
python -m pytest tests/test_strategy_*.py -v

# 带覆盖率
python -m coverage run -m pytest tests/ --tb=short --ignore=tests/test_link.py --ignore=tests/test_e2e.py --ignore=tests/smoke_test.py
python -m coverage report --omit="*/migrations/*,*/tests/*,*/site-packages/*"
```

## 测试统计

| 指标 | 数值 |
|------|------|
| 测试总数 | **286** |
| 通过 | **286** |
| 失败（预知） | 2（gateway.py — 需要独立端口） |
| 跳过 | 2（embeddings — 需要 sentence-transformers） |
| 整体覆盖率 | **48%** |
| 测试文件数 | 47 |
| 源文件数 | ~130（13,070 语句） |
| 最后运行 | 2026-07-10 |

## 覆盖率详细

### 高覆盖（≥80%）

| 模块 | 覆盖率 |
|------|--------|
| `pipeline/decay.py` | 100% |
| `pipeline/metrics.py` | 97% |
| `core/tracer.py` | 95% |
| `pipeline/arc_status.py` | 95% |
| `pipeline/suggest.py` | 93% |
| `strategy/entity.py` | 93% |
| `strategy/buffer.py` | 93% |
| `core/lifecycle.py` | 93% |
| `strategy/kg.py` | 91% |
| `strategy/sharing.py` | 90% |
| `pipeline/backup.py` | 90% |
| `strategy/registry.py` | 87% |
| `pipeline/classify.py` | 85% |
| `core/entity_extractor.py` | 81% |

### 中覆盖（50-80%）

| 模块 | 覆盖率 |
|------|--------|
| `pipeline/epoch.py` | 80% |
| `config.py` | 77% |
| `pipeline/narrative.py` | 76% |
| `strategy/summary.py` | 76% |
| `pipeline/echo.py` | 76% |
| `pipeline/ops.py` | 75% |
| `pipeline/pipeline.py` | 73% |
| `strategy/base.py` | 70% |
| `pipeline/enrich.py` | 68% |
| `pipeline/session.py` | 62% |
| `core/context_assembler.py` | 60% |
| `pipeline/convergence.py` | 58% |
| `pipeline/adaptive.py` | 56% |
| `federation/family.py` | 55% |
| `core/thin_waist.py` | 50% |

### 低覆盖（<50%）

| 模块 | 覆盖率 | 说明 |
|------|--------|------|
| `gateway.py` | 15% | 需要 HTTP 服务器测试 |
| `mcp/tools/*.py` | 9-85% | 部分已有测试，部分需 MCP 模拟 |
| `pipeline/persona.py` | 9% | LLM 依赖，需要 mock |
| `mcp/adapter.py` | 21% | MCP 全链路测试 |
| `federation/conflict.py` | 12% | 需要联邦设置 |
| `search/*.py` | 19-44% | 需要向量扩展 |

## 测试分类

### 1. 核心层测试（core/）

| 测试文件 | 覆盖模块 | 说明 |
|---------|---------|------|
| `test_thin_waist.py` | `core/thin_waist.py` | capture/retrieve 基本流程、质量门控、去重、更新 |
| `test_thin_waist_advanced.py` | `core/thin_waist.py` | connect/traverse/update 高级、smart_store、normalize_agent_name |
| `test_thin_waist_edge.py` | `core/thin_waist.py` | 边缘场景：质量门拒绝、store_batch、无效 relation、L5 状态、thread_id |
| `test_nlp.py` | `core/nlp.py` | TF-IDF、余弦相似度 |
| `test_context_assembler.py` | `core/context_assembler.py` | build_context 三层组装 |
| `test_config.py` | `config.py` | 配置加载、env 覆盖、dot-path |
| `test_entity_extractor.py` | `core/entity_extractor.py` | 实体提取、三元组提取、实体解析 |
| `test_metrics.py` | `core/metrics.py` | 指标收集 |
| `test_hooks.py` | `core/lifecycle.py` | HookRegistry、dispatch_lifecycle、hook 装饰器 |

### 2. 管线测试（pipeline/）

| 测试文件 | 覆盖模块 | 说明 |
|---------|---------|------|
| `test_pipeline.py` | `pipeline/pipeline.py` | 管线步骤注册、执行、质量门 |
| `test_classify.py` | `pipeline/classify.py` | 分类步骤（L4/L6/L7 等） |
| `test_distill.py` | `pipeline/distill.py` | 蒸馏步骤（L9 生成） |
| `test_reflect.py` | `pipeline/reflect.py` | 反思步骤（L6） |
| `test_convergence.py` | `pipeline/convergence.py` | 讨论收敛 |
| `test_forget.py` | `pipeline/forget.py` | 遗忘（过期/低价值/归档） |
| `test_decay.py` | `pipeline/decay.py` | 置信度衰减 |
| `test_enrich.py` | `pipeline/enrich.py` | 富化步骤 |
| `test_ops.py` | `pipeline/ops.py` | 记忆操作（合并/拆分/标记/去重） |
| `test_session.py` | `pipeline/session.py` | 会话管理（start/end/summary） |
| `test_epoch.py` | `pipeline/epoch.py` | Epoch 管理 |
| `test_improve.py` | `pipeline/improve.py` | 改进建议 |
| `test_suggest.py` | `pipeline/suggest.py` | 建议生成 |
| `test_backup.py` | `pipeline/backup.py` | 备份 + 轮转 |
| `test_narrative.py` | `pipeline/narrative.py` | 叙事生成 |
| `test_cluster.py` | `pipeline/cluster.py` | 聚类 |
| `test_adaptive.py` | `pipeline/adaptive.py` | 自适应清理 |
| `test_security.py` | `pipeline/security.py` | 安全审计 |
| `test_arc_status.py` | `pipeline/arc_status.py` | 决策弧状态 |
| `test_time_slice.py` | `pipeline/time_slice.py` | 时间片 |
| `test_bridge.py` | `pipeline/bridge.py` | 桥接分析 |

### 3. 策略测试（strategy/）— 新增

| 测试文件 | 覆盖模块 | 覆盖率 |
|---------|---------|--------|
| `test_strategy_buffer.py` | `strategy/buffer.py` | 93% ✅ |
| `test_strategy_entity.py` | `strategy/entity.py` | 93% ✅ |
| `test_strategy_kg.py` | `strategy/kg.py` | 91% ✅ |
| `test_strategy_registry.py` | `strategy/registry.py` | 87% ✅ |
| `test_strategy_sharing.py` | `strategy/sharing.py` | 90% ✅ |
| `test_strategy_summary.py` | `strategy/summary.py` | 76% ✅ |

### 4. MCP 工具测试（mcp/tools/）

| 测试文件 | 覆盖模块 | 说明 |
|---------|---------|------|
| `test_mcp_tools.py` | `mcp/tools/__init__.py` | _handle_write/read/persona/discussion/system/hooks |

### 5. 联邦测试（federation/）

| 测试文件 | 覆盖模块 | 说明 |
|---------|---------|------|
| `test_federation_family.py` | `federation/family.py` | 联邦家庭数据库 |
| `test_federation_health.py` | `federation/health.py` | 联邦健康检查 |

### 6. 集成测试

| 测试文件 | 说明 |
|---------|------|
| `test_gateway.py` | Gateway HTTP 服务（start/stop/capture） |
| `test_e2e.py` | 端到端流程 |
| `smoke_test.py` | 冒烟测试（不通过 pytest 收集） |

### 7. 辅助

| 测试文件 | 说明 |
|---------|------|
| `test_core_db.py` | `core/db.py` | DB 工具函数、pool_conn、init_db |
| `test_scheduler.py` | `plugins/scheduler.py` | TaskScheduler、任务添加/删除/执行 |

## 编写新测试

### 基本模式

```python
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


def test_my_feature():
    """Description of what this test verifies."""
    from tests.test_helpers import init_temp_db, cleanup_temp_db
    from memall.core.thin_waist import capture

    db_path, patcher = init_temp_db()
    try:
        # ... test logic ...
        assert result is not None
        print("  PASS test_my_feature")
    finally:
        cleanup_temp_db(db_path, patcher)
```

### 重要约定

1. **每次测试独立 DB**：使用 `init_temp_db()` / `cleanup_temp_db()` 确保隔离
2. **内容长度**：capture() 的内容需 >= 50 字符以通过质量门
3. **level 选择**：L4/L6 需要更高质量内容，测试失败可先用 raw SQL 插入
4. **打印 PASS**：每个测试结束时打印 `"  PASS test_name"` 便于定位
5. **sys.path**：所有测试文件必须 `sys.path.insert(0, ...)` 添加 src 路径

### 绕过质量门的技巧

当测试只需要 DB 中有数据而不需要经过 capture 的质量过滤时：

```python
from memall.core.db import get_conn, content_hash as ch
from datetime import datetime, timezone

conn = get_conn()
now = datetime.now(timezone.utc).isoformat()
h = ch("test content")
conn.execute(
    "INSERT INTO memories (content, content_hash, level, agent_name, occurred_at, created_at, updated_at) "
    "VALUES (?, ?, 'L4', 'test_agent', ?, ?, ?)",
    ("test content", h, now, now, now),
)
conn.commit()
conn.close()
```

## 低覆盖模块（需补充测试）

| 模块 | 当前覆盖率 | 难度 | 策略 |
|------|-----------|------|------|
| `gateway.py` | 15% | 高 | 需要 HTTP 服务器，可用 `aiohttp.test_utils` |
| `mcp/tools/*.py` | 9-40% | 中 | 直接调用 handler 函数（已有 11 个测试） |
| `core/thin_waist.py` | 50% | 中 | 补充 hybrid_search、_score_quality |
| `search/vec0_provider.py` | 29% | 高 | 需要 sqlite-vec 扩展 |
| `search/faiss_provider.py` | 19% | 高 | 需要 FAISS |
| `graph/embeddings.py` | 30% | 高 | 需要 sentence-transformers |
| `federation/conflict.py` | 12% | 高 | 需要联邦设置 |

## 常见问题

**Q: 测试运行时卡在 "import sentence_transformers"？**
A: 这是已知问题 — torch DLL 在部分 Windows 上初始化失败。`_check_st_available()` 有 5 秒超时。

**Q: gateway 测试总是失败？**
A: `test_gateway.py` 需要空闲端口 19940/19941。运行前请确保无其他 gateway 实例。

**Q: 如何只运行新增的策略测试？**
A: `python -m pytest tests/test_strategy_*.py -v`

**Q: 测试需要外部 API key 吗？**
A: 不需要。所有测试使用内存 SQLite 数据库，无外部依赖（需要 sentence-transformers 的嵌入测试除外）。