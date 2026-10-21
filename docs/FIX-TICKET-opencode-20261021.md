# FIX TICKET — 2026-10-21 — opencode

> 发现于：全量读取生产库（5,477 条记忆）过程中实证复现
> 关联：#999743（诊断记忆）、#999766（P0 派单卡）、#999768（P1 派单卡）、#14417（端到端实测方法论）
> 执行方：codex（EM）｜优先级：P0 + P1｜需老陈确认（P0）

---

## 摘要

2026-10-21 的全量读取暴露出两个此前未被发现的契约缺陷：

| 编号 | 缺陷 | 位置 | 级别 |
|---|---|---|---|
| A | `reflect_step` **整列覆盖** `metadata`，并**绕过 level 钉住** | `src/memall/pipeline/reflect.py:97` | **P0** |
| B | `CaptureInput` 未声明 capture 支持的 8 个字段，校验器在 handler 前**静默剥离** | `src/memall/mcp/models.py:8` | **P1** |

两者同源：**pydantic 模型没声明的字段，在到达业务逻辑之前就被上游层扔掉了。**
Oct 20 的审查（`203f03a`）修好了**读侧**（`VectorSearchInput` / `UnifiedSearchInput` 的 `project`/`category`/`level`），**写侧漏修**。

---

## 缺陷 A（P0）：`reflect_step` 覆盖 metadata + 绕过钉住

### 根因

`src/memall/pipeline/reflect.py:97`

```python
meta = {"l6_source": "reflect_step", "quality": quality}
conn.execute("UPDATE memories SET level='L6', summary=?, metadata=?, updated_at=? WHERE id=?",
             (new_summary, json.dumps(meta), now, r["id"]))
```

`meta` 是**新建**的对象，不是原 `metadata` 的合并结果 → 整列覆盖。
同时这里**没有**调用 `is_field_pinned(meta, "level")`。

对比：`classify.py:302-314` 与 `event_processor.py:185-191` **都**正确检查了 `is_field_pinned`。
`pins.py` 的文档写的是"both classifier paths … never overwrite what was pinned" —— 但 `reflect_step` 是**第三条**会改 `level` 的路径，漏了。

### 三个后果

**1. 覆写调用方全部 metadata。**
实测 `#999743`（本 ticket 的证据记忆）：

| 时点 | level | metadata 键 |
|---|---|---|
| capture 落库后 | `L4` | `pinned_fields, measurement, links, source, enrich, …` |
| 管线跑完后 | `L6` | `l6_source, quality, enrich, procedure` ← `pinned_fields`/`measurement`/`links`/`source` 全部消失 |

`summary` 也被替换成模板文本：`[L6 反思 架构决策] 我注意到之前有个地方需要调整：…`

**2. 绕过 level 钉住 → P1-16 的保护是自毁的。**
`203f03a` 用 `metadata.pinned_fields` 保护调用方显式选择的 `category`/`level`。
但 `reflect_step` 第一次跑就把 `pinned_fields` 抹掉 → **之后 `classify` 又能改写 `category`/`level`**。
即：钉住保护只存活一个管线周期。

**3. 直接摧毁 L5 任务链。**
`notify_pending_tasks` 的查询条件是 `json_extract(metadata,'$.status')='active'`。
`reflect_step` 一旦命中，`status` 连同整个 `metadata` 一起被抹掉 → 任务从待办链消失。

### 生产影响实测

- `level='L6'` 且带 `l6_source=reflect_step` 的行：**595 条**
- 其中 `metadata` 只剩 reflect 两个键（原有 `enrich` 等全部丢失）的：**404 条**
- `json_extract(metadata,'$.status')='active'` 的行：**40 条**
- 用 `reflect.py` 的**真实**关键词表与选取条件（`level NOT IN ('L6','L7','L9','L11') AND stream='knowledge'` + `CORRECTION_KEYWORDS`/`POSITIVE_KEYWORDS`）模拟下一次 reflect：

