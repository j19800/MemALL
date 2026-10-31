/* MemALL sample memory dataset — used by the landing-page simulator and
   the dashboard demo mode. Mirrors the real `memories` row shape:
   level ∈ core/levels.LEVEL_ORDER, category ∈ pipeline/classify.CATEGORY_RULES,
   subject prefixed with core/thin_waist._LEVEL_SUBJECT_PREFIX. */
export const SAMPLE_MEMORIES = [
  { id: 1041, level: "L4", agent: "claude", category: "decision", subject: "网关选型：aiohttp 而非 FastAPI", content: "本次会话决定：网关采用 aiohttp——MCP Streamable HTTP 与 SSE 长连接需在同一进程复用事件循环，路由手工注册、启动更快。", created_at: "2026-10-18 14:22", access_count: 27, edges: [["refines", 1042], ["supersedes", 1039]] },
  { id: 1042, level: "L3", agent: "claude", category: "rule", subject: "瘦腰（thin-waist）单入口规范", content: "所有写入统一走 thin_waist 一条流程：capture→classify→link，MCP / REST / CLI 三个门面共享同一步骤序列，禁止旁路写库。", created_at: "2026-10-17 09:10", access_count: 41, edges: [["cites", 1003]] },
  { id: 1039, level: "L4", agent: "codex", category: "architecture", subject: "曾用 FastAPI 原型（已废弃）", content: "会话记录：早期原型用 FastAPI + Uvicorn 暴露 REST，压测下 SSE 多连接内存增长过快，被 aiohttp 方案取代。", created_at: "2026-09-30 21:48", access_count: 6, edges: [] },
  { id: 1003, level: "L10", agent: "system", category: "knowledge", subject: "跨类目整合：约定漂移是回归主因", content: "对本 Agent 的 1400+ 条 L6 反思做跨类目聚合：约 64% 回归源于文档口径与代码默认值不一致（端口、步数、工具数）。所有数字以 list_pipeline_steps() 等运行时接口为准。", created_at: "2026-10-15 03:12", access_count: 19, edges: [["extends", 1005]] },
  { id: 1005, level: "L9", agent: "system", category: "domain", subject: "部署类知识蒸馏（1.2 万会话 → 187 节点）", content: "同 Agent 同类目压缩：安装与环境类经验蒸馏为 187 个知识节点，覆盖 pip extras、sqlite-vec 加载失败、ONNX 模型缓存位置等高频问题。", created_at: "2026-10-12 22:40", access_count: 12, edges: [] },
  { id: 1101, level: "L6", agent: "opencode", category: "reflection", subject: "本次管线调试复盘", content: "做对了：先跑完迁移再复现。做错了：直接改生产 DB 副本。教训：下次用 MEMALL_DB_PATH 指向临时库，避免污染正式数据。", created_at: "2026-10-20 01:05", access_count: 9, edges: [["refines", 1042]] },
  { id: 1102, level: "L7", agent: "claude", category: "general", subject: "用户偏好：中文回复、小响应", content: "用户多次纠正：我喜欢中文优先、回复保持简短、代码引用带绝对路径。作为检索时的偏好层复用。", created_at: "2026-10-19 18:30", access_count: 58, edges: [] },
  { id: 1111, level: "L1", agent: "claude", category: "general", subject: "老陈：MemALL 联合创始人", content: "我的角色是联合创始人，负责产品与架构决策；我熟悉 Python 与 SQLite，我看重本地优先和数据不出本机。", created_at: "2026-10-08 10:02", access_count: 52, edges: [["cites", 1041]] },
  { id: 1103, level: "L5", agent: "workbuddy", category: "planning", subject: "v0.2.0 发布路线图", content: "计划：Web Dashboard 正式版 + 用户体系 + Pro 分层。里程碑 2026-11-15，下一步把待办拆成任务卡分派给 claude 与 codex。", created_at: "2026-10-21 08:00", access_count: 4, edges: [["cites", 1104]] },
  { id: 1104, level: "P0", agent: "codex", category: "problem", subject: "Bug #123：大数据集导入 OOM", content: "紧急：>50 万行导入时进程 RSS 冲到 3.1GB。根因初判：批量 embed 未分片。需要立刻定位并修复。", created_at: "2026-10-19 11:26", access_count: 14, edges: [] },
  { id: 1105, level: "P2", agent: "codex", category: "fix", subject: "修复 PR #456：embed 分片 + 背压", content: "embed_index 改为 batch=256 分片提交、每片 commit，RSS 峰值回落至 620MB；回归测试 #789 通过。", created_at: "2026-10-20 16:44", access_count: 8, edges: [["supersedes", 1104], ["extends", 1101]] },
  { id: 1106, level: "L8", agent: "opencode", category: "implementation", subject: "FTS5 中文分词空格注入法", content: "在索引触发器里对 CJK 字符逐字插空格，令 unicode61 分词器可按二元组召回中文内容——被 23 条边反复引用，按 ≥3 边规则晋升为关系层知识。", created_at: "2026-10-11 07:55", access_count: 33, edges: [["cites", 1042]] },
  { id: 1107, level: "L11", agent: "system", category: "domain", subject: "领域情报：记忆赛道的差异化与护城河", content: "竞品口径：Mem0 以云端为主、Letta 依赖 PostgreSQL、Zep 偏会话摘要。MemALL 差异化是本地单文件 + 14 级生命周期，护城河在自进化管线与图谱，GTM 主打一人公司的多 Agent 场景。", created_at: "2026-10-21 12:02", access_count: 5, edges: [["contradicts", 1108]] },
  { id: 1108, level: "L2", agent: "claude", category: "config", subject: "2026-08-02 端口口径上线", content: "2026-08-02 部署完成：MCP Streamable HTTP 端点 127.0.0.1:9876/mcp，REST 网关监听 127.0.0.1:9920，昨天结束压测并 merged 配置。", created_at: "2026-08-02 10:00", access_count: 3, edges: [] },
  { id: 1109, level: "L5", agent: "system", category: "task", subject: "讨论收敛：重排器默认用哪个模型", content: "claude 主张 ONNX INT8（零依赖），codex 主张 CrossEncoder（高 4.2 分），workbuddy 提出降级链。收敛结论：三级降级链、默认 ONNX，已生成任务卡待办。", created_at: "2026-10-16 20:15", access_count: 21, edges: [["refines", 1041]] },
  { id: 1110, level: "L3", agent: "workbuddy", category: "idea", subject: "MemPort：跨系统记忆迁移流程", content: "流程：第一步导出 Mem0 / Letta 的 JSON，第二步映射到 P0–L11，然后走 thin_waist 批量 capture，最后校验边完整性——降低换用成本，作为增长入口。", created_at: "2026-10-14 13:38", access_count: 7, edges: [] },
];

