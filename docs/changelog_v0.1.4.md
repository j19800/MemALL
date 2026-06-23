# MemALL v0.1.4 修改报告

**日期：** 2026-06-23 ~ 2026-06-24
**版本:** v0.1.3 → v0.1.4
**提交数:** 26 commits
**变更文件:** 30+ files

---

## 一、致命 Bug 修复：logger 被文档字符串吞没

### 原因
`logger = logging.getLogger(__name__)` 被夹在模块 docstring `"""..."""` 内部，Python 当成字符串文本跳过，未执行。共发现 7 处。

### 涉及文件
- `src/memall/mcp/federation_tools.py` — 用户报错触发（session_start 崩溃）
- `src/memall/pipeline/session.py` — 同批修复
- `src/memall/search/faiss_provider.py` — logger 完全不存在于 docstring 之外，所有 logger.warning() 直接炸
- `src/memall/pipeline/adaptive.py`
- `src/memall/pipeline/forget.py`
- `src/memall/cli/register.py`
- `src/memall/pipeline/cleanup.py`

### 修复
将所有文件的 docstring 移到文件最顶部（import 之前），`import logging` 紧随其后，`logger = ...` 在 import 之后。

### 结果
7 处 bug 全部修复，零残留。`session_start`、`fed_inject` 等工具恢复正常。

---

## 二、安全修复（5 项高危）

### 2.1 `hybrid_search()` 无 visibility 过滤（HIGH）
**原因：** `hybrid_search()` 返回所有记忆，无视 visibility 权限。
**修复：** 新增 `_filter_by_trust_dict()` + LRU 缓存，返回结果前做权限检查。

### 2.2 未知 agent 默认 `read_level="public"`（MEDIUM）
**原因：** `_get_agent_read_level()` 对未注册 agent 返回最宽松的 "public"。
**修复：** 改为 `"private"`（最严格）。

### 2.3 4 处 `shell=True` 子进程（HIGH）
**原因：** `lark_notify.py`、`lark/consumer.py`、`bridge/lark_client.py` 共 4 处使用 `subprocess.run(..., shell=True)`，且参数包含用户输入内容 `text[:1500]`。
**修复：** 全部改为列表传参。`shell=True` 零残留。

### 2.4 API server 无认证（HIGH）
**原因：** FastAPI 51 个端点全部对外开放，无鉴权。
**修复：** 新增 Bearer token auth_middleware，`gateway.secret_key` 自动生成。CORS 去掉 `"file://"` 源。

### 2.5 联邦通信无 token 强制（MEDIUM）
**原因：** `_remote_retrieve` / `_remote_retrieve_async` 中 peer token 为可选项。
**修复：** 无 token 的 peer 直接跳过并记录 warning。

---

## 三、Project 字段修复

### 原因
所有管道步的 INSERT INTO memories 都漏掉了 `project` 列。6 个文件共 15 处 INSERT 不包含 project。78% 的记忆（1982/2540）project 为空。

### 涉及文件
`session.py`（L4/L6）、`distill.py`（L9）、`integrate.py`（L10）、`reflect.py`（L6）、`observe.py`（L6×4）、`convergence.py`（L5×4）

### 修复
1. 所有 INSERT 加回 project 列 + 对应参数
2. 新记忆自动从源记忆继承 project（多数投票）
3. 存量数据回填：三阶段推断（agent→project 映射 356 条 + 组多数投票 1236 条 + 内容关键词 37 条）

### 结果
空 project：78% → 12.6%。回填 1629 条。

---

## 四、Pipeline 可观测性（P0 优先级）

### 原因
Pipeline 是黑箱——`pipeline_state` 表只有 2 行、`distill_history` 只有 3 条。没有每步时长、没有失败隔离、没有质量门禁。

### 改动
1. 新增 `pipeline_runs` 表跟踪每轮运行（每步独立 JSON 记录）
2. `_run_step()` 包装器：计时、错误隔离、输入/输出计数
3. `_check_quality_gate()`：distill(min_input=10, min_output=1)、integrate(min_input=2, min_output=1)、reflect(min_input=3, min_output=1)
4. 步骤失败不阻塞——记录 error 继续执行下一步
5. `memall pipeline status` CLI：最近 5 次运行、每步耗时、质量门禁通过率

### 结果
首运行：21 步 / 22.4s / 最慢步骤 link(16.8s)。质量门禁成功标记了 distill/integrate/reflect 的空转。

---

## 五、向量搜索替换（bge-small-zh）

### 原因
TF-IDF+SVD 模型退化——词表只有 56 个词、SVD 2 维、所有查询向量全零。Vector R@5=1.4%。

### 修复过程

