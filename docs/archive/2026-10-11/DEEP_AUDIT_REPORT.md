# MemALL 代码深度审计与修复报告

> 仓库：`j19800/MemAll`，分支 `master`，基线 HEAD `4cfcfcd`，审计/修复日期 2026-11-08（Asia/Shanghai）。
> 审计范围：backend / frontend / website / obsidian-plugin / scripts / install / tooling / tests / CI / docs / 工程文件。
> 阶段一：只读深度审计（不修改源码）。阶段二：应用户「从 p0 开始 全部修复 无需我确认」要求，按 P0→P3 逐项修复。
> 未安装依赖、未启动服务、未构建、未提交（工作区存在其他 agent 的并发改动，故不 commit/push）。

---

## 0. 结论概览

| 级别 | 审计项 | 已修复 | 复核更正 | 说明 |
|---|---|---|---|---|
| P0 | 5（含新增 1） | 5 | — | 联邦健康空库、发布 UPSERT、导入元数据、导出 N+1、Obsidian 加载崩溃 |
| P1 | 6 | 5 | 1 项误判撤销 | viewer 透传实际已完成，非缺陷 |
| P2 | 5 | 4 | 1 项误判撤销 | requirements 当前与 pyproject 一致，非漂移 |
| P3 | 5 | 3 | 1 项「已受控」 | subprocess 全部带 timeout；前端/插件已专项审计 |

**修复文件 16 个**（详见 §7），**新增文件 2 个**（`requirements-dev.txt`、本报告）。
所有修改均通过：Python `compileall` 全量编译、AST 解析、Node `--check`、以及针对每个修复点的**隔离运行时验证**（stub 依赖，真实 SQLite 行为）。

---

## 1. 复核更正（审计报告初版的误判，未据此乱改代码）

修复前对全部结论做了逐行复核，撤销 3 处基于文档文本而非代码的误判：

1. **P1-5「`unified_search` viewer 透传不完整」→ 撤销（实际已修复）**
   `src/memall/core/thin_waist.py:1805-1811, 1879, 1911, 1940, 1969` 的 direct / fts5 / vector / hybrid 各分支均已
   `viewer = arguments.get("viewer") or arguments.get("agent_name") or ""` 并透传；`agent_name_filter` 已作为独立的内容
   过滤参数分离（`:1824, :1869, :1901, :1939, :1967`）。`AGENTS.md §1.1` 中「P0-4b 未做」是**过期的任务看板文本**，
   与当前代码不符。**未修改此文件**（改会引入回归）。建议后续由维护者更新 AGENTS.md 看板状态。

2. **P2-3「`requirements.txt` 与 `pyproject.toml` 依赖漂移」→ 撤销（当前无漂移）**
   逐行比对 `requirements.txt:1-12` 与 `pyproject.toml:38-51`，12 项依赖及版本约束完全一致。仅补了同步说明注释，
   并新增 `requirements-dev.txt` 解决测试依赖缺失（P3-1）。

3. **P3-2「子进程调用缺少超时/清理」→ 复核为「已受控」**
   全仓 18 处 `subprocess.run/Popen`：无 `shell=True`（仅出现在安全注释与 `check_fixes.py` 检测逻辑中）；
   所有 `subprocess.run` 均带 `timeout`（`management_commands.py:1101,1108`、`scheduler.py:321`、`lark/consumer.py:40`、
   `lark_notify.py:22`、`bridge/lark_client.py:58` 等）。属推断风险，非确认缺陷，未改动。

---

## 2. P0 — 阻塞 / 正确性（5 项，全部修复）

