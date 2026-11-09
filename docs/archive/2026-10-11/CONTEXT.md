# CONTEXT.md — MemALL 共享语言与术语表

> 目的：消除"同词异义 / 文档漂移"。所有 Agent 会话、文档、代码共用本文件定义。
> 维护规则：任何术语变更必须先在本文件更新，再改代码/文档（mattpocock `domain-modeling`）。
> 最后校准：2026-09-26（对照 `src/memall/mcp/tools/__init__.py`、`pyproject.toml`、`src/memall/migrations/`）。

---

## 1. 顶层 MCP 工具（真实清单 · 7 个）

MemALL 对 MCP 客户端暴露 **7 个顶层工具**，每个工具通过 `action` 字段路由到子命令：

| 顶层工具 | 职责 | 主要 action（子命令） |
|---|---|---|
| `memall_write` | 写入/更新/连接/遗忘/运维 | capture, smart_store, store_batch, update, connect, forget, ops, quick |
| `memall_read` | 检索/搜索/溯源/图谱/时间线/联邦查询 | retrieve, search, chat, vector_search, hybrid_search, trace, traverse, timeline, fed_query, fed_conflicts |
| `memall_persona` | 画像/身份/数字分身/前瞻 | persona, persona_profile, identity, ask, profile_preload, profile_search, foresight |
| `memall_discussion` | 多 Agent 讨论与收敛 | create, respond, status |
| `memall_federation` | 跨 Agent 联邦 | query, publish, conflicts, inject, extract, deliver |
| `memall_system` | 管道/会话/网关/Hub/DB/安全/自适应/接入/反思/索引/日报/热榜 | run_pipeline, distill, gateway, hub_connect, hub_sync, session_start/end/summary, db, security, adaptive, onboarding, reflect, index_rebuild, digest, hot, import |
| `memall_hooks_recent` | 查看最近 Hook 活动 | （无 action，n 参数） |

> ⚠️ **口径区分**：README 中的"6 MCP tools"已过时（实际 **7** 个）。
> 记忆里常说的"38 个 MCP 工具"指的是**底层 action 子命令数**，不是顶层工具数。
> 讨论时必须区分"顶层工具（7）"与"action 子命令（~40）"。

---

## 2. 记忆层级（level）— 以代码枚举为准

**唯一真相源**：`memall_write` input_schema 的 `level` 枚举：

```
P0, P1, P2, L1, L2, L3, L4, L5, L6, L7, L8, L9, L10, L11
```

| 取值 | 语义 |
|---|---|
| P0 / P1 / P2 | 规划（Planning） |
| L1 | 原始事实（Raw fact） |
| L2 | 约定/规范（Convention） |
| L3 | 商业点子（Business idea） |
| L4 | 决策（Decision） |
| L5 | 多 Agent 讨论（Discussion） |
| L6 | 自我反思（Self-reflection） |
| L7 | 用户偏好（Preference） |
| L8 | 图谱提升（Edge-promoted） |
| L9 | 蒸馏知识（Distillation，按 agent+category 聚合低层记忆） |
| L10 | 跨领域系统洞察（Integration，跨 ≥2 category 的 L9 合并） |
| L11 | 商业洞察（Business insight） |

> ⚠️ **历史口径冲突（已在 ADR-0001 定案）**：曾并存三种说法——"10 层 L0–L10"、"11 层 L1–L11"、代码枚举。
> **定案：以代码枚举为唯一真相。** 文档统一表述为"规划层 P0/P1/P2 + 知识层 L1–L11"；不再出现 L0。

---

## 3. 关键数据字段

| 字段 | 定义 | 写入时机 |
|---|---|---|
| `project` | 记忆所属项目 | `capture()` 在缺失时经 `core/project_infer.infer_project()` 推断（唯一真源，ADR-0002） |
| `thread_id` | 指向同会话线程 root 记忆的 ID；root 自身为 NULL | `capture()` 按 (agent_name, project, 时间窗) 自动挂载 |
| `agent_name` | 写入 Agent 标识（`normalize_agent_name` 归一化） | 写入时；`agent_name_locked` 为安全加固项（migrations/020） |
| `tags` | JSON 数组标签 | 经 `MemoryInput.tags` 传入，由 `capture()` 落库（migrations/014 起有列） |
| `level` | 见 §2 | 写入 / 分类器重定级 |
| `memory_status` / `arc_status` | 生命周期状态 | migrations/023、归档流程 |

**工程约定**：`project` 一律小写（生产库已于 2026-09-26 将 154 条 `MemALL` 归一为 `memall`）。

---

## 4. 架构词汇表

| 术语 | 含义 |
|---|---|
| **thin_waist** | 统一写入薄层（`core/thin_waist.py` 的 `capture()`），**唯一写入入口**；数据质量护栏在此强制（ADR-0002） |
| **upsert contract** | 管线合成层（L9/L10）的基数约定：每个键只保留一条，更新而非追加（ADR-0003） |
| **gateway** | HTTP/API 网关层（`gateway*.py`） |
| **federation** | 跨 Agent / 跨实例知识联邦（发布/注入/抽取/投递） |
| **hook** | 生命周期钩子（post_capture / pre_retrieve 等事件驱动自动化） |
| **distill (L9)** | 按 (agent, category) 把低层记忆压缩为结构化知识节点 |
| **integrate (L10)** | 把一个 agent 跨 ≥2 领域的 L9 合并为系统洞察 |
| **pipeline** | 自进化管道（enrich→classify→epoch→reflect→distill→integrate→observe…） |
| **backfill** | 对历史数据补齐字段的运维算子（`pipeline/_backfill_*.py`） |
| **OODA** | Observe→Orient→Decide→Act 自主循环 |

---

## 5. 决策记录索引

见 [`docs/ADR/`](docs/ADR/)：

- **ADR-0001** 记忆层级定义的唯一真相源
- **ADR-0002** 写入入口统一收敛到 `thin_waist.capture`
- **ADR-0003** L9/L10 合成层的 upsert 基数约定
- **ADR-0004** `MEMALL_*` 环境变量对带下划线嵌套键的覆盖规则

---

## 6. 单一事实来源（SSOT）

- 工具契约：`src/memall/mcp/tools/__init__.py`（`ToolRegistry`）
- 记忆模型：`src/memall/core/models.py`（`MemoryInput` / `Memory`）
- 写入入口：`src/memall/core/thin_waist.py`（`capture()`）
- 项目推断：`src/memall/core/project_infer.py`
- 配置：`src/memall/config.py`（`_DEFAULT_CONFIG`）+ `~/.memall/config.json` + `MEMALL_*` 环境变量
- 数据库 schema：`src/memall/migrations/`