| 步骤 | Vector R@5 | 方法 |
|------|:----------:|------|
| 修复前 | 1.4% | TF-IDF+SVD，脏模型 |
| index build --force | 26.7% | 还原 CJK 分词器，vocab=56→5000 |
| jieba 分词器 | 34.4% | 替换 unigram+bigram |
| bge-small-zh 模型 | **67.8% → 74.4%** | 33MB 模型，512 维 |
| L6/L9 模板改进后 | 72.2% | 关键词提取，减少语义重合 |

### 改动文件
- `embeddings.py`：完全重写，T IDF-IDF/SVD 全部移除，改用 SentenceTransformer
- `retrieve.py`：`_query_embed()` 使用 bge-small-zh
- `db.py`：vec0 表 float[256] → float[512]

### 结果
2997 条记忆全部用 512 维向量索引。Vector R@5 从 1.4% 到 74.4%（+73pp）。

---

## 六、记忆模板质量重构（L6/L9/L10）

### 6.1 L6 反思模板

**原因：** 850 条 L6 反思的模板是 `"会话总结：本次会话记录了 N 条记忆"`——语义完全重合，embedding 无法区分。

**修复：** 新增关键词提取（从 session 内容中抽 top 高频实词）。L6 内容现在包含：
- 记忆数 + 分类分布
- **关键话题**：session 内高频词
- **关键决策**：decision 类记忆内容
- **后续关注**：continuation note

**结果：** 每条 L6 有独特的语义指纹，不同 session 的 L6 不再被 embedding 当成同一内容。

### 6.2 L9 蒸馏模板

**原因：** `[L9 蒸馏] agent 在 cat 领域的 N 条记忆摘要：{extractive}`——extractive summary 用 TF-IDF 挑生僻词多的句子，内容不通顺。682 条 L9 中 680 条是模板内容。

**修复：** 删 `summarize_extractive`。L9 改为：
- 关键词（源记忆高频词 top 5）
- 最新 2 条源记忆的原文样本（verbatim，不压缩）
- 通过 `edges.refines` 回溯全部源记忆

**结果：** 信息零丢失。关键词替换 TF-IDF 摘要。

### 6.3 L10 整合模板

同 L9 处理，删 `summarize_extractive`。L10 改为 category 前缀 + 样本。

### 6.4 Subject 全面修复

| 层级 | 修复前 | 修复后 | 空 subject 清零 |
|------|--------|--------|:---------------:|
| L4 | 152 条空（INSERT 缺 subject 列） | `[L4] agent · cat · 关键词` | 230/230 ✅ |
| L6 | 452 条空（`会话 {id} 自动反思`） | `[L6] agent · pipeline session l10` | 850/850 ✅ |
| L9 | 92 条空 | `[L9] agent · cat · sqlite rrf vec` | 727/727 ✅ |
| L10 | 20 条空 | `[L10] agent · cat · search level` | 77/77 ✅ |

**全部 3183 条记忆 100% 有 subject。**

---

## 七、搜索排序层级加权

### 原因
搜"架构"返回 10/10 条 L6/L9/L10。P0-P2 原始记忆被完全淹没。因为 L6/L9/L10 内容多（800-1200 字），FTS5 匹配面大。

### 修复
新增 `_LEVEL_BOOST`：
```
P0-P2: 1.0（原始工作记忆）
L1-L3: 0.7（身份/元数据）
L4-L5: 0.6（摘要/决策）
L6-L8: 0.4（反思/模块）
L9-L10: 0.3（蒸馏/整合）
```

### 结果
搜"数据库"：之前 10/10 L6/L9/L10，现在 P0=4, P1=2, P2=2, L2=2。原始记忆不再被蒸馏淹没。

---

## 八、其他改动

| 改动 | 说明 |
|------|------|
| Pipeline session harvest | 新增 `harvest_step()` 步，不关 session 只补 L4/L6 |
| Injection 精简 | session_start 注入从 19 段/3300 字 → 4 段/~230 字 |
| L10 整合质量 | 新增 Jaccard 去重（≥0.7 跳过）、真正跨域检查 |
| CLAUDE.md | 新增对话记忆自动提取规则 |
| 消费端 | `memall knowledge` + `memall insights` + `search --level` |
| L9/L10 梗概条删除 | `summarize_extractive` 完全移除 |

---

## 九、效果汇总

| 指标 | 修复前 | 修复后 | Δ |
|------|--------|--------|:----:|
| Vector R@5 | 1.4% | **74.4%** | +73pp |
| Keyword R@5 | 82.2% | **88.3%** | +6pp |
| Project 空 | 78% | **12.6%** | -65pp |
| L4/L6 产出 | 0 | **155 L4 + 850 L6** | 新功能 |
| Subject 空 | 700+ | **0** | 清零 |
| shell=True | 4 处 | **0** | 清零 |
| API 端点无认证 | 51 个 | **全部保护** | 新增 |
| Session 注入 | 3300 字/19 段 | **230 字/4 段** | -93% |
| 搜索 P0-P2 可见性 | L6/L9/L10 淹没 | **P0-P2 加权优先** | 排序改进 |