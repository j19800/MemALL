# 技术负债清单

更新时间：2026-06-26（v2 — 8/13 S0 已修复）
基础版本：0.1.12
代码行数：28,057（156 文件）

## 负债分类规则

| 等级 | 标签 | 定义 | SLA |
|------|------|------|-----|
| 🔴 S0 | Critical | 安全漏洞、数据丢失、运行时崩溃 | 24h |
| 🟠 S1 | Major | 性能瓶颈、设计缺陷、风险暴露面 | 1 周 |
| 🟡 S2 | Minor | 命名、死代码、可读性 | 按需 |
| 🔵 S3 | Architecture | 架构演进方向，非紧急 | 迭代规划 |

## 模块索引

| 前缀 | 模块 | 文件数 | 行数 | S0 | S1 | S2 | S3 |
|------|------|--------|------|----|----|----|-----|
| CORE | core/ | 7 | 2,465 | 2→1 | 7 | 4 | 2 |
| PL | pipeline/ | 40 | 11,528 | 5→3 | 12 | 6 | 4 |
| MCP | mcp/ | 12 | 4,193 | 3→2 | 4 | 3 | 1 |
| GW | gateway | 1 | ~2,500 | 3→0 | 3 | 2 | 1 |
| CLI | cli/ | 7 | 6,800 | 0 | 1 | 2 | 2 |
| TST | tests/ | ~30 | ~3,000 | 0 | 2 | 4 | 0 |
| SRH | search/ | 4 | 401 | 0 | 2 | 1 | 2 |
| GRP | graph/ | 5 | 753 | 1→0 | 1 | 1 | 1 |
| BRG | bridge/ | 7 | 631 | 0 | 1 | 1 | 0 |
| **合计** | | | **~28,057** | **13→0** | **33** | **24** | **13** |

---

# 🔴 S0 — Critical（13 项）

## S0-001 运行时错误：session_end 引用未定义变量 ✅
- **模块：** PL (session.py:770)
- **发现时间：** 2026-06-26
- **描述：** 重复的 `if count > 3` 块引用 `session_project` 变量导致 NameError，被外层 `except Exception` 静默吞掉。
- **修复方式：** 移除重复代码块，`session_project` 在 `_harvest_session()` 内定义和使用。
- **修复时间：** v0.1.11
- **状态：** ✅ 已修复

## S0-002 外键约束全局禁用 ✅
- **模块：** PL (distill.py:15)
- **发现时间：** 2026-06-26
- **描述：** `PRAGMA foreign_keys=OFF` 在 distill 步骤开始前全局关闭外键约束，无 try/finally 恢复。崩溃后约束永久失效，允许孤立边缘记录。
- **修复方式：** try 前保存状态，finally 恢复。
- **修复时间：** v0.1.11
- **状态：** ✅ 已修复

