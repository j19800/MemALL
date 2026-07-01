## [v0.1.39] - 2026-07-01

### Added

- **L7 weight/accumulation system**: Repeated lessons gain weight and influence session behavior proportionally:
  - `accumulate_key` parameter in `capture()` — caller-specified key stored in metadata; same key on a later L7 capture → weight++ instead of duplicate
  - Content-prefix matching in `distill_l7.py` — auto-detects repeated lesson patterns by first 40 chars of normalized content and increments weight
  - Weight-ordered injection: `auto_inject()` now `ORDER BY weight DESC, created_at DESC` so heavier lessons appear first
  - Weight badges `[xN]` shown in session [LESSONS] and behavioral instructions (`src/memall/core/thin_waist.py`, `src/memall/mcp/federation_tools.py`, `src/memall/mcp/tools/session.py`, `src/memall/pipeline/session.py`, `src/memall/pipeline/distill_l7.py`)

### Fixed

- **`sentence_transformers` import hang on Windows**: deferred `import sentence_transformers` from module level to `_get_model()` with a 5-second thread timeout guard. The package was installed but its import (pulling in torch) hung indefinitely. `from memall.graph.embeddings import EMBED_DIM` now returns instantly. (`src/memall/graph/embeddings.py`)
- **L7 accumulate_key content_hash staleness**: weight++ path now updates `content_hash` alongside content to prevent stale hash collisions with subsequent content-hash dedup. (`src/memall/core/thin_waist.py`)

## [v0.1.38] - 2026-07-01

### Changed

