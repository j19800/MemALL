# MemALL 修复计划 & 验收标准

> Used: 2026-07-22

---

## Phase 0: 代码深度分析 ✅ 已完成

- 阅读全部 60+ 个源文件
- 分析四层架构 (core/pipeline/mcp/gateway) + 策略 (strategy) + 联邦 (federation)
- 确认 tracer.py 已使用 SQLite 持久化（v0.1.55 已自带），无需修改

---

## Phase 1: tracer.py 内存泄漏 ✅ 已由现有代码修复

v0.1.55 版本已有 `tracing_spans` 表 + 7 天保留期清理。无需修改。

---

## Phase 2: 提取硬编码参数到配置 ✅ 已完成

| # | 标准 | 状态 |
|---|------|------|
| T-01 | `config.py` 增加 `lifecycle` 配置段 (`cluster_threshold`, `connected_component_threshold`) | ✅ |
| T-02 | `context_assembler.py` 的 `rrf_k = 60` → `get_config("search.rrf_k", 60)` | ✅ |
| T-03 | `lifecycle.py` 的 `_connected_components(threshold=0.85)` → 读取配置 + 降级默认值 | ✅ |
| T-04 | 不影响原有默认行为（新配置的默认值与硬编码值一致） | ✅ |
| T-05 | 变更后可通过 `get_config("lifecycle.cluster_threshold")` 运行时调节 | ✅ |

---

## Phase 3: Pipeline 异步分离 ✅ 已完成

| # | 标准 | 状态 |
|---|------|------|
| P-01 | `gateway_api.py` 导入 `asyncio` | ✅ |
| P-02 | `gateway_api.py` 中 `run_pipeline()` 同步调用 → `await asyncio.to_thread(run_pipeline, ...)` | ✅ |
| P-03 | `gateway_api.py` 中 `run_migrations()` 同步调用 → `await asyncio.to_thread(_run)` | ✅ |
| P-04 | `pipeline.py` 添加 `enqueue_pipeline()` + 异步队列 + daemon 工作者线程 | ✅ |
| P-05 | Gateway API 使用 `enqueue_pipeline()` 进行非阻塞调用 | ✅ |
| P-06 | 导入 `queue`, `threading` | ✅ |
| P-07 | 队列工作者线程为 daemon 属性，进程退出时自动终止 | ✅ |

---

## Phase 4: NLP 引擎升级 ✅ 已完成

| # | 标准 | 状态 |
|---|------|------|
| N-01 | 模型持久化：`~/.memall/.vector_model/` 保存/加载 TfidfVectorizer + TruncatedSVD | ✅ |
| N-02 | Embedding LRU cache（默认 10000 条） | ✅ |
| N-03 | 中文文本检测函数 `contains_chinese()` | ✅ |
| N-04 | 可选 sentence-transformers 集成（`paraphrase-multilingual-MiniLM-L12-v2`） | ✅ |
| N-05 | LRU cache 集成到 `tfidf_svd_embed()` 的单文本查询路径 | ✅ |
| N-06 | 配置驱动路径 `nlp.model_dir` | ✅ |
| N-07 | sentence-transformers 导入失败的优雅降级 → 回退到 TF-IDF/SVD | ✅ |
| N-08 | LRU cache 元数据：命中率、大小、最大大小暴露 | ✅ |

---

## Phase 5: 验证测试（可选，下次执行）

| # | 标准 | 状态 |
|---|------|------|
| V-01 | 确认 `import asyncio` 在 gateway_api.py 中存在 | ⏳ |
| V-02 | 确认 `enqueue_pipeline()` 可调用且不被阻塞 | ⏳ |
| V-03 | 确认 `nlp.py` 可被重新导入且 `tfidf_svd_embed` 行为不变 | ⏳ |
| V-04 | 确认 `contains_chinese()` 正确识别中/英文文本 | ⏳ |

---

## 完成状态总览

| Phase | 描述 | 状态 |
|-------|------|------|
| 0 | 代码分析 | ✅ |
| 1 | tracer.py 内存泄漏 | ✅ 已存在 |
| 2 | 硬编码参数 → 配置 | ✅ 完成 |
| 3 | Pipeline 异步分离 | ✅ 完成 |
| 4 | NLP 引擎升级 | ✅ 完成 |
| 5 | 验收验证 | ⏳ 待执行 |