| ID | level | category | agent | 命中 | 后果 |
|---|---|---|---|---|---|
| 999016 | L5 | task | dsh | `修正` | → L6 + 丢 `status` → `notify_pending_tasks` 不再返回 |
| 999766 | L5 | task | codex | `修正` | 同上（本 ticket 的 P0 派单卡） |
| 999768 | L5 | task | codex | `修正` | 同上（本 ticket 的 P1 派单卡） |

> **999016 是 dsh「第3批检索五修」的任务卡**。
> Oct 20 验收清单第 10 条（"task survives as L5/task/active; notify_pending_tasks returns it"）**在验收跑里通过，生产跑一轮就回归**。

### 修复要点

1. `reflect_step` 改为**合并** metadata：读出原 `metadata`，只新增/覆盖 `l6_source` 与 `quality`，**不得丢任何既有键**。
2. 更新 `level` 前检查 `is_field_pinned(meta, "level")`；被钉住时**只**更新 `summary` 与 `metadata`，不改 `level`。
3. 历史补回：给已损坏的 404 条行出一份一次性迁移脚本（从 `archive.db` / 备份还原原 metadata，或至少补回 `status` / `pinned_fields` 等契约键）。**动数据前先 `memall backup`**。
4. `check_fixes.py` 新增两条守卫：
   - 被钉住的 `level` 经完整管线后不变
   - 被钉住行的 metadata 键集合在管线前后**不减少**

---

## 缺陷 B（P1）：`CaptureInput` 未声明 capture 支持的 8 个字段

### 根因

| 层 | 位置 | 声明的字段数 |
|---|---|---|
| handler | `src/memall/mcp/tools/capture.py` `_CAPTURE_FIELDS` | **18** |
| pydantic 模型 | `src/memall/mcp/models.py` `CaptureInput` | **10** |
| 校验器 | `src/memall/mcp/validator.py:199` `model_dump(exclude_unset=True)` | 把未声明字段静默丢弃（pydantic v2 默认 `extra='ignore'`） |

`capture.py` 里的 `payload = {k: v for k, v in arguments.items() if k in _CAPTURE_FIELDS}` 本来会转发这 8 个字段 —— 但字段在到达 handler **之前**已被删除。

### 实测（直接调用 `validate_tool_input`，未写库）

```
surviving keys : ['action', 'category', 'content', 'level']
DROPPED        : ['confidence', 'creator', 'occurred_at',
                  'source_ids', 'stream', 'supersedes', 'tags', 'visibility']
```

这 8 个**全部**在 `_CAPTURE_FIELDS` 里，也**全部**是 `core.models.MemoryInput` 的合法字段 —— 存储层完全支持，只是被中间层剥掉了。

### 影响

| 字段 | 后果 |
|---|---|
| `visibility` | 调用方设的 `shared`/`public` 静默变 `private`（fail-safe，但共享与联邦流程失效） |
| `confidence` | 静默用默认 `0.5`，调用方的质量信号丢失 |
| `tags` | 调用方标签被自动派生标签取代。实测：传 `tags=[数据质量,诊断,…]` 被忽略，落库变成 `["SQLite","L6","MCP","REAL_CONTENT","L9","L4","L2","L11"]` → `memall_read` 的 exact-tag 过滤对所有 MCP 写入的记忆无效 |
| `occurred_at` | 事件时间被重置为 `now`，历史回填不可用 |
| `supersedes` / `source_ids` | 溯源边丢失 |
| `creator` | 署名丢失 |

### 修复要点

1. `CaptureInput` 补声明这 8 个字段（或改为"校验器保留未声明但 `_CAPTURE_FIELDS` 支持的字段"，二选一并说明取舍）。
2. 新增守卫：`CaptureInput` 声明字段集 ⊇ `capture._CAPTURE_FIELDS`，且 `MemoryInput` 支持这些字段。
3. **把 `203f03a` 验收第 [1] 条的验收标准从"ignored_fields 被如实上报"改成"被声明支持的字段真的生效"** —— 前者正是漏掉写侧的原因。

---

## 验收标准（合并）

