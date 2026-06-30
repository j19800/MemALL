# MemALL 功能规格说明书

> Multi-agent collaborative memory OS — 本地优先的 AI Agent 记忆系统
>
> 版本：0.1.0 | 更新：2026-06-07

---

## 目录

1. [系统概述](#1-系统概述)
2. [核心引擎](#2-核心引擎)
3. [CLI 命令行接口](#3-cli-命令行接口)
4. [MCP 协议层](#4-mcp-协议层)
5. [记忆管线系统](#5-记忆管线系统)
6. [联邦系统](#6-联邦系统)
7. [知识图谱](#7-知识图谱)
8. [HTTP 网关](#8-http-网关)
9. [插件系统](#9-插件系统)
10. [基础设施](#10-基础设施)
11. [数据库 Schema](#11-数据库-schema)
12. [附录：架构全景图](#12-附录架构全景图)

---

## 1. 系统概述

### 1.1 定位

MemALL 是一个**本地优先、MCP 原生**的 AI Agent 记忆系统。它为 AI Agent 提供持久化记忆存储、语义检索、知识图谱关联、自动分类聚类、人格画像生成、跨 Agent 联邦共享、自我进化与衰减遗忘等能力。

### 1.2 核心架构

```
┌─────────────────────────────────────────────────────────┐
│                    AI Agent Layer                        │
│     Claude / Cursor / OpenCode / Solo / 自定义 Agent    │
└────────────────────┬────────────────────────────────────┘
                     │ MCP Protocol (STDIO)
┌────────────────────▼────────────────────────────────────┐
│                   MCP 协议层 (mcp/)                       │
│   28 个工具 · Pydantic 校验 · JSON-RPC STDIO 服务器      │
└────────────────────┬────────────────────────────────────┘
                     │
┌────────────────────▼────────────────────────────────────┐
│                  核心引擎 (core/)                         │
│  SQLite · NLP · 向量搜索 · 图遍历 · 信任级别             │
└───────┬────────────────────┬──────────────────┬─────────┘
        │                    │                  │
┌───────▼───────┐  ┌────────▼───────┐  ┌──────▼────────┐
│  记忆管线      │  │  联邦系统       │  │  HTTP 网关     │
│  (pipeline/)  │  │  (federation/) │  │  (gateway.py)   │
│  21 个模块     │  │  4 个模块       │  │  设备互联配对  │
└───────────────┘  └───────────────┘  └───────────────┘
```

### 1.3 设计原则

- **本地优先**：所有数据存储在用户本地 `~/.memall/`，无需云服务
- **MCP 原生**：通过 Model Context Protocol 与任何支持 MCP 的 AI Agent 对接
- **自演化**：记忆自动分类、聚类、蒸馏、衰减、反思
- **人格画像**：为每个 Agent 生成五色人格原型，追踪认知演化
- **联邦协作**：跨 Agent、跨设备共享记忆，冲突检测与自动解决

---

## 2. 核心引擎

### 2.1 数据库层 — `core/db.py`

SQLite 为核心存储引擎，采用 WAL 模式保证读写并发安全。

| 功能 | 说明 |
|------|------|
| 连接池 | 线程安全，基于 `queue.Queue`，默认最大 5 连接，上下文管理器 |
| Schema 初始化 | 自动创建 12 张表，FTS5 全文搜索触发器 |
| 内容去重 | SHA-256 content_hash，碰撞时仅更新 access_count |
| 数据库维护 | VACUUM（回收空间）、ANALYZE（更新统计）、PRAGMA optimize |
| 自动迁移 | migrations/ 目录文件型迁移引擎，自动发现/顺序执行/回滚 |

### 2.2 数据模型 — `core/models.py`

| 模型 | 关键字段 | 用途 |
|------|---------|------|
| `Memory` | id, content, level(P0/P1/P2/L6/L7/L9), owner, agent_name, subject, project, category, summary, confidence, visibility(public/shared/family/trusted/private), content_hash, access_count, embedding, tags, metadata | 完整记忆数据类 |
| `MemoryInput` | content, level, owner, agent_name, subject, project, category, tags | 记忆输入模型 |

### 2.3 NLP 工具 — `core/nlp.py`

| 功能 | 说明 |
|------|------|
| 分词 | CJK 字符拆分 + 英文 token 提取，~80 个中英停用词过滤 |
| TF-IDF | 稀疏向量化，返回 `[{term: score}]` 格式 |
| 余弦相似度 | 两个稀疏 TF-IDF 字典的相似度计算，范围 [0,1] |
| SVD 嵌入 | TF-IDF → TruncatedSVD 管道输出 (n, 256) 维向量 |

### 2.4 核心业务逻辑 — `core/thin_waist.py`

#### 2.4.1 基础 CRUD

| 操作 | 函数 | 特性 |
|------|------|------|
| 存储 | `capture()` | 支持 MemoryInput/dict/str 三种输入；content_hash 去重；自动 Agent 权限校验 |
| 检索 | `retrieve()` | 按 ID 精确查询 / FTS 全文搜索 / 字段过滤 / 信任级别过滤 |
| 更新 | `update()` | 更新 level/category/project/summary/subject/confidence/visibility/content |
| 删除 | — | 通过 forget/decay 管线进行 |

#### 2.4.2 图操作

| 操作 | 函数 | 特性 |
|------|------|------|
| 创建边 | `connect()` | 6 种关系类型：extends, contradicts, refines, cites, supersedes, related；去重 |
| 图遍历 | `traverse()` | 支持深度限制（默认 1，最大 5）和 relation_type 过滤；返回 {root, nodes, edges} |
| 时间线 | `timeline()` | 按 occurred_at 降序，支持 FTS / 分类 / 项目 / 时间范围 / 天数过滤 |

#### 2.4.3 增强存储

| 操作 | 函数 | 特性 |
|------|------|------|
| 智能存储 | `smart_store()` | 先精确哈希去重，再语义相似度去重（TF-IDF+SVD，阈值可调默认 0.85） |
| 批量存储 | `store_batch()` | 一次存储多条记忆，返回 {ids, count} |
| 向量搜索 | `vector_search()` | 委托 graph.retrieve 进行语义向量搜索 |

#### 2.4.4 信任与权限

| 机制 | 说明 |
|------|------|
| 可见性等级 | public < shared < family < trusted < private（从低到高） |
| Agent 可读级别 | 从 identities 表读取 permission_level |
| 写可见性 | 根据 Agent 权限级别计算允许写入的最大可见级别 |
| 信任过滤 | 根据 viewer 的身份 ID 和信任关系过滤记忆列表 |

### 2.5 上下文组装 — `core/context_assembler.py`

| 功能 | 说明 |
|------|------|
| 人格提取 | 从 L6/L7 级别记忆中提取：最近决策、活跃主题、未解决矛盾、衍生见解 |

---

## 3. CLI 命令行接口

通过 `memall <command> [subcommand] [options]` 访问所有功能。

### 3.1 记忆管理

| 命令 | 功能 | 关键参数 |
|------|------|---------|
| `memall init` | 初始化数据库与 Schema | — |
| `memall capture` | 存储一条记忆 | `--content`, `--owner`, `--agent`, `--subject`, `--project`, `--category`, `--level` |
| `memall search` | 搜索记忆 | `--query`, `--owner`, `--agent`, `--category`, `--limit` |
| `memall get` | 按 ID 获取记忆 | `--id` |
| `memall update` | 更新记忆字段 | `--id`, `--content`, `--category`, `--project`, `--level` |
| `memall timeline` | 记忆时间线 | `--hours`, `--category`, `--project`, `--limit` |

### 3.2 图谱操作

| 命令 | 功能 | 关键参数 |
|------|------|---------|
| `memall connect` | 创建记忆间关系边 | `--source`, `--target`, `--relation`, `--weight` |
| `memall traverse` | 图遍历 | `--node`, `--depth`, `--relation` |
| `memall graph-visualize` | 图谱可视化 | `--center`, `--limit`, `--format`(html/png), `--output` |

### 3.3 管线运维

| 命令 | 功能 | 关键参数 |
|------|------|---------|
| `memall pipeline` | 运行增强管线 | `--dry-run`, `--include-persona`, `--include-cluster`, `--include-distill`, `--include-reflect` |
| `memall forget` | 自动遗忘 | `--action`(expired/low_value/review/stats/all), `--days`, `--agent` |
| `memall adaptive` | 自适应子系统 | `--action`(clean/index/distill/all/report), `--agent` |
| `memall security` | 安全治理 | `--action`(audit/permit/check/score/list), `--agent`, `--level` |
| `memall ops` | 记忆运维 | `--action`(merge/split/tag/batch_tag/archive/restore/dedup) |
| `memall index` | 管理向量索引 | `--action`(build/status) |
| `memall retrieve` | 语义检索 | `--query`, `--mode`(keyword/vector/hybrid), `--top-k` |

### 3.4 人格画像

| 命令 | 功能 | 关键参数 |
|------|------|---------|
| `memall persona` | 生成/查看人格画像 | `--agent`, `--evolution`, `--window-days` |
| `memall cluster` | 话题聚类管理 | `--action`(list/show/stats) |
| `memall cluster-show` | 查看簇详情 | `--cluster-id` |
| `memall narrative` | 生成叙事报告 | `--agent`, `--span`(weekly/monthly/phase) |

### 3.5 会话与问答

| 命令 | 功能 | 关键参数 |
|------|------|---------|
| `memall ask` | 数字孪生问答 | `--question`, `--mode`(stance/pattern/predict), `--agent`, `--scope` |
| `memall suggest` | 管理建议 | `--action`(list/show/stats) |

### 3.6 联邦与网关

| 命令 | 功能 | 关键参数 |
|------|------|---------|
| `memall publish` | 发布到共享库 | `--id`, `--trust-level`, `--category` |
| `memall federation` | 联邦管理 | `--action`(health/conflicts/report) |
| `memall family init` | 创建家庭圈 | `--name` |
| `memall family invite` | 邀请成员 | `--member`, `--role` |
| `memall family search` | 跨成员搜索 | `--query`, `--trust-level` |
| `memall gateway start` | 启动 HTTP 网关 | `--port`(默认 9919) |
| `memall gateway discover` | 发现 LAN 设备 | `--timeout` |
| `memall gateway pair` | 配对远程设备 | `--address` |
| `memall gateway federated` | 联邦查询 | `--query`, `--max-peers` |

### 3.7 系统管理

| 命令 | 功能 | 关键参数 |
|------|------|---------|
| `memall start` | 启动调度器守护进程 | — |
| `memall stop` | 停止调度器 | — |
| `memall status` | 系统状态概览 | — |
| `memall doctor` | 诊断并修复数据库 | — |
| `memall migrate` | 应用数据库迁移 | `--check`(预览), `--list` |
| `memall setup` | 配置 AI Agent 的 MCP 连接 | `--all`, `--agent`(claude/cursor/opencode/solo) |
| `memall register` | 注册自定义 Agent | `--agent`, `--type`(mcp/http/stdio) |
| `memall uninstall` | 移除 MCP 配置 | `--purge`(删除数据目录) |
| `memall backup` | 备份数据库 | `--output` |
| `memall restore` | 从备份恢复 | `--input` |

### 3.8 数据工具

| 命令 | 功能 | 关键参数 |
|------|------|---------|
| `memall export` | 导出记忆 | `--agent`, `--format`(json/markdown/yaml) |
| `memall db optimize` | 数据库优化 | — |
| `memall db stats` | 数据库统计 | — |
| `memall db vacuum` | 回收空间 | — |
| `memall onboarding` | 新用户引导 | `--step` |

---

## 4. MCP 协议层

MCP 协议层将 MemALL 的所有功能暴露为标准化的 MCP 工具，任何支持 MCP 的 AI Agent（Claude Desktop、Cursor、OpenCode、Solo 等）可直接调用。

### 4.1 协议规范

- **传输方式**：STDIO（标准输入输出）
- **协议版本**：2025-03-26
- **支持方法**：`initialize`、`tools/list`、`tools/call`
- **工具数量**：28 个
- **输入校验**：Pydantic v2 模型，33 个校验模型

### 4.2 工具清单

#### 核心 CRUD（5 个）

| 工具 | 输入 | 输出 | 对应 CLI |
|------|------|------|----------|
| `capture` | content(必填), owner, agent_name, subject, project, category, level | memory_id | `capture` |
| `retrieve` | query, owner, agent_name, category, limit(20) | memory[] | `search` |
| `connect` | source_id(必填), target_id(必填), relation_type, weight | edge_id | `connect` |
| `traverse` | node_id(必填), depth(1), relation_filter | {root, nodes, edges} | `traverse` |
| `timeline` | query, hours(24), category, project, limit(50) | memory[] | `timeline` |

#### 智能存储（3 个）

| 工具 | 功能 | 特性 |
|------|------|------|
| `memall_smart_store` | 智能去重存储 | 精确哈希 + 语义相似度双重去重，阈值可调 |
| `memall_store_batch` | 批量存储 | 一次存储多条记忆 |
| `memall_update` | 更新记忆 | 更新 level/category/project/summary/content |

#### 向量搜索（1 个）

| 工具 | 功能 | 特性 |
|------|------|------|
| `memall_vector_search` | 语义搜索 | TF-IDF+SVD 256 维向量，余弦相似度排序 |

#### 人格画像（3 个）

| 工具 | 功能 | 特性 |
|------|------|------|
| `memall_persona` | 获取数字人格 | 认知特征 + 五色比例 + 人格原型 + 演化趋势 |
| `memall_persona_profile` | 3 层 Agent 画像 | L1 认知特征+L2 网络拓扑+L3 行为模式 |
| `memall_ask` | 数字孪生问答 | stance/pattern/predict 三种推理模式 |

#### 会话管理（3 个）

| 工具 | 功能 | 特性 |
|------|------|------|
| `memall_session_start` | 开始会话 | 可选自动注入 Agent Profile + 语义片段 |
| `memall_session_end` | 结束会话 | 自动摘要，可选提取事实到共享记忆 |
| `memall_session_summary` | 会话摘要 | 按会话 ID 或 Agent 名称查询 |

#### 知识图谱（1 个）

| 工具 | 功能 | 特性 |
|------|------|------|
| `memall_graph` | 图谱探索 | 1-2 跳扩展，支持 relation 过滤 |

#### 联邦协作（5 个）

| 工具 | 功能 | 特性 |
|------|------|------|
| `memall_fed_query` | 跨 Agent 查询 | 搜索 shared_memories 共享空间 |
| `memall_fed_publish` | 发布到共享 | 自动脱敏 `<private>` 标签 |
| `memall_fed_conflicts` | 冲突列表 | 关键词 + 语义矛盾检测 |
| `memall_fed_inject` | 自动注入 | Agent Profile + 语义片段（session_start 时自动调用） |
| `memall_fed_extract` | 自动提取 | 会话事实提取（session_end 时自动调用） |

#### 遗忘衰减（1 个）

| 工具 | 功能 | 特性 |
|------|------|------|
| `memall_write action=forget` | 自动遗忘 | TTL 过期(90天) + 低值(<30字+7天) + 预览审查 + 自动备份 |

#### 自适应（1 个）

| 工具 | 功能 | 特性 |
|------|------|------|
| `memall_system action=adaptive` | 自适应子系统 | 动态清洗(aggressive/standard/compression) + 动态索引 + 动态蒸馏 |

#### 安全治理（1 个）

| 工具 | 功能 | 特性 |
|------|------|------|
| `memall_security` | 安全治理 | 5 类敏感数据扫描 + 脱敏 + 三级权限 + 访问控制 + 安全评分(0-100) |

#### 运维（1 个）

| 工具 | 功能 | 特性 |
|------|------|------|
| `memall_ops` | 记忆运维 | merge/split/tag/batch_tag/archive/restore/dedup |

#### 网关（1 个）

| 工具 | 功能 | 特性 |
|------|------|------|
| `memall_gateway` | 网关操作 | start/stop/export/import/discover/pair/peers/federated |

#### 数据库维护（1 个）

| 工具 | 功能 | 特性 |
|------|------|------|
| `memall_system action=db` | 数据库维护 | optimize/stats/vacuum |

---

## 5. 记忆管线系统

管线系统是 MemALL 的核心处理引擎，按序执行多个处理步骤，实现记忆的全生命周期管理。

### 5.1 管线总控 — `pipeline.py`

**执行流程**（按顺序）：

```
step 1: enrich_step()         → 记忆丰富
step 2: classify_step()       → 自动分类
step 3: link_step()           → 关系推断
step 4: decay_step()          → 记忆衰减
step 5: backup_step()         → 自动备份
step 6: [可选] narrative_step() → 叙事生成
step 7: [可选] cluster_step()   → 话题聚类
step 8: [可选] suggest_step()   → 建议提取
step 9: [可选] bridge_analysis_step() → 桥接分析
step 10: [可选] distill_step()    → 记忆蒸馏
step 11: [可选] reflect_step()    → 自我反思
step 12: collect_metrics()       → 指标采集
```

支持 `--dry-run` 预览模式，不执行实际数据修改。

### 5.2 模块详情

#### ① enrich — 记忆丰富

扫描所有非 P0 记忆，自动提取以下信息存入 metadata：

- **实体提取**：人物、技术名词、关键术语
- **时间引用**：日期、时间短语
- **问题描述**：包含"问题/如何/为什么"等的问题性内容
- **决策信息**：包含"决定/选择/采用/放弃"等决策关键词的内容
- **引用发现**：正则检测 ID 引用，自动创建 `derived_from` 关系边

#### ② classify — 自动分类

基于 15 条正则规则的记忆自动分类系统：

| 类别 | 匹配关键词示例 |
|------|--------------|
| decision | 决定、选择、采用、放弃 |
| problem | 问题、bug、错误、失败 |
| architecture | 架构、设计、方案、模式 |
| implementation | 实现、编写、编码、开发 |
| testing | 测试、验证、检查 |
| deployment | 部署、上线、发布 |
| meeting | 会议、讨论、同步 |
| documentation | 文档、注释、手册 |
| planning | 计划、目标、路线图 |
| learning | 学习、研究、理解 |
| idea | 想法、思路、建议 |
| reflection | 反思、回顾、总结 |
| fix | 修复、patch、hotfix |
| config | 配置、参数、设置 |
| rule | 规则、规范、标准 |

扫描 category='general' 的记忆，匹配最高得分类别并更新。

#### ③ link — 关系推断

扫描非 P0 记忆对，自动推断关系：

- **Jaccard 相似度**：>=0.45 为高度相关，使用 RELATION_PATTERNS 推断关系类型
- **矛盾检测**：>=0.2 相似度 + CONTRADICT_PAIRS 关键词对检测 contradicts 关系
- **关系类型**：contradicts / cites / refines / extends

#### ④ decay — 记忆衰减

每个衰减周期执行：

- **清除**：P0 级别 + confidence<0.3 + 从未被访问的记忆
- **衰减**：非 P0 记忆 14 天未访问 → confidence 减 0.02（下限 0.1）
- **清理**：删除悬挂边（orphan edges）

#### ⑤ forget — 自动遗忘

| 操作 | 行为 | 安全 |
|------|------|------|
| `forget_expired` | 删除超过 TTL（默认 90 天）的记忆和关联边 | 自动备份 |
| `forget_low_value` | 删除 <30 字符 + 无关联边 + 超过 7 天的记忆 | 自动备份 |
| `forget_review` | 预览将被删除的记忆（不执行） | 安全预览 |
| `forget_stats` | 遗忘记快照：总数、边数、过期数等 | 统计报告 |

#### ⑥ distill — 记忆蒸馏

按 `agent_name + category` 分组，每组 >=3 条时：

- 创建 L9 级别蒸馏摘要记忆
- 通过 `supersedes` 和 `refines` 边关联原始记忆
- 摘要内容自动合并多条记忆的关键信息

#### ⑦ reflect — 自我反思

扫描非 L6/L7/L9 记忆，检测纠正/错误关键词：

- **关键词集**：不对、修正、纠正、错了、实际上是、反而、正确应该是等
- **处理**：提取问题描述和建议，升级 level 为 'L6'，写入结构化摘要

#### ⑧ persona — 人格画像

**三层 Agent Profile 系统：**

| 层 | 名称 | 内容 |
|----|------|------|
| **L1** | 认知特征 | 样本量、捕获频率、爆发布尔比、规律性、领域广度/深度/新领域率、知识缺口、矛盾数/解决率、自信度 → 五色比例 → 25 种人格原型之一 |
| **L2** | 网络拓扑 | 出入度、聚类系数、桥接节点、网络 leverage、矛盾自索引 |
| **L3** | 行为模式 | 时段节奏、日分布、领域流（转移+粘性）、爆发布尔分析、会话分组 |

**五色人格原型系统：**

| 颜色 | 维度 | 高值特征 |
|------|------|---------|
| 白 (White) | 结构 | 规律性、领域深度 |
| 蓝 (Blue) | 理解 | 领域广度、知识缺口填补 |
| 黑 (Black) | 行动力 | 捕获频率、决策比 |
| 红 (Red) | 强度 | 爆发布尔比、矛盾数 |
| 绿 (Green) | 连接 | 桥接比、新领域率 |

**25 种原型**：Anchor, Arbiter, Strategist, Innovator, Catalyst 等。

**演化追踪**：按时间窗口计算活动度/自信度/决策力趋势。

#### ⑨ ask — 数字孪生问答

三种推理模式：

| 模式 | 功能 | 输出 |
|------|------|------|
| `stance` | 立场分析 | 支持/反对/中立 + 自信度评估 + 论据引用 |
| `pattern` | 模式分析 | 知识领域分布 + 矛盾分析 + 认知特征 |
| `predict` | 行为推演 | 基于人格特征推断 Agent 在给定问题上的可能行为模式 |

#### ⑩ narrative — 叙事生成

为每个 Agent 自动生成三档叙事报告：

| 类型 | 时间跨度 | 内容 |
|------|---------|------|
| weekly | 7 天 | 领域分布、优先级分析、事件时间线 |
| monthly | 30 天 | 同上 |
| phase | 全阶段 | 同上（从第一条记忆开始） |

叙事存入 narratives 表，支持增量更新。

#### ⑪ suggest — 建议提取

从记忆内容中提取可行动建议：

- **5 种正则模式**：自动匹配建议句式
- **8 类分类**：architecture / security / performance / ux / ops / product / quality / other
- **自动去重**：MD5 哈希去重

#### ⑫ bridge — 桥接分析

分析跨簇边（bridge edges）分布：

- 计算每个 Agent 的跨簇边比例（bridge_ratio）
- 按 relation_type 统计
- 基于 bridge_ratio 校准人格色彩权重：高桥接→提升绿色、降低红色

#### ⑬ adaptive — 自适应子系统

根据记忆增长率、总量和查询模式动态调整策略：

| 子系统 | 策略 | 触发条件 |
|--------|------|---------|
| **智能清洗** | aggressive / standard / compression | 增长率、总量阈值 |
| **智能索引** | 建立/清理加速表 | query_log 高频词分析 |
| **智能蒸馏** | 立即蒸馏 / 跳过 / 正常 | 高频/低频增长率 |

**aggressive 清洗**：删除空/标点内容
**compression 清洗**：Jaccard 去重
**智能索引**：高频词 → idx_accel_* 加速表 → 清理 30 天前日志 → 删除 7 天未使用表

#### ⑭ security — 安全治理

| 功能 | 说明 |
|------|------|
| **敏感数据扫描** | 5 类模式：api_key（关键字）、email（正则）、IP（IPv4 验证）、phone（中国手机号）、id_card（身份证），含脱敏预览 |
| **三级权限** | public / trusted / private |
| **访问控制** | private 仅自身、trusted 需在 family_circle 中、public 允许所有 |
| **安全评分** | 0-100 分，4 维度加权：敏感暴露率(40) + 私有记忆比(20) + 孤立敏感比(20) + 权限覆盖率(20)，等级 A-F |

#### ⑮ ops — 记忆运维

| 操作 | 功能 | 说明 |
|------|------|------|
| merge | 合并 | 源内容追加到目标，重定向所有边，删除源记忆 |
| split | 拆分 | 按分隔符拆分为多条，保留边，原记忆归档 |
| tag | 单条打标签 | add/set/remove 三种模式 |
| batch_tag | 批量打标签 | 按 agent+category 批量操作 |
| archive | 归档 | 将旧记忆 level 设为 'archived' |
| restore | 恢复 | 将 archived 恢复为 P2 |
| dedup | 去重 | TF-IDF + cosine 相似度 > 阈值的短记忆合并到长记忆 |

#### ⑯ session — 会话管理

| 操作 | 功能 |
|------|------|
| session_start | 创建会话（UUID[:8]），可选自动注入 Agent Profile / 语义片段 |
| session_end | 结束会话：统计记忆数 + 生成摘要（Top5 类别+样本）+ 可选提取事实到共享记忆 |
| session_summary | 按会话 ID 或 Agent 名称查询 |

#### ⑰ backup — 备份轮转

| 功能 | 说明 |
|------|------|
| 备份创建 | SQLite VACUUM INTO 创建时间戳备份文件 |
| 保留策略 | 保留最近 N 个每日备份 + M 个每周备份，自动删除旧文件 |

#### ⑱ metrics — 指标采集

采集并持久化到 `~/.memall/metrics.jsonl`：

- 总记忆数、边数、连接密度
- 分类覆盖率（已分类/未分类）
- 类别数量
- 活跃 Agent 数量
- 历史趋势查询

---

## 6. 联邦系统

联邦系统实现多 Agent、多设备之间的记忆共享与协作。

### 6.1 家庭圈管理 — `federation/family.py`

| 功能 | 说明 |
|------|------|
| 创建家庭圈 | 初始化 family.db，创建 shared_memories 和 family_circle 表 |
| 邀请成员 | 按 circle_name + member_name + role 添加成员 |
| 成员列表 | 查看家庭圈成员列表 |
| 记忆发布 | 4 级信任过滤：trusted → family → shared → public |
| 跨成员搜索 | 按 query / trust_level / member_filter 搜索共享记忆 |
| 统计 | 总数、各 Agent 贡献、成员数、信任级别分布 |

### 6.2 冲突检测 — `federation/conflict.py`

| 功能 | 说明 |
|------|------|
| 模式 | keyword（20 对矛盾关键词）+ semantic（语义相似度）+ all |
| 关键词对 | "采用/放弃"、"推荐/避免"、"支持/反对"等 20 对 |
| 触发词 | but, however, 实际上, 但是 等 |
| 自动解决 | 根据关键词权重 + 确定性 + 长度打分选择胜者 |
| 手动解决 | 指定 conflict_id + winner_memory_id |

### 6.3 健康监控 — `federation/health.py`

输出联邦系统健康状态：总数、Agent 贡献、冲突状态、趋势数据、近似重复和孤岛记忆列表。

### 6.4 联邦可视化 — `federation/visualize.py`

生成 HTML/PNG 格式联邦报告，包含：

- KPI 卡片（总记忆数/Agent 数/冲突数）
- Agent 贡献分布柱状图
- Conflict Status 饼图
- 记忆增长趋势折线图
- 孤岛记忆和近似重复详情表

---

## 7. 知识图谱

### 7.1 嵌入索引 — `graph/embeddings.py`

| 功能 | 说明 |
|------|------|
| 构建索引 | 为所有记忆构建 TF-IDF+SVD 嵌入向量（256 维），增量更新 |
| 状态查询 | 总记忆数、已嵌入数、待处理数、模型名、维度 |

### 7.2 语义检索 — `graph/retrieve.py`

三种检索模式：

| 模式 | 说明 | 适用场景 |
|------|------|---------|
| `keyword` | LIKE 模糊匹配 | 精确关键词搜索 |
| `vector` | 余弦相似度向量搜索 | 语义近似匹配 |
| `hybrid` | 向量搜索 + 图一跳邻居扩展（加权） | 知识增强检索 |

### 7.3 图谱可视化 — `graph/visualize.py`

| 格式 | 库 | 特性 |
|------|----|------|
| HTML | pyvis | 交互式，节点可拖拽，关联关系可视化 |
| PNG | matplotlib | 静态图，适合报告嵌入 |

支持以指定记忆为中心（2 跳扩展）或按访问量排序 TOP N 节点。

---

## 8. HTTP 网关

网关模块实现 MemALL 实例之间的设备互联。

### 8.1 本地 HTTP 网关 — `gateway.py`

- **框架**：aiohttp 异步
- **端口**：默认 9919
- **模式**：后台线程，非阻塞

**路由：**

| 路由 | 方法 | 功能 |
|------|------|------|
| `/health` | GET | 健康检查 + 记忆计数 + 运行时间 |
| `/capture` | POST | 存储记忆 |
| `/retrieve` | POST | 搜索记忆 |
| `/traverse` | POST | 图遍历 |
| `/timeline` | POST | 时间线查询 |
| `/profile` | POST | 获取 Agent 3 层画像 |
| `/pair` | POST | 设备配对 |

### 8.2 数据同步

| 操作 | 功能 |
|------|------|
| 导出包 | 导出 Agent 全部数据（memories + edges + identity）为可移植 JSON 文件 |
| 导入包 | 导入数据包，自动去重合并 |

### 8.3 LAN 设备发现与配对

| 操作 | 说明 |
|------|------|
| 发现广播 | UDP 广播信标（5 秒间隔） |
| 设备监听 | 监听 LAN 发现信标，返回去重设备列表 |
| 配对 | 向远程网关发送配对请求，记录到 peers.json |
| 同伴列表 | 查看所有已配对的设备 |
| 联邦查询 | 本地 + 远程并行检索，结果去重合并（默认最多 3 个同伴） |

---

## 9. 插件系统

### 9.1 插件加载器 — `plugins/loader.py`

| 功能 | 说明 |
|------|------|
| 自动发现 | 扫描 plugins/ 目录列出所有 .py 文件 |
| 动态加载 | importlib.import_module 运行时加载 |
| 热重载 | importlib.reload 实现热更新 |
| 钩子分发 | 遍历已加载插件调用 on_capture/on_retrieve 等钩子 |
| 卸载 | 从注册表中移除插件 |

### 9.2 Dashboard 插件 — `plugins/dashboard.py`

生成单文件 HTML 仪表盘，零外部依赖：

- **统计卡片**：总记忆数、边数、Agent 数、分类覆盖率
- **类别分布**：Canvas API 柱状图
- **时间线表格**：最近 10 条记忆

### 9.3 导出插件 — `plugins/exporter.py`

| 格式 | 特性 |
|------|------|
| Markdown | headings + 元数据 + 内容 + 分隔线 |
| JSONL | 每行一个完整记忆对象 |
| CSV | 11 列：id/agent_name/category/level/content/tags 等 |
| HTML | 自包含页面，支持按 Level 筛选和全文搜索 |

### 9.4 通知插件 — `plugins/notifier.py`

| 功能 | 说明 |
|------|------|
| 系统通知 | Windows: win10toast / 其他: stderr fallback |
| 遗忘告警 | 检查即将过期的记忆（TTL-7 天内），触发警告 |
| 安全告警 | 发现敏感数据时触发告警 |

### 9.5 调度器插件 — `plugins/scheduler.py`

轻量级后台线程调度器：

- 任务注册/移除/列表
- 内置每日任务：forget_low_value + audit_sensitive
- 运行间隔从 config 读取

---

## 10. 基础设施

### 10.1 调度器守护进程 — `scheduler/scheduler.py`

独立进程运行的调度器守护（区别于插件中的 TaskScheduler）：

| 周期 | 任务 | 说明 |
|------|------|------|
| 5 分钟 | 心跳同步 | 更新 identities.heartbeat，写 heartbeat 记忆，同步旧版 DB |
| 5 分钟 | Marvis 检查 | 从旧版 facts 表读取新消息，写入 memories 表 |
| 6 小时 | 管线执行 | 调用 run_pipeline() 执行全流程管线 |
| 1 小时 | 健康检查 | 检测心跳超时的 Agent，标记为 offline（7 次未心跳） |

### 10.2 配置管理器 — `config.py`

**配置加载顺序（后加载覆盖前）：**

```
1. 内置默认配置
2. config.json（CWD → ~/.memall/）
3. memall.yaml（CWD → ~/.memall/）
4. MEMALL_* 环境变量
```

**默认配置结构：**

```yaml
db:
  path: ~/.memall/data.db
gateway:
  host: 127.0.0.1
  port: 9919
discovery:
  port: 9920
forget:
  ttl_days: 90
  low_value_days: 7
plugins:
  auto_load: true
scheduler:
  forget_interval: 86400       # 24h
  audit_interval: 86400        # 24h
  heartbeat_interval: 300      # 5min
  pipeline_interval: 21600     # 6h
  doctor_interval: 3600        # 1h
  missed_heartbeat_limit: 7
logging:
  level: INFO
```

### 10.3 数据迁移 — `migrate.py`

文件型迁移引擎：

- 迁移文件命名：`NNN_description.py`，存放在 `migrations/` 目录
- 支持预览（`--check`）和执行
- 迁移记录跟踪（`_migrations` 表）
- 已有迁移：`001_add_identity_trusted_by.py`、`002_add_owner_type.py`

### 10.4 FastAPI REST API — `api/server.py`

| 路由 | 方法 | 功能 |
|------|------|------|
| `/memories` | POST | 存储记忆 |
| `/memories/search` | GET | 搜索记忆 |
| `/edges` | POST | 创建关系边 |
| `/graph/{node_id}` | GET | 图遍历 |
| `/timeline` | GET | 时间线 |
| `/health` | GET | 健康检查 |

端口：8199（可通过参数修改）

---

## 11. 数据库 Schema

### 11.1 核心表

| 表名 | 用途 | 关键字段 |
|------|------|---------|
| `memories` | 核心记忆存储 | id, content, content_hash, level(P0/P1/P2/L6/L7/L9), owner, agent_name, subject, project, category, summary, confidence, visibility(public/shared/family/trusted/private), access_count, occurred_at, created_at, updated_at, metadata(JSON), tags(JSON), embedding |
| `edges` | 记忆间关系 | id, source_id, target_id, relation_type(extends/contradicts/refines/cites/supersedes/related), weight, metadata, created_at |
| `identities` | Agent 身份 | id, agent_name, agent_type, trusted_by(JSON), profile_json, heartbeat, permission_level |

### 11.2 全文搜索表

| 表名 | 用途 |
|------|------|
| `memories_fts` | FTS5 全文搜索虚拟表（自动与 memories 表同步） |

### 11.3 聚类表

| 表名 | 用途 |
|------|------|
| `clusters` | 话题聚类标签 |
| `memory_clusters` | 记忆-聚类关联 |
| `narrative_clusters` | 叙事-聚类关联 |

### 11.4 其他表

| 表名 | 用途 |
|------|------|
| `narratives` | Agent 叙事/周报存储 |
| `embeddings` | 向量嵌入存储 |
| `suggestions` | 建议/待办项 |
| `_migrations` | 迁移版本跟踪 |

---

## 12. 附录：架构全景图

```
┌──────────────────────────────────────────────────────────────────────┐
│                        AI Agent Layer                                 │
│  Claude Desktop  │  Cursor  │  OpenCode  │  Solo  │  自定义 Agent    │
└──────────┬──────────┬──────────┬──────────┬──────────┬──────────────┘
           │          │          │          │          │
           │    MCP Protocol (STDIO JSON-RPC)        │
           │          │          │          │          │
┌──────────▼──────────▼──────────▼──────────▼──────────▼──────────────┐
│                         MCP 协议层 (mcp/)                            │
│                                                                      │
│  ┌──────────────┐  ┌────────────────┐  ┌──────────────────────────┐ │
│  │  server.py   │  │  adapter.py    │  │  validator.py + models   │ │
│  │  STDIO 服务   │  │  28 工具定义    │  │  33 个 Pydantic 模型     │ │
│  └──────────────┘  └────────────────┘  └──────────────────────────┘ │
│                                    ┌──────────────────────────────┐ │
│                                    │  federation_tools.py          │ │
│                                    │  联邦 MCP 工具实现            │ │
│                                    └──────────────────────────────┘ │
└──────────────────────────────────────────────────────────────────────┘
                                    │
┌──────────────────────────────────────────────────────────────────────┐
│                        核心引擎 (core/)                               │
│                                                                      │
│  ┌────────────┐  ┌────────────┐  ┌──────────────┐  ┌─────────────┐ │
│  │  db.py     │  │  models.py │  │  nlp.py      │  │  thin_waist │ │
│  │  SQLite    │  │  Memory    │  │  分词/TF-IDF │  │  CRUD/图    │ │
│  │  连接池/DDL│  │  MemoryIn  │  │  SVD嵌入     │  │  遍历/批处理 │ │
│  └────────────┘  └────────────┘  └──────────────┘  └──────┬──────┘ │
│  ┌──────────────────────────────────────────────────────────┴─────┐ │
│  │  context_assembler.py — Agent 人格提取+上下文组装              │ │
│  └────────────────────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────────────────────┘
           │                    │                    │
┌──────────▼──────────┐ ┌──────▼──────────┐ ┌──────▼──────────────────┐
│  记忆管线 (pipeline/) │ │  联邦 (federation/)│ │  HTTP 网关 (gateway.py) │
│                      │ │                  │ │                        │
│  enrich  → classify  │ │  family.py       │ │  aiohttp 本地 HTTP 服务 │
│  → link → decay      │ │  家庭圈管理       │ │  export/import 同步     │
│  → backup → ...      │ │  conflict.py     │ │  LAN 发现/配对/联邦     │
│                      │ │  冲突检测与解决    │ │                        │
│  persona 三层画像     │ │  health.py       │ │                        │
│  ask 数字孪生        │ │  健康监控         │ │                        │
│  narrative 叙事      │ │  visualize.py    │ │                        │
│  adaptive 自适应      │ │  报告生成         │ │                        │
│  security 安全治理    │ │                  │ │                        │
│  ops 记忆运维         │ │                  │ │                        │
│  session 会话管理     │ │                  │ │                        │
│  etc (21 个模块)      │ │                  │ │                        │
└──────────────────────┘ └──────────────────┘ └────────────────────────┘
           │                    │                    │
┌──────────▼──────────────────▼────────────────────▼──────────────────┐
│                        知识图谱 (graph/)                              │
│                                                                      │
│  ┌──────────────┐  ┌──────────────┐  ┌────────────────────────────┐ │
│  │ embeddings.py│  │ retrieve.py  │  │ visualize.py               │ │
│  │ 256维 SVD嵌入 │  │ keyword/     │  │ pyvis HTML + matplotlib PNG│ │
│  │ 增量索引构建   │  │ vector/hybrid│  │                            │ │
│  └──────────────┘  └──────────────┘  └────────────────────────────┘ │
└──────────────────────────────────────────────────────────────────────┘
           │                    │                    │
┌──────────▼──────────────────▼────────────────────▼──────────────────┐
│                        基础设施                                       │
│                                                                      │
│  ┌──────────────┐  ┌──────────────┐  ┌────────────┐  ┌────────────┐ │
│  │  scheduler/  │  │  config.py   │  │  migrate.py│  │  api/      │ │
│  │  调度器守护   │  │  配置管理器    │  │  迁移引擎   │  │  FastAPI   │ │
│  └──────────────┘  └──────────────┘  └────────────┘  └────────────┘ │
│  ┌──────────────────────────────────────────────────────────────────┐│
│  │  插件系统 (plugins/)                                              ││
│  │  loader(加载器) │ dashboard(仪表盘) │ exporter(导出) │             ││
│  │  notifier(通知) │ scheduler(调度)                                  ││
│  └──────────────────────────────────────────────────────────────────┘│
└──────────────────────────────────────────────────────────────────────┘
```

---

*本文档由系统自动生成，覆盖 MemALL v0.1.0 全部已实现功能。*
