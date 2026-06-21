# 竞品对比：MemALL vs Mem0 / Letta / Zep

## 核心定位差异

| 维度 | MemALL | Mem0 | Letta | Zep |
|------|--------|------|-------|-----|
| **定位** | Multi-agent Memory OS / Loop Engineering 记忆基础设施 | 个人 AI 记忆层 | Agent 服务端框架 | 对话记忆中间件 |
| **记忆模型** | 10 层生命周期（P0-L10） | 用户/会话 两层 | 智能体/记忆块 | 会话/摘要 两层 |
| **持久化** | SQLite（本地优先） | 云 API | PostgreSQL | 云 API |
| **协议** | MCP Server | REST API | REST + gRPC | REST API |
| **开源** | ✅ 全开源 | ⚠️ 部分 | ✅ | ⚠️ 部分 |

## 功能差异

### 记忆生命周期

MemALL 独有：P0（紧急）→ P1/P2（规划）→ L1-L10（认知层级），每条记忆有明确的层级语义和转换规则。竞品只有"用户记忆"和"会话记忆"两类。

### 时间线

| 能力 | MemALL | Mem0 | Letta | Zep |
|------|--------|------|-------|-----|
| 预聚合时间片 | time_slices（日/周/月） | ❌ | ❌ | ❌ |
| 时期分段 | epochs（自动检测 gap/主题漂移/反思拐点） | ❌ | ❌ | ❌ |
| 时间衰减 | temporal_weight（指数+epoch_boost） | ❌ | ❌ | 基础衰减 |
| 感知时长 | ❌（Phase 3） | ❌ | ❌ | ❌ |

### 决策追踪

| 能力 | MemALL | Mem0 | Letta | Zep |
|------|--------|------|-------|-----|
| 决策生命周期 | 决策弧（open→in_progress→closed） | ❌ | ❌ | ❌ |
| 任务关联 | L5 任务自动关联决策 | ❌ | ❌ | ❌ |
| 反思闭环 | L6 反思自动闭合决策弧 | ❌ | ❌ | ❌ |
| 未闭合决策注入 | session_start 自动提示 | ❌ | ❌ | ❌ |

### 记忆管线

MemALL 有 21 步自动管线（enrich → classify → time_slice → arc_status → echo → epoch → reflect → distill → integrate → ...）。竞品依赖用户手动触发或简单 CRUD。

### 讨论收敛

MemALL Phase 1.5 支持多 Agent 讨论自动推进到共识并 capture 为可追溯的决策记忆。竞品无此能力。

### 多 Agent 协作

| 能力 | MemALL | Mem0 | Letta | Zep |
|------|--------|------|-------|-----|
| 跨 Agent 共享 | 共享记忆 + 权限控制 | ❌ | 同 Agent 内 | ❌ |
| Agent 身份 | identities 表 + L1/L7 画像 | ❌ | 智能体定义 | ❌ |
| 联邦记忆 | 跨设备同步 + LAN 发现 | ❌ | ❌ | ❌ |

## 适用场景

| 场景 | 推荐 |
|------|------|
| 个人 AI 助手记忆 | Mem0 / MemALL |
| 单 Agent 长期对话 | Zep |
| 自主 Agent 服务端 | Letta |
| **多 Agent 协作系统** | **MemALL** |
| **Loop Engineering 基础设施** | **MemALL** |
| **本地优先 / 离线场景** | **MemALL** |
| 团队 / 企业级 | Letta / Zep Cloud |

## MemALL 的核心差异总结

1. **生命周期深度**：10 层不是噱头，每条记忆知道自己的"认知权重"
2. **时间线维度**：time_slices + epochs = Agent 知道自己"从哪来、在哪个阶段"
3. **决策弧**：让 Agent 知道"哪些决策没有收尾"，这是 Loop Engineering 中 Sub-agents 协作的前提
4. **离线优先**：SQLite 本地运行，不依赖云 API，适合需要数据主权的场景
5. **管线自动化**：21 步管线自动处理，无需手动维护
