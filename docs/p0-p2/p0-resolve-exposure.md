# P0：稳住测试基线 + 增量暴露合同

## 目标
1. 确保 `tests/test_capture_resolve_engine.py` 全部通过（已验收：4/4 pass）。
2. 让 resolve/validity 字段在 `retrieve`/`unified_search`/`vector_search`/`hybrid_search` 的 MCP 响应里**增量暴露**：即在 `metadata` 里出现 `slot_key`、`resolve.status/reason/confidence`，旧代码解析不报错、甚至不可见亦无妨。

## 已完成改动（均已纳入 commit `92863b6` / 本地验收 39 passed）

### 1. `tests/test_capture_resolve_engine.py`
- 修复 `_reset_config_env` autouse fixture：确保 `config.reset_config()` 在每个 test 前后都调用；删除末尾 `from memall.config import set_config, get_config` 报 `ImportError` 的行（已在 fixture 中通过 `monkeypatch.delenv` + `config.reset_config()` 替代）。
- `test_resolve_can_be_disabled`：通过 `monkeypatch.setenv("MEMALL_CAPTURE_RESOLVE_ENABLED", "false")` + `config.reset_config()` 验证 resolve 可关闭时 `metadata` 里不含 `resolve` 字段。

### 2. `src/memall/mcp/tools/retrieve.py`
- `handle_retrieve` / `handle_unified_search` / `handle_vector_search` / `handle_hybrid_search` 在返回的 `Memory` JSON/对象里**增量**加入以下 `metadata` 字段（若不存在则给空默认值）：
  - `metadata.slot_key`  → 空串 `""`（老库/无槽时不报错）
  - `metadata.resolve.status` → `""` / `null`
  - `metadata.resolve.reason` → `""` / `null`
  - `metadata.resolve.confidence` → `""` / `null`
  - `metadata.valid_from` → `""`
  - `metadata.invalid_at` → `""`
  - `metadata.memory_status` → `""`
- 通过 `_cognitive_fields` / `_row_meta_dict` / `_cognitive_item_from_meta` 等辅助函数（已在 `commit 92863b6` 中新增），保持 `Memory` dataclass 字段不变，仅在 metadata 层增量暴露。

### 3. `src/memall/core/thin_waist.py`
- `_row_to_memory`：通过 `row.get("metadata", "{}")` 兼容读取旧库；从 metadata 里选读 `slot_key`、`resolve`，无则空串/`{}`。
- `_cognitive_item_from_meta` / `_row_meta_dict`：新增助手，将 metadata 结构化为认知循环字段（`slot_key`、`resolve_status`、`resolve` 等），向后兼容。

### 4. `src/memall/core/thin_waist_search.py`
- `vector_search` / `hybrid_search`：支持 `source_pipeline` 参数，在 SQL 中通过 `json_extract(m.metadata, '$.source_pipeline') = ?` 过滤，与 `kb_policy` 契约一致。

## 验收
```bash
cd /vol1/@appshare/fn-deepseek-harness/MemALL
PYTHONPATH=.:src ./.venv/bin/python -m pytest tests/test_capture_resolve_engine.py -q
# 4 passed

cd /vol1/@appshare/fn-deepseek-harness/MemALL
PYTHONPATH=.:src ./.venv/bin/python -m pytest tests/test_kb_ingest_p2.py tests/test_kb_ingest_p2_expanded.py -q
# 39 passed
```
