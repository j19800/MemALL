# DeepSeek CLI 接入 our free model（无人介入唤醒的传输层）

> **文档状态**：现行 · 运维必读 — 无人介入唤醒的传输层配置；换 CLI/换模型时先读此份

> 日期：2026-10-29 ｜ 状态：已接通并端到端验证
> 相关：`src/memall/pipeline/agent_waker.py`、`~/.memall/config.json` 的 `agents.wake`

## 为什么需要这一层

目标生命周期（`goals.py`）能算出"下一步该谁做什么"，但 **agent 不会自己醒来**：提示只有在它下次调工具时才被看到。没有会话就没有推进。

MCP 无法反向唤醒 agent（协议是 client-driven，`sampling` 需客户端自愿实现，本 server 未声明）。唯一可行的杠杆是**启动一个 headless 的 agent 进程**。

本机可选的三条路：

| 路径 | 结论 |
|---|---|
| `claude -p` / `codex exec` / `opencode run` | 均未安装 |
| `hermes chat --oneshot` | 可用，但默认 provider 指向不可达的 zenmux，需另行配置 |
| **`dsh --profile headless`** | **DeepSeek 官方，专为"答一个任务并退出"设计，已采用** |

## dsh 修复（四层故障，缺一不可）

| # | 故障 | 处理 |
|---|---|---|
| 1 | `zod/v4/locales/index.js` 缺失（`v4/core` 引它） | 从本机另一份 zod 补齐 |
| 2 | **24 个插件 `failed to import`** | `npm i -g @deepseek-ai/dsh@0.2.0-rc.2 --force`（538 包更新） |
| 3 | 默认模型 `deepseek-official` 的 key 失效 | 补丁把默认 provider 换成可用的 |
| 4 | **headless profile 没有任何 MCP client** | 补丁里 `insert` 一个 `dsh-mcp-client` 指向 memall |

第 4 条最容易漏：不补它就等于唤醒了一个没有手脚的 agent。

## 接入 our free model

本地网关 `127.0.0.1:18899`（OpenAI 兼容，`forwardKey` 在 `~/.dsh/our-free-model/settings.json`）。

**2026-10-29 实测可用性**（很重要，会变）：

| 模型 | 结果 |
|---|---|
| 所有 `*-free`（deepseek-v4.1-flash-free、glm-5.3-flash-free、mimo-v2.6-flash-free…） | **HTTP 502 `Free usage limit reached`**，上游明确写"只能在 OpenCode 客户端内使用" |
| `opencode/deepseek-v4.1-flash-free` | 流被提前关闭 |
| `deepseek-ai/deepseek-v4.1-flash`、`z-ai/glm-5.3-flash` | 超时（>150s） |
| **`minimaxai/minimax-m3.1-flash`** | **✅ 4.8s 出话，且 `tools=true`（支持 tool-calling，MCP 必须）** |

所以 ff 模型层实际不可用，走的是**非 free 的 minimax**。

## 配置

### 1) `~/.dsh/patches/headless-nvidia.yml`

```yaml
- id: llm-pi-ai
  config:
    providers:
      our-free-model:
        displayName: our free model (local gateway)
        apiKeyEnv: OFM_API_KEY          # 注意：字面 `apiKey` 不生效，必须用 apiKeyEnv
        api: openai-completions
        baseURL: http://127.0.0.1:18899/v1
        models:
          - id: minimaxai/minimax-m3.1-flash
            name: minimax-m3.1-flash
            contextWindow: 200000
      nvidia-fast:                       # 兜底
        apiKeyEnv: NVIDIA_API_KEY
        api: openai-completions
        baseURL: https://integrate.api.nvidia.com/v1
        models:
          - id: nvidia/nemotron-3.5-lightning-30b-a3b
- id: agent-default-model
  config:
    provider: our-free-model
    model: minimaxai/minimax-m3.1-flash
- insert:
    - id: memall-mcp
      name: '@deepseek-ai/dsh-mcp-client'
      config:
        serverName: memall
        transport: stdio
        # 必须用工作区外的解释器，见下节
        command: 'C:\Users\Administrator\AppData\Local\Programs\Python\Python312\python.exe'
        args: ['-m', 'memall.mcp.server']
        env:
          MEMALL_AGENT_NAME: dsh
          MEMALL_HOME: 'C:\Users\Administrator\.memall'
          MEMALL_PROJECT_DIR: 'E:\MemALL'
          PYTHONPATH: 'E:\MemALL\src;E:\MemALL\.venv\Lib\site-packages'
```

