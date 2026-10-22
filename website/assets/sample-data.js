/* MemALL sample memory dataset — used by the landing-page simulator and
   the dashboard demo mode. Mirrors the real `memories` row shape. */
export const SAMPLE_MEMORIES = [
  { id: 1041, level: "L4", agent: "claude", category: "决策", subject: "网关技术选型 aiohttp", content: "决定网关使用 aiohttp 而非 FastAPI：需要与 MCP Streamable HTTP、SSE 长连接同进程复用事件循环，且路由为手工注册、启动更快。", created_at: "2026-10-18 14:22", access_count: 27, edges: [["refines", 1042], ["supersedes", 1039]] },
  { id: 1042, level: "L2", agent: "claude", category: "约定", subject: "瘦腰（thin-waist）接口约定", content: "所有写入必须经过 thin_waist 单一入口：MCP / REST / CLI 三个门面共享同一条 capture→classify→link 管线，禁止旁路写库。", created_at: "2026-10-17 09:10", access_count: 41, edges: [["cites", 1003]] },
  { id: 1039, level: "L4", agent: "codex", category: "决策", subject: "曾用 FastAPI 原型（已废弃）", content: "早期原型用 FastAPI + Uvicorn 暴露 REST，压测下 SSE 多连接内存增长过快，被 aiohttp 方案取代。", created_at: "2026-09-30 21:48", access_count: 6, edges: [] },
  { id: 1003, level: "L10", agent: "workbuddy", category: "洞察", subject: "跨类别洞察：约定漂移是回归主因", content: "对本 Agent 的 312 条 L6 反思做跨类别聚合：约 64% 回归源于文档口径与代码默认值不一致（端口、步数、工具数），建议所有数字以 list_pipeline_steps() 等运行时接口为准。", created_at: "2026-10-15 03:12", access_count: 19, edges: [["extends", 1005]] },
  { id: 1005, level: "L9", agent: "workbuddy", category: "蒸馏", subject: "部署类知识蒸馏（1.2 万会话 → 187 节点）", content: "同 Agent 同类目压缩：安装/环境类经验蒸馏为 187 个知识节点，覆盖 pip extras、sqlite-vec 加载失败、ONNX 模型缓存位置等高频问题。", created_at: "2026-10-12 22:40", access_count: 12, edges: [] },
  { id: 1101, level: "L6", agent: "opencode", category: "反思", subject: "本次管线调试复盘", content: "做对了：先跑 41 个迁移再复现；做错了：直接改了生产 DB 副本，应使用 MEMALL_DB_PATH 指向临时库。已沉淀为 L2 约定。", created_at: "2026-10-20 01:05", access_count: 9, edges: [["refines", 1042]] },
  { id: 1102, level: "L7", agent: "claude", category: "偏好", subject: "用户偏好：中文回复、小响应", content: "用户多次纠正：回复保持简短、中文优先、代码引用带绝对路径。作为检索时的偏好层加权。", created_at: "2026-10-19 18:30", access_count: 58, edges: [] },
  { id: 1103, level: "P0", agent: "workbuddy", category: "计划", subject: "v0.2.0 发布计划", content: "发布目标：Web Dashboard 正式版 + 用户体系 + Pro 分层。截止 2026-11-15。", created_at: "2026-10-21 08:00", access_count: 4, edges: [["cites", 1104]] },
  { id: 1104, level: "L1", agent: "codex", category: "事实", subject: "Bug #123：大数据集 OOM", content: "用户报告：>50 万行导入时进程 RSS 冲到 3.1GB。根因初判：批量 embed 未分片。", created_at: "2026-10-19 11:26", access_count: 14, edges: [] },
  { id: 1105, level: "L1", agent: "codex", category: "事实", subject: "修复 PR #456：embed 分片 + 背压", content: "embed_index 改为 batch=256 分片提交、每片 commit，RSS 峰值回落至 620MB；回归测试 #789 通过。", created_at: "2026-10-20 16:44", access_count: 8, edges: [["supersedes", 1104], ["extends", 1101]] },
  { id: 1106, level: "L8", agent: "opencode", category: "边提升", subject: "FTS5 中文分词空格注入法", content: "在索引触发器里对 CJK 字符逐字插空格，令 unicode61 分词器可按二元组召回中文内容——被 23 条边反复引用，自动提升为高权重知识。", created_at: "2026-10-11 07:55", access_count: 33, edges: [["cites", 1042]] },
  { id: 1107, level: "L11", agent: "federation", category: "领域情报", subject: "跨 Agent 领域模式：端口口径混乱", content: "在 ≥5 个 Agent 的记忆中重复出现同族困惑：9876 / 9919 / 9920 并存。属于跨项目领域级信号，应统一为单一事实来源。", created_at: "2026-10-21 12:02", access_count: 5, edges: [["contradicts", 1108]] },
  { id: 1108, level: "L2", agent: "claude", category: "约定", subject: "MCP 端点约定 :9876/mcp", content: "文档约定 MCP HTTP 端点为 127.0.0.1:9876/mcp。", created_at: "2026-08-02 10:00", access_count: 3, edges: [] },
  { id: 1109, level: "L5", agent: "multi", category: "讨论", subject: "讨论：重排器默认用哪个模型", content: "claude 主张 ONNX INT8（零依赖），codex 主张 CrossEncoder v2-m3（效果好 4.2 分），workbuddy 提出降级链。收敛结论：三级降级链，默认 ONNX。", created_at: "2026-10-16 20:15", access_count: 21, edges: [["converged_to", 1041]] },
  { id: 1110, level: "L3", agent: "workbuddy", category: "创意", subject: "MemPort：跨系统记忆迁移", content: "把 Mem0 / Letta 的导出格式映射为 14 级记忆的一次性迁移工具，降低换用成本，作为增长入口。", created_at: "2026-10-14 13:38", access_count: 7, edges: [] },
];

