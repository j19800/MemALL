# ADR — 架构决策记录

## 什么是 ADR

Architecture Decision Record：把"**为什么这么定**"固化成可查、可推翻、可继承的短文档，避免同一问题在代码注释、聊天记录、跨会话记忆里反复争论。

## 何时写

- 影响数据模型 / 写入语义 / 对外契约的决策
- 修掉一个**根因级缺陷**并改变了既有行为（而非只打补丁）
- 存在多个合理方案、最终选定其一时

**不需要**写 ADR：纯重构、格式调整、单个 bug 的局部修复。

## 模板

```markdown
# ADR-00XX：<一句话标题>

- **状态**：Proposed / Accepted / Deprecated / Superseded by ADR-00YY
- **决策者**：<谁拍板>
- **相关**：<涉及的文件 / 配置键>

## 背景
问题是什么（要带**真实数据**，不要写"可能有性能问题"）。
有哪些候选方案，各自代价。

## 决策
选了什么，以及具体约束（基数约定、门控开关、回滚方式）。
要求：可被测试验证。

## 后果
- **正**：量化收益
- **负**：接受了什么代价
- **遗留**：本次没解决、下次要跟的
- **验证**：哪个测试文件守护这条决策
```

## 索引

| ADR | 标题 | 状态 |
|---|---|---|
| [0001](0001-memory-level-canonical-definition.md) | 记忆层级定义的唯一真相源 | Accepted |
| [0002](0002-single-write-entry-thin-waist.md) | 所有写入收敛到 `thin_waist.capture()` | Accepted |
| [0003](0003-l9-l10-upsert-cardinality.md) | L9/L10 管线合成层采用 upsert 基数约定 | Accepted |
| [0004](0004-config-env-override-flattened-fallback.md) | `MEMALL_*` 环境变量覆盖带下划线嵌套键的规则 | Accepted |
