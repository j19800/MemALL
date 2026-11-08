# MemALL 快速上手

## 安装

```bash
pip install memall-os
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
memall start-gateway    # 启动 HTTP + MCP 网关 (端口 9920)
```

启动后：
- MCP 服务就绪于 `http://127.0.0.1:9920/mcp`
- REST API 就绪于 `http://127.0.0.1:9920`
- Web 仪表盘就绪于 `http://127.0.0.1:9920/`
- 健康检查 `http://127.0.0.1:9920/health`

> 网关默认仅监听 loopback (127.0.0.1)。`/mcp`、`/api` 等 API 端点需要 Bearer
> token 认证；token 在启动日志中打印（可用 `MEMALL_SECRET_KEY` 固定，见下文）。

## 连接 MCP 客户端

**方式一：HTTP 网关**（推荐，网关需已通过 `memall start-gateway` 启动）。
先取 token（用固定的 `MEMALL_SECRET_KEY`，见下文）：

```json
{
  "mcpServers": {
    "memall": {
      "url": "http://127.0.0.1:9920/mcp",
      "headers": { "Authorization": "Bearer <your-token>" }
    }
  }
}
```

**方式二：stdio**（每次由客户端拉起子进程）：

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

# 启动 Web 仪表盘 + HTTP/MCP 网关 (同 9920 端口)
memall start-gateway
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