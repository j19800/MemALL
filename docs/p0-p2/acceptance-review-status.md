# 验收与代码审查现状（2026-11-10）

> **文档状态**：现行。回答用户三问：「所有功能测试都跑了吗？代码审查做了吗？符合验收标准吗？」——不粉饰。
> 本文件在「按蓝图补齐」提交 `f1af71b` 之后、用户质疑后补做审查复核并修复，记录全过程。

## 1. 测试覆盖

| 项 | 事实 |
|---|---|
| 测试文件 | `tests/` 下 86 个 `test_*.py` |
| 默认收集 | `pytest tests/` → **612 个测试**（`tests/conftest.py` 的 `collect_ignore` 排除 3 个顶层脚本 `smoke_test.py`/`stress_test.py`/`quality_test.py`；`benchmark.py` 不匹配默认命名，从未被收集） |
| 全量回归 | 修复前 **610 passed, 2 skipped, 0 failed**；修复后全量见 §5 |
| 是否漏跑 | **未漏**：默认收集数 612 = 610+2；此前几轮全量命令里手动 `--ignore` 的文件与 conftest 既有排除重合（无害）；2 个 skip = `test_embeddings.py` 缺 `sentence_transformers` 的条件跳过，与改动无关 |
| 新增功能测试 | `tests/test_blueprint_completion.py`（复核后 15）、`tests/test_utility_rerank_p3.py`（10）、`tests/test_kb_enrich_worker_p2.py`（20）、`tests/test_capture_quality_workline.py`（6）等，均在默认收集内 |

⚠️ 但「全量回归绿」≠「每个验收标准都有严格测试」=「代码审查已做」。见 §2/§3。

## 2. 代码审查：此前确实没做，本轮补做

- **此前多轮**：是「自己写、自己测、自己提交」，**没有做独立代码审查**（无第二视角、无 adversarial review）。这是真实缺口，不能声称「已做审查」。
- **本轮补做**：启动 2 个独立 subagent 做审查复核。两个 agent 均中途失败（未产出完整分级报告），但**第二个留下了一条已用脚本坐实的【严重】回归**（见 §4），这就是独立审查的价值——它抓到了主 agent 自查没发现的问题。
- 审查结论：不能声称「通过独立代码审查」（agent 未完整收尾），但**它报的问题已被主 agent 复现、修复、并写成回归测试**。其余清单项由主 agent 逐项实测（见 §4），未发现其它未修的【严重】问题。

## 3. 验收标准逐条实测（关键：不靠自己写的测试自证）

主 agent 用独立脚本逐条跑 ADR 0028 §2 的 5 条验收句：

| 阶段 | 验收句要点 | 初次实测 | 修复后 |
|---|---|---|---|
| P0 | retrieve/unified_search JSON 含 `metadata.slot_key`/`resolve.status` | ✅ 通过 | ✅ |
| P1 | continuity_pack 契约字段 + `kb_excluded` + KB 不进包 | ✅ 通过 | ✅ |
| P2 | KB ingest provenance / `source_pipeline` 过滤 / 不阻塞 | ✅ 通过 | ✅ |
| P3 | **`tracking_enabled=True` 时 retrieve 排序受 utility 影响，关闭无影响** | ❌ 未通过：仍需调用方传 `utility_rerank=True` | ✅ 已修（`_utility_tracking_active()` 主开关，config-only 全入口生效；实测 fts5 JSON 带 `utility_rerank_signal`） |
| P4 | **维护后 `metadata.resolve.status`、`memory_status` 按规则更新；归档在 `ADR/archived_sessions/`；`include_archived=True` 可读；无静默删除** | ❌ 未通过：只加 `cognitive_maintenance` marker，两个状态字段都没改 | ✅ 已修（`_policy_status_rules()` + `_mark()` 按规则写值；实测 ACCEPTED→REVIEW、ACTIVE→archived、rollback 精确恢复） |

### 诚实结论（修复前）
之前把 P3 的「仍需 flag」和 P4 的「非破坏性标记」登记成「已知偏差 / 刻意更保守的取舍」，并据此声称「蓝图全部实施完毕、符合验收」——**这是自证，不成立**。按 ADR 原文，这两条当时是**验收未通过**。

## 4. 审查/复核发现并修掉的真实缺陷（共 5 个）

1. **【验收不符·P3】** config 不是主开关 → 新增 `thin_waist._utility_tracking_active()`，在 unified_search / enhanced_search / `_wants_enhancements` / apply_enhancements 让 config 单独生效；严格测试改为 config-only 无 flag + 三处入口断言。
2. **【验收不符·P4】** 维护只加 marker 不改状态 → 新增 `_policy_status_rules()` 按规则写 `resolve.status`/`memory_status`（保留 before 快照可回滚）；测试用与规则目标**不同**的 before 值（ACCEPTED / ACTIVE）严格断言更新 + 回滚。
3. **【严重·独立审查报出】** config-only 让 fts5 改走 `enhanced_search`，后者把 viewer 身份当**作者内容过滤**，author1 写的 2 行被 viewer1 检索时召回变 0（开关改变了召回集合，违反「只影响排序」）。修法：`enhanced_search(viewer_is_identity=...)` 显式区分 viewer/作者过滤；`unified_search` 与 `handle_retrieve` 增强路径传 True，并把 `agent_name` 同步到 `viewer` 供 trust 过滤；`handle_retrieve` plain 路径也统一语义。**既有直接调 `enhanced_search(agent_name=X)` 的 legacy fallback 语义保留**（默认 False）。
4. **【中等·主 agent 复核】** `_ensure_interaction_columns` 用 `r["name"]` 读 PRAGMA，在普通 sqlite3 连接（tuple 行）上抛 TypeError 被 `except: return` 吞掉 → **老库永远补不上 8 个新列**。修：兼容 tuple/Row 两种形态 + 幂等回归测试。
5. **【中等·SQL】** `maintenance._select_candidates` 的 `conflict_review` 查询 `A OR B AND archived过滤` 缺括号（AND 优先级高）→ 已归档 CONFLICT 行被重新选中。修：加括号。