### P0-1. `federation_health()` 在空库/未初始化库崩溃
- **证据**：`src/memall/federation/health.py:36-52`（原）；查询 `shared_memories` / `conflicts` 但只建 `health_metrics`。
- **分类**：确认缺陷。**触发**：`memall federation health` 或 `federation visualize`（`visualize.py:166`）在从未发布过的环境运行。
- **影响**：`sqlite3.OperationalError: no such table`，CLI 与可视化均失败。
- **修复**：`health.py` 入口先 `init_family_db()` + `conflict._migrate_family_db()`（带降级日志），确保两张表存在；
  同时对 `family.py` / `conflict.py` 的迁移补了 `CREATE TABLE IF NOT EXISTS shared_memories` 与父目录 `mkdir`，
  使任意模块单独首次调用也安全。
- **验证**：stub 运行时，全新 `family.db` 返回 `total=0` 且自动建出 `shared_memories/conflicts/family_circle/health_metrics`。

### P0-2. `publish_to_family` 用 `INSERT OR REPLACE` 覆盖并重置冲突状态
- **证据**：`src/memall/federation/family.py:314-321`（原）。
- **分类**：确认缺陷。**触发**：同一 `(original_id, source_agent)` 重复发布（内容更新）。
- **影响**：`REPLACE` 删除旧行再插入 → `id` 改变（`conflicts.memory_id_a/b` 外键引用断裂）+ `conflict_status` 被重置为 `none`。
- **修复**：改为 `INSERT ... ON CONFLICT(original_id, source_agent) DO UPDATE SET ...`，只刷新可变列，保留 `id` 与冲突状态。
- **验证**：重复发布后 `id` 不变、`conflict_status='potential'` 保留。

### P0-3. 导入器丢弃所有元数据、错误 sys.path、未知 level 静默降级
- **证据**：`src/memall/cli/import_data.py:20, 42-59, 62-77`（原）。
- **分类**：确认缺陷。**触发**：任何 JSONL/CSV/JSON/Mem0 导入。
- **影响**：`metadata: {}` 丢失 status/assignee/task_id/timestamps/ownership；`sys.path` 指向不存在的 `src/memall/src`；
  未知 level 静默映射 `P2`。
- **修复**：移除错误 `sys.path`；新增 `_normalize_metadata()` 保留传入 metadata（dict/JSON 字符串），无 `metadata` 键时从扁平字段
  （status/assignee/thread_id/occurred_at/...）收集；未知 level 记 `logger.warning`；Mem0 分支同样补齐 metadata。
- **验证**：`{"level":"session","metadata":{"status":"done","task_id":42}}` → `L4` + 完整 metadata；扁平字段 `status/task_id` 亦保留；
  现有 `tests/test_level_and_owner_invariants.py` 的 3 个导入用例全部通过。

### P0-4. 导出 N+1 查询 + 同日覆盖 + 冻结 DB_PATH
- **证据**：`src/memall/cli/export.py:21-67, 109, 147, 201, 244-251`（原）。
- **分类**：确认缺陷（性能/正确性）。**触发**：导出大型库；同日导出两次；运行时 DB 路径被 patch。
- **影响**：每条记忆一次 edges 查询（O(N) SQL）；全量物化；同日文件名相同互相覆盖；`source_db` 用冻结的 `DB_PATH`。
- **修复**：单条 `IN (...)` 批量 edges 查询 + 内存分组；`get_db_path()` 替换 3 处 `DB_PATH`；默认文件名加 `_%H%M%S` 时间戳与序号后缀防覆盖。
- **验证**：5 条记忆 / 5 条边一次查询；连续两次导出生成 2 个不同文件；`source_db` 反映实时路径。

### P0-5（新增）. Obsidian 插件加载即崩溃
- **证据**：`obsidian-plugin/main.js:7, 384, 396`（阶段二前端专项审计发现）。
- **分类**：确认缺陷。`require("obsidian")` 只解构了 `Plugin, Notice, TFile, TFolder, parseYaml, stringifyYaml, moment`，
  但 `:384` 的 `class MemAllSyncSettingTab extends PluginSettingTab` 与 `:396` 的 `new Setting(...)` 使用了**未导入**的
  `PluginSettingTab` / `Setting`。