/* Display names come from core/thin_waist._LEVEL_SUBJECT_PREFIX.
   L7 covers both code paths: classify (偏好) and distill_l7 (教训). */
export const LEVEL_META = {
  P0: { label: "原始 · 最高优先", cls: "t-p" },
  P1: { label: "原始 · 中优先", cls: "t-p" },
  P2: { label: "原始 · 默认", cls: "t-p" },
  L1: { label: "身份", cls: "t-a" },
  L2: { label: "时间", cls: "t-a" },
  L3: { label: "流程", cls: "t-a" },
  L4: { label: "会话", cls: "t-a" },
  L5: { label: "计划", cls: "t-b" },
  L6: { label: "反思", cls: "t-b" },
  L7: { label: "教训 / 偏好", cls: "t-b" },
  L8: { label: "关系", cls: "t-b" },
  L9: { label: "蒸馏", cls: "t-c" },
  L10: { label: "整合", cls: "t-c" },
  L11: { label: "商业", cls: "t-c" },
};

/* graph/retrieve._LEVEL_BOOST — level weighting of the production retriever. */
export const LEVEL_BOOST = {
  P0: 1.0, P1: 1.0, P2: 1.0,
  L1: 0.7, L2: 0.7, L3: 0.7,
  L4: 0.6, L5: 0.6,
  L6: 0.4, L7: 0.4, L8: 0.4,
  L9: 0.7, L10: 0.7, L11: 0.7,
};
export const LEVEL_BOOST_DEFAULT = 0.4;

/* pipeline/pipeline._PIPELINE_STEPS — order is authoritative (31 steps). */
export const PIPELINE_STEPS = [
  ["event_processor", "事件处理器：消费 capture 事件队列，管线入口"], ["enrich", "富化：补全 subject/summary/tags 字段"],
  ["cleanup", "清理：去噪与坏行回收"], ["classify", "分类：排除链定级 P0–L11（first-match-wins）"],
  ["procedure", "程序性记忆抽取"], ["time_slice", "时间切片：日/周/月预聚合"],
  ["arc_status", "决策弧：open→in_progress→closed"], ["echo", "回声评分：边 25 + 访问 20 + 新近 20 + 层级 20 + 质量 15"],
  ["epoch", "时期检测：间隔>48h、类目漂移等 4 规则"], ["convergence", "讨论收敛：推进待议定，产出 L5 任务卡"],
  ["link", "自动建边：模式匹配 + Jaccard≥0.6"], ["decay", "衰减：低价值记忆降权"],
  ["backup", "自动备份"], ["session", "会话收割：session_end 汇总入库"],
  ["extract", "事实抽取：从会话正文提取可复用事实"], ["entity_extraction", "实体抽取：entities + memory_entities + knowledge_triples"],
  ["embed_index", "向量索引：512 维 vec0 增量写"], ["reflect", "L6 反思：会话质量复盘"],
  ["distill_l7", "L7 蒸馏：从 L6 反思抽取教训/偏好"], ["distill", "L9 同域蒸馏：同 Agent 同类目压缩"],
  ["integrate", "L10 整合：同 Agent 跨 ≥2 类目洞察"], ["improve", "改进项收集"],
  ["promote", "热度晋升：按访问量沿 P2→L1→…→L11 阶梯上升"], ["observation", "观测：运行统计报表"],
  ["identity", "身份画像更新"], ["reasoning", "推理链关联"],
  ["auto_summarize", "抽取式自动摘要"], ["discover_assoc", "联想发现"],
  ["cross_agent", "跨 Agent 蒸馏：向量聚类产出 L10/L11"], ["adaptive_ttl", "自适应 TTL：高频与被引用延长寿命"],
  ["archive", "归档：L6/L9/L10/L11 过期移出热库"],
];
export const OPTIONAL_STEPS = [
  ["persona", "人格画像"], ["narrative", "叙事生成"], ["cluster", "聚类"],
  ["suggest", "建议生成"], ["bridge", "桥接分析"],
];