## S0-003 API 认证绕过：`/api/*` 全部无需认证 ✅
- **模块：** GW (gateway.py:239-240)
- **发现时间：** 2026-06-26
- **描述：** `_auth_middleware` 中 `if request.path.startswith("/api/"): return await handler(request)` 绕过所有 `/api/*` 端点的认证。
- **修复方式：** 改为仅 GET/HEAD /api/* 免认证，POST/PUT/DELETE 需 Bearer token。
- **修复时间：** v0.1.11
- **状态：** ✅ 已修复

## S0-004 凭据泄露：`/pair` 返回 master token ✅
- **模块：** GW (gateway.py:237+1998)
- **发现时间：** 2026-06-26
- **描述：** `/pair` 端点无认证，且在配对响应中返回 `self._auth_token`（完整的 64 位 hex 凭证）。
- **修复方式：** 移除配对响应中的 token 字段。
- **修复时间：** v0.1.11
- **状态：** ✅ 已修复

## S0-005 参数注入 DoS：`int()` 裸转换 ✅
- **模块：** GW (gateway.py:980,1204,1225,1252,1620)
- **发现时间：** 2026-06-26
- **描述：** `int(request.query.get("days", 30))` 无 try/except，非数字输入导致 `ValueError` → 500 错误。共 5 处。
- **修复方式：** 统一使用 `_safe_int()` 替代裸 `int()`。
- **修复时间：** v0.1.11
- **状态：** ✅ 已修复

## S0-006 MCP HTTP 无认证 ✅
- **模块：** MCP (http_transport.py:63-66)
- **发现时间：** 2026-06-26
- **描述：** `/mcp` HTTP 端点（端口 9876）不检查 Bearer token、Origin、IP。
- **修复方式：** 新增可选 Bearer token 中间件（`MEMALL_MCP_TOKEN`）。
- **修复时间：** v0.1.11
- **状态：** ✅ 已修复

## S0-007 UUID 截断导致碰撞 ✅
- **模块：** PL (session.py:353)
- **发现时间：** 2026-06-26
- **描述：** `str(uuid.uuid4())[:8]` 将 128 位 UUID 截断到 32 位空间。
- **修复方式：** 使用完整 uuid4。
- **修复时间：** v0.1.13
- **状态：** ✅ 已修复

## S0-008 O(n²) 相似度遍历无上限 ✅
- **模块：** PL (link.py:113-151)
- **发现时间：** 2026-06-26
- **描述：** 嵌套循环 `for i in range(len(rows)): for j in range(i+1, len(rows))` 遍历全部记忆计算 Jaccard。
- **修复方式：** 添加 `ORDER BY id LIMIT 2000` 限制候选集。
- **修复时间：** v0.1.12
- **状态：** ✅ 已修复

## S0-009 N+1 边缘计数 ✅
- **模块：** PL (classify.py:203-209)
- **发现时间：** 2026-06-26
- **描述：** 每行候选记忆额外执行 `COUNT(*) FROM edges`。
- **修复方式：** 预聚合 `GROUP BY source_id` 一次性查完。
- **修复时间：** v0.1.13
- **状态：** ✅ 已修复

## S0-010 全表加载富化 ✅
- **模块：** PL (enrich.py:84)
- **发现时间：** 2026-06-26
- **描述：** `SELECT ... FROM memories WHERE level != 'P0' ORDER BY id` 无 LIMIT，全表加载到内存。
- **修复方式：** 添加 `ORDER BY id LIMIT 2000`。
- **修复时间：** v0.1.12
- **状态：** ✅ 已修复

## S0-011 O(n²) 自适应去重 ✅
- **模块：** PL (adaptive.py:118-163)
- **发现时间：** 2026-06-26
- **描述：** 压缩模式嵌套 O(n²) Jaccard 对比，全表加载。
- **修复方式：** 添加 `LIMIT 5000` 限制候选集。
- **修复时间：** v0.1.13
- **状态：** ✅ 已修复

## S0-012 数据模型与 schema 不一致 ✅
- **模块：** CORE (models.py + thin_waist.py)
- **发现时间：** 2026-06-26
- **描述：** `Memory` dataclass 缺少 `thread_id` 和 `agent_name_locked` 字段。
- **修复方式：** 补全 dataclass 字段 + `_row_to_memory` 同步添加。
- **修复时间：** v0.1.13
- **状态：** ✅ 已修复

## S0-013 Embedding 模块静默失败 ✅
- **模块：** GRP (embeddings.py)
- **发现时间：** 2026-06-26 (来自内部已知issue)
- **描述：** `sentence_transformers` 缺失时 `_auto_embed` 被 `except Exception` 吞掉异常。
- **修复方式：** 移除 `_vec0_upsert` 和 `_auto_embed` 中的异常吞噬，异常正确传播给调用方。
- **修复时间：** v0.1.12
- **状态：** ✅ 已修复

---

# 🟠 S1 — Major（33 项）

## CORE 层（7 项）

### S1-CORE-01 配置写入非原子
- **文件：** config.py:288-290
- **描述：** `save_config()` 直接写文件无 temp+rename，崩溃时 config.json 损坏。
- **预估工时：** 15 分钟

### S1-CORE-02 pool_conn 自动 COMMIT 覆盖回滚
- **文件：** db.py:517-518
- **描述：** 上下文退出时无条件 `conn.commit()`，调用方意图回滚时被覆盖。
- **预估工时：** 10 分钟

### S1-CORE-03 LOWER() 阻止索引
- **文件：** context_assembler.py:10,33,48
- **描述：** `WHERE LOWER(agent_name)=LOWER(?)` 使 `idx_memories_agent` 索引失效。
- **预估工时：** 30 分钟

### S1-CORE-04 硬编码日期
- **文件：** thin_waist.py:1064
- **描述：** `if created[:10] > "2026-06-15"` 会在 7 月后失效。
- **预估工时：** 5 分钟

### S1-CORE-05 冷启动 N+1
- **文件：** context_assembler.py:38-39,52
- **描述：** contradictions 循环内逐条查 DB。
- **预估工时：** 30 分钟

### S1-CORE-06 health.py 模块级 NOW 冻结
- **文件：** health.py:16
- **描述：** `NOW = datetime.now(timezone.utc)` 在 import 时固定不变。
- **预估工时：** 5 分钟

### S1-CORE-07 nlp.py 丢弃单字 CJK
- **文件：** nlp.py:41
- **描述：** `len(t) > 1` 过滤掉如"大""高""新"等有意义的单字 CJK 词，降低 TF-IDF 质量。
- **预估工时：** 10 分钟

## PL 层（13 项）

### S1-PL-01 classify 阈值形同虚设
- **文件：** classify.py:56
- **描述：** `_LAYER_SCORE_THRESHOLD = 2` 但权重最低 45 → 从未触发。
- **预估工时：** 15 分钟

### S1-PL-02 distill PRAGMA 关闭外键
- **文件：** distill.py:15
- **描述：** 同 S0-002，重复列出以确保追踪。

### S1-PL-03 distill 无 LIMIT
- **文件：** distill.py:18
- **描述：** `ORDER BY agent_name, category, created_at` 无 LIMIT，全表扫描。
- **预估工时：** 15 分钟

### S1-PL-04 echo OFFSET 分页漂移
- **文件：** echo.py:122-176
- **描述：** 越往后页面越慢，改用游标分页。
- **预估工时：** 30 分钟

### S1-PL-05 echo 逐条边缘计数
- **文件：** echo.py:135-138
- **描述：** 每行额外 `COUNT(*) FROM edges`，类似 S0-009。
- **预估工时：** 30 分钟

### S1-PL-06 pipeline 每步双连接
- **文件：** pipeline.py:69-81
- **描述：** 25 步 × 2 次连接 = 50 次，每步前后各统计一次。
- **预估工时：** 1 小时

### S1-PL-07 pipeline 组件注册硬编码
- **文件：** pipeline.py:348-359
- **描述：** `if step_name == "reflect" and not include_reflect` 每新增一步需要加 if。
- **预估工时：** 2 小时（需重构）

### S1-PL-08 集成候选类别逻辑复杂
- **文件：** integrate.py:139-147
- **描述：** 回退路径排序+切片+检查，无错误也能跳过。
- **预估工时：** 30 分钟

### S1-PL-09 反射层中文分割不可靠
- **文件：** reflect.py:146
- **描述：** `text.split()` 在中文上行为不确定，影响 contradiction 检测。
- **预估工时：** 1 小时

### S1-PL-10 硬编码 50/100 评分常数
- **文件：** echo.py:140,148
- **描述：** `min(1.0, edge_count / 50.0)` 应根据实际分布配置。
- **预估工时：** 15 分钟

### S1-PL-11 convergence 正则错误
- **文件：** convergence.py:407
- **描述：** `\[??\]` 中 `?` 使 `\[` 可选，匹配了 `]` 而非预期。
- **预估工时：** 10 分钟

### S1-PL-12 多处 N+1
- **文件：** reflect.py:26-35 等
- **描述：** 每 agent 单独 COUNT，50 agents = 50 次查询。
- **预估工时：** 30 分钟

### S1-PL-13 forget_l5_archive 无 LIMIT
- **文件：** forget.py:318
- **描述：** `SELECT ... WHERE level='L5'` 无 LIMIT，全表加载。
- **预估工时：** 15 分钟

## MCP/Gateway 层（7 项）

### S1-MCP-01 Hub 消息内容未清理
- **文件：** federation_tools.py:632-638
- **描述：** Hub 外部 agent 的 content 拼接进消息，无 sanitize。
- **预估工时：** 15 分钟

### S1-MCP-02 UDP socket FD 泄漏
- **文件：** gateway.py:2341-2372
- **描述：** `discover_peers()` 二次 bind 失败时 socket 未 close。
- **预估工时：** 10 分钟

### S1-MCP-03 ThreadPool 永不 shutdown
- **文件：** http_transport.py:21-23,43-46
- **描述：** 模块级 executor 在 shutdown 时不清理。
- **预估工时：** 15 分钟

### S1-MCP-04 工具输入验证不一致
- **文件：** adapter.py, gateway.py 多处
- **描述：** MCP tool 有 Pydantic 校验但 gateway REST 没有，重复实现。
- **预估工时：** 3 小时

### S1-MCP-05 遍历 depth 无上限
- **文件：** gateway.py:1915-1923
- **描述：** 用户可传 `depth: 9999` 导致 BFS 爆炸。
- **预估工时：** 5 分钟

### S1-MCP-06 client_max_size 未显式设置
- **文件：** gateway.py:209, http_transport.py:313
- **描述：** 依赖 aiohttp 默认值（不同版本不一致）。
- **预估工时：** 5 分钟

### S1-MCP-07 导入路径不安全
- **文件：** gateway.py:2237
- **描述：** import_bundle 路径校验在 Windows 上大小写敏感。
- **预估工时：** 15 分钟

## CLI/Tests 层（3 项）

### S1-CLI-01 archive/ 目录含 60+ 调试脚本
- **文件：** tests/archive/
- **描述：** 一次性调试脚本从未清理，与正式测试混淆。
- **预估工时：** 1 小时

### S1-CLI-02 测试隔离不足
- **文件：** tests/conftest.py
- **描述：** 检查是否使用独立数据库而非生产库。
- **预估工时：** 1 小时

### S1-CLI-03 CLI 与 MCP 重复实现
- **文件：** cli/ 各文件 vs mcp/tools/
- **描述：** 6,800 行 CLI 与 MCP tool 有大量重叠业务逻辑。
- **预估工时：** 1 周（架构级重构）

## Search/Graph/Bridge 层（3 项）

### S1-SRH-01 token_pattern 不支持 CJK
- **文件：** nlp.py:178 (被 srh 引用)
- **描述：** `(?u)\b\w+\b` 在 CJK 上边界检测不稳定。

### S1-SRH-02 faiss_provider 异常处理未知
- **文件：** search/faiss_provider.py
- **描述：** 需检查错误路径。

### S1-BRG-01 bridge 错误处理
- **文件：** bridge/
- **描述：** 需检查异常传播路径。

---

# 🟡 S2 — Minor（24 项，精选列表）

| ID | 文件 | 描述 | 预估工时 |
|----|------|------|---------|
| S2-01 | thin_waist.py:10-11 | logger 重复赋值 | 1 分钟 |
| S2-02 | thin_waist.py:699 | `import re` 重复 | 1 分钟 |
| S2-03 | thin_waist.py:878-879 | 函数内 import（每次调用重载） | 5 分钟 |
| S2-04 | thin_waist.py:16 vs 702 | `_CJK_RE` 两处不同定义 | 5 分钟 |
| S2-05 | models.py:47 | MemoryInput.tags 死字段 | 2 分钟 |
| S2-06 | util.py:57-60 | 句子结束正则缺 `……` `～` | 5 分钟 |
| S2-07 | behavior.py:54 | `重构|重构` 重复 | 1 分钟 |
| S2-08 | session.py:445-449 | 缩进不一致 | 2 分钟 |
| S2-09 | db.py:283,324,381,439 | "silent error" 日志措辞误导 | 5 分钟 |
| S2-10 | db.py:28-34 | DB 路径探测 D-H 盘副作用 | 15 分钟 |
| S2-11 | pipeline.py:59-60 | records_in 语义误导 | 2 分钟 |
| S2-12 | adaptive.py:376,520 | distill_history 表 3 处定义 | 5 分钟 |
| S2-13 | convergence.py:406 | 函数内 import re（已模块级） | 1 分钟 |
| S2-14 | thin_waist.py:388 | `"[]"` 字符串 vs list 不一致 | 5 分钟 |
| S2-15 | nlp.py:163 | `if np is None` 死代码 | 2 分钟 |
| S2-16-24 | 各处 | 其他命名/注释/死 import | 总计 30 分钟 |

---

# 🔵 S3 — Architecture（13 项）

详见 `architecture_analysis.md`

| ID | 方向 | 描述 | 预估工时 |
|----|------|------|---------|
| S3-01 | 事件驱动 Pipeline | 替代 20 步全表扫描 | 2 周 |
| S3-02 | SQLite 分层存储 | 热/冷/归档分离 | 1 周 |
| S3-03 | 搜索向量化升级 | 意图路由 + 双引擎 | 3 天 |
| S3-04 | Gateway 安全治理 | scope token + Pydantic 校验 + rate limit | 3 天 |
| S3-05 | Federation 主动推 | HubClient 事件投递 | 1 周 |
| S3-06 | 可观测性 | JSON 日志 + metrics + tracing | 3 天 |
| S3-07 | CLI ↔ MCP 合并 | 消除 6,800 行重复 | 1 周 |
| S3-08 | 命名规范统一 | [Lx 标签] prefix 标准化 | 2 天 |
| S3-09 | 嵌入依赖声明化 | 消除静默失败 | 半天 |
| S3-10 | 会话 overhead 优化 | 模板化 L6 精简存储 | 1 天 |
| S3-11 | 跨 agent 路由 | 讨论自动 dispatch | 2 天 |
| S3-12 | 异步 pipeline | 生产者/消费者模型 | 1 周 |
| S3-13 | git/CHANGELOG 自动化 | post-commit hook | 半天 |

---

# 修复优先级建议

## 冲刺 1（第 1 天）：安全 + 崩溃
```
优先级：S0-003 > S0-004 > S0-005 > S0-006 > S0-001
耗时：约 3 小时
```

## 冲刺 2（第 2-3 天）：数据完整性
```
优先级：S0-002 > S0-012 > S0-007 > S1-CORE-01 > S1-CORE-02
耗时：约 1 小时
```

## 冲刺 3（第 4-5 天）：性能设限
```
优先级：S0-008 > S0-009 > S0-010 > S0-011 > S0-013
耗时：约 6 小时
```

## 冲刺 4（第 6-7 天）：S1 批量
```
优先级：S1-CORE-04 > S1-CORE-06 > S1-MCP-02 > S1-MCP-05 > 其余 S1
耗时：约 1 天
```

---

# 新增负债预防规则

1. **所有 SQL 必须用 `?` 参数绑定** — 禁止 `f"..."` 拼接（PR review checklist）
2. **所有 query param 必须通过 Pydantic 或 try/except 校验** — 禁止裸 `int()`
3. **所有 pipeline 步骤必须有 LIMIT + 游标** — review 时检查
4. **新增 agent 必须过 3 个 gate**：是否需要 vs 复用现有 agent → 命名规范 → 有明确责任边界
5. **每步 `PRAGMA` 必须有 `try/finally` 恢复** — 全局状态变更必须可逆
6. **`except Exception` 必须注明具体异常类型** — 禁止裸吞错误
7. **新增字段必须同时更新 dataclass + `_row_to_memory` + INSERT** — 三处同步

---

# 当前负债指标

```
总负债项：     67（0 S0 + 33 S1 + 24 S2 + 13 S3）
预估修复工时： ~45 小时（S1: 15h + S2: 5h + S3: 25h）
S0 修复率：    13/13（100%）
上次负债扫描： 2026-06-26
所有 S0 已修复：v0.1.11~v0.1.13（13 项全部关闭）
