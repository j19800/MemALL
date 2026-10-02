# ADR-0001：记忆层级定义的唯一真相源

- **状态**：Accepted（2026-09-26）
- **决策者**：老陈
- **相关**：`src/memall/core/models.py`、`src/memall/mcp/tools/__init__.py`、`CONTEXT.md` §2

## 背景

同一个"MemALL 有几层记忆"的问题，项目内曾并存三种互斥答案：

| 来源 | 说法 |
|---|---|
| 用户长期记忆 | 10 层（L0–L10） |
| README | 11 层（L1–L11） |
| 代码枚举（`memall_write` input_schema.level） | P0/P1/P2 + L1–L10（另有一处使用 L11） |

差异导致跨会话沟通成本：Agent 与新会话无法判断"L9 是蒸馏还是洞察"、"有没有 L0"。

## 决策

1. **代码枚举是唯一真相源**。文档、记忆、对话一律以 `memall_write` 的 `level` 枚举为准。
2. 表述统一为 **规划层 P0/P1/P2 + 知识层 L1–L11**；**不存在 L0**。
3. 新增层级必须：先改代码枚举 → 再改 `CONTEXT.md` → 最后同步 README/CHANGELOG。反向顺序一律视为文档漂移。

## 后果

- **正**：消除同词异义；新 Agent 读 `CONTEXT.md` 即可对齐。
- **负**：README 与旧记忆中的"10 层/11 层"表述仍会短暂存在，属已知漂移，遇到即以本 ADR 覆盖。
- **验证**：`tests/` 中涉及 `level` 的用例以代码枚举为断言依据。