export const LEVEL_META = {
  P0: { label: "计划 · 高优先", cls: "t-p" },
  P1: { label: "计划 · 中优先", cls: "t-p" },
  P2: { label: "计划 · 低优先", cls: "t-p" },
  L1: { label: "原始事实", cls: "t-a" },
  L2: { label: "约定", cls: "t-a" },
  L3: { label: "业务创意", cls: "t-a" },
  L4: { label: "决策", cls: "t-a" },
  L5: { label: "讨论/共识", cls: "t-b" },
  L6: { label: "自我反思", cls: "t-b" },
  L7: { label: "用户偏好", cls: "t-b" },
  L8: { label: "边提升", cls: "t-b" },
  L9: { label: "同域蒸馏", cls: "t-c" },
  L10: { label: "跨类别洞察", cls: "t-c" },
  L11: { label: "跨 Agent 领域情报", cls: "t-c" },
};

export const PIPELINE_STEPS = [
  ["event_processor", "事件处理器：消费 capture 事件队列，管线入口"], ["enrich", "富化：补全 subject/summary/tags 字段"],
  ["cleanup", "清理：去噪与坏行回收"], ["classify", "分类：排除链定级 P0–L11"],
  ["procedure", "程序性记忆抽取"], ["time_slice", "时间切片：日/周/月预聚合"],
  ["arc_status", "决策弧：open→in_progress→closed"], ["echo", "回声评分：引用/访问/新近度加权"],
  ["epoch", "时期检测：间隔>48h、类目漂移等 4 规则"], ["convergence", "讨论收敛：多 Agent 共识推进"],
  ["link", "自动建边：模式匹配 + Jaccard≥0.6"], ["decay", "衰减：低价值记忆降权"],
  ["backup", "自动备份"], ["session", "会话收割：session_end 汇总入库"],
  ["extract", "事实抽取"], ["entity_extraction", "实体抽取：entities 三元组"],
  ["embed_index", "向量索引：512 维 vec0 增量写"], ["reflect", "L6 反思：会话质量复盘"],
  ["distill_l7", "L7 偏好蒸馏"], ["distill", "L9 同域蒸馏压缩"],
  ["integrate", "L10 跨类别整合洞察"], ["improve", "改进项收集"],
  ["promote", "L8 边提升"], ["observation", "观测：运行统计报表"],
  ["identity", "身份画像更新"], ["reasoning", "推理链关联"],
  ["auto_summarize", "抽取式自动摘要"], ["discover_assoc", "联想发现"],
  ["cross_agent", "跨 Agent 聚合：产出 L10/L11"], ["adaptive_ttl", "自适应 TTL 遗忘"],
  ["archive", "归档：L5 过期入档"],
];
export const OPTIONAL_STEPS = [
  ["persona", "人格画像"], ["narrative", "叙事生成"], ["cluster", "聚类"],
  ["suggest", "建议生成"], ["bridge", "桥接分析"],
];
