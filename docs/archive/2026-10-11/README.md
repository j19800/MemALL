# 归档：2026-10 ~ 2026-11 历史工作文档

> 整理日期：2026-11-09（A+B 档清理，见 `ops_facts` 的 `cleanup_20261109`）
> 本目录**只做归档，不删除**，保留可追溯性。这些文件都不是当前项目的活跃说明。

## 为什么在这

2026-10-22 ~ 11-08 期间，多 agent 协作产生了大量**一次性**产物：
- 调研/分析报告（Multica、OpenSpace、GitHub 多 agent、dsh-superagent …）
- 提案与论证（PROPOSAL、多 Agent 协作改进方案的初稿/完整版/定稿）
- 代码审查、红蓝对抗、干跑清单、推广话术
- 交接/上下文/比较、版本发布说明、审计报告、工作日报告

它们记录的是**当时**的决策过程，不是现行架构/用法。现行说明以根目录为准：
`README.md` / `README.zh-CN.md` / `AGENTS.md` / `CHANGELOG.md` /
`MemALL_Function_Spec.md` / `QUICKSTART.md` / `CONTRIBUTING.md` / `CLAUDE.md`，
以及局域网接入的 `LAN-TUTORIAL.md` / `LAN-CLIENT-GUIDE.md`。

## 内容分类

| 类别 | 文件（举例） |
|---|---|
| 调研/分析 | `Multica-*`、`OpenSpace-*`、`GitHub多Agent协作项目调研-*`、`dsh-superagent-调研-*`、`深度分析-*`、`现状调研-*` |
| 提案/方案 | `PROPOSAL-多agent自动化-待论证-*`、`MemALL多Agent协作改进方案-*`（初稿/完整版/定稿）、`重新梳理-*` |
| 审查/验证 | `代码审查-*`、`代码审查-红蓝对抗-*`、`核验-*`、`干跑清单-*` |
| 其他 | `推广话术-*`、`FIX-TICKET-*`、`DeepSeekCLI-接入our-free-model-*` |
| 报告/交付（根目录移入） | `DEEP_AUDIT_REPORT.md`、`WORK_REPORT_20261108.md`、`HANDOVER.md`、`CONTEXT.md`、`COMPARISON.md`、`RELEASE_NOTES_v0.1.0.md` |

## 注意

- 这些文档里的**行号、commit、版本、端口、部署形态**多为历史快照，勿据此改当前代码；
  例如 `DEEP_AUDIT_REPORT.md` 的基线是 `4cfcfcd`，部分端口/CORS 结论后来已修正。
- 若需引用历史决策，优先看 `docs/ADR/`（4 个架构决策记录，仍为活跃参考）。
- 本目录文件仍受 git 跟踪（用 `git mv` 移入，保留历史），不是临时文件。
