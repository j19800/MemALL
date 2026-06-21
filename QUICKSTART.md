# MemALL 快速上手

## 安装

```bash
pip install memall-db
```

或从源码安装：

```bash
git clone https://github.com/j19800/MemALL
cd MemALL
pip install -e .
```

## 初始化

```bash
memall init    # 创建数据库
memall start   # 启动服务
```

启动后：
- MCP 服务就绪于 `http://127.0.0.1:9876/mcp`
- REST API 就绪于 `http://127.0.0.1:8199`

## 连接 MCP 客户端

在 Claude Desktop / Cursor / Cline 的 `mcp.json` 中添加：

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

## 基本使用

```bash
# 记录一条记忆
memall capture "项目X: 决定用FastAPI, 原因: 异步支持"

# 搜索记忆
memall search "FastAPI"

# 查看记忆详情
memall get 1

# 仪表盘
memall dashboard
```

## 记忆层级

| 层级 | 含义 |
|------|------|
| P0-P2 | 规划 |
| L1-L4 | 事实与决策 |
| L5 | 讨论 |
| L6 | 自我反思 |
| L9 | 知识蒸馏 |
| L10 | 系统洞察 |

## 更多

```bash
memall --help          # 查看所有命令
memall status          # 系统状态
memall doctor          # 诊断问题
```

详细文档见 [README.md](README.md)。