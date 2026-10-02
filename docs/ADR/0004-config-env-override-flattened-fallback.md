# ADR-0004：`MEMALL_*` 环境变量覆盖带下划线嵌套键的规则

- **状态**：Accepted（2026-09-26）
- **决策者**：老陈
- **相关**：`src/memall/config.py`（`_apply_env_overrides`）、`tests/test_config_env_override.py`

## 背景

`_apply_env_overrides` 把 env 名中的下划线**无差别转成点**：

```
MEMALL_CAPTURE_THREAD_INFERENCE_WINDOW_MINUTES
  → capture.thread.inference.window.minutes
```

而真实配置键是 `capture.thread_inference_window_minutes`（**段内带下划线**）。结果是：凡含下划线的嵌套键（`capture.*`、`nlp.sentence_transformers`）**永远无法通过环境变量覆盖**，只能改 `config.json`，运维体验割裂。

## 决策

采用**点路径优先 + 扁平键回退**的两段式解析，且只在首段之后的点才折叠为下划线：

1. 先按点路径匹配（保留 `MEMALL_DB_PATH → db.path` 等既有契约）；
2. 匹配不到时，回退为"保留首段点、其后折叠为下划线"的扁平键再匹配一次；
3. 仍匹配不到 → 沿用旧行为，创建点路径（支持自定义 section）；
4. **存在性判断必须基于未被污染的原始 config**，不能基于正在被改写的 `result`（否则前一条 env 创建的 `capture.thread` 会让后续判断误判为"已存在"）。

## 后果

- **正**：`MEMALL_CAPTURE_THREAD_INFERENCE_WINDOW_MINUTES`、`MEMALL_NLP_SENTENCE_TRANSFORMERS` 等首次可用；既有契约不变。
- **负**：极少数同时存在 `a.b_c` 与 `a.b.c` 两种键的配置会产生歧义——当前配置树中不存在此冲突。
- **验证**：`tests/test_config_env_override.py`（5 例，锁定三类路径：纯点路径、段内下划线、未知键 legacy 行为）。