### 2) `~/.memall/config.json`

```json
"agents": {
  "wake": {
    "enabled": true,
    "dry_run": false,
    "cooldown_seconds": 60,
    "max_per_hour": 3,
    "timeout_seconds": 900,
    "commands": {
      "dsh": {
        "argv": ["node", "<npm global>/@deepseek-ai/dsh/lib/bin.js",
                 "--profile", "headless", "--patch", "<上面的补丁>", "-"],
        "env": {"MEMALL_AGENT_NAME": "{agent}", "OFM_API_KEY": "<gateway forwardKey>"},
        "stdin_prompt": true
      }
    }
  }
}
```

`-` 表示从 stdin 读任务；`stdin_prompt: true` 让唤醒器把 prompt 写进子进程 stdin，**完全不经 argv 与 shell**，prompt 内容无法被重新解释。

## 两个必须知道的坑

### 坑 1：解释器位置决定能不能写库

MemALL 的 `.venv` 位于 `E:\MemALL` 内，**Codex 沙箱会把镜像在工作区内的进程限制为只能写工作区**，于是：

| 解释器 | 位置 | 写 `~/.memall/data.db` |
|---|---|---|
| `E:\MemALL\.venv\Scripts\python.exe` | 工作区内 | ❌ `attempt to write a readonly database` |
| 系统 Python 3.12 | 工作区外 | ✅ |

报错长得像 SQLite 只读/锁竞争，实际是**进程来源**问题。诊断时 ACL 检查会返回 `NOT_THIS_CLASS`（权限齐全），Restart Manager 也报"无进程持有"——因为持有者是跨会话的。

**所以 MCP server 必须用工作区外的解释器 + `PYTHONPATH`**，否则被唤醒的 agent 能读不能写，目标推不动。

### 坑 2：`apiKey` 字面值不生效

`llm-pi-ai` 的 provider 只认 `apiKeyEnv`（经 dsh 的凭证系统解析）。写 `apiKey: xxx` 会得到：

```
dsh: PI_AI_ERROR: No API key for provider: our-free-model
```

解法：用 `apiKeyEnv: OFM_API_KEY`，并在**子进程环境**里注入 `OFM_API_KEY`（唤醒器的 `env` 已做）。

## 验证结果

**单次调用**

```
$ echo "Reply with exactly: PONG" | node bin.js --profile headless --patch <补丁> -
→ PONG        （18s；stderr 可见 Session identity bound: dsh / Client initialized）
```

**无人介入全链路**（唤醒器自动启动，非手动）

```
目标 #1001360  创建 → phase=intake
  → fallback tick → 唤醒器 spawn dsh（our free model）
  → +220s  phase=decomposed
  → plan:
      [0] 依赖体检 - 设计阶段   deps=[]
      [1] 依赖体检 - 执行阶段   deps=[0]      ← 依赖契约生效
  → dependency_warnings: null
```

另一次（手动复现）拆出 **4 个子任务，完整 0→1→2→3 依赖链**，且无警告。

## 维护提示

- 网关模型状态会变（`~/.dsh/our-free-model/availability.json`，每 15 分钟探测）。若 `minimaxai/minimax-m3.1-flash` 变不可用，先探测新模型再改补丁。
- 换模型只需改补丁里的 `agent-default-model`，不必动唤醒器。
- 唤醒器默认 `dry_run` 为真（代码默认，守卫 `agent_waker_is_safe` 钉住）：在没有可用传输层时不会盲目启动进程。
