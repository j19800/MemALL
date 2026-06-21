<p align="center">
  <img src="https://img.shields.io/badge/license-MIT-blue" alt="MIT License">
  <img src="https://img.shields.io/badge/python-3.9%2B-blue" alt="Python 3.9+">
  <img src="https://img.shields.io/badge/MCP-2025--03--26-purple" alt="MCP">
  <img src="https://img.shields.io/github/stars/j19800/MemALL?style=social" alt="GitHub stars">
</p>

<h1 align="center">MemALL</h1>
<p align="center"><strong>Multi-agent Memory OS</strong> — 本地优先的 AI Agent 持久化记忆系统</p>

<p align="center">
  <i>38 个 MCP 工具 · 10 层记忆生命周期 · 自进化管线 · 知识图谱 · 多 Agent 共享记忆</i>
</p>

<p align="center">
  <a href="README.md">English</a> · <b>中文</b>
</p>

---

## ✨ 什么是 MemALL？

MemALL 为 AI Agent 提供**跨会话、跨工具、跨 Agent 的持久化记忆**。安装后连接 Claude Desktop / Cursor / Cline 或任何 MCP 客户端，你的 Agent 就不再每次对话都从零开始。

```bash
pip install memall-db          # 安装
memall init                    # 初始化
memall start                   # 启动服务
# → MCP 就绪于 http://127.0.0.1:9876/mcp
# → MCP 就绪于 stdio（添加到你的 mcp.json）
```

在任意 MCP 客户端中：

```json
{
  "mcpServers": {
    "memall": {
      "command": "memall",
      "args": ["serve"]
    }
  }
}
```

```python
# /capture "决定开源：MIT 协议，GitHub 优先分发"
# /retrieve "定价决策"
# → 找到上面的记忆，以及它的上下文、时间戳和关联关系
```

---

## 🔥 为什么用 MemALL？

### 🧠 10 层记忆生命周期

不只是"存和取"。每条记忆都有语义层级：

| 层级 | 含义 | 示例 |
|------|------|------|
| P0/P1/P2 | 规划 | "6月23日前发布 v0.1.0" |
| L1 | 原始事实 | "用户报告了登录 bug" |
| L3 | 商业想法 | "MemPort — 跨平台记忆迁移" |
| L4 | 决策 | "用 FastAPI，不用 Flask" |
| L5 | 讨论 | 多 Agent 辩论并收敛 |
| **L6** | **自我反思** | "这次会话做对了什么/做错了什么" |
| **L9** | **蒸馏** | "1万条对话 → 200 个知识节点" |
| **L10** | **系统洞察** | 跨领域模式检测 |

**21 步自动管线** 自动完成：丰富 → 分类 → 时间切片 → 反思 → 蒸馏 → 整合。无需手动 CRUD。

### 🔗 知识图谱

记忆通过带类型的边（`refines`、`cites`、`contradicts`、`supersedes`）连接。从 "bug #123" 到 "修复 PR #456" 再到 "回顾性 L6 反思" 一步可达。

```
/capture "Bug #123：大数据集 OOM"
/connect 123 456 --relation "resolved_by"
/traverse 123
→ 找到 #456（修复）、#789（回归测试）、#912（L6 反思）
```

### 🤝 多 Agent 共享记忆

Claude、opencode、Codex、WorkBuddy — 都读写同一个记忆库。跨 Agent 查询只需一次 MCP 调用。

```
/fed_query "上周的架构决策" agent_name="claude"
→ 返回跨 Agent 结果，附带来源归属
```

### 🔄 自进化管线

MemALL 不只是存储 — 它**自我改进**：

1. **L6 反思** — 自动审查工作质量，识别模式，纠正错误
2. **L9 蒸馏** — 将原始对话压缩为结构化知识
3. **遗忘与自适应** — 基于 TTL 的衰减、低价值清理、自动重索引
4. **OODA 循环** — 观察 → 定向 → 决策 → 行动，无需人工干预

### 🏠 100% 本地运行