另修：MCP `handle_retrieve` 增强结果缺 **per-result** utility 信号（只有顶层 map）→ 已挂到每个 item，与 fts5 payload 一致。
另修：`tests/test_blueprint_completion.py` 里 2 个「假通过」断言（P3 验收测试仍传 flag、P4 回滚断言带 `or ...=={}` 永真分支）→ 改严。

## 5. 第二轮独立审查（用户「再跑一次独立审查」）
- 启动 2 个窄任务 subagent（正确性/安全性、验收/测试质量），**仍均未完整收尾**（消息通道无法催办）。
- 兜底：lead 以第二视角逐项实测，产出完整分级报告 `independent-review-report.md`：
  - 【严重】无（当前工作区）；【中等】1 项且为 `retrieve()` 基线既有信任过滤（非本轮引入，登记不改）；【轻微】3 项（audit 投影不含 content、子开关 True 被双门拦截、subagent 流程缺口）。
  - 6 类正确性/安全性检查点全部通过；ADR 5 条验收句全部真实通过；config 开关不改变召回集合。
  - 测试：默认收集 615 = 613 passed + 2 skipped，无漏跑/无假通过；提交态导出 492 文件自洽。
- 结论：**代码通过正确性与安全性审查、5 条验收句全部通过、测试覆盖完整**；保留项为「严格双人独立签字」未达成（subagent 未收尾）与既有信任过滤负债。

## 6. 已闭环
- [x] 修复后全量回归（`pytest tests/`，默认收集）→ 613 passed/2 skipped/0 failed/0 errors。
- [x] 所有修复 + 文档落地本地 commit `5bc7198`（不 push）。
- [x] 第二轮独立审查（见 §5 + `independent-review-report.md`）。

## 6. 已知偏差（非验收不符，透明登记）
- P3 配置键前缀用 `quality.utility.*`（蓝图写顶层 `utility.*`），但 `tracking_enabled` 验收键按字面保留且已严格生效。
- P1 `conversation.session_end` 注入为 opt-in（默认仍用既有 `session.end`），避免改变线上默认行为。
- P4 自动动作仍是非破坏性（不删行、不改 content），只是现在会按规则更新状态字段。
- 独立审查 agent 未完整收尾：结论以「已复现并修复其报出的问题 + 主 agent 逐项实测」为准，不夸大为「全套审查通过」。

## 7. 第三轮：换攻击面复现式审查（2026-11-10 晚）
用户要求「换一种审查方式，看是否有什么缺陷、安全性问题」。方法从「扫检查清单」改为「把每个接口当攻击面，写最小复现实锤」。2 个 subagent 仍未完整收尾，其一坐实了一条假通过断言。结果：**又挖出 4 个前两轮清单式审查漏掉的真缺陷（共 6 个修复，其中 2 个可实锤的严重安全项）**。

| # | 级别 | 缺陷 | 复现证据 | 修复 |
|---|---|---|---|---|
| 1 | 严重(测试) | `assert "applied" in up` 对 `{'applied':False}` 也通过 | policy=`{'applied': False}` 时断言仍真 | 双门ON断 `is True`+engine+字段；负面控制断 `is False` |
| 2 | 严重(召回) | 显式 `project` 在增强路由被吃掉 | plain n=1 vs enhanced n=2 | `enhanced_search` 重新注入 retrieve 过滤 |
| 3 | 中(审计) | `_resolve_decision` 早退路径无 `utility_policy` | NOOP 路径缺字段 | 所有返回路径统一挂载 |
| 4 | **严重(安全)** | `rollback_from_audit(entry)` 信任 caller `before` = 任意写 | forged `{pwned:true,archived}` 改写任意行成功 | 快照仅取服务端审计日志定位；caller `before` 忽略；CAS 陈旧拒绝；拒绝回滚rollback；恢复前校验 |
| 5 | 严重(授权) | `actions=["expire"]` 越过 `expire=False` 门 | 记忆 ACTIVE→archived | caller 只可缩小不可放大；`skipped_unauthorised`；无授权 ran=False |
| 6 | **严重(安全)** | `read_audit` 经 MCP `mode=audit` 跨身份泄露 metadata 快照 | 响应含 `api_key`+victim 正文 | `redact=True` 脱敏投影+`metadata_sha256` 指纹；`redact=False` 仅受信内部；4MB 尾读防 OOM |

信任模型修正后性质：**可请求（限运维授权集）/ 可定位（凭日志标识）/ 不可注入（快照只从服务端日志）/ 不可窥探（默认脱敏）**。
验证：`test_blueprint_completion.py` **19 passed**（+4 新安全/回归测试，重写 2 个）；MCP mode=audit 复验无泄露；identifiers 回滚仍精确恢复；project 过滤三路由一致；config 门不可越过。全量数字见 PRECOMMIT 第三轮条目。
登记（非缺陷）：`_paths()` 相对锚为 cwd（生产用绝对路径）；审计无轮转；rollback 定位限于尾窗口（超窗保守拒绝=fail-safe）。

### 补充：第 7 项（授权边界缺失，已修）
`memall_system action=maintenance` 写入子动作原本无身份校验（任意 MCP 调用方可触发归档/写锚点，实测）。现要求 supervisor 或 human_owner；只读 `status`/`audit` 开放。新增回归测试；`test_blueprint_completion.py` **20 passed**。
