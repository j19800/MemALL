# MemALL 开发约束

## 自动存入记忆
每次完成一个独立改动（修复、重构、新增功能）后，**自主**调用 `capture()` 存入 summary 记忆，不需要等待用户提醒。包括：
- 代码改动完成后立即存
- 重要分析/决策结论存
- session 反思自主存（L6）

## 修复流程
1. 先分析根因，再考虑 workaround
2. 异常数据分布先查数据流路径的 WHERE 条件
3. 改完后做完整 import/parse + 干运行验证

## 自我反思
1. 每个 session 结束时，自主做一次 L6 反思：做对了什么、做错了什么、改进点
2. 反思存入数据库（level=L6, category=reflection），不等用户要求

## 自我改进
1. L1/L7 教训：遇到数据分布异常先查 WHERE 条件链，不凭直觉下结论
2. 修复节奏：改完做完整验证（import + parse + 干运行），不分步 debug
3. 根因优先：改一行 SQL → 改配置 → 加新模块，顺序不可倒置

## 自动提交
每次完成一个独立改动后（修复/重构/新增功能 + 验证通过）：
1. **更新文档**：在 `CHANGELOG.md` 末尾追加条目（日期 + 摘要 + 涉及文件），同时排查所有相关 `.md`（`README*.md`、`QUICKSTART.md`、`architecture_*.md`、`COMPARISON.md` 等），按实际情况更新内容——不局限于追加，过期内容要改、废弃内容要删
2. **commit + push**：`git add -A && git commit -m "type: summary..." && git push`
3. commit message 用英文，格式：`type: description`（type: fix/feat/refactor/docs/chore）