- **触发**：`onload()` → `this.addSettingTab(new MemAllSyncSettingTab(...))` → `ReferenceError`，插件无法加载。
- **修复**：在 `require("obsidian")` 解构中补 `PluginSettingTab, Setting`。
- **验证**：`node --check obsidian-plugin/main.js` 通过；静态确认所有标识符均已导入。

---

## 3. P1 — 高风险（5 项修复 / 1 项撤销）

### P1-1. DB 路径冻结（import-time `DB_PATH` 绑定）
- **证据**：`src/memall/core/db.py:50`；`cli/backup_restore.py:15-31`、`pipeline/backup.py:1-10`、`mcp/federation_tools.py:66`、`cli/export.py:12`（原）。
- **分类**：确认缺陷。**触发**：`gateway.py:205` / `mcp/http_transport.py:559` / `api/start_server.py:16` 在启动时 patch
  `memall.core.db.DB_PATH`，或测试通过 `MEMALL_DB_PATH` 切换库。
- **影响**：备份/恢复/导出/联邦发布读写错误库。
- **修复**：`backup_restore.py` 改为 `from memall.core import db as _core_db` + `_get_db_path()` 读取**实时模块属性**（`get_db_path()` 会
  重新解析 config/env，看不到 patch，故不用它）；`BACKUP_DIR/DAILY_DIR/WEEKLY_DIR` 改为函数；`pipeline/backup.py` 同步；
  `export.py` 3 处改 `get_db_path()`。
- **验证**：stub 中 patch `memall.core.db.DB_PATH` 后，备份目录/恢复目标立刻跟随；`tests/test_backup.py` 已由协作 agent 同步改为
  `patch("memall.core.db.DB_PATH", ...)`，手动执行 `test_backup_step_no_db` 通过。

### P1-2. 进程级懒初始化守卫在 DB 路径切换后失效
- **证据**：`family.py:19`（`_FAMILY_DB_INITIALIZED`）、`conflict.py:30`（`_CONFLICT_DB_MIGRATED`）（原）。
- **分类**：确认缺陷。**触发**：同一进程内切换 family.db 路径（测试 / 多租户 / `MEMALL_DB_PATH` 变更）。
- **影响**：新库缺表，查询崩溃。
- **修复**：改为按 `resolve()` 后路径键控的 `_FAMILY_INIT_PATHS` / `_CONFLICT_MIGRATED_PATHS` 集合；保留原布尔变量以兼容
  `tests/test_federation_family.py` / `tests/test_conflict.py` 的 `patch(..., False)`；布尔为 False 时清空路径集合。另补父目录
  `mkdir` 与 `CREATE TABLE IF NOT EXISTS shared_memories`。
- **验证**：切换到全新嵌套目录的 `family2.db`，**不手动重置守卫**的情况下 health 仍自动建表成功。

### P1-3. Lark 轮询 seen 集合截断顺序不确定
- **证据**：`src/memall/lark/consumer.py:242, 275, 291-294`（原 `set(list(self._seen)[-1000:])`）。
- **分类**：确认缺陷。`set` 迭代顺序不确定，截断保留的是任意子集 → 可能重复处理/漏处理消息。
- **修复**：`_seen` 改为 `OrderedDict[str, None]`；截断用 `popitem(last=False)` 丢弃最旧的，保留最新 1000 条。
- **验证**：2500 条 → 保留 `m1500..m2499`，顺序正确。

### P1-4. `auto_resolve()` 静默自动解决冲突
- **证据**：`src/memall/federation/conflict.py:252-283`（原）；`cli/commands/federation_commands.py:160-162`。
- **分类**：设计气味（与 AGENTS.md §五 独立验收冲突）。keyword/length 启发式直接把一方标为 `superseded`，无人工确认、无审计。
- **修复**：`auto_resolve(apply=False)` 默认**干跑**，返回 `recommendations`（含双方分数与 reason）不写库；`--auto` 只出建议，
  新增 `--apply` 才提交；CLI 明确打印「No changes written. Add --apply to commit」。
