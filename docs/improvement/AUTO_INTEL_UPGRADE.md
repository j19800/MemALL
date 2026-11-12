# MemALL 自动入库/语义去重/自进化/智能化 强化设计文档

## 背景
当前系统已有基础能力，但四大维度仍有显著提升空间：
- 自动入库：仅 3 个 MCP 工具源，覆盖面窄
- 语义去重：默认关闭，仅同 Agent 近期窗口，无 Embedding 级
- 自进化：仅 L6→corrections，缺 L4→L6、规则验证、元学习
- 智能化：subject 机械前缀、无摘要、无自动建边、无效用驱动

## 设计目标
1. **自动入库覆盖率** ≥ 80% 高价值事件（对话/代码/工具/错误/任务）
2. **语义去重召回率** ≥ 90%（在护栏约束下），误合并率 0%
3. **自进化闭环**：L4→L6→Correction→Session Injection→Effectiveness Tracking 全链路
4. **智能化指标**：subject 可读性 +40%、检索命中率 +15%、存储效率 +20%

## 架构设计

### 模块新增
```
src/memall/pipeline/
├── auto_capture_v2.py      # 扩展自动捕获源
├── semantic_dedup_v2.py    # 三层语义去重
├── evolution_engine.py     # 自进化引擎（L4→L6→规则→验证）
├── intelligence_layer.py   # 智能化层（摘要/建边/效用）
└── quality_gate_v2.py      # 分层质量门控
```

### 配置扩展 (config.py)
```python
"capture": {
    "auto_capture_sources": ["mcp_tools", "conversation", "git_commits", "errors", "task_events"],
    "semantic_dedup": {
        "enabled": True,                    # 默认开启
        "threshold_jaccard": 0.85,
        "threshold_cosine": 0.90,
        "window_same_agent": 50,
        "window_cross_agent": 0,            # 0=关闭
        "window_cross_session": 0,
        "use_embeddings": True,
    },
    "quality_gate": {
        "strict_min_len": 30,
        "lenient_min_len": 15,
        "fallback_to_p2": True,
    }
},
"evolution": {
    "l4_to_l6_enabled": True,
    "l4_to_l6_min_confidence": 0.7,
    "rule_effectiveness_tracking": True,
    "cross_agent_consensus_threshold": 2,
    "internalization_keywords": [...],
},
"intelligence": {
    "auto_subject_enabled": True,
    "auto_summary_enabled": True,
    "auto_edge_creation_enabled": True,
    "utility_driven_retention": True,
    "subject_prefix_style": "compact",      # "compact"=[L4] / "full"=[L4 会话]
}
```

## 实施阶段

### Phase 1: 基础设施与配置 (Day 1)
- [ ] 扩展 config.py 新增四大维度配置段
- [ ] 创建 quality_gate_v2.py 分层门控
- [ ] 迁移 subject 前缀风格（兼容 migration 027）

### Phase 2: 语义去重升级 (Day 1-2)
- [ ] 实现 semantic_dedup_v2.py 三层架构
- [ ] 集成到 capture() 写入路径
- [ ] 补充安全测试（极性/数量/时间/主语/方向护栏）

### Phase 3: 自动入库扩展 (Day 2)
- [ ] 实现 auto_capture_v2.py 多源提取器
- [ ] 注册新 hook 点（conversation/git/error/task）
- [ ] 智能分流与分层质量门控

### Phase 4: 自进化引擎 (Day 2-3)
- [ ] 实现 evolution_engine.py (L4→L6 提取)
- [ ] 规则有效性跟踪 + 元学习
- [ ] 集成 session_start 注入

### Phase 5: 智能化层 (Day 3)
- [ ] 实现 intelligence_layer.py (摘要/建边/效用)
- [ ] Subject 紧凑模式 + 迁移兼容
- [ ] 效用驱动保留策略

### Phase 6: 集成测试与验收 (Day 3-4)
- [ ] 端到端测试：自动入库→去重→进化→智能化
- [ ] 回归测试全量通过
- [ ] 性能基准：5000 条记忆 < 1s 检索，写入 < 50ms
- [ ] 生成技术债务账本与提交清单

## 兼容性保证
- Migration 027 依赖的 `[L4 会话]` 前缀：新增 `subject_prefix_style` 配置，默认 `compact` 生成 `[L4]`，迁移脚本可通过配置或内容前缀识别
- 现有 API 不变，仅增强内部实现
- 所有新功能默认可通过配置关闭

## 验收标准
| 指标 | 目标 | 测试方式 |
|------|------|----------|
| 自动入库覆盖率 | ≥80% | 模拟 5 类事件源，统计入库率 |
| 语义去重召回率 | ≥90% | 标注近似重复对，验证合并 |
| 误合并率 | 0% | 极性/数量/时间/主语/方向护栏用例 |
| L4→L6 提取准确率 | ≥85% | 人工标注决策结果对 |
| Subject 可读性 | 前缀 ≤4 字符 | 统计前缀长度分布 |
| 检索命中率提升 | ≥15% | 固定查询集对比 |
| 全量测试通过率 | 100% | pytest tests/ -q |

## 风险与缓解
| 风险 | 缓解 |
|------|------|
| 语义去重误合并 | 保守阈值 + 多重护栏 + 可配置关闭 |
| 自动入库噪音 | 分层质量门控 + 显式来源标记 + 审计日志 |
| 进化规则失效 | 有效性跟踪 + 内化信号过期 + 人工复核入口 |
| 性能回归 | 异步后台处理 + 批量写入 + 连接池复用 |