1. 造一条 `level=L5` / `category=task` / `metadata.status=active` 的记忆，跑**完整管线**，结束后：level 仍 `L5`、category 仍 `task`、`status` 仍 `active`、`pinned_fields` 仍在，且 `notify_pending_tasks(agent)` 仍能返回它。
2. 造一条 `level=L4` / `category=decision` / `pinned_fields=['category','level']` 的记忆，跑**完整管线**，结束后：level 仍 `L4`、category 仍 `decision`、钉住与调用方自定义 metadata 键均未丢失。
3. `memall_write action=capture` 传齐 B 的 8 个字段，落库后逐字段比对，全部与传入值一致；`ignored_fields` 不再包含它们。
4. `check_fixes.py` 三条新守卫全绿。
5. **必须用真实 MCP 协议路径（`handle_call` + 长驻 server）验证，不只跑进程内 pytest** —— 沿用 #14417 的教训：长驻进程不重启、SQL 形态语义、变量赋值错位、执行顺序类 bug 只有真实协议路径能抓。

---

## 交付物

- 代码改动 + commit
- `check_fixes.py` 新守卫
- 上述第 1/2/3 条断言的**真实回显**
- 历史 404 条的补回方案与可执行脚本

**deadline**: 2026-10-22 18:00

---

## 协调事项（开工前必读）

**本仓库在 2026-10-21 00:50–01:00 存在其他 agent 的未提交改动**（`git status` 可见）：

```
 M src/memall/core/thin_waist.py   ← update() 里 fields.pop("__extra_args__", None)
 M src/memall/mcp/adapter.py       ← __extra_args__ 只在 action=='capture' 时注入
 M src/memall/mcp/models.py        ← UpdateInput 补 metadata 字段
```

这三处是在修**第三个任务链缺陷**：`UpdateInput` 没声明 `metadata` → 校验器剥离 → 执行方无法把任务标记为 done（任务永远留在 `pending_tasks`）。
配套 E2E 脚本：`%TEMP%\upd3.py`（probe3）。

**与本 ticket 的关系：互补，不重叠，但有同文件风险。**

| 缺陷 | 谁在修 | 涉及文件 |
|---|---|---|
| A：`reflect_step` 抹掉 `metadata`（含 `status`/`pinned_fields`） | **本 ticket → codex** | `pipeline/reflect.py`（+ `check_fixes.py`） |
| C：`UpdateInput` 缺 `metadata`，任务无法置 done | 其他 agent（在途） | `mcp/models.py` |
| B：`CaptureInput` 缺 8 个字段 | **本 ticket → codex** | `mcp/models.py` ⚠️ |

⚠️ **缺陷 B 与在途改动同在 `mcp/models.py`**。开工前先 `git diff` 看清楚他人未提交的内容，**不要用 `git commit -a`**（会把别人的在途改动一起提交），只提交自己碰的行。

三条缺陷合起来是一条完整的链：
**校验器剥离 → 写不进 → 读不回 → 任务链断。**
C 修「写不进」，A 修「被抹掉」，B 修「另一组字段写不进」。三条都要修，否则任务链仍会断。

---

## 附：证据导出

全量读取的原始导出在 `E:\MemALL\_memory_export\`（未改动任何原始数据）：

| 文件 | 内容 |
|---|---|
| `memories_full.md` | 全量 5,477 条，按层级分组 |
| `memories_full.jsonl` | 全部 39 字段原始数据 |
| `REAL_CONTENT.md` | 去除样板噪声后的 4,028 条实质记忆 |
| `level_L1.md` … `level_L11.md` | 按层级拆分 |
| `TOP_L4/L5/L6/L9/L11/P2.md` | 按 echo_score 排序的高价值条目 |
| `ACTIVE_TASKS.md` | 18 条 active L5 任务全文 |
| `READ_ALL_REPORT.md` | 完整分析报告（噪声 26.4% / L6 高质量 0 条 / 40% 孤立记忆） |
| `FINAL_STATE2.txt` | 系统状态快照 |