SQLite + FTS5 + 向量搜索。零云端依赖。数据留在你的机器上。

---

## 🛠️ 38 个 MCP 工具

| 分类 | 工具 |
|------|------|
| **记忆 CRUD** | `capture`, `retrieve`, `update`, `smart_store`, `store_batch` |
| **知识图谱** | `connect`, `traverse`, `timeline` |
| **搜索** | `vector_search`, FTS5 全文搜索 |
| **会话** | `session_start`, `session_end`, `session_summary` |
| **身份与人格** | `persona`, `persona_profile`, `identity`, `ask` |
| **讨论与决策** | `discussion_create`, `discussion_respond`, `discussion_status`, `trace` |
| **联邦** | `fed_query`, `fed_publish`, `fed_conflicts`, `fed_inject`, `fed_extract` |
| **Hub 同步** | `hub_connect`, `hub_sync` |
| **管线与进化** | `run_pipeline`, `reflect_interact`, `forget`, `adaptive`, `index_rebuild` |
| **安全与运维** | `security`, `ops`, `gateway`, `db` |
| **引导** | `onboarding` |

---

## 🚀 快速开始

```bash
# 1. 安装
pip install memall-db

# 2. 初始化
memall init
memall start

# 3. 连接你的 MCP 客户端
# 添加到你的 mcp.json：
# {
#   "mcpServers": {
#     "memall": { "command": "memall", "args": ["serve"] }
#   }
# }

# 4. 开始记录
/capture "项目 X：决定用 FastAPI，原因：异步支持"
/capture "修复 NLP 管线 OOM bug — 根因：向量维度不匹配"
/retrieve "FastAPI 决策"
```

或从源码安装：
```bash
git clone https://github.com/j19800/MemALL
cd MemALL
pip install -e .
```

---

## 📊 竞品对比

| 特性 | MemALL | Mem0 | Letta | Zep |
|------|--------|------|-------|-----|
| **记忆模型** | 10 层 (P0-L10) | 用户/会话 | Agent/记忆块 | 会话/摘要 |
| **知识图谱** | ✅ 原生 + 遍历 | ❌ | ❌ | ❌ |
| **自进化管线** | ✅ 21 步自动 | ❌ | ❌ | ❌ |
| **多 Agent 共享** | ✅ 联邦机制 | ❌ | 仅同 Agent | ❌ |
| **决策追踪** | ✅ Arc 生命周期 | ❌ | ❌ | ❌ |
| **讨论收敛** | ✅ 多 Agent 自动 | ❌ | ❌ | ❌ |
| **协议** | **MCP（原生）** | REST API | REST + gRPC | REST API |
| **本地优先** | ✅ SQLite | ❌ 云 | PostgreSQL | ❌ 云 |
| **开源** | ✅ MIT | ⚠️ 部分 | ✅ | ⚠️ 部分 |

---

## 📁 项目结构

```
src/memall/
├── cli/          # CLI（40+ 命令）
├── core/         # SQLite / NLP / 向量搜索
├── api/          # FastAPI REST（35 路由）
├── mcp/          # MCP 适配器（38 工具）
├── pipeline/     # 21 步自动管线
├── graph/        # 知识图谱
├── federation/   # 跨设备记忆同步
├── plugins/      # 仪表盘 / 导出 / 调度器
└── migrations/   # 数据库迁移
```

---

## 📝 路线图

- [x] **v0.1.0** — 核心 MCP 服务器、38 工具、CLI、PyPI 包
- [ ] **v0.2.0** — Web 仪表盘、用户系统、Pro 版
- [ ] **v0.3.0** — 云同步、团队协作、API 网关

---

## 🤝 贡献

欢迎 PR！详见 [CONTRIBUTING.md](CONTRIBUTING.md)。

- 报告 Bug → [Issues](https://github.com/j19800/MemALL/issues)
- 提问 → [Discussions](https://github.com/j19800/MemALL/discussions)
- 浏览代码 → [Repository](https://github.com/j19800/MemALL)

---

## 📄 许可证

MIT License。详见 [LICENSE](LICENSE)。