- **验证**：dry-run 后 conflict 仍 `open` 且 `auto_resolved=0`；`apply=True` 才 `resolved`。协作 agent 已同步更新
  `tests/test_conflict.py` 断言 dry-run + apply 两段。

### P1-5 / P1-6 → 已并入 P0-1 / 撤销
- P1-6（health 只建 metrics 表）已在 P0-1 一并修复。
- P1-5（viewer 透传）见 §1 复核更正，撤销。

---

## 4. P2 — 设计 / 性能 / 可维护性（4 项修复 / 1 项撤销）

- **P2-1 超大文件**：`thin_waist.py`(3279) / `convergence.py`(1465) / `management_commands.py`(1449) / `src/check_fixes.py`(5347)。
  分类：设计气味。**保留未拆分**——拆分属架构重构，风险高于收益，超出「修复缺陷」范围，列为后续技术债建议。
- **P2-2 硬编码 Windows 路径**：`src/memall/tools/kg_visualize.py:41-42`（原 `C:\Users\Administrator...` / `E:\MemALL...`）。
  改为 `get_db_path()` / `Path.home()` / `Path.cwd()`；`--db` 默认改 `None`（运行时解析，不再 import 时冻结）。
- **P2-3 requirements 漂移** → §1 撤销，仅补同步注释 + 新增 `requirements-dev.txt`。
- **P2-4 版本号过期**：`src/memall/ARCHITECTURE.md:3` `v0.1.66`→`v0.1.80`；`website/index.html:33` `v0.1.68`→`v0.1.80`；
  FAQ/website README 中「v0.1.68 逐项核对」是**历史基线声明**，改为「当前 v0.1.80，数字最初于 v0.1.68 核对」以保留原意。
- **P2-5 错误 sys.path** → 已在 P0-3 移除。

---

## 5. P3 — 工程卫生（3 项修复 / 1 项受控 / 1 项已专项审计）

- **P3-1 测试依赖缺失**：新增 `requirements-dev.txt`（`-r requirements.txt` + pytest/pytest-cov/flake8/mypy/autoflake），
  与 `pyproject.toml [project.optional-dependencies].dev` 对齐；首选仍是 `pip install -e ".[dev]"`。
- **P3-2 子进程** → §1 复核「已受控」，未改动。
- **P3-3 前端/网站/插件专项审计（已完成）**：
  - `frontend/index.html`(2594 行)：`esc()` 在 55 处 `innerHTML` 插值一致使用；`localStorage` 仅存 API URL，无 token；无明显 XSS。
  - `website/assets/{dashboard,api,landing,sample-data}.js`：`esc()` 转义、SSE/fetch 用法、快照 URL 均复核；`node --check` 全通过。
  - `obsidian-plugin/main.js`：发现并修复 **P0-5**（缺 import）；另复核 `authToken` 存于插件 settings（Obsidian 本地，属预期）、
    默认网关 `9919` 与 `config.py:24` 一致（保留）。
  - `website/tools/{sync,build}.mjs`：`node --check` 通过。
- **P3-4 restore 覆盖活动 DB 不处理 WAL**：`backup_restore.py` 新增 `_sqlite_backup()`（SQLite 在线 backup API + 临时文件原子重命名），
  替换 `shutil.copy2` 的 pre-restore 备份、restore 写入与 `_vacuum_backup` fallback；移除 `import shutil`。
- **P3-5 kg_visualize 无分页**：`load_graph(max_nodes=5000, max_links=20000)`，实体/三元组查询加 `LIMIT ?`。

---

## 6. 验证汇总

| 验证手段 | 结果 |
|---|---|
| `python3 -m compileall -q src/memall` | 通过（exit 0） |
| 11 个修改 Python 文件 AST 解析 | 全部 OK |
| `node --check` × 7 个 JS/MJS 文件 | 全部 OK |
| 联邦库隔离运行时（P0-1/P0-2/P1-2/P1-4） | 6 项断言全部通过 |
| 导入/导出隔离运行时（P0-3/P0-4） | 5 项断言全部通过 |
| 备份/恢复隔离运行时（P1-1/P3-4） | 5 项断言全部通过 |
| Lark OrderedDict 截断 / kg LIMIT（P1-3/P2-2/P3-5） | 通过 |
| 现有测试 `test_level_and_owner_invariants`(3) / `test_backup_step_no_db` | 手动执行通过 |
| 完整 pytest 套件 | **未运行**（环境缺 pytest 及全部运行时依赖，按要求不安装） |

> 注：`pytest` 与 `pydantic/aiohttp/numpy/sqlite_vec/jieba/onnxruntime/tokenizers/sklearn` 均缺失。所有验证用 stub 注入 +
> 真实 SQLite 完成，覆盖了修复点的实际行为，但**不等于完整集成测试**；建议在装有 `pip install -e ".[dev]"` 的环境跑一次全量 pytest。

---

## 7. 修改文件清单（本人本次修复）

| 文件 | 涉及项 |
|---|---|
| `src/memall/federation/health.py` | P0-1 / P1-6 |
| `src/memall/federation/family.py` | P0-2 / P1-2 / 健壮性 |
| `src/memall/federation/conflict.py` | P1-2 / P1-4 / 健壮性 |
| `src/memall/cli/import_data.py` | P0-3 / P2-5 |
| `src/memall/cli/export.py` | P0-4 / P1-1 |
| `src/memall/cli/backup_restore.py` | P1-1 / P3-4 |
| `src/memall/cli/main.py` | P1-4（`--apply`） |
| `src/memall/cli/commands/federation_commands.py` | P1-4 |
| `src/memall/pipeline/backup.py` | P1-1 |
| `src/memall/lark/consumer.py` | P1-3 |
| `src/memall/tools/kg_visualize.py` | P2-2 / P3-5 |
| `src/memall/ARCHITECTURE.md` | P2-4 |
| `website/index.html` / `website/README.md` | P2-4 / P3-3 |
| `obsidian-plugin/main.js` | **P0-5** |
| `requirements.txt` | P2-3（同步注释） |
| `requirements-dev.txt`（新增） | P3-1 |
| `DEEP_AUDIT_REPORT.md`（本报告） | 交付物 |

> 工作区另有其他 agent 的并发改动（`AGENTS.md` 部署更正、`gateway_bus.py`、`mcp_identity.py`、`ops_facts`、网关/SSE 总线合并、
> `tests/test_conflict.py` / `tests/test_backup.py` 的配套断言更新等）及运行时文件（`.gateway-token`、`gateway.pid`、
> `keepalive.pid`、`run_gateway.py` 等）。这些**非本次修复产生，未触碰、未提交**。其中两个测试文件的改动与本次修复**互补且兼容**。

---

## 8. 剩余建议（未纳入本次修复）

1. 在装有依赖的环境跑 `pip install -e ".[dev]" && pytest -q` 做完整回归。
2. 更新 `AGENTS.md §1.1` 看板：P0-4b「未做」与当前 `thin_waist.py` 代码不符（§1 已说明）。
3. P2-1 超大文件拆分属架构重构，建议单独立项、配套测试后分批进行。
4. 统一网关端口口径：`config.py`/`gateway.py` 文档写 9919，CLI `serve/start-gateway/quickstart` 默认 9920，
   `gateway_commands.py` 两处分别 9919/9920——目前能跑但易误导，建议统一为一个常量。
5. `src/memall/mcp/http_transport.py:451` 与 `gateway_mcp_handlers.py:223` 硬编码 CORS 源 `http://127.0.0.1:9876`，
   与 `gateway_utils.py` 的允许清单不一致，建议统一到 `origin_allowed()`。

---

*阶段一审计 + 阶段二修复，2026-11-08，DeepSeek Harness Agent（Team Lead）。未提交任何 commit。*