- **print() → logging in 6 non-CLI modules**: Replaced all diagnostic print() calls with structured logging across config.py, api/start_server.py, mcp/http_transport.py, plugins/loader.py, plugins/notifier.py, plugins/scheduler.py (20+ calls). Uses `logger.warning/info/error` with printf-style formatting (`%s`) and `exc_info=True` for exception context. Non-CLI prints preserved in onboarding.py (interactive UX), scheduler/scheduler.py daemon_start/daemon_stop (CLI output), bridge/run_bridge.py (run script). Also fixed pre-existing bug in plugins/notifier.py where `logger.warning(...)` was called on line 227 but `logger` was never defined. (`src/memall/config.py`, `src/memall/api/start_server.py`, `src/memall/mcp/http_transport.py`, `src/memall/plugins/loader.py`, `src/memall/plugins/notifier.py`, `src/memall/plugins/scheduler.py`)
- **Narrow `except Exception:` to specific types across 21 files**: ~90 instances narrowed to proper exception types (`sqlite3.Error`, `json.JSONDecodeError`, `OSError`, `(ImportError, AttributeError)`, `ValueError`, etc.). Core DB operations → `sqlite3.Error`; DDL (ALTER TABLE) → `sqlite3.OperationalError`; JSON parsing → `json.JSONDecodeError`; file I/O → `OSError`; dynamic import → `(ImportError, AttributeError)`. System-boundary code (gateway.py, mcp/*.py, plugins/*.py, cli/*.py, api/*.py, pipeline/ops.py) intentionally kept broad. Added 14 missing `import sqlite3`. (`src/memall/core/db.py`, `src/memall/core/thin_waist.py`, `src/memall/core/health.py`, `src/memall/core/utils.py`, `src/memall/core/tracer.py`, `src/memall/config.py`, `src/memall/pipeline/pipeline.py`, `src/memall/pipeline/forget.py`, `src/memall/pipeline/session.py`, `src/memall/pipeline/stream.py`, `src/memall/pipeline/extract.py`, `src/memall/pipeline/decay.py`, `src/memall/pipeline/archive.py`, `src/memall/pipeline/observe.py`, `src/memall/pipeline/bridge.py`, `src/memall/pipeline/distill_l7.py`, `src/memall/graph/embeddings.py`, `src/memall/graph/retrieve.py`, `src/memall/search/faiss_provider.py`, `src/memall/onboarding.py`, `src/memall/pipeline/stream.py`)

## [v0.1.37] - 2026-07-01

### Fixed

- **插件加载修复**: 在 adapter.py import 时加载所有插件，否则 `_loaded_plugins` 为空导致无任何 hook 事件产生 (`src/memall/mcp/adapter.py:23-25`)
- **had_plugin 检测逻辑修复**: `dispatch_lifecycle()` 中 `had_plugin = plugin_func is not None` 改为检查 `run_plugin_hook()` 的实际返回值，使无插件实现的 hook 点（post_search/post_store/post_retrieve/step_ok/step_fail）正确自动记录事件 (`src/memall/mcp/hooks.py:172,177`)

### Added

- **Hook Effects — 异步 hook 事件对 Agent 可见**: 新增 ring buffer (maxlen=200, 线程安全) 收集所有异步 hook 活动并注入到 MCP 工具响应中，Agent 不再"看不见"后台行为：
  - `_meta.hook_activity` 自动注入到每次工具调用的 JSON 响应，Agent 在自己的对话窗口直接看到 pipeline 运行、通知、检查的状态和耗时
  - 新增 `memall_hooks_recent` MCP 工具，Agent 可随时按需查询最近的 hook 活动 (`src/memall/mcp/tools/__init__.py:346-368`)
  - dispatch_lifecycle() 自动记录无插件处理的 hook 点事件 (`src/memall/mcp/hooks.py`)
  - scheduler/notifier 插件记录丰富的语义描述（含状态、结果、耗时） (`src/memall/plugins/scheduler.py`, `src/memall/plugins/notifier.py`)
  - 新文件 `src/memall/mcp/hook_effects.py` — HookEvent dataclass + ring buffer + consume/peek/format

## [v0.1.36] - 2026-07-01

### Added

- **Hook 驱动的系统人性化自动化**: 利用 lifecycle hook 让系统从被动响应变为主动响应：
  - **capture → 自动轻量 pipeline**: `on_capture` hook 触发 debounce(60s) 轻量 pipeline（classify + convergence + distill_l7 + reflect + session），避免高频保存压垮系统。 (`src/memall/plugins/scheduler.py:360-387`)
  - **capture → 讨论自动收敛**: `on_capture` hook 检查新记忆的 `supersedes` 字段关联的讨论，若全部参与者已回复则自动调用 `converge_discussion()` 收敛。 (`src/memall/plugins/scheduler.py:242-357`)
  - **retrieve → 上下文注入**: `on_pre_retrieve` hook 在每次检索时自动检查待处理讨论和任务，创建 P2 提醒记忆使它们自然出现在检索结果中。 (`src/memall/plugins/scheduler.py:390-422`)
  - **pipeline → 丰富结果报告**: `on_pipeline` hook 统计步骤成功/跳过/失败数，生成结构化摘要通知（ok>0 或耗时>5s 时通知）。 (`src/memall/plugins/notifier.py:120-179`)
  - 新增 `run_lightweight_pipeline()` 函数封装轻量 pipeline 配置。 (`src/memall/pipeline/pipeline.py:449-481`)

### Changed

- 新增 3 个 hook 响应函数（`on_capture`、`on_pre_retrieve`、`on_pipeline`），增强 `scheduler/notifier` 插件
- 所有 post-capture 工作在 daemon 线程中异步执行，capture() 即时返回不阻塞

## [v0.1.35] - 2026-07-01

### Added

- **Hook 生命周期系统全面打通**: 新增 `dispatch_lifecycle()` 桥接函数，串联 HookRegistry（MCP 层）和插件 `run_plugin_hook()`（lazy import 避免循环依赖）。13 个生命周期 Hook 覆盖全部核心操作：
  - **capture()**: `pre_capture`（blocking，可中止）+ `post_capture`
  - **smart_store()**: `pre_store` + `post_store`
  - **retrieve()**: `pre_retrieve` + `post_retrieve`
  - **hybrid_search()**: `pre_search` + `post_search`
  - **pipeline**: `pre_pipeline` + `post_pipeline`（`run_pipeline` 首尾）、`pre_step` + `step_ok` + `step_fail`（`_run_step` 每个步骤）
  - (`src/memall/mcp/hooks.py`, `src/memall/core/thin_waist.py`, `src/memall/pipeline/pipeline.py`)
- **4 个内置插件响应 Hook 事件**: dashboard 统计 capture 次数和 pipeline 摘要、exporter 每 10 次 capture 自动导出 JSONL、notifier 步骤失败和长 pipeline 完成通知、scheduler 记录 pipeline 日志。 (`src/memall/plugins/dashboard.py`, `src/memall/plugins/exporter.py`, `src/memall/plugins/notifier.py`, `src/memall/plugins/scheduler.py`)
- **CLI `memall hook` 子命令**: `memall hook list` 列出已注册 Hook，`memall hook register <hook_point> --action log|print --blocking` 动态注册。 (`src/memall/cli/main.py`, `src/memall/cli/commands/management_commands.py`)

## [v0.1.34] - 2026-07-01

### Added

- **Debt dashboard 全面升级**: 主仪表盘新增技术负债概览卡片；负债详情表支持按严重程度/文件路径/行号/描述列排序 + 全量分页（每页 25 条）；新增文件维度负债分布面板（Top 20 文件，显示 Critical/Major/Minor 圆点标记）；新增扫描历史趋势 SVG 线图（需至少 2 次扫描记录）。 (`frontend/index.html`, `src/memall/api/server.py`, `debt/scan.py`)
- **debt/scan.py 移除 50 条记录上限**: `scan_known_patterns()` 不再截断详情，返回全部匹配条目供前端分页。 (`debt/scan.py:58`)
- **后端扫描缓存保留历史**: `_save_debt_cache()` 追加扫描摘要到 `history[]`（保留最近 20 次），`/debt/stats` 返回 `file_summary` + `history`。 (`src/memall/api/server.py`)

### Fixed

- **第二轮全审查 32 项修复**: 覆盖 CRASH/P0/P1 三级，追溯审查 44 个源文件，修复分为三轮：
  - **Round 1 (CRASH)** — 6 项运行时崩溃修复：`tracer.py` 连接上下文管理（pool_conn()→with）、`gateway.py` timeline 变量定义和 federation_event 缩进、`hub_client.py` urllib.request.quote→urllib.parse.quote、`scheduler.py` watchdog 无限递归（run_daemon_with_watchdog→run_daemon）、`gateway.py` stale_ids 提前初始化。
  - **Round 2 (P0)** — 3 项数据/逻辑修复：`federation_tools.py` capture() 返回 int 而非 dict、`test_helpers.py` init_temp_db 真正创建临时数据库隔离、`register.py` 补全 urllib.request/error 导入。3 项分析后判定非真 bug 跳过。
  - **Round 3 (P1)** — 4 项显著缺陷修复：`db.py` put_nowait queue.Full 异常处理、`gateway.py` exc_info=True 位置参数→关键字参数、`agent_round.py` str.replace→json.dumps、`thin_waist.py` 移除未用 SVD 计算。
  - **Round 4 (P2)** — 11 项结构/UX 修复：`server.py` 移除 3 处死代码路由（2 个裸 root_memories_stats + 重复 search 路由）、`bridge/main.py` 损坏 Unicode 修复、"来自 @agent 的飞书消息"、`tools/__init__.py` 补全 archive_stats/archive_vacuum enum、`tools/gateway.py` stop 传递 port 参数、`tools/pipeline.py` 模块级 ThreadPoolExecutor 缓存、`log_setup.py` root.__class__ 继承 ExtraLogger + Python 3.12 _log 签名兼容、`test_distill.py` 过期 docstring 修正、"2"、`test_gateway.py` time.sleep→_wait_for_health/_wait_for_stop 轮询、`frontend/index.html` ?api_url= URL 参数支持。
  - 验证：语法检查 + 模块导入 + CLI pipeline dry-run + 37 测试通过（1 项预存 config path 失败无回归）。
  - (`core/tracer.py`, `gateway.py`, `mcp/hub_client.py`, `scheduler/scheduler.py`, `mcp/federation_tools.py`, `tests/test_helpers.py`, `cli/register.py`, `core/db.py`, `scheduler/agent_round.py`, `core/thin_waist.py`, `src/memall/api/server.py`, `src/memall/bridge/main.py`, `src/memall/mcp/tools/__init__.py`, `src/memall/mcp/tools/gateway.py`, `src/memall/mcp/tools/pipeline.py`, `src/memall/core/log_setup.py`, `tests/test_distill.py`, `tests/test_gateway.py`, `frontend/index.html`)

### Fixed (Round 5 — Static Analysis Audit)

- **ORDER BY 无 LIMIT (6 处)**: `gateway.py` 3 个 API handler（epochs/epochs_agent/arcs）、`convergence.py` 2 个（list_active/list_all_discussions）、`observe.py` 1 个（L6 reflection scan）均追加 `LIMIT 1000`。 (`gateway.py:1393,1405,1432`, `convergence.py:202,241`, `observe.py:210`)
- **裸 except Exception (1 处)**: `server.py api_debt_stats()` 中 archive.db 查询失败的裸 `pass` 替换为 `logger.warning`。 (`server.py:679`)
- **硬编码日期 (1 处)**: `gateway.py` 工单页面中的 "2026-06-25" 改为 `datetime.now()` 动态格式化。 (`gateway.py:621`)
- **P2 后续增强 (4 项)**:
  - **gateway stop 增强**: 改用 `_active_gateway` 模块级变量追踪运行实例，确保 stop 正确关闭工作线程。 (`src/memall/mcp/tools/gateway.py`)
  - **ExtraLogger kwargs 透传**: `_log()` 将 `**kwargs` 传给 `super()._log()`，避免丢失 `stacklevel`/`stack_info`。 (`src/memall/core/log_setup.py`)
  - **版本统一**: `server.py` FastAPI app 和 health endpoint 版本统一为 `0.1.2`（匹配 `__init__.py`）。 (`src/memall/api/server.py`)
  - **QUICKSTART.md 命令修正**: `memall dashboard` → `memall server`。 (`QUICKSTART.md`)

## [v0.1.33] - 2026-07-01

### Fixed

- **observe.py growth_log new-timeline INSERT binding count mismatch**: `_update_growth_log()` 新建反思时间线时，INSERT 有8个 `?` 占位符（对应 content, content_hash, project, summary, occurred_at, created_at, updated_at, metadata），但 values tuple 只传了 6 个值（缺少 project 和 summary），触发 `ProgrammingError: Incorrect number of bindings`。修复：补全 `""` 和 `"📅 反思时间线"`。 (`pipeline/observe.py:332-336`)

## [v0.1.32] - 2026-07-01

### Changed

- **I3 integrate.py Jaccard threshold 0.7→0.85**: 更紧的去重阈值，减少"部署架构"和"部署测试"等 50%+ 重叠议题被错误去重的概率。 (`pipeline/integrate.py:31`)
- **I4 integrate.py category top-3 combined**: L10 整合记忆的 category 从单一多数胜出改为 top-3 组合标签（如 `"architecture+testing"`），保留跨领域信号。 (`pipeline/integrate.py:172-174`)
- **D1 distill.py min_group 3→2**: 2 条高度相关的架构决策也能产生 L9 蒸馏，不再因缺第 3 条而被跳过。 (`pipeline/distill.py:33`)
- **D2 distill.py source limit 10→20**: 超过 10 条的组不再静默忽略 80% 内容，源记忆上限翻倍。 (`pipeline/distill.py:36,98`)
- **R1 reflect.py cold‑start threshold 50→20**: 冷启动阈值从 50 降至 20，小规模 agent 也能获得 L6 反思。 (`pipeline/reflect.py:34`)
- **R3 reflect.py chain overlap 20→40**: 反思链边类型区分(token 重叠)阈值从 20 升至 40，降低 contradicts/refines 误判。 (`pipeline/reflect.py:116`)
- **O2 observe.py L6 自指排除**: observation 自身生成的 L6 不再计入自己的健康统计指标（l6_total/l6_recent）。 (`pipeline/observe.py:36-57`)
- **ID1 identity.py scan window 2000→8000**: 身份信号扫描从仅前 2000 字符扩展到 8000，长记忆中的身份信号不再被忽略。 (`pipeline/identity.py:42`)
- **UX1 thin_waist.py quality gate ValueError→soft warning**: 质量门控失败不再抛 ValueError(HTTP 500/异常文本)，改为 logger.warning + 继续存储。 (`core/thin_waist.py:296-300`)
- **C5 classify.py L8 promotion substance gate**: 边数 ≥3 即升 L8 前先校验内容长度 ≥50 字符，防止 P2 级碎片凭边数巧合升级为"枢纽知识"。 (`pipeline/classify.py:153-158`)
- **CD1 util.py safe_parse_metadata()**: 提取 30+ 处重复的 `json.loads(row["metadata"])` 模式为统一工具函数，处理 None/JSON string/dict 三种类型。 (`pipeline/util.py:88-97`)

### Fixed

- **pipeline.py _coerce_int() fallback 返回 dict**: 当步骤返回 dict 且不识别任何 key 时，`return val` 将 dict 传回 → 质量门控 `result >= gate["min_output"]` 触发 `TypeError: '>= not supported between instances of 'dict' and 'int'`。修复：fallback 改为 `return 0`，并补全 `scanned`/`upgraded_to_l6`/`distilled` 等 key。 (`pipeline/pipeline.py:42-43`)

## [v0.1.31] - 2026-07-01

### Fixed

- **[C6] classify.py cursor reset loop**: 空结果时 DELETE 游标，下次运行重新扫描最新 500 条形成无限循环。修复：空结果直接返回，保留游标位置。(`pipeline/classify.py`)
- **[O1] observe.py self-check wrong baseline**: `prev = history[0]`（最旧条目）应为 `history[-2]`（前一次运行），导致遗忘率/领域宽度变化幅度被放大。修复：改为 `history[-2]`。(`pipeline/observe.py:140`)
- **[I2] integrate.py thread_id semantic misuse**: L10 合成记忆的 thread_id 设为 source L9 的 memory ID，而非会话/对话 ID，语义混淆。修复：L10 是管道合成洞察、无对话线程，设为 None。(`pipeline/integrate.py:207`)
- **[ID3] identity.py profile overwrite**: 每次运行全量覆写 profile，上次提取 20 个特质、本次只找到 5 个则 profile 缩水。修复：改为合并（保留旧条目、追加新条目、去重后 cap 20）。(`pipeline/identity.py:106-108`)

### Changed

- **distill L9 content overwrite** (v0.1.30): line 94 `merged_content = header` 覆写了 line 92 正确组装的内容。修复：删除覆写行。(`pipeline/distill.py`)
- **observe.py week/month identical key bug**: lines 204-205 `week_start` 和 `month_key` 均为 `today[:7]`（YYYY-MM），line 228 周分组使用 `dt[:7]` 即按月份分组，周总结实际等于月总结。修复：周分组改为 ISO 标准周 `YYYY-WW`。(`pipeline/observe.py`)
- **reflect.py L6 aggregation date‑based grouping**: line 201 使用 `ts[:10]`（YYYY-MM-DD）作为聚合键，同周不同日期的 L6 反思永不聚合（阈值 4 条永远达不到）。修复：改用 `datetime.isocalendar()` 提取 ISO 周。(`pipeline/reflect.py`)

## [v0.1.29] - 2026-06-30

### Changed
- **pyproject.toml 版本 0.1.4 → 0.1.29**: 同步 PyPI 包版本至最新 changelog 版本。(`pyproject.toml`)

### Fixed

- **distill GROUP BY 使用原始 agent_name**: `distill.py` line 26 GROUP BY key 直接使用 `r["agent_name"]` 而非 `normalize_agent_name(r["agent_name"])`，导致 `system.agent_name` 和 `system` 形成独立分组 → L9 产生重复/噪声。修复后 1263 条系统代理噪声清理完毕（458 L9 + 44 L10 删除，992 条目重命名）。(`pipeline/distill.py`)
- **integrate.py normalize_agent_name 未 import**: `integrate.py` 调用 `normalize_agent_name()` 但从未 import，任何 integrate 运行都会 NameError 崩溃。(`pipeline/integrate.py`)

## [v0.1.28] - 2026-06-30

### Fixed

- **debt scan cache 缺少 import json**: `server.py` 的 `_save_debt_cache()` / `_load_debt_cache()` 使用 `json` 模块但未 import，导致 NameError。 (`api/server.py`)

## [v0.1.27] - 2026-06-30

### Fixed

- **Agent 注册机制允许垃圾名称入库**: `normalize_agent_name()` 增加 4 条新校验规则 — 单字符英文拒绝、单字符 CJK 拒绝、花括号拒绝、`.agent_name` 后缀拒绝。4 个绕过 normalize 的管线步骤全部修复：`distill.py`、`observe.py`（2 处）、`reflect.py`、`integrate.py` 写入 agent_name 前统一调用 `normalize_agent_name()`。存量清理：212 条记忆 agent_name 重置为 "system"、33 条垃圾 identity 删除。(`core/thin_waist.py`, `pipeline/distill.py`, `pipeline/observe.py`, `pipeline/reflect.py`, `pipeline/integrate.py`)

## [v0.1.26] - 2026-06-30

### Fixed

- **pipeline.observation 模块名不存在**: `_PIPELINE_STEPS` 中 observation 步的 module_path 为 `"memall.pipeline.observation"`，但文件名已重命名为 `observe.py`。改为 `"memall.pipeline.observe"`。 (`pipeline/pipeline.py`)
- **vec0 虚拟表 INSERT OR REPLACE 不支持**: `_vec0_upsert()` 用 `INSERT OR REPLACE INTO mem_vec(rowid, embedding)` 在 vec0 virtual table 上触发 UNIQUE constraint failed on primary key。改为先 `DELETE WHERE rowid=?` 再 `INSERT`，绕过 vec0 对 OR REPLACE 支持不完整的问题。 (`graph/embeddings.py`)

## [v0.1.25] - 2026-06-30

### Docs

- **README 同步 42→6 工具数**: 更新 README.md 和 README.zh-CN.md 中所有"42 工具"引用为"6 个合并工具"（副标题、节标题、项目结构、路线图）。 (`README.md`, `README.zh-CN.md`)
- **MemALL_Function_Spec.md 同步工具名**: 更新 `memall_forget`→`memall_write action=forget`、`memall_adaptive`→`memall_system action=adaptive`、`memall_db`→`memall_system action=db`。 (`MemALL_Function_Spec.md`)

## [v0.1.24] - 2026-06-30

### Added

- **Agent 详情页**: 点击 Agent 卡片后全页展开，顶部展示 persona/记忆总量/分类分布，支持实时搜索过滤 + "加载更多"分页，最多展示 1000 条记忆。 (`frontend/index.html`)

### Changed

- **42 个 MCP 工具合并为 6 个 action 路由工具**: `memall_write`、`memall_read`、`memall_persona`、`memall_discussion`、`memall_federation`、`memall_system`。每个工具通过 `action` 参数路由到原 handler，消除 ~4,500–6,000 tokens 的 tools/list 响应。内部已有 `action` 参数的工具使用 `sub_action` 映射。 (`mcp/tools/__init__.py`, `src/memall/mcp/adapter.py`, `src/memall/mcp/shared.py`, `src/memall/mcp/registry.py`, `src/memall/mcp/models.py`, `tests/test_e2e.py`)

### Fixed

- **聊天历史静默丢失**: `AskRequest` 新增 `history: list = []` 字段，前端 `POST /ask` 发送的 `{question, history}` 中 history 不再被 Pydantic 静默丢弃。 (`server.py`)
- **仪表盘连接失败空白**: `doLoadDashboard()` 的 `.catch` 从静默失败改为展示红色错误条，提示"无法连接后端服务"。 (`frontend/index.html`)

## [v0.1.22] - 2026-06-29

### Changed

- **Web Dashboard 前端去重 + 动态化**: 删除 `desktop/index.html`（1873 行过期拷贝）和 `src/memall/api/frontend/index.html`（2156 行安装模式拷贝），仅保留 `frontend/index.html` 为唯一规范副本；server.py 前端路径搜索从双候选循环简化为单路径 + index.html 存在性检查；Debt Dashboard 从硬编码静态 HTML 改为 JS 动态渲染，通过 `/debt/stats` API 获取实时数据（记忆总量、连接数、层级分布、类别分布、归档记录数），饼图/热力图/卡片栏全部动态生成。 (`frontend/index.html`, `src/memall/api/server.py`)

### Added

- **代码扫描集成到前端**: 新增 `/debt/scan` POST 端点，动态导入 `debt/scan.py` 运行实时代码扫描（10 种负债模式 × 所有 .py 文件），返回扫描时间、行数、严重程度统计、前 50 条详情、负债密度。前端 Debt Dashboard 新增"扫描代码"按钮，触发后展示 4 级严重程度卡片 + 负债密度 + 发现详情表格，支持跨页面导航保持扫描结果。删除 `debt/dashboard.html`、`debt/dashboard_*.png`（已被 SPA 取代）。 (`src/memall/api/server.py`, `frontend/index.html`, `debt/`)

## [v0.1.21] - 2026-06-29

### Fixed

- **双 SentenceTransformer 模型实例浪费 21s + 33MB**: `memall.graph.embeddings._MODEL` 和 `memall.graph.retrieve._EMBED_MODEL` 各自独立加载 `BAAI/bge-small-zh-v1.5`。`retrieve._get_embed_model()` 改为委托 `embeddings._get_model()`，共享同一实例，冷启动减少 21s。 (`graph/retrieve.py`)

### Added

- **性能基准测试 (`perf_benchmark.py`)**: 8 维度覆盖 DB 读写延迟、capture/搜索/图操作吞吐、Pipeline 步骤、并发搜索、数据库体积。评分 95/100 S 级。 (`perf_benchmark.py`)
- **冒烟测试 (`smoke_test.py`)**: 40 项测试覆盖 DB 状态、FTS5、timeline、traverse、图谱、extract/harvest/classify/archive 管线步骤、混合搜索、CJK 多关键词召回、archive.db、session、核心模块 import。40/40 通过。 (`smoke_test.py`)

## [v0.1.20] - 2026-06-28

### Fixed

- **L2 MODULE 噪声过滤**: 存量 51 条 MODULE 注册记录（如 `[MODULE:root/agent_memory]`）被误分类为 L2 — 执行 SQL 回退至 P2。`_detect_layers()` 的 `[MODULE` 正则过滤已确认有效覆盖新内容。 (`pipeline/classify.py`)
- **`tests/smoke_test.py` pytest 收集崩溃**: 模块级 `sys.exit(0)` 导致 pytest 报 INTERNALERROR — 加 `if __name__ == "__main__":` 守卫，同时加 `__test__ = False` 标记。 (`tests/smoke_test.py`)

### Tests

- **classify 测试覆盖扩展**: 新增 `test_detect_layers_module_noise`（直接测 `_detect_layers` 对 MODULE 返回 P2）和 `test_classify_step_module_noise`（测完整 classify_step 将 MODULE L2 重分类为 P2）。7/7 pass。 (`tests/test_classify.py`)

### Docs

- **README 全量同步当前功能**: 工具数 38→42，层级 10→11（新增 L2/L7/L8/L11），管线 22 步→24 核心+5 可选，更新 11 层生命周期表、竞品对比表、项目结构图、MCP 工具分类表、Quick Start。中英文同步修改。 (`README.md`, `README.zh-CN.md`)

## [v0.1.19] - 2026-06-28

### Added

- **全方位冒烟测试 (`tests/smoke_test.py`)**: 覆盖 39 模块 import、DB init、capture/search/retrieve、28 个 pipeline step、config、MCP hooks、gateway、onboarding、federation、DB maintenance、tracer。92 项全部通过。(`tests/smoke_test.py`)

### Fixed

- **event_processor.py `sqlite3.Row.get()` bug**: `_dispatch_new_memory()` 和 `_inline_classify()` 中 `row.get("summary")` 和 `row.get("category")` 在 `sqlite3.Row` 对象上调用 `.get()` 失败 — 改用 `row["key"]` 直接索引；SELECT 补上 `summary` 列。 (`pipeline/event_processor.py`)

- **embed_index.py 无 sentence-transformers 崩溃**: 模块级 `from memall.graph.embeddings import build_index` 在缺少 sentence-transformers 时引发 ImportError — 改为函数内部 lazy import，捕获 ImportError 返回 skip 结果。 (`pipeline/embed_index.py`)

- **冒烟测试 6 处适配修复**: `capture()` 签名修正（content→第一个参数）、`observation` 模块已删除、`get_onboarding_status`→`status`、`list_conflicts`→`family`、`get_trace`→`ensure_trace`、`set_config` 移除。 (`tests/smoke_test.py`)

## [v0.1.18] - 2026-06-28

### Added

- **会话知识提取步 (extract_step)**: 新增 `extract.py` pipeline 步骤，扫描已结束 session 的记忆，按 category（decision/architecture/problem/fix/rule）分类；每组 ≥2 条时创建结构化 L6 条目（含关键句子提取 + `derived_from` 边 + `thread_id` 关联 L4）。首轮运行创建 6 条 L6 提取、29 条边。 (`pipeline/extract.py`)

- **注册到 pipeline**: 在 session 步骤之后、embed_index 之前注册 extract step，确保 harvest 后立即提取。 (`pipeline/pipeline.py`)

### Fixed

- **session.py l6_ch 未定义崩溃**: `_harvest_session()` 创建 L6 时引用了未定义的 `l6_ch` 变量 → 改用 `hashlib.sha256(l6_content.encode()).hexdigest()`。 (`pipeline/session.py`)

- **session.py l4_id 作用域泄露**: `l4_id` 仅在 `if not existing_l4:` 内定义，但被外部 L6 代码使用 → 添加 `else: l4_id = existing_l4["id"]`。 (`pipeline/session.py`)

## [v0.1.17] - 2026-06-28

### Added

- **11 层记忆架构重构**: 废除权重竞争，改用互斥优先级链（L6→L11→L7→L3→L5→L1→L4→L2→P2），每层独立准入条件（min_matches 要求不同模式组命中，min_content_len 最小内容长度）。支持降级重分类。 (`classify.py`)

- **L8 边晋升覆盖**: ≥3 条不同关系的边或 module_refs → L8。 (`classify.py`)

- **数据清理脚本**: 模板 L6 摘要降级 L4、薄 L9 删除、L10 近重复合并。 (`cleanup_levels.py`)

### Fixed

- **FTS5 CJK 召回率修复**: `_row_to_memory()` 中 `row.get("thread_id")` → `row["thread_id"]`（`sqlite3.Row` 无 `.get()` 方法），修复全部 FTS5 `retrieve()` 调用触发 AttributeError 导致 0 结果的 bug。 (`thin_waist.py`)

- **FTS5 CJK 分词策略重写**: `fts_query()` 从 jieba AND 模式改为 OR 扩展模式（原始 CJK 短语 + jieba 子词 + 2-char 二元回退），解决 jieba 不拆分的长 CJK 词（如"数据处理"）在 FTS5 unicode61 下 0 匹配的问题。经测试所有 9 类中英文查询均返回 ≥1 结果，FTS5 零结果查询从 2/10 降为 0/10。 (`thin_waist.py`)

- **死代码清理**: 移除 `fts_query()` 中对 `_split_cjk()` 的引用，该函数已不被 CJK 分支使用。 (`thin_waist.py`)

- **pipeline.py 作用域 bug**: `run_pipeline()` 中重复的 `from memall.core.db import get_conn` 导致 `UnboundLocalError`，移除内部 import 修复。 (`pipeline.py`)

- **classify.py L6 阈值**: 最小内容长度从 40 降到 25，配合 distinct pattern 计数防止误报，允许短真反思正确分类。 (`classify.py`)

- **S3-02 LIMIT 防护**: link.py 新增 `_EDGES_SCAN_LIMIT=50000` / `_PRUNE_GROUP_LIMIT=10000` / `_MEMORY_BATCH_LIMIT=2000`；forget.py 新增 `_L5_SCAN_LIMIT=2000` + 可覆盖参数。 (`link.py`, `forget.py`)

- **测试修复**: 同步 classify 重构后的 return dict（`category_updates` → `scanned`/`changed`/`layer_distribution`）到 5 个 test；修复 distill.py `sqlite3.Row.get()` bug 和 `[L9 蒸馏]` 前缀；修复 test_convergence.py `participants` 断言和 `convergence_rule` 参数；修复 test_adaptive.py `distill_history` 表未创建问题。 (`tests/test_classify.py`, `tests/test_distill.py`, `tests/test_convergence.py`, `src/memall/pipeline/adaptive.py`)

## [v0.1.16] - 2026-06-27

### Added

- **thread_id 继承链**: 全线贯通 L4→L6→L9→L10。`session.py` harvest_step 通过 content_hash 定位 l4_id 传给 L6 INSERT；`distill.py` distill_step L9 thread_id = source_ids[0]；`integrate.py` integrate_step L10 thread_id = source_ids[0]。zombie 字段 thread_id 现被所有 capture 路径填充。 (`session.py`, `distill.py`, `integrate.py`)

- **图谱 thread-aware 展开**: `traverse()` 新增 `thread_aware` 参数，展开时自动查询 thread_id 关联的同线程记忆（父节点 + 兄弟节点 + 子节点），以虚线琥珀色边 `same_thread` 标记，加入 BFS 搜索前沿。支持 FastAPI `/graph/{node_id}` 和 aiohttp gateway `/traverse` 两个入口。 (`thin_waist.py`, `server.py`, `gateway.py`, `mcp/models.py`, `frontend/index.html`, `desktop/index.html`, `api/frontend/index.html`)

## [v0.1.15] - 2026-06-26

### Added

- **S3-03**: 搜索向量化升级 CLI ↔ MCP 合并 — `federation/family.py` 扩展参数支持 MCP 重用：`search_family(content_length=200)` MCP 可请求 500；`publish_memory(redact=False)` MCP 可传入 True 以触发内容审计日志。架构对齐，消除约 76 行重复 INSERT 逻辑。 (`federation/family.py`)

- **S3-08**: [Lx] 前缀标准化：① `reflect.py` focus_tag 嵌套方括号修复（`[[L6]]` → `[L6]`）；② `thin_waist.py` 新增 L6-聚合/周反思/月反思 + L9-聚合 前缀常量；③ `federation_tools.py` `startswith('[L7')` → `startswith('[L7 ')` 修复；④ `mcp/tools/distill.py` startswith + 操作符优先级 bug 修复。 (`reflect.py`, `core/thin_waist.py`, `mcp/federation_tools.py`, `mcp/tools/distill.py`)

- **S3-11**: 跨 agent 路由 — `create_discussion()` 存储 `participants` 到 metadata；Lark 通知传递真实 participants/timeout_hours；`check_pending_discussions()` 按 participants LIKE 过滤；`mcp/tools/discussion.py` handle_create() 转发 participants/timeout_hours。 (`federation/discussion.py`, `lark/notify.py`, `mcp/tools/discussion.py`)

- **S3-05**: Federation 主动推 — Hub → MemALL push 机制：① `federation_tools.py` 新增 `fed_deliver()`（Hub → MemALL 事件投递，写入本地 DB）；② `hub_client.py` 新增 `hub_deliver_event()`（MemALL → Hub REST POST `/api/deliver`）+ `start_websocket_listener()`（aiohttp 异步 WebSocket 背景监听器，自动重连）；③ `mcp/tools/federation.py` 新增 `handle_deliver()` + 注册 `memall_fed_deliver` MCP 工具；④ `gateway.py` 新增 `POST /federation/events` 端点（Hub 调用 MemALL 的 push receiver）。 (`mcp/federation_tools.py`, `mcp/hub_client.py`, `mcp/tools/federation.py`, `mcp/tools/__init__.py`, `gateway.py`)

- **S3-04**: Gateway 安全治理：① 新增 `core/rate_limiter.py` `SlidingWindowRateLimiter`（内存滑动窗口，线程安全，默认 POST 30/min、GET 100/min、MCP 60/min）；② gateway.py 和 http_transport.py 注入限流中间件；③ `gateway.py` `/api/discussions/create` 和 `/api/discussions/respond` 从手动 `data.get()` 改为 Pydantic `DiscussionCreateInput`/`DiscussionRespondInput` 校验；④ `http_transport.py` 添加 auth 失败日志、强化安全检查。 (`core/rate_limiter.py`, `gateway.py`, `mcp/http_transport.py`)

- **S3-06**: 可观测性：① 新增 `core/log_setup.py` 统一日志配置（JSON 单行输出，6 个入口点统一调用 `configure()`，`MEMALL_PLAIN_LOG` 环境变量切回明文）；② 新增 `core/metrics.py` `MetricsCollector`（线程安全计数器 + 直方图，`GET /metrics` 端点暴露）；③ 新增 `core/tracer.py` `span()` 上下文管理器（写入 `tracing_spans` SQLite 表）；④ `mcp/adapter.py` `handle_call()` 注入 metrics（计数器 + latency）+ tracing（span）；⑤ `pipeline/pipeline.py` pipeline step span 包裹 + 7 天 trace retention cleanup。 (`core/log_setup.py`, `core/metrics.py`, `core/tracer.py`, `mcp/adapter.py`, `pipeline/pipeline.py`, `core/db.py`, `cli/main.py`, `bridge/main.py`, `bridge/run_bridge.py`, `mcp/http_transport.py`, `scheduler/scheduler.py`, `lark/consumer.py`)

### Chores

- **Dead imports**: Removed 13 unused imports across 9 MCP Python files (`hooks.py`, `hooks_builtin.py`, `http_transport.py`, `hub_client.py`, `registry.py`, `server.py`, `shared.py`, `tools/capture.py`, `tools/distill.py`).

### Fixed

- **S1-CLI-03 CLI 与 MCP 重复消除**: 创建 `memall.cli.handle_call.mcp_call()` 包装器，所有 CRUD 和 pipeline 命令改走 `adapter.handle_call()` 而非直调 thin_waist；MCP 成为唯一业务入口，CLI 退化为纯视图层；保留基础设施命令 CLI-only（init/start/stop/doctor/serve 等 19 个）。 (`cli/handle_call.py`, `cli/commands/base.py`, `cli/commands/pipeline_commands.py`, `cli/commands/management_commands.py`, `mcp/models.py`)

### Note

- **S1 全部清零 33/33 (100%)** 🎉 46 项（13 S0 + 33 S1）技术负债全部修复完毕。剩余 S2(24)/S3(13) 按需/迭代处理。

- **S1-CLI-02 init_temp_db 重复隔离逻辑**: conftest.py 已有 autouse fixture（monkeypatch+tmp_path）做每测试隔离，init_temp_db() 额外做 tempfile+patch+init_db 造成重复开销 → 改为返回 (None,None) 空操作桩，26 个测试文件无需修改。 (`tests/test_helpers.py`)

- **S1-BRG-01 bridge 错误处理**: lark_client.py Popen 加 try/except 捕获异常并 return、stdout 遍历加 try/finally 确保 proc.wait() 即使 handler 崩溃也执行；main.py stop() 加 try/finally 保护两个 watcher 都执行、MCP capture 失败日志从 DEBUG 升级到 WARNING、mentions 加 isinstance(m, dict) 防止非字典元素 AttributeError、两个 "silent error" 日志替换为具体描述。 (`bridge/lark_client.py`, `bridge/main.py`)

- **S1-SRH-01 CJK tokenization**: 3 处 TfidfVectorizer 从 token_pattern=r'(?u)\b\w+\b' 改为 tokenizer=tokenize（nlp.tokenize 已支持 CJK [\w\u4e00-\u9fff]+）。 (`core/nlp.py`, `pipeline/cluster.py`)

- **S1-SRH-02 faiss_provider 错误日志**: _encode() 两个 "silent error" 日志替换为描述性消息 + exc_info=True。 (`search/faiss_provider.py`)

- **S1-MCP-04 gateway 输入验证**: 添加 _validate() 静态方法复用 mcp/models.py Pydantic 模型，校验 5 个 POST handler（capture/retrieve/traverse/timeline/profile），消除手动 data.get() 式验证。 (`gateway.py`)

### Note

- **S1 进度 32/33 (97.0%)**: 唯一剩余 CLI-03（CLI/MCP 重复，约 1 周重构量）。git push 因端口 443 不可达暂缓。

### Chores

- **Dead imports batch (S2-16~24)**: 批量清理 40+ 处死 import，涉及 30 个文件 — agent_memory, api/server, bridge/main+config, core/context_assembler+db+nlp, federation/conflict+family+health, gateway, graph/embeddings+retrieve, lark/consumer, lark_notify, mcp/hooks+hooks_builtin+http_transport+hub_client+registry+server+shared+tools/*, migrations/004, pipeline/ask+behavior+bridge+cleanup+cluster+distill_l7+dream+improve+observe+session+stream+time_slice, scheduler, search/faiss_provider. 测试全绿。

- **S2-12**: adaptive.py 移除 `_get_adaptive_snapshot()` 中重复的 distill_history CREATE TABLE（由 adaptive_distill() 先创建）。

### Fixed

- **S3-08**: 命名规范统一 — ① `reflect.py` 修复 `focus_tag` 嵌套方括号问题（`[L6 反思 [工程实践]]` → `[L6 反思 工程实践]`）；② `thin_waist.py` `_LEVEL_SUBJECT_PREFIX` 补充 L6/L9 子类型变体（`L6-聚合/周反思/月反思`、`L9-聚合`）；③ `federation_tools.py` 修复 `startswith('[L7')` → `startswith('[L7 ')` 防止误匹配；④ `mcp/tools/distill.py` 修复 `startswith("[L9")` 缺少闭合括号 + 操作符优先级 bug。 (`pipeline/reflect.py`, `core/thin_waist.py`, `mcp/federation_tools.py`, `mcp/tools/distill.py`)

- **S3-11**: 跨 agent 路由 — 讨论自动 dispatch 修复：① `convergence.py` `create_discussion()` 存储 `participants` 到 metadata，修复查询全部依赖参与者过滤的断链；② `convergence.py` Lark 通知改用真实 participants/timeout_hours；③ `convergence.py` `check_pending_discussions()` 按 participants LIKE 过滤（不再广播给所有活跃 agent）；④ `mcp/tools/discussion.py` `handle_create()` 转发 `participants` 和 `timeout_hours`。 (`pipeline/convergence.py`, `mcp/tools/discussion.py`)

### Chores

- **S3-13**: git/CHANGELOG 自动化 — 新增 `scripts/post_commit_hook.py`（自动检测 CHANGELOG 版本号创建 git tag + 警告未更新 CHANGELOG）；`.git/hooks/post-commit.bat` 作为 hook 入口。 (`scripts/post_commit_hook.py`, `.git/hooks/post-commit.bat`)

### Docs

- **技术负债看板审计修复**: 基于逐文件行数统计校准 cli/ (6,800→4,338) 和 tests/ (3,000→11,476) 行数；验证 13 项 S0 代码级存在性（S0-003/S0-006 本轮修复，其余 11 项已核实）；Kanban 合并为单列"13/13 全部已修复"；Sprint 表替换为 S1 批量计划（5 项 ~45m）；饼图移除 S0 段重算（S1 47%/S2 34%/S3 19%）；热力图 85 项计数不一致修复。 (`frontend/index.html`, `src/memall/api/frontend/index.html`, `debt/INVENTORY.md`, `debt/DASHBOARD.md`)

### Note

- S0-004/S0-005 经审计确认当前代码已不存在裸漏洞（token leak 不在 handler 中，int() 已用 _safe_int/except 保护），标注"已核实"而非"已修复"。
- 缺失模块（lark/api/federation/scheduler/plugins/migrations 约 5,800 行）尚未纳入负债扫描，需后续 scan.py 规则收敛后补充。

## [v0.1.13] - 2026-06-26

### Fixed

- **S0-007 UUID 截断碰撞风险**: `str(uuid.uuid4())[:8]` 截断到 32 位 → 使用完整 UUID 字符串，消除 10 万次操作 50% 碰撞风险。 (`pipeline/session.py`)

- **S0-009 N+1 边缘计数**: `classify_step()` 每行执行独立 `COUNT(*) FROM edges` WHERE source_id=? → 预聚合 `GROUP BY source_id` 一次性查完，消除每 batch 500 次额外查询。 (`pipeline/classify.py`)

- **S0-011 O(n²) 自适应去重**: `adaptive.py` compression 模式 `SELECT id, content FROM memories ORDER BY id` 无 LIMIT → 添加 `LIMIT 5000`，防止大库时 O(n²) 性能爆炸。 (`pipeline/adaptive.py`)

- **S0-012 Memory dataclass 字段缺失**: `Memory` dataclass 缺 `thread_id` 和 `agent_name_locked` → 补全字段；`_row_to_memory()` 同步添加 `.get()` 安全读取；下游代码可通过 Memory 对象直接访问所有 DB 字段。 (`core/models.py`, `core/thin_waist.py`)

- **S0 清零确认**: 全部 13 项 S0 Critical 负债已修复（v0.1.11~v0.1.13）。
  - 安全类：S0-003~006（auth bypass、token leak、int crash、MCP auth）
  - 数据类：S0-002（PRAGMA FK）、S0-007（UUID）、S0-012（dataclass）
  - 性能类：S0-008（link O(n²)）、S0-009（N+1）、S0-010（enrich LIMIT）、S0-011（adaptive O(n²)
  - 静默失败：S0-013（embedding）
  - 运行时：S0-001（NameError）

## [v0.1.12] - 2026-06-26

### Fixed

- **Embedding 静默失败**: `_vec0_upsert()` 和 `_auto_embed()` 不再吞没异常，异常正确传播给调用方；`build_index()` 中 `DELETE FROM mem_vec` 失败时记录 warning 而非 bare `pass`。 (`graph/embeddings.py`)

- **NLP CJK 单字过滤**: `nlp.py:41` `len(t) > 1` 原过滤所有单字 token（含 CJK），改为保留单字 CJK 字符（如"猫""狗"）同时仍过滤单英文字母，修复中文搜索无结果问题。 (`core/nlp.py`)

- **link.py O(n²) 无边界**: `SELECT ... ORDER BY id` 无 LIMIT，2000+ 记忆时 O(n²) 全表比较 → 添加 `LIMIT 2000`，防止 pipeline 长时间阻塞。 (`pipeline/link.py`)

- **Pipeline 各步缺失 LIMIT**: `enrich.py`、`distill.py`(x2)、`integrate.py` 均无 LIMIT，大库时全表扫描 — 统一添加 `LIMIT 2000`~`5000`；`observe.py` 合并 3 次冗余 L6 metadata 全表扫描为 1 次；`reflect.py` 已自带 LIMIT 500。 (`pipeline/enrich.py`, `pipeline/distill.py`, `pipeline/integrate.py`, `pipeline/observe.py`)

## [v0.1.11] - 2026-06-26

### Security

- **L7 自助化闭环**: `handle_session_start()` 从 `auto_inject()` 结果中提取 L7 lessons/preferences 和 L6 reflections，格式化为显式行为指导文本返回，Claude 在 session 启动时即可读取并遵循。 (`mcp/tools/session.py`)

- **/pair 端点泄漏 auth_token**: 移除配对响应中的 `token` 字段，防止未授权用户通过 `/pair` 获取凭据。 (`gateway.py`)

- **所有 /api/* 绕过认证**: 改为仅 GET/HEAD /api/* 免认证（只读公开），POST/PUT/DELETE 需要 Bearer token，修复 `POST /api/discussions/create` 和 `/respond` 无认证问题。 (`gateway.py`)

- **MCP HTTP 零认证**: `handle_mcp_post` 新增可选的 Bearer token 检查（`MEMALL_MCP_TOKEN` 环境变量），作为 127.0.0.1 绑定之外的纵深防御。 (`mcp/http_transport.py`)

### Fixed

- **int(query_param) 非数字入参崩溃**: 4 处 `int(request.query.get(...))` 改为 `_safe_int()`，非数字入参返回默认值而非 500。 (`gateway.py`)

- **PRAGMA foreign_keys=OFF 无恢复**: `distill_step()` 在 try 前保存 `PRAGMA foreign_keys` 状态，finally 中恢复，防止连接池复用后外键永久失效。 (`pipeline/distill.py`)

- **discover_peers socket fd 泄漏**: 二次 bind 失败时关闭 socket 再 raise，防止 fd 泄漏。 (`gateway.py`)

- **Hub 消息未做清理**: 从数据库拼接 agent_name/subject/content 到消息体时过滤非打印字符，限制长度。 (`mcp/federation_tools.py`)

- **ThreadPoolExecutor 永不 shutdown**: `_on_shutdown` 中调用 `executor.shutdown(wait=False)`，确保平滑退出。 (`mcp/http_transport.py`)

- **session_end 重复 if count>3 块**: 移除第 2 个重复的 `if count > 3:` 块（lines 683-781），该块与第一个块（line 109）功能重复，会创建重复的 L4 会话记忆和 L6 反思。 (`pipeline/session.py`)

## [v0.1.10] - 2026-06-26

### Fixed

- **agent_name_locked 列缺少迁移**: 新建 `020_add_memories_agent_name_locked.py`，对已有数据库执行 `ALTER TABLE ADD COLUMN`。防止 `capture()` 因缺失列而崩溃。 (`migrations/020_add_memories_agent_name_locked.py`)

- **"system" 身份未在 identities 表注册**: `init_db()` 中 seed "system" agent；`capture()` 改为自动注册未知 agent_name 而非 raise ValueError，修复 gateway HTTP API 对新 agent 请求返回 500 的问题。 (`core/db.py`, `core/thin_waist.py`)

- **confirm_discussion 硬编码 [??] 前缀未随 Phase 1 更新**: 主题剥离改用 regex 同时兼容 `[??]` 和 `[讨论]` 前缀；L4 decision subject 和 content 改为 `[L4 会话]` 标准格式。 (`pipeline/convergence.py`)

- **update() 静默规范化 agent_name**: 当 agent_name 被 normalize 改变时（如小写化、黑名单命中→"system"）添加 logger.warning 告警。 (`core/thin_waist.py`)

## [v0.1.9] - 2026-06-25

### Changed

- **Phase 3: 废弃 _L8_WORDS 关键词正则**: `_L8_WORDS` 正则替换为废弃注释，`_LAYER_RULE_LIST` 移除 L8 条目（不再通过关键词匹配标记 L8）。L8 升级仅保留 edges 检测路径（edges 表 JOIN + module_refs）。L8 加入 `_TERMINAL_LAYERS`——一旦通过边提升到 L8即不可变。`_LAYER_RANK` 保留 L8 用于排名兼容。95 tests pass。 (`pipeline/classify.py`)

- **Phase 2: Gateway 图谱页面 `/graph` + JSON API `/api/graph`**: 新增 gateway 图谱可视化页面——整体统计（记忆数、关系数、图密度）、关系类型分布表（14 种类型带占比）、活跃节点 TOP 20（可点击跳转节点详情）、节点详情页（`?node_id=N` 显示该节点的 50 条最近边）。`/api/graph` 返回 JSON 格式的 totals/types/hubs。95 tests pass。 (`gateway.py`)

- **Phase 1: [GRAPH] 段从 L8 关键词查询改为 edges 实时聚合**: `auto_inject` 中 4 条 edges 查询替换了旧 L8 memories 查询——时间窗口计数(24h/7d/total)、类型分布(GROUP BY)、最近 5 条边(ID 无 JOIN)、活跃节点 TOP5 含 subject。`session_start` 中 `[GRAPH]` 从单行 subjects 升级为 4 行结构化输出。95 tests pass。 (`mcp/federation_tools.py`, `pipeline/session.py`)

### Fixed

- **supersedes FK constraint — schema + all INSERT paths**: `db.py` still had `supersedes TEXT NOT NULL DEFAULT '[]'` but models use `Optional[str] = None`. Fresh DBs rejected all INSERTs with `None` for supersedes. Fixed schema to `INTEGER REFERENCES memories(id)` (no NOT NULL), changed all 4 INSERT paths in `convergence.py` + guard in `thin_waist.py`. (`core/db.py`, `core/models.py`, `core/thin_waist.py`, `pipeline/convergence.py`)

- **converge_discussion string action_items missing assignee**: When action_items are plain strings (not dicts), the loop left `assigned_to=""`. Now extracts `participants` from discussion metadata and rotates through them as fallback assignees. Also adds `"assignee"` to task_meta dict for proper task attribution. (`pipeline/convergence.py`)

- **agent_name 规范化管理 (方案 C)**: 从 `capture()` 中提取 `normalize_agent_name()` 独立函数至 `core/thin_waist.py`，应用于 `update()`、`convergence.py` 中 4 处直接 INSERT（`create_discussion`、`confirm_discussion`、`converge_discussion` L5 task、`check_pending_discussions`）以及 `gateway.py` 中 `_import_identity` 和 `_import_memories` 路径。数据清理：40 条空 agent_name → "system"。代理名称统一经过 strip+lower+regex+黑名单校验。 (`core/thin_waist.py`, `pipeline/convergence.py`, `gateway.py`)

- **Phase 1: 层级命名规范统一 — subject 前缀**: 新增 `_LEVEL_SUBJECT_PREFIX` 映射表（level → `[Lx 标签]`），`_make_subject()` 签名增加 `level` 参数，优先使用 level prefix 再 fallback 到 category prefix。distill.py L9 subject 追加 `[L9 蒸馏]` 前缀，integrate.py L10 subject 从 `"L10:{agent}跨领域洞察({})"` 改为 `"[L10 整合] {agent} 跨领域洞察({})"`。85 tests pass（无新增失败）。 (`core/thin_waist.py`, `pipeline/distill.py`, `pipeline/integrate.py`)

- **Phase 2: 遗留 subject 数据清理**: 975 条 L9 旧数据追加 `[L9 蒸馏]` 前缀，4 条 L4 `[??]` 编码残损修复为 `[L4 会话]`。不改新生成逻辑，只清理存量。 (`一次性数据迁移`)

- **Artifact 页面 `/artifact`**: 新增 gateway 公共路由，展示 session 成果清单（commits、讨论收敛、Agent 评估矩阵），添加到导航栏和 auth 白名单。 (`gateway.py`)

## [v0.1.8] - 2026-06-25

### Fixed

- **classify_step LIMIT 500 无声丢失**: SQL 查询缺少 ORDER BY，每次仅重复扫描旧 500 条，830 条非 terminal 记忆（含 48 条有 edges 候选）从未处理。改为游标分页 — `pipeline_cursors` 表追踪 `last_classify_id`，每次跑 500 条，渐进覆盖全部 1330 条，跑完自动重置循环。L8 边缘检测（module_refs + edges 表）现在能覆盖全部记忆。 (`pipeline/classify.py`)

## [v0.1.7] - 2026-06-25

### Changed

- **Lightweight session_start**: Added TTL cache (300s) to `auto_inject()` — after first call per agent, all subsequent calls return cached data with 0 SQL queries. Moved L4 summaries, L5 todos, and BEHAVIOR annotations into the cache. session_start SQL reduced from ~23 queries to ~3 (stale check + session create + cache miss). (`mcp/federation_tools.py`, `pipeline/session.py`)

## [v0.1.6] - 2026-06-25

### Added

- **Phase 0: Composite index idx_level_agent**: New index `idx_memories_level_agent ON memories(level, agent_name)` accelerates all level+agent queries in session_start (used by 28-40 SQL queries). Zero data dependency, immediate effect. (`core/db.py`)

- **L3 scope field**: New `metadata.scope` field (values: `agent`/`family`/`shared`, default=`agent`) controls L3 workflow visibility across agents. Backward compatible — NULL defaults to `agent`. (`mcp/federation_tools.py`, `pipeline/session.py`)

- **Phase 1: Behavioral stage annotation**: New `pipeline/behavior.py` module with regex-based OODA loop detection (observe→model→predict→deviate→correct). Integrated into `enrich_step()` — 222 memories annotated on first run. `session_start()` now includes `[BEHAVIOR]` section with stage distribution and common sequences. (`pipeline/behavior.py`, `pipeline/enrich.py`, `pipeline/session.py`)

### Changed

- **L3 scope-aware queries**: `auto_inject()` workflow_skills and `session_start()` category matching now filter L3 by scope — agent-scoped workflows only visible to their creator, family/scoped visible to all agents. (`mcp/federation_tools.py`, `pipeline/session.py`)

- **Existing L3 memories scoped**: Discussion participation workflow (#10395) → `scope=family` (跨agent通用), codex research (#10396) → `scope=agent` (私有调研报告). (`core/thin_waist.py`)

### Fixed

- **confirm_discussion auto-converge**: Function was inserting a P2 response but not converging the discussion — docstring said "immediately converges" but code returned "responded". Now properly calls `converge_discussion()`. (`pipeline/convergence.py`)

- **converge_discussion supersedes=None (3 more)**: L4 decision and L5 task INSERTs still had `None` for `supersedes` column — missed in the v0.1.5 fix. L5 task INSERT also missing `project` value (latent bug masked by L4 supersedes error). (`pipeline/convergence.py`)

## [v0.1.5] - 2026-06-25

### Added

- **L11 Domain Knowledge Layer**: New terminal layer (rank 95) for business/strategy/domain knowledge, distinct from L3 workflow templates. 89 existing L3 memories bulk-reclassified to L11. (`pipeline/classify.py`)
- **L11 classify rules**: `_L11_WORDS` regex (weight 70) captures business, domain, strategy signals — automatically classifies new captures. (`pipeline/classify.py`)
- **L11 in auto_inject + session injection**: `auto_inject()` now returns `domain_knowledge` (L11 memories). `session_start()` formats `[DOMAIN]` section in context injection. (`mcp/federation_tools.py`, `pipeline/session.py`)
- **L11 infrastructure**: forget TTL (730d), thin_waist validation, search boost (0.3x), frontend color (#14b8a6), CLI --level choices, pipeline level checks, terminal exclusions in reflect/distill/identity. (11 files)

### Changed

- **L3 clarified purpose**: Layer 3 reserved for reusable multi-stage workflow templates (roles/stages/transitions). Existing non-workflow L3 content moved to L11. (`pipeline/classify.py`)

## [v0.1.4] - 2026-06-23

### Added

- **SDK Layer — `agent_memory.py`**: New `add()` / `search()` high-level API with automatic project inference. Every memory stored via `add()` gets a non-empty `project` field — inferred from `agent_name` (workbuddy→memall, douyin-daily→douyin-daily) or content keywords, with `"memall"` as default fallback. (`agent_memory.py`)
- **Project field fallback in all capture paths**: MCP `capture` tool, MCP `smart_store` tool now auto-fill project via `infer_project()` when the caller omits it. (`mcp/tools/capture.py`, `mcp/tools/memory_write.py`)

### Fixed

- **Pipeline INSERTs missing project column**: All 6 pipeline files (`session.py`, `distill.py`, `integrate.py`, `reflect.py`, `observe.py`, `convergence.py`) — INSERT INTO memories now includes `project`, derived from source memories via majority vote. (`pipeline/*.py`)
- **Scripts INSERTs missing project**: `daily_checkin.py`, `daily_explore.py`, `self_task.py`, `weekly_checkin.py`, `scheduler/agent_round.py` — all raw INSERTs updated to include `project` column. (`scripts/*.py`, `scheduler/agent_round.py`)
- **Backfill migration**: `_backfill_project.py` scanned 1982 empty-project memories and backfilled 1629 (82%) via agent mapping, group majority, and content heuristics. Empty rate: 78% → 13.9%. (`pipeline/_backfill_project.py`)
- **logger-in-docstring bugs (5 more)**: `faiss_provider.py`, `adaptive.py`, `forget.py`, `register.py`, `cleanup.py` — same pattern as the original `federation_tools.py` bug. Zero instances remain across `src/memall/`. (`search/faiss_provider.py`, `pipeline/adaptive.py`, `pipeline/forget.py`, `cli/register.py`, `pipeline/cleanup.py`)

### Security

- **hybrid_search() visibility filtering**: Results now pass through `_filter_by_trust_dict()` before returning; unknown agents default to `read_level="private"` (was `"public"`). (`core/thin_waist.py`)
- **4 shell=True subprocess calls removed**: All changed to list-arg style — eliminates command injection risk from user-controlled `text[:1500]`. (`lark_notify.py`, `lark/consumer.py`, `bridge/lark_client.py`)
- **API server Bearer token auth**: 57 routes protected via middleware; token auto-generated on first start and persisted to config. CORS `"file://"` origin removed. (`api/server.py`)
- **Federation peer token enforcement**: `_remote_retrieve` and `_remote_retrieve_async` now require peer token — skip peer with warning if unconfigured. (`gateway.py`)

## [v0.1.3] - 2026-06-23

### Added

- **E2E Test Suite**: 25-test end-to-end test covering capture, retrieve, timeline, connect, traverse, session lifecycle, smart store, vector search, DB ops, identity, persona, onboarding, pipeline, index rebuild, dedup, and error handling — all calling `handle_call` directly (no HTTP server) with retry-on-BUSY pattern. (`tests/test_e2e.py`, `tests/test_helpers.py`)
- **Memory Health System**: New `memall.core.health` module with `collect()` for actionable memory diagnostics. Integrated into `memall doctor --deep` for deep health checks and `session_start` as `[HEALTH]` section. Reports graph coverage, reflection rate, isolated memories, stale discussions, pipeline freshness, and DB size with issue/recommendation hints. (`core/health.py`, `cli/commands/management_commands.py`, `pipeline/session.py`)
- **Export/Import/Sync System**: JSONL export format with content_hash dedup, `--since` time filter, `memall import <file>` for JSON/JSONL import, and `memall sync --from <file>` for incremental sync with state tracking in `~/.memall/sync_state.json`. (`cli/export.py`, `cli/main.py`, `cli/commands/management_commands.py`)

### Fixed

- **Category Taxonomy Normalization**: Eliminated all 122 composite categories and consolidated 100+ labels → 25 clean categories. Fixed root cause in `integrate.py` (L10 merge no longer concatenates categories with `、`; picks majority category instead). Applied DB cleanup via migration script to standardize synonyms (`bugfix→fix`, `business_idea→business`, `discussion_response→discussion`, `daily_summary→report`, etc.). (`pipeline/integrate.py`)
- **ops.py SyntaxError**: Moved `import logging` to module level to fix `expected 'except' or 'finally' block` crash introduced in earlier commit. (`pipeline/ops.py`)

### Changed

- **Lazy Auto-Init**: `get_conn()` and `ConnectionPool._new_conn()` now call `init_db()` on their first invocation, so no explicit `memall init` is required for new users or agents that clone the repo. (`core/db.py`)

### Fixed

- **Gateway import global content_hash dedup**: Dedup check was scoped by `agent_name`, but the `UNIQUE` constraint is global — switched to a global lookup. (`gateway.py`)
- **Connection Pool Write Lock**: `pool_conn()` returned connections with uncommitted implicit write transactions, causing "database is locked" on reused connections. Added `conn.commit()` in pool_conn context manager's finally block. (`core/db.py`)
- **vec0 Dimension Mismatch**: `build_index()` passed raw k-dim SVD vectors (k ≪ 256 for small datasets) to vec0 expecting 256-dim vectors. Added padding to `EMBED_DIM=256` before `tobytes()`. (`graph/embeddings.py`)
- **Pipeline Hook TypeError**: `_hook_pipeline_stop` assumed all step results were `int`, but `classify_step()` returns `dict`. Added `_count()` helper to extract integer from dict. (`mcp/hooks_builtin.py`)
- **OpsInput None Defaults**: Pydantic model had `Optional[int] = None` which `model_dump()` preserved as `None`, causing `TypeError` in dedup operator. Changed to explicit `Field(...)` defaults. (`mcp/models.py`)
- **`_auto_embed` Missing Table**: Called `SELECT` on `memory_embeddings` before table existed on fresh DB. Added `_ensure_embeddings_table()` guard. (`graph/embeddings.py`)
- **`_load_embeddings_matrix` Missing Table**: Queried `memory_embeddings` without creating it first. Added `_ensure_embeddings_table()` call. (`graph/embeddings.py`)
- **`_query_embed` Dimension Mismatch**: SVD produced k-dim query vectors (k < `EMBED_DIM`) causing matmul shape error. Added padding to `EMBED_DIM=256`. (`graph/retrieve.py`)
- **Migration 015/017/018 Silent Errors**: `logger = logging.getLogger(__name__)` placed inside docstrings, never executed — migrations silently caught all exceptions. Extracted logger assignment above docstring. (`migrations/015_*.py`, `migrations/017_*.py`, `migrations/018_*.py`)
- **Missing `identity_profile` Column**: Column referenced in code but missing from base schema DDL. Added to `CREATE TABLE identities`. (`core/db.py`)
- **Thread-Safe Connection Close**: `ConnectionPool.get()` tried to close connections owned by another thread, causing `ProgrammingError`. Added specific catch for `sqlite3.ProgrammingError`. (`core/db.py`)
- **SyntaxWarning `\\w`**: Invalid escape sequence `\w` in docstring triggered Python 3.12 warning. Escaped backslash. (`graph/embeddings.py`)
- **`doctor --deep` UnboundLocalError**: Redundant `import json` inside `cmd_doctor()` shadowed the module-level import, causing `UnboundLocalError` on all non-`--fix` runs. Removed the local import. (`cli/commands/management_commands.py`)
- **MCP stdout GBK crash**: `_respond()` wrote JSON with `ensure_ascii=False` to `sys.stdout`, which crashes on Windows GBK consoles when Unicode chars (✅) appear. Added `sys.stdout.reconfigure(encoding='utf-8')` at `serve()` entry + `PYTHONIOENCODING=utf-8` env var in MCP config. (`mcp/server.py`, `.claude/settings.json`)
- **DB default on C: drive**: `_resolve_db_path()` now prefers first available non-system drive (D:, E:, …) on Windows instead of always dropping in `C:\Users\...\.memall`. Backups and `memall doctor` path checks follow the same logic. (`core/db.py`, `cli/backup_restore.py`, `cli/commands/management_commands.py`)

### Publishing

- **PyPI `memall-os` 0.1.2 published** under account `j19800-dev` (new account created after the old `j19800` account got locked out by 2FA). Package renamed from `memall-db` → `memall-os` since `memall` is too similar to the existing `memall-db` project (PyPI rejects similar names). Install: `pip install memall-os`.

## [v0.1.1] - 2026-06-21

### Fixed

- **HTTP Transport Crash**: Root cause fixed — sync `handle_call()` blocked aiohttp event loop. Offloaded to `ThreadPoolExecutor` (12 fast + 2 heavy workers) with `asyncio.wait_for()`. Auto-restart on crash/port conflict. (`http_transport.py`, `shared.py`)
- **DB Connection Deadlock**: `ConnectionPool.get()` had no timeout on `Queue.get()` — added 30s barrier. 21 raw `sqlite3.connect()` calls missing `timeout=10` — all backfilled across federation, lark, cli, pipeline modules. (`core/db.py`, 8 federation/cli/api files)

### Added

- **L7 Lifecycle Closure**: `auto_inject` defaults to True across all entry points (5 files) — new sessions automatically inject `[L7约束]` behavioral rules. L6→L7 auto-distillation via `distill_l7.py` regex-based lesson extraction, registered in pipeline after `reflect_step()`. (`pipeline/distill_l7.py`, `mcp/models.py`, `mcp/tools/__init__.py`, `mcp/tools/session.py`, `pipeline/session.py`, `api/server.py`, `pipeline/pipeline.py`)

### Changed

- **CLAUDE.md**: Added "自动提交" rule — each independent change auto-updates ALL relevant .md (not just CHANGELOG) + commit + push + notify user.

### Changed

- **Lazy Auto-Init**: `get_conn()` and `ConnectionPool._new_conn()` now call `init_db()` on their first invocation, so no explicit `memall init` is required for new users or agents that clone the repo. (`core/db.py`)

## [v0.1.0] - 2026-06-19

### Added

- **Memory Lifecycle**: 10-layer memory architecture (P0/L1-L10) with automatic pipeline
- **Decision Arcs**: Full L4→L5→L6 lifecycle with convergence engine for multi-agent discussions
- **Timeline System**: Pre-aggregated time_slices (day/week/month) + epoch detection (gaps, topic drift, reflection inflection points)
- **Self-Reflection (L6)**: Automatic quality review, pattern recognition, error correction
- **Knowledge Distillation (L9)**: Compress raw memories into structured knowledge graph
- **Multi-Agent Federation**: Cross-agent memory publish/query/conflict resolution with trust hierarchy
- **LAN Discovery**: Auto-detect nearby peers via mDNS, bidirectional sync
- **Hybrid Search**: FTS5 exact match + sqlite-vec (256-dim) semantic similarity
- **Session Management**: session_start with auto-inject, session_end with summary, session_summary
- **Agent Identity**: L1 identity traits + L7 preferences profiling
- **Onboarding System**: 5-step guided setup for new users
- **OODA Self-Improvement**: Observe-Orient-Decide-Act loop without human intervention
- **Quality Gates**: 8-dimension scoring in pipeline (relevance, coherence, novelty, actionability, etc.)
- **Auto-Forget**: TTL expiration + low-value decay with review mechanism
- **Memory Ops**: merge, split, tag, archive, restore, dedup tools
- **Security Governance**: audit, permit, check, score subsystem
- **Gateway Server**: HTTP export/import, LAN discovery, federated queries

### Changed

- **Architecture Redesign**: From legacy 62-action surface to Thin Waist 5-method (capture/recall/connect/traverse/timeline)
- **MCP Tool Consolidation**: 19 independent tools → 4 tool sets (core, AI, graph, system) → 37 unified MCP tools
- **Pipeline v3**: 21-step automatic pipeline (enrich → classify → time_slice → arc_status → echo → epoch → reflect → distill → integrate → ...)
- **Configuration**: All config stored in SQLite `config` table, env overridable
- **Identity evolved**: Agent identities table with L1/L7 portrait generation
- **Discipline Migration**: Legacy daemon → Windows Scheduled Tasks (04:00 pipeline, 03:00 forget)
- **MCP Server**: Unified STDIO + HTTP transport via config-based routing
- **Pricing positioning**: Freemium model (Free: 5k memory limit, Pro: $9.99/mo) defined

### Fixed

- **DB Path Resolution**: Config-based path respecting overrides (#7905, #8144)
- **OpenBLAS OOM**: Pipeline crash on 2000+ memories (#8133)
- **Scheduler Restored**: After 12-day downtime, migrated to Windows Tasks (#7895)
- **Discussion Dual-Path**: _meta/value duplicate entries (#8177)
- **Discussion Metadata Migration**: Legacy table drop without re-wrap cycle (#7958, #7963)
- **Silent Errors**: 79 blocks across 33 files migrated from bare pass to logger.warning (#7965)
- **Database Copy Bug**: Fixed concurrent write corruption (#5558)
- **Classify Level Loss**: layer field not persisted in classify step (#4894)
- **Bridge N+1**: Per-edge queries converted to batch IN (#5306)
- **Migration Cleanup**: Double migration system removed (#5305)
- **Test Isolation**: conftest.py + production DB protection (#6292)
- **10-Layer Health Skew**: Resolved architecture imbalance (#4975, #4977)
- **Consumer Recovery**: Message consumption restored after refactor (#4979-#4981)
- **FTS5 Repair**: memory-doctor.py for database integrity checks
- **Discussion Status**: Removed bare status after cleanup (#7963)
- **Backup Restoration**: Added memall backup/restore/check commands

### Removed

- Legacy daemon process (replaced by Windows Scheduled Tasks)
- _run_migrations + 7 migration files (dual migration system)
- Legacy SmartMemoryInjector (integrated into pipeline)
- Kronvex from comparison table (blocking marketplace listing)
- FTS5 as standalone MCP tool (SQLite built-in, not a tool)

### Security

- **3-Layer Safety Net**: Permission + circuit-breaker + recovery
- **QR Pairing**: LAN device authentication without network exposure
- **PII Redaction**: Optional content sanitization in scrape/parse pipelines
- **API Key Auth**: MCP Server authentication module

## [v0.0.2] - 2026-06-06

### Added

- Phase 2 compression and decay mechanisms (DreamGenerator, MemoryLifecycle)
- Timeline dimension: time_slices, epochs, session summary injection
- Decision Arc: full L4→L5→L6 lifecycle
- Discussion convergence engine
- MCP Marketplace listing draft
- LAN discovery and federation prototype

### Changed

- Legacy -> Thin Waist architecture migration completed
- 19 MCP tools -> 4 tool sets
- Tag normalization: 352 unique tags → 33 (91% reduction), 5-dimension standard set
- Database path: sandbox (~/.MemALL) → workspace

### Fixed

- Dead code and script cleanup
- L9 decay pipeline timeout
- FTS5 + vector search hybrid
- BOM illegal characters (8 files)
- Indentation/syntax errors (5 files)

## [v0.0.1] - 2026-05-25

### Added

- Initial MemALL prototype with SQLite-backed memory storage
- MCP server with HTTP + STDIO transport
- CLI with 40+ subcommands
- Agent SDK Python client
- Basic capture/retrieve/timeline/search operations
- 10-layer architecture: P0/L1-L10
- FTS5 full-text search
- Identity and Agent Registry
- Self-improvement framework (HOT memory injection)
