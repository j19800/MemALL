# MemALL 官网 + 数据看板（website/）

零构建静态站点，双页：**index.html** 产品官网，**dashboard.html** 数据看板。

数据源三级回退：本地网关（`/health` 可达）→ `data/snapshot.json` 真实快照 → 内置演示样例。状态胶囊会显示当前用的是哪一级。

## 真实数据快照（无需启动网关）

```bash
node tools/build.mjs              # 源码 → dist/，并生成 dist/data/snapshot.json（默认口径 shared-only）
node tools/build.mjs --no-snapshot   # 只打包前端
node tools/sync.mjs --include-private # 全量快照，写在 dist/ 之外（website/data/），仅供本地查看，不会被打包上传
node tools/sync.mjs --db D:\path\data.db --max 400
```

`tools/sync.mjs` 用 `node:sqlite` 只读打开 `~/.memall/data.db`：聚合统计（层级分布、关系类型、决策弧、epochs、管线运行）覆盖全库，
逐条文本只导出 `visibility='shared'` 的记忆，并对疑似凭证/邮箱/手机号/身份证做正则脱敏。默认 150 节点 / 420 边的真实图谱子集里，
private 节点只显示 `L# <id>`，不含文本。更新数据 = 重跑 `node tools/build.mjs`。

Dashboard 连真实网关：先 `memall quickstart` 启动网关（默认 `127.0.0.1:9920`），
看板右上角可改地址，或用 `dashboard.html?api_url=http://127.0.0.1:9920`，地址会记在 localStorage。
托管版可用 `dashboard.html?snapshot_url=<你的快照地址>` 指向自己的快照。

## 本地运行

```bash
# 任选一种静态服务器，站点无构建步骤
python -m http.server 8788          # 或 npx serve . / node 任意静态服务器
# 打开 http://127.0.0.1:8788/index.html
```

## 文件

| 文件 | 作用 |
|---|---|
| `index.html` | 官网：定位、14 级记忆模型、31+5 管线、浏览器内 RRF 检索沙盒、图谱/联邦/MCP、竞品对比、快速开始、FAQ |
| `dashboard.html` | 看板：总览 KPI、层级分布、记忆浏览（过滤/展开）、检索台、力导向图谱、Agent 联邦、管线 |
| `assets/styles.css` / `dashboard.css` | 设计令牌（GitHub-dark 系）与两页布局 |
| `assets/landing.js` / `dashboard.js` | 页面逻辑；`api.js` 网关 fetch 封装；`sample-data.js` 演示数据集与管线元数据 |
| `tools/sync.mjs` | 只读快照生成器：`data.db` → `data/snapshot.json`（聚合全库 + shared 文本 + 脱敏） |
| `tools/build.mjs` | 源码 → `dist/`（跳过 `dist/tools/data/README.md`），随后调用 sync 打包快照 |
| `dist/` | 可托管产物（含 `data/snapshot.json`），站点打包只取这个目录 |

## 使用的网关接口（loopback SPA 免 token）

`GET /health`、`GET /ui/session`（CSRF）、`GET /memories?limit&hours`、
`GET /memories/search?query`、`GET /memories/vector-search?query`、`GET /agents`、
`GET /api/arcs`、`GET /api/epochs`、`GET /api/graph/center`、`GET /graph/{id}`、
`POST /pipeline/run`（带 `X-MemAll-CSRF` 头）。

## 数字口径

本站所有数字按源码核对，而非 README 宣传值：14 级（P0–P2 + L1–L11）、管线 31 核心 + 5 可选
（`pipeline.list_pipeline_steps()`）、MCP 7 端点 ≈50 actions、CLI 52 命令、41 个迁移、79 个测试文件、
vec0 512 维、RRF k=60、限流 100 req/60s、网关默认端口 9920（注意 config 中 9919 与文档中 9876 的口径分歧，上线前建议统一）。
