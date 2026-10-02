"""Gateway HTML handlers — extracted from gateway.py (Stage-1 split).

Holds every server-rendered HTML page handler as ``HtmlHandlersMixin`` so
``MemAllGateway`` composes routing/JSON/HTML/federation concerns from
separate modules.  The handlers touch no locks or thread pools, so the mixin
carries no shared mutable state.
"""

import logging
from datetime import date, datetime, timedelta, timezone
from aiohttp import web
from memall.gateway_utils import (
    esc_html,
    _density_color,
    _safe_int,
    _epoch_narrative,
)
from memall.core.db import pool_conn
from memall.core.thin_waist import timeline

logger = logging.getLogger("memall.gateway.html")


# Shared navigation bar for HTML pages
_NAV_HTML = '<div style="margin-bottom:16px">' \
    '<a href="/recent" style="color:#555;text-decoration:none;margin-right:16px">最近</a>' \
    '<a href="/timeline" style="color:#555;text-decoration:none;margin-right:16px">时间线</a>' \
    '<a href="/dashboard" style="color:#555;text-decoration:none;margin-right:16px">仪表盘</a>' \
    '<a href="/todos" style="color:#555;text-decoration:none;margin-right:16px">待办</a>' \
    '<a href="/discussions" style="color:#555;text-decoration:none;margin-right:16px">讨论</a>' \
    '<a href="/graph" style="color:#555;text-decoration:none;margin-right:16px">图谱</a>' \
    '<a href="/artifact" style="color:#555;text-decoration:none;margin-right:16px">工单</a>' \
    '<a href="/features" style="color:#555;text-decoration:none;margin-right:16px">功能</a>' \
    '</div>'


class HtmlHandlersMixin:
    """Server-rendered HTML page handlers."""

    _HTML_STYLE = """
    <style>
      body { font-family: system-ui, sans-serif; max-width: 800px; margin: 0 auto; padding: 20px; background: #f5f5f5; }
      h1 { color: #333; border-bottom: 2px solid #ddd; padding-bottom: 8px; }
      .card { background: #fff; border-radius: 8px; padding: 16px; margin: 12px 0; box-shadow: 0 1px 3px rgba(0,0,0,.1); }
      .card h3 { margin: 0 0 6px 0; color: #555; }
      .card .meta { font-size: 12px; color: #999; margin-bottom: 8px; }
      .card .content { font-size: 14px; line-height: 1.5; color: #333; white-space: pre-wrap; }
      .tag { display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 11px; margin-right: 4px; background: #e0e0e0; }
      .tag.l5-active { background: #c8e6c9; }
      .tag.l5-done { background: #e0e0e0; }
      .tag.l4 { background: #bbdefb; }
      .tag.l3 { background: #fff9c4; }
      .trait-card { background: #fff; border-radius: 8px; padding: 16px; margin: 12px 0; box-shadow: 0 1px 3px rgba(0,0,0,.1); }
      .trait-card h3 { margin: 0 0 12px 0; color: #555; font-size: 15px; border-left: 3px solid #888; padding-left: 10px; }
      .trait-item { display: inline-block; background: #f0f4ff; border-radius: 16px; padding: 4px 12px; margin: 4px 6px 4px 0; font-size: 13px; color: #333; }
      .trait-item .type-tag { font-size: 10px; color: #888; margin-right: 4px; }
      .persona-header { text-align: center; padding: 20px; margin-bottom: 16px; }
      .persona-header .prototype { font-size: 22px; font-weight: bold; color: #333; }
      .persona-header .subtitle { font-size: 14px; color: #888; margin-top: 4px; }
      .color-bar { display: flex; height: 8px; border-radius: 4px; overflow: hidden; margin: 12px 0; }
      .color-bar .seg { height: 100%; }
      .color-bar .seg.white { background: #e0e0e0; }
      .color-bar .seg.blue { background: #64b5f6; }
      .color-bar .seg.black { background: #424242; }
      .color-bar .seg.red { background: #ef5350; }
      .color-bar .seg.green { background: #81c784; }
      .empty-state { text-align: center; color: #999; padding: 40px; }
    </style>"""

    async def _handle_options(self, request: web.Request) -> web.Response:
        return web.Response(status=204,)

    async def _handle_recent_html(self, request: web.Request) -> web.Response:
        from memall.gateway_html import handle_recent as _hr
        with pool_conn() as conn:
            return web.Response(text=_hr(conn), content_type="text/html")

    async def _handle_todos_html(self, request: web.Request) -> web.Response:
        from memall.gateway_html import handle_todos as _ht
        with pool_conn() as conn:
            return web.Response(text=_ht(conn), content_type="text/html")

    async def _handle_identity_html(self, request: web.Request) -> web.Response:
        from memall.gateway_html import handle_identity as _hi
        agent_name = request.match_info.get("agent_name", "system")
        with pool_conn() as conn:
            return web.Response(text=_hi(conn, agent_name), content_type="text/html")

    async def _handle_graph_html(self, request: web.Request) -> web.Response:
        from memall.gateway_html import handle_graph_stats as _hg
        with pool_conn() as conn:
            return web.Response(text=_hg(conn), content_type="text/html")


    def _render_artifact_html(self) -> str:
        """Build the artifact overview HTML page (static, shared CSS + nav)."""
        style = self._HTML_STYLE + """
        .commit { background:#e8f5e9; border-left:3px solid #4caf50; padding:12px; margin:8px 0; border-radius:0 8px 8px 0; }
        .commit .hash { font-family:monospace; color:#2e7d32; font-size:13px; }
        .disc-item { background:#fff3e0; border-left:3px solid #ff9800; padding:12px; margin:8px 0; border-radius:0 8px 8px 0; }
        .agent-tag { display:inline-block; padding:1px 8px; border-radius:10px; font-size:11px; margin:2px; }
        .agent-tag.ok { background:#c8e6c9; }
        .agent-tag.pending { background:#fff9c4; }
        .section { margin:20px 0; padding:16px; background:#fff; border-radius:8px; box-shadow:0 1px 3px rgba(0,0,0,.1); }
        .section h2 { margin:0 0 12px 0; font-size:17px; color:#333; border-bottom:1px solid #eee; padding-bottom:8px; }
        ul { margin:4px 0; padding-left:20px; }
        li { margin:4px 0; line-height:1.5; }
        .stat-row { display:flex; gap:12px; flex-wrap:wrap; margin:12px 0; }
        .stat-card { flex:1; min-width:100px; background:#f5f5f5; border-radius:8px; padding:12px; text-align:center; }
        .stat-card .num { font-size:24px; font-weight:bold; color:#333; }
        .stat-card .label { font-size:12px; color:#888; }"""
        return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>MemALL · 工单</title>{style}
</head><body>
{_NAV_HTML}

<h1>本 Session 成果清单</h1>
<p style="color:#888;font-size:14px">更新至 {datetime.now().strftime("%Y-%m-%d")} · 2 个推送 · 1 个讨论收敛</p>

<div class="stat-row">
  <div class="stat-card"><div class="num">2</div><div class="label">推送</div></div>
  <div class="stat-card"><div class="num">1</div><div class="label">讨论收敛</div></div>
  <div class="stat-card"><div class="num">4</div><div class="label">参与 Agent</div></div>
  <div class="stat-card"><div class="num">40</div><div class="label">数据清理</div></div>
</div>

<div class="section">
<h2> Commits</h2>

<div class="commit">
<div class="hash">4eb0d0e</div>
<strong>agent_name 规范化管理</strong>
<span class="tag">#8476</span>
<ul>
<li><code>normalize_agent_name()</code> 从 capture() 提取为独立函数</li>
<li>convergence.py 4 处 INSERT 路径加校验</li>
<li>gateway.py _import_identity / _import_memories 加校验</li>
<li>数据清理：40 条空 agent_name → "system"</li>
<li>修改文件：thin_waist.py, convergence.py, gateway.py</li>
</ul>
</div>

<div class="commit">
<div class="hash">c14c980</div>
<strong>Phase 1: 层级命名规范统一</strong>
<span class="tag">#10780</span>
<ul>
<li>新增 <code>_LEVEL_SUBJECT_PREFIX</code> 映射表 (level → [Lx 标签])</li>
<li>_make_subject() 签名增加 level 参数，优先使用 level prefix</li>
<li>L9 蒸馏词条追加 [L9 蒸馏] 前缀</li>
<li>L10 整合从 "L10:agent跨领域洞察()" → "[L10 整合] agent 跨领域洞察()"</li>
<li>旧数据不动，85 tests pass（无新增失败）</li>
</ul>
</div>

</div>

<div class="section">
<h2> 讨论 #10780 — 层级命名规范统一</h2>

<div class="disc-item">
<strong>方案决策：方案 C（渐进统一）</strong> — 4 位 Agent 全数通过
</div>

<table style="width:100%;border-collapse:collapse;margin:12px 0">
<tr style="background:#f5f5f5"><th style="padding:6px;text-align:left;border-bottom:1px solid #ddd">Agent</th><th style="padding:6px;text-align:left;border-bottom:1px solid #ddd">评估项</th><th style="padding:6px;text-align:left;border-bottom:1px solid #ddd">结果</th></tr>
<tr><td style="padding:6px"><span class="agent-tag ok">opencode</span></td><td style="padding:6px">全层级 DB 抽样 + distill/integrate 兼容性</td><td style="padding:6px;color:#2e7d32">方案 C，无阻塞</td></tr>
<tr><td style="padding:6px"><span class="agent-tag ok">claude</span></td><td style="padding:6px">capture._make_subject() 改动量评估</td><td style="padding:6px;color:#2e7d32">方案 C，20 行</td></tr>
<tr><td style="padding:6px"><span class="agent-tag ok">codex</span></td><td style="padding:6px">gateway/session_start subject 依赖</td><td style="padding:6px;color:#2e7d32">方案 C，无解析依赖</td></tr>
<tr><td style="padding:6px"><span class="agent-tag ok">workbuddy</span></td><td style="padding:6px">内容前缀与 classify 正则兼容性</td><td style="padding:6px;color:#2e7d32">方案 C，_LAYER_PREFIX_RE 无冲突</td></tr>
</table>

<p><strong>结论：</strong>Phase 1 已实施（capture + distill + integrate），Phase 2（遗留清理）可选延期。Q1（level vs category 标识）已解决：优先 level prefix，fallback category prefix。Q2 无冲突。Q3：L8 不受影响。</p>

</div>

</body></html>"""

    async def _handle_artifact(self, request: web.Request) -> web.Response:
        """展示本 session 任务成果总览页面。"""
        html = self._render_artifact_html()
        return web.Response(text=html, content_type="text/html")

    def _render_features_html(self) -> str:
        """Build the features overview HTML page (static, shared CSS + nav)."""
        style = self._HTML_STYLE + """
        .section { margin:20px 0; padding:16px; background:#fff; border-radius:8px; box-shadow:0 1px 3px rgba(0,0,0,.1); }
        .section h2 { margin:0 0 12px 0; font-size:17px; color:#333; border-bottom:2px solid #eee; padding-bottom:8px; }
        .section h3 { margin:12px 0 6px 0; font-size:14px; color:#555; }
        .section h4 { margin:8px 0 4px 0; font-size:13px; color:#666; }
        table { width:100%; border-collapse:collapse; margin:8px 0; font-size:13px; }
        th { background:#f5f5f5; padding:6px; text-align:left; border-bottom:1px solid #ddd; font-weight:600; }
        td { padding:5px 6px; border-bottom:1px solid #eee; }
        .tag { display:inline-block; padding:1px 6px; border-radius:3px; font-size:10px; margin:1px; background:#e8e8e8; }
        .tag.c { background:#e3f2fd; }
        .tag.mcp { background:#fce4ec; }
        .tag.api { background:#e8f5e9; }
        .tag.pipe { background:#fff3e0; }
        .tag.arch { background:#f3e5f5; }
        .code { font-family:monospace; font-size:12px; background:#f5f5f5; padding:1px 4px; border-radius:2px; }
        .layer-row { display:flex; gap:4px; flex-wrap:wrap; margin:8px 0; }
        .layer-item { padding:2px 10px; border-radius:12px; font-size:12px; background:#f5f5f5; border:1px solid #ddd; }
        .layer-item.l0 { background:#e8e8e8; }
        .layer-item.l1 { background:#e3f2fd; border-color:#90caf9; }
        .layer-item.l2 { background:#fff3e0; border-color:#ffcc80; }
        .layer-item.l3 { background:#fce4ec; border-color:#f48fb1; }
        .layer-item.l4 { background:#f3e5f5; border-color:#ce93d8; }
        .layer-item.t { background:#c8e6c9; border-color:#81c784; }
        .two-col { display:flex; gap:16px; flex-wrap:wrap; }
        .col { flex:1; min-width:280px; }"""
        return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>MemALL · 功能清单</title>{style}
</head><body>
{_NAV_HTML}

<h1>MemALL 功能报告</h1>
<p style="color:#888;font-size:14px">v0.1.2 · Python + SQLite + aiohttp · 端口 9919(Gateway) / 9876(MCP)</p>

<div class="section">
<h2>一、记忆层级体系</h2>
<div class="layer-row">
<span class="layer-item l0">P0 原始</span>
<span class="layer-item l0">P1 原始</span>
<span class="layer-item l0">P2 原始</span>
<span class="layer-item l0">P3 原始</span>
<span class="layer-item l0">P4 原始</span>
<span class="layer-item l1">L1 身份</span>
<span class="layer-item l1">L2 时间</span>
<span class="layer-item l1">L3 流程</span>
<span class="layer-item l2">L4 会话</span>
<span class="layer-item l2">L5 计划</span>
<span class="layer-item l3">L6 反思</span>
<span class="layer-item l3">L7 教训</span>
<span class="layer-item l3">L8 关系</span>
<span class="layer-item l4">L9 蒸馏</span>
<span class="layer-item l4">L10 整合</span>
<span class="layer-item l4">L11 商业</span>
<span class="layer-item t">终端不可变</span>
</div>
<p>每个层级有独立的关键词规则、置信度评分、升级策略（只升级不降级）。L6/L8/L9/L10/L11 为终端层，一旦到达不可变更。</p>
</div>

<div class="section">
<h2>二、核心 API（thin_waist.py）</h2>
<table>
<tr><th>函数</th><th>功能</th><th>特点</th></tr>
<tr><td class="code">capture()</td><td>存入记忆</td><td>8维质量评分 + 内容去重 + 可见性校验 + agent身份校验</td></tr>
<tr><td class="code">retrieve()</td><td>搜索/查询</td><td>按 ID/keyword/agent/level/category/project 过滤</td></tr>
<tr><td class="code">update()</td><td>更新字段</td><td>白名单控制 + agent_name 规范化</td></tr>
<tr><td class="code">smart_store()</td><td>自动去重</td><td>语义相似度阈值 0.85</td></tr>
<tr><td class="code">store_batch()</td><td>批量存储</td><td>事务内完成</td></tr>
<tr><td class="code">connect()</td><td>创建关系</td><td>8+ 关系类型</td></tr>
<tr><td class="code">traverse()</td><td>图谱遍历</td><td>最多 5 跳 BFS</td></tr>
<tr><td class="code">vector_search()</td><td>向量搜索</td><td>TF-IDF+SVD 嵌入</td></tr>
<tr><td class="code">hybrid_search()</td><td>混合搜索</td><td>FTS5 + vec0 双通道 + RRF</td></tr>
<tr><td class="code">timeline()</td><td>时间线查询</td><td>小时/日/周聚合</td></tr>
<tr><td class="code">normalize_agent_name()</td><td>名称规范化</td><td>strip+lower+regex+黑名单</td></tr>
</table>
</div>

<div class="section">
<h2>三、HTTP 网关（:9919）</h2>
<div class="two-col">
<div class="col">
<h3>HTML 页面（10 个）</h3>
<table>
<tr><td class="code">/health</td><td>服务器健康</td></tr>
<tr><td class="code">/recent</td><td>最近记忆</td></tr>
<tr><td class="code">/todos</td><td>任务管理</td></tr>
<tr><td class="code">/timeline</td><td>Epoch 时间线</td></tr>
<tr><td class="code">/identity/{{name}}</td><td>Agent 档案</td></tr>
<tr><td class="code">/dashboard</td><td>综合仪表盘</td></tr>
<tr><td class="code">/discussions</td><td>讨论列表</td></tr>
<tr><td class="code">/graph</td><td>图谱可视化</td></tr>
<tr><td class="code">/artifact</td><td>成果工单</td></tr>
<tr><td class="code">/features</td><td>功能报告</td></tr>
</table>
</div>
<div class="col">
<h3>REST API（10+ 个）</h3>
<table>
<tr><td class="code">/api/slices</td><td>时间切片</td></tr>
<tr><td class="code">/api/epochs</td><td>周期数据</td></tr>
<tr><td class="code">/api/arcs</td><td>决策弧</td></tr>
<tr><td class="code">/api/graph</td><td>图谱 JSON</td></tr>
<tr><td class="code">/api/discussions</td><td>讨论数据</td></tr>
<tr><td class="code">/capture</td><td>POST 存记忆</td></tr>
<tr><td class="code">/retrieve</td><td>POST 搜索</td></tr>
<tr><td class="code">/traverse</td><td>POST 遍历</td></tr>
<tr><td class="code">/profile</td><td>POST 画像</td></tr>
<tr><td class="code">/pair</td><td>POST 配对</td></tr>
</table>
</div>
</div>
</div>

<div class="section">
<h2>四、MCP 工具（44+ 个，:9876）</h2>
<div class="two-col">
<div class="col">
<h3>CRUD <span class="tag c">6</span></h3>
<p>capture, retrieve, connect, traverse, timeline, update</p>
<h3>智能存储 <span class="tag c">2</span></h3>
<p>smart_store, store_batch</p>
<h3>搜索 <span class="tag c">2</span></h3>
<p>vector_search, hybrid_search</p>
<h3>画像/问答 <span class="tag c">4</span></h3>
<p>persona, persona_profile, memall_ask, identity</p>
<h3>会话 <span class="tag c">3</span></h3>
<p>session_start, session_end, session_summary</p>
<h3>知识图谱 <span class="tag c">2</span></h3>
<p>memall_traverse, memall_graph</p>
</div>
<div class="col">
<h3>联邦 <span class="tag api">5</span></h3>
<p>fed_query, fed_publish, fed_conflicts, fed_inject, fed_extract</p>
<h3>生命周期 <span class="tag pipe">5</span></h3>
<p>forget, adaptive, security, ops, db</p>
<h3>讨论 <span class="tag arch">3</span></h3>
<p>discussion_create, discussion_respond, discussion_status</p>
<h3>反思/溯源 <span class="tag arch">2</span></h3>
<p>reflect_interact, memall_trace</p>
<h3>管道/蒸馏 <span class="tag pipe">3</span></h3>
<p>run_pipeline, distill_pending, index_rebuild</p>
<h3>其他 <span class="tag">3</span></h3>
<p>gateway, hub_connect, hub_sync, onboarding</p>
</div>
</div>
</div>

<div class="section">
<h2>五、数据处理管道</h2>
<div class="two-col">
<div class="col">
<h3>核心 20 步（顺序执行）</h3>
<table>
<tr><td>1. enrich</td><td>语义增强</td></tr>
<tr><td>2. cleanup</td><td>数据清理</td></tr>
<tr><td>3. classify</td><td>自动分类</td></tr>
<tr><td>4. time_slice</td><td>预聚合</td></tr>
<tr><td>5. arc_status</td><td>决策弧</td></tr>
<tr><td>6. echo</td><td>价值评分</td></tr>
<tr><td>7. epoch</td><td>周期检测</td></tr>
<tr><td>8. convergence</td><td>讨论收敛</td></tr>
<tr><td>9. link</td><td>关联</td></tr>
<tr><td>10. decay</td><td>衰减</td></tr>
<tr><td>11. backup</td><td>备份</td></tr>
<tr><td>12. session</td><td>会话采集</td></tr>
<tr><td>13. embed_index</td><td>索引</td></tr>
<tr><td>14. reflect</td><td>反思</td></tr>
<tr><td>15. distill_l7</td><td>L7 提取</td></tr>
<tr><td>16. distill</td><td>L9 蒸馏</td></tr>
<tr><td>17. integrate</td><td>L10 整合</td></tr>
<tr><td>18. improve</td><td>自我改进</td></tr>
<tr><td>19. observation</td><td>观察</td></tr>
<tr><td>20. identity</td><td>身份更新</td></tr>
</table>
</div>
<div class="col">
<h3>可选 5 步</h3>
<table>
<tr><td>persona</td><td>Agent 认知画像</td></tr>
<tr><td>narrative</td><td>叙事生成</td></tr>
<tr><td>cluster</td><td>K-means 聚类</td></tr>
<tr><td>suggest</td><td>建议提取</td></tr>
<tr><td>bridge</td><td>桥接分析</td></tr>
</table>

<h3 style="margin-top:16px">后台调度器</h3>
<table>
<tr><td>pipeline</td><td>每 6 小时</td></tr>
<tr><td>doctor</td><td>每 1 小时</td></tr>
<tr><td>forget</td><td>每 24 小时</td></tr>
<tr><td>security</td><td>每 24 小时</td></tr>
<tr><td>heartbeat</td><td>每 5 分钟</td></tr>
</table>
</div>
</div>
</div>

<div class="section">
<h2>六、搜索能力</h2>
<table>
<tr><th>方式</th><th>引擎</th><th>维度</th></tr>
<tr><td>FTS5 全文搜索</td><td>SQLite FTS5</td><td>CJK 分词 + 命中高亮</td></tr>
<tr><td>向量搜索</td><td>vec0 (sqlite-vec)</td><td>BGE 512 维 + TF-IDF+SVD</td></tr>
<tr><td>混合搜索</td><td>RRF 融合</td><td>FTS5 + vec0 双通道</td></tr>
<tr><td>交叉编码重排</td><td>BGE-reranker-v2-m3</td><td>选择性加载（约 1.8GB）</td></tr>
<tr><td>FAISS 插件</td><td>可选 Provider</td><td>大规模部署</td></tr>
</table>
</div>

<div class="section">
<h2>七、联邦与安全</h2>
<div class="two-col">
<div class="col">
<h3>联邦能力</h3>
<table>
<tr><td>跨 Agent 共享</td><td>shared_memories 表</td></tr>
<tr><td>家庭圈</td><td>管理员/成员，邀请制</td></tr>
<tr><td>冲突检测</td><td>关键词 + 语义双重</td></tr>
<tr><td>自动解决</td><td>投票制（置信度+时间+权重）</td></tr>
<tr><td>Agent Hub</td><td>:12431 双向同步</td></tr>
<tr><td>LAN 发现</td><td>UDP 广播配对</td></tr>
</table>
</div>
<div class="col">
<h3>安全能力</h3>
<table>
<tr><td>敏感扫描</td><td>API Key/邮箱/IP/手机/身份证</td></tr>
<tr><td>三级权限</td><td>public / trusted / private</td></tr>
<tr><td>写入校验</td><td>agent 必须在 identities 表中注册</td></tr>
<tr><td>综合评分</td><td>扫描 + 权限 + 联邦 打分</td></tr>
</table>
</div>
</div>
</div>

<div class="section">
<h2>八、集成 & 其他</h2>
<table>
<tr><td>Lark/飞书 IM</td><td>多 bot 消息收发，讨论通知卡片</td></tr>
<tr><td>文件桥接</td><td>inbox/outbox 目录监听，文件 ↔ 记忆双向同步</td></tr>
<tr><td>导出</td><td>Markdown / JSONL / CSV / HTML</td></tr>
<tr><td>新手引导</td><td>5 步交互式向导</td></tr>
<tr><td>测试</td><td>35 个活跃测试文件，85+ 用例</td></tr>
<tr><td>DB 迁移</td><td>20 个正式迁移（001-019）</td></tr>
<tr><td>插件</td><td>白名单加载，热重载</td></tr>
</table>
</div>

</body></html>"""

    async def _handle_features(self, request: web.Request) -> web.Response:
        html = self._render_features_html()
        return web.Response(text=html, content_type="text/html")

    async def _handle_todos(self, request: web.Request) -> web.Response:
        from memall.pipeline.task_lifecycle import list_active_tasks, list_blocked_tasks
        agent_filter = request.query.get("agent", "").strip()

        active_tasks = list_active_tasks(agent_filter)
        blocked_tasks = list_blocked_tasks(agent_filter)

        # Resolved tasks (recent)
        with pool_conn() as conn:
            resolved_rows = conn.execute(
                "SELECT id, subject, agent_name, metadata, created_at "
                "FROM memories WHERE level='L5' AND category='task' "
                "AND json_extract(metadata, '$.status') = 'resolved' "
                "ORDER BY created_at DESC LIMIT 20"
            ).fetchall()

        def _task_card(tid, subject, agent, status, extra=""):
            tag_class = "l5-active" if status == "active" else "l5-done"
            return (
                '<div class="card">'
                '<div class="meta">#{} <span class="tag {}">{}</span> {} {}</div>'
                '<h3>{}</h3>'
                '</div>'
            ).format(tid, tag_class, status, esc_html(agent), extra, esc_html(subject or "(no subject)"))

        items = ""
        if active_tasks:
            items += "<h2>Active ({})</h2>".format(len(active_tasks))
            for t in active_tasks:
                ack_mark = "ack" if t.get("acknowledged_at") else "unack"
                age = (t.get("created_at") or "")[:10]
                items += _task_card(t["task_id"], t["subject"], t["agent_name"], "active", ack_mark + " (" + age + ")")

        if blocked_tasks:
            items += "<h2>Blocked ({})</h2>".format(len(blocked_tasks))
            for b in blocked_tasks:
                reason = (b.get("blocked_reason") or "")[:60]
                items += _task_card(b["task_id"], b["subject"], b["agent_name"], "blocked", reason)

        if resolved_rows:
            items += "<h2>Resolved (recent {})</h2>".format(len(resolved_rows))
            for r in resolved_rows:
                items += _task_card(r["id"], r["subject"], r["agent_name"], "resolved", (r["created_at"] or "")[:10])

        filter_info = " | agent=" + esc_html(agent_filter) if agent_filter else ""
        html = "<!DOCTYPE html>\n<html><head><meta charset='utf-8'><title>MemALL Task Board</title>{}</head><body>{}<h1>Task Board <span style='font-size:14px;color:#999;font-weight:normal'>{} active, {} blocked{}</span></h1>{}</body></html>".format(
            self._HTML_STYLE, _NAV_HTML,
            len(active_tasks), len(blocked_tasks), filter_info,
            items or '<p style="color:#999">No tasks</p>',
        )
        return web.Response(text=html, content_type="text/html")

    async def _handle_dashboard(self, request: web.Request) -> web.Response:
        agent_name = request.query.get("agent_name", "").strip() or None
        days = _safe_int(request.query.get("days", 30))

        with pool_conn() as conn:
            # Daily slices
            if agent_name:
                slice_rows = conn.execute(
                    "SELECT * FROM time_slices WHERE agent_name = ? AND granularity = 'day' "
                    "ORDER BY window_start DESC LIMIT ?",
                    (agent_name, days),
                ).fetchall()
            else:
                slice_rows = conn.execute(
                    "SELECT * FROM time_slices WHERE agent_name = '*' AND granularity = 'day' "
                    "ORDER BY window_start DESC LIMIT ?",
                    (days,),
                ).fetchall()
                if not slice_rows:
                    # Fallback: show all agent-specific slices grouped
                    slice_rows = conn.execute(
                        "SELECT * FROM time_slices WHERE granularity = 'day' "
                        "ORDER BY window_start DESC LIMIT ?",
                        (days * 5,),
                    ).fetchall()

            # Active epochs (ended_at IS NULL)
            if agent_name:
                epoch_rows = conn.execute(
                    "SELECT * FROM epochs WHERE agent_name = ? AND ended_at IS NULL "
                    "ORDER BY started_at DESC",
                    (agent_name,),
                ).fetchall()
            else:
                epoch_rows = conn.execute(
                    "SELECT * FROM epochs WHERE ended_at IS NULL "
                    "ORDER BY started_at DESC LIMIT 20"
                ).fetchall()

            # Recently ended epochs
            recent_epochs = conn.execute(
                "SELECT * FROM epochs WHERE ended_at IS NOT NULL "
                "ORDER BY ended_at DESC LIMIT 15"
            ).fetchall()

            # Decision Arc status
            arc_stats = conn.execute(
                "SELECT arc_status, COUNT(*) as cnt FROM memories WHERE level = 'L4' "
                "AND arc_status IS NOT NULL GROUP BY arc_status"
            ).fetchall()
            arc_counts = {r["arc_status"]: r["cnt"] for r in arc_stats}
            stale_cutoff = (date.today() - timedelta(days=21)).isoformat()
            stale_count = conn.execute(
                "SELECT COUNT(*) as cnt FROM memories WHERE level = 'L4' AND arc_status = 'open' "
                "AND created_at < ? AND id NOT IN ("
                "  SELECT DISTINCT source_id FROM edges WHERE relation_type != 'deleted' "
                "  AND target_id IN (SELECT id FROM memories WHERE level = 'L5')"
                "  UNION "
                "  SELECT DISTINCT target_id FROM edges WHERE relation_type != 'deleted' "
                "  AND source_id IN (SELECT id FROM memories WHERE level = 'L5')"
                ")",
                (stale_cutoff,),
            ).fetchone()
            stale_total = stale_count["cnt"] if stale_count else 0

            # All epochs summary
            all_epochs = conn.execute(
                "SELECT COUNT(*) as total, COUNT(DISTINCT agent_name) as agents FROM epochs"
            ).fetchone()
            total_epochs = all_epochs["total"] if all_epochs else 0
            epoch_agents = all_epochs["agents"] if all_epochs else 0

        # Build heatmap data: bar chart per day
        heatmap_bars = ""
        max_count = 1
        counts = []
        for r in reversed(slice_rows):
            counts.append(r["memory_count"])
            if r["memory_count"] > max_count:
                max_count = r["memory_count"]
        max_count = max(max_count, 1)

        for i, r in enumerate(reversed(slice_rows)):
            pct = (r["memory_count"] / max_count) * 100
            intensity = min(255, 180 + int(75 * (1 - r["memory_count"] / max_count)))
            color = f"rgba(100, 181, 246, {max(0.2, r['memory_count'] / max_count)})"
            date_label = r["slice_key"]
            heatmap_bars += (
                f'<div style="display:flex;align-items:center;margin:2px 0;font-size:12px">'
                f'<span style="width:80px;color:#999">{date_label[-5:]}</span>'
                f'<div style="flex:1;height:16px;background:#eee;border-radius:3px;overflow:hidden">'
                f'<div style="height:100%;width:{pct:.1f}%;background:{color};border-radius:3px"></div></div>'
                f'<span style="width:40px;text-align:right;color:#555;margin-left:6px">{r["memory_count"]}</span>'
                f'</div>'
            )

        if not heatmap_bars:
            heatmap_bars = '<p style="color:#999">暂无时间片数据（需先运行 pipeline）</p>'

        # Arc status cards
        arc_html = ""
        open_c = arc_counts.get("open", 0)
        ip_c = arc_counts.get("in_progress", 0)
        closed_c = arc_counts.get("closed", 0)
        total_c = open_c + ip_c + closed_c
        if total_c > 0:
            closure = round(closed_c / total_c * 100)
            arc_html += (
                f'<div class="card" style="display:inline-block;min-width:80px;text-align:center;margin:4px">'
                f'<div style="font-size:20px;color:#e53935">{open_c}</div>'
                f'<div style="font-size:11px;color:#999">开放</div></div>'
                f'<div class="card" style="display:inline-block;min-width:80px;text-align:center;margin:4px">'
                f'<div style="font-size:20px;color:#fb8c00">{ip_c}</div>'
                f'<div style="font-size:11px;color:#999">进行中</div></div>'
                f'<div class="card" style="display:inline-block;min-width:80px;text-align:center;margin:4px">'
                f'<div style="font-size:20px;color:#43a047">{closed_c}</div>'
                f'<div style="font-size:11px;color:#999">已闭环</div></div>'
                f'<div style="margin-top:8px;font-size:12px;color:#666">'
                f'闭合率 {closure}%'
            )
            if stale_total > 0:
                arc_html += f' · <span style="color:#e53935">{stale_total} 条搁置(&gt;21d)</span>'
            arc_html += '</div>'
            arc_html += (
                f'<div style="margin-top:8px"><a href="/api/arcs" style="font-size:12px">查看详情 →</a></div>'
            )
        else:
            arc_html = '<p style="color:#999">暂无决策弧数据</p>'

        # Epoch cards
        epoch_cards = ""
        for r in epoch_rows:
            label = r["label"] or "(未命名)"
            meta_info = f'{r["boundary_reason"]} · {r["started_at"][:16]}'
            if r["memory_count"]:
                meta_info += f' · {r["memory_count"]} 条记忆'
            epoch_cards += (
                f'<div class="card">'
                f'<h3>{label[:60]}</h3>'
                f'<div class="meta">{meta_info}</div>'
                f'</div>'
            )
        if not epoch_cards:
            epoch_cards = '<p style="color:#999">暂无活跃时期</p>'

        # Recent epochs list
        recent_epoch_list = ""
        for r in recent_epochs[:10]:
            recent_epoch_list += (
                f'<div style="font-size:12px;color:#666;padding:4px 0;border-bottom:1px solid #eee">'
                f'<span style="color:#999">{r["started_at"][:10]}</span> → '
                f'<span style="color:#999">{r["ended_at"][:10]}</span> '
                f'<strong>{r["agent_name"]}</strong>: {(r["label"] or "(未命名)")[:50]} '
                f'<span class="tag">{r["boundary_reason"]}</span>'
                f'</div>'
            )

        # Stats cards
        stats_html = ""
        if slice_rows:
            total_mem = sum(r["memory_count"] for r in slice_rows)
            stats_html += (
                f'<div class="card" style="display:inline-block;min-width:120px;text-align:center;margin-right:8px">'
                f'<div style="font-size:24px;font-weight:bold;color:#333">{len(slice_rows)}</div>'
                f'<div style="font-size:12px;color:#999">日切片</div></div>'
                f'<div class="card" style="display:inline-block;min-width:120px;text-align:center;margin-right:8px">'
                f'<div style="font-size:24px;font-weight:bold;color:#333">{total_mem}</div>'
                f'<div style="font-size:12px;color:#999">记忆数</div></div>'
            )
        stats_html += (
            f'<div class="card" style="display:inline-block;min-width:120px;text-align:center;margin-right:8px">'
            f'<div style="font-size:24px;font-weight:bold;color:#333">{total_epochs}</div>'
            f'<div style="font-size:12px;color:#999">时期(总)</div></div>'
            f'<div class="card" style="display:inline-block;min-width:120px;text-align:center">'
            f'<div style="font-size:24px;font-weight:bold;color:#333">{epoch_agents}</div>'
            f'<div style="font-size:12px;color:#999">Agent</div></div>'
        )

        # Filter form
        filter_html = (
            '<form class="filter-bar" method="get">'
            '<label>Agent: <input type="text" name="agent_name" value="' + (agent_name or "") + '" placeholder="全部" style="width:120px"></label>'
            '<label>天数: <input type="number" name="days" value="' + str(days) + '" min="1" max="365" style="width:60px"></label>'
            '<button type="submit">刷新</button>'
            '</form>'
        )

        dashboard_style = """
        <style>
          .dashboard-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
          .dashboard-section { background: #fff; border-radius: 8px; padding: 16px; box-shadow: 0 1px 3px rgba(0,0,0,.1); }
          .dashboard-section h2 { font-size: 15px; color: #555; margin: 0 0 12px 0; border-bottom: 1px solid #eee; padding-bottom: 8px; }
          .dashboard-section.full { grid-column: 1 / -1; }
          .filter-bar { background: #fff; border-radius: 8px; padding: 12px 16px; margin-bottom: 20px; box-shadow: 0 1px 3px rgba(0,0,0,.1); display: flex; gap: 12px; align-items: flex-end; flex-wrap: wrap; }
          .filter-bar label { font-size: 13px; color: #555; }
          .filter-bar input { border: 1px solid #ccc; border-radius: 4px; padding: 4px 8px; font-size: 13px; }
          .filter-bar button { background: #64b5f6; color: #fff; border: none; border-radius: 4px; padding: 6px 16px; cursor: pointer; font-size: 13px; }
        </style>
        """

        full_style = self._HTML_STYLE.replace("</style>", dashboard_style + "</style>")

        html = (
            "<!DOCTYPE html>\n<html><head><meta charset='utf-8'><title>MemALL · 仪表盘</title>{style}</head><body>{nav}"
            "<h1>时间线仪表盘</h1>{filter}"
            "<div style='margin-bottom:16px'>{stats}</div>"
            "<div class='dashboard-grid'>"
            "<div class='dashboard-section'><h2>记忆热力</h2>{heatmap}</div>"
            "<div class='dashboard-section'><h2>决策弧</h2>{arc}</div>"
            "<div class='dashboard-section'><h2>活跃时期</h2>{epoch}</div>"
            "<div class='dashboard-section full'><h2>最近结束的时期</h2>{recent}</div>"
            "</div>"
            "</body></html>"
        ).format(
            style=full_style, nav=_NAV_HTML, filter=filter_html, stats=stats_html,
            heatmap=heatmap_bars, arc=arc_html,
            epoch=epoch_cards, recent=recent_epoch_list or '<p style="color:#999">暂无</p>',
        )

        return web.Response(text=html, content_type="text/html")

    async def _handle_discussions(self, request: web.Request) -> web.Response:
        """HTML page: list all discussion topics with status badges."""
        from memall.pipeline.convergence import list_all_discussions
        all_rows = list_all_discussions()

        cards = ""
        for topic in all_rows:
            participants = topic.get("participants") or []
            resp_count = topic.get("response_count", 0)
            meta_status = topic.get("status", "active")
            color = "#43a047" if meta_status == "converged" else (
                     "#e53935" if meta_status == "stale" else "#fb8c00")
            status_badge = f'<span style="color:{color};font-weight:bold">{meta_status}</span>'
            summary = topic.get("summary", "") or ""
            cards += (
                '<div class="card">'
                '<div class="meta">{} {} · {} 条回复 · {} 位参与者</div>'
                '<h3>{}</h3>'
                '<div class="content">{}</div>'
                '</div>'
            ).format(
                status_badge,
                (topic.get("created_at") or "")[:19],
                resp_count,
                len(participants),
                topic.get("subject", "(无标题)"),
                summary[:200],
            )

        empty_placeholder = '<p style="color:#999">暂无讨论话题</p>'
        html = (
            '<!DOCTYPE html>\n<html><head><meta charset="utf-8">'
            f'<title>MemALL · 讨论看板</title>{self._HTML_STYLE}</head>'
            f'<body>{_NAV_HTML}<h1>讨论看板</h1>'
            f'{cards or empty_placeholder}'
            '</body></html>'
        )
        return web.Response(text=html, content_type="text/html")

    async def _handle_timeline_html(self, request: web.Request) -> web.Response:
        days = _safe_int(request.query.get("days", 7))
        agent_name = request.query.get("agent_name", "").strip() or None
        category = request.query.get("category", "").strip() or None

        # ── 1. Density data from time_slices ──
        from datetime import timedelta
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        density_data: list[dict] = []
        max_density = 1
        with pool_conn() as conn:
            if agent_name:
                den_rows = conn.execute(
                    "SELECT slice_key, memory_count FROM time_slices "
                    "WHERE agent_name = ? AND granularity = 'day' AND window_start >= ? ORDER BY slice_key",
                    (agent_name, cutoff),
                ).fetchall()
            else:
                den_rows = conn.execute(
                    "SELECT slice_key, SUM(memory_count) as memory_count FROM time_slices "
                    "WHERE granularity = 'day' AND window_start >= ? "
                    "GROUP BY slice_key ORDER BY slice_key",
                    (cutoff,),
                ).fetchall()
            for r in den_rows:
                c = r["memory_count"]
                density_data.append({"date": r["slice_key"], "count": c})
                if c > max_density:
                    max_density = c

        # ── 2. Memories and epoch assignment ──
        results = timeline(days=days, category=category)
        if agent_name:
            results = [r for r in results if r.agent_name and r.agent_name.lower() == agent_name.lower()]

        with pool_conn() as conn:
            if agent_name:
                epoch_rows = conn.execute(
                    "SELECT * FROM epochs WHERE agent_name = ? AND "
                    "(ended_at IS NULL OR ended_at >= ?) AND started_at <= ? ORDER BY started_at",
                    (agent_name, cutoff, datetime.now(timezone.utc).isoformat()),
                ).fetchall()
            else:
                epoch_rows = conn.execute(
                    "SELECT * FROM epochs WHERE "
                    "(ended_at IS NULL OR ended_at >= ?) AND started_at <= ? ORDER BY agent_name, started_at",
                    (cutoff, datetime.now(timezone.utc).isoformat()),
                ).fetchall()

        epoch_map: dict[int, dict] = {0: {"id": 0, "label": "未归属", "started_at": cutoff, "ended_at": None,
                                           "boundary_reason": "auto", "memory_count": 0, "agent_name": "",
                                           "is_active": False}}
        for e in epoch_rows:
            e_dict = dict(e)
            is_active = e_dict.get("ended_at") is None
            epoch_map[e_dict["id"]] = {**e_dict, "is_active": is_active}

        epoch_children: dict[int, list] = {eid: [] for eid in epoch_map}
        for mem in results:
            mem_occurred = (mem.occurred_at or mem.created_at or "")
            assigned = False
            for e in sorted(epoch_map.values(), key=lambda x: x.get("started_at", "")):
                e_start = e.get("started_at", "")
                e_end = e.get("ended_at") or "9999"
                if e_start <= mem_occurred <= e_end:
                    epoch_children.setdefault(e["id"], []).append(mem)
                    assigned = True
                    break
            if not assigned:
                epoch_children.setdefault(0, []).append(mem)

        # ── 3. Build epoch group HTML ──
        ordered_epochs = sorted(
            [e for eid, e in epoch_map.items() if eid != 0 and epoch_children.get(eid)],
            key=lambda x: x.get("started_at", ""), reverse=True,
        )
        unassigned = epoch_children.get(0, [])

        cards_html = ""
        for e in ordered_epochs:
            mems = epoch_children[e["id"]]
            # epoch header
            duration = ""
            if e.get("ended_at"):
                dur_days = (datetime.fromisoformat(e["ended_at"]) - datetime.fromisoformat(e["started_at"])).days
                duration = f"{dur_days}天" if dur_days > 0 else "<1天"
            else:
                dur_days = (datetime.now(timezone.utc) - datetime.fromisoformat(e["started_at"])).days
                duration = f"{dur_days}天（进行中）"
            active_class = " epoch-active" if e.get("is_active") else ""

            boundary_label = {
                "gap": "间隔", "category_shift": "主题切换",
                "l6_viewpoint_change": "观点转变", "manual": "手动",
            }.get(e.get("boundary_reason", ""), e.get("boundary_reason", ""))

            with pool_conn() as conn2:
                group_html = "\n".join(
                    self._render_timeline_card(m, conn2)
                    for m in mems
                )

            cards_html += (
                f'<div class="epoch-group{active_class}">'
                f'<div class="epoch-header">'
                f'<span class="epoch-label">{esc_html(e.get("label", "")[:60])}</span>'
                f'<span class="epoch-meta">'
                f'{e.get("started_at", "")[:10]} → {esc_html(duration)}'
                f' · <span class="epoch-badge">{boundary_label}</span>'
                f' · {len(mems)} 条'
                f'{" · @" + e.get("agent_name", "") if e.get("agent_name") else ""}'
                f'</span>'
                f'</div>'
                f'<div class="epoch-narrative">{_epoch_narrative(mems)}</div>'
                f'<div class="timeline-line">{group_html}</div>'
                f'</div>'
            )

        # Unassigned memories (before any epoch)
        if unassigned:
            with pool_conn() as conn2:
                group_html = "\n".join(self._render_timeline_card(m, conn2) for m in unassigned)
            cards_html += (
                f'<div class="epoch-group">'
                f'<div class="epoch-header" style="opacity:0.6">'
                f'<span class="epoch-label">未归属记忆</span>'
                f'<span class="epoch-meta">{len(unassigned)} 条 · 不在任何 Epoch 范围内</span>'
                f'</div>'
                f'<div class="timeline-line">{group_html}</div>'
                f'</div>'
            )

        if not cards_html:
            cards_html = '<div class="empty-state" style="margin-top:40px"><p>该时间段内暂无记忆</p></div>'

        # ── 4. Density chart HTML ──
        density_html = ""
        if density_data:
            bars = "".join(
                '<div class="density-bar" style="height:{}px;background:{}" '
                'title="{}: {} 条" data-date="{}"></div>'.format(
                    max(4, round(d["count"] / max_density * 60)),
                    _density_color(d["count"], max_density),
                    d["date"], d["count"], d["date"],
                )
                for d in density_data
            )
            density_html = f'<div class="density-chart"><div class="density-label">记忆密度</div><div class="density-bars">{bars}</div></div>'

        # ── 5. Filter form ──
        filter_html = (
            '<form class="filter-bar" method="get">'
            '<label>天数: <input type="number" name="days" value="{}" min="1" max="365" style="width:60px"></label>'
            '<label>Agent: <input type="text" name="agent_name" value="{}" placeholder="全部" style="width:120px"></label>'
            '<label>分类: <input type="text" name="category" value="{}" placeholder="全部" style="width:120px"></label>'
            '<button type="submit">筛选</button>'
            '</form>'
        ).format(days, esc_html(agent_name or ""), esc_html(category or ""))

        # ── 6. Style ──
        timeline_style = """
        <style>
          .filter-bar { background: #fff; border-radius: 8px; padding: 12px 16px; margin-bottom: 20px; box-shadow: 0 1px 3px rgba(0,0,0,.1); display: flex; gap: 12px; align-items: flex-end; flex-wrap: wrap; }
          .filter-bar label { font-size: 13px; color: #555; }
          .filter-bar input { border: 1px solid #ccc; border-radius: 4px; padding: 4px 8px; font-size: 13px; }
          .filter-bar button { background: #64b5f6; color: #fff; border: none; border-radius: 4px; padding: 6px 16px; cursor: pointer; font-size: 13px; }
          .filter-bar button:hover { background: #42a5f5; }
          .density-chart { background: #fff; border-radius: 8px; padding: 12px 16px; margin-bottom: 16px; box-shadow: 0 1px 3px rgba(0,0,0,.1); }
          .density-label { font-size: 12px; color: #999; margin-bottom: 8px; }
          .density-bars { display: flex; align-items: flex-end; gap: 2px; height: 70px; overflow-x: auto; flex-wrap: nowrap; }
          .density-bar { min-width: 8px; border-radius: 2px 2px 0 0; cursor: pointer; flex-shrink: 0; transition: opacity .2s; }
          .density-bar:hover { opacity: .7; }
          .epoch-group { margin-bottom: 28px; position: relative; }
          .epoch-group.epoch-active { border-left: 3px solid #4caf50; padding-left: 12px; margin-left: -3px; }
          .epoch-header { font-size: 15px; font-weight: bold; color: #444; margin-bottom: 10px; padding: 8px 12px; background: #fafafa; border-radius: 6px; border: 1px solid #e0e0e0; display: flex; justify-content: space-between; align-items: baseline; flex-wrap: wrap; gap: 4px; }
          .epoch-label { color: #333; }
          .epoch-meta { font-size: 12px; font-weight: normal; color: #999; }
          .epoch-badge { display: inline-block; padding: 1px 6px; border-radius: 3px; font-size: 11px; background: #e3f2fd; color: #1976d2; }
          .epoch-narrative { font-size: 12px; color: #888; margin: -6px 0 10px 16px; padding-left: 4px; font-style: italic; }
          .timeline-line { border-left: 3px solid #ddd; padding-left: 16px; margin-left: 8px; }
          .timeline-card { background: #fff; border-radius: 6px; padding: 10px 14px; margin: 8px 0; box-shadow: 0 1px 2px rgba(0,0,0,.08); position: relative; }
          .timeline-card::before { content: ''; position: absolute; left: -22px; top: 14px; width: 10px; height: 10px; border-radius: 50%; background: #bbb; border: 2px solid #fff; }
          .timeline-card .meta { font-size: 12px; color: #999; margin-bottom: 4px; }
          .timeline-card .time { font-family: monospace; font-size: 11px; color: #aaa; }
          .timeline-card .content { font-size: 13px; line-height: 1.5; color: #333; white-space: pre-wrap; }
          .rel-badge { display: inline-block; padding: 1px 5px; border-radius: 3px; font-size: 10px; margin-left: 4px; }
          .rel-supersedes { background: #e0e0e0; color: #666; }
          .rel-refines { background: #e3f2fd; color: #1565c0; }
        </style>
        """

        full_style = self._HTML_STYLE.replace("</style>", timeline_style + "</style>")
        title = f"记忆时间线 · 最近 {days} 天"
        if agent_name:
            title += f" · {agent_name}"
        if category:
            title += f" · {category}"

        html = "<!DOCTYPE html>\n<html><head><meta charset='utf-8'><title>MemALL · {}</title>{}</head><body>{}</body></html>".format(
            title, full_style,
            _NAV_HTML
            + '<h1>🧠 记忆时间线 <span style="font-size:14px;color:#999;font-weight:normal">最近 {} 天{}{}</span></h1>'.format(
                days,
                f" · {agent_name}" if agent_name else "",
                f" · {category}" if category else "",
            )
            + density_html
            + filter_html
            + cards_html,
        )
        return web.Response(text=html, content_type="text/html")

    def _render_timeline_card(self, mem, conn) -> str:
        sup_cnt = conn.execute(
            "SELECT COUNT(*) as c FROM edges WHERE source_id = ? AND relation_type = 'supersedes'",
            (mem.id,),
        ).fetchone()["c"]
        ref_cnt = conn.execute(
            "SELECT COUNT(*) as c FROM edges WHERE source_id = ? AND relation_type = 'refines'",
            (mem.id,),
        ).fetchone()["c"]
        rel_badges = ""
        if sup_cnt > 0:
            rel_badges += f'<span class="rel-badge rel-supersedes" title="已取代 {sup_cnt} 条">已取代 {sup_cnt}</span>'
        if ref_cnt > 0:
            rel_badges += f'<span class="rel-badge rel-refines" title="基于 {ref_cnt} 条">基于 {ref_cnt}</span>'
        agent_tag = f' <span class="tag" style="background:#f0e6ff">@{mem.agent_name}</span>' if mem.agent_name else ""
        return (
            '<div class="timeline-card">'
            '<div class="meta">'
            '<span class="time">{}</span> '
            '<span class="tag">{}</span> '
            '<span class="tag l4">{}</span>{}'
            '<span style="float:right">{}</span>'
            '</div>'
            '<div class="content">{}</div>'
            '</div>'
        ).format(
            (mem.occurred_at or "")[11:19],
            esc_html(mem.level or ""),
            esc_html(mem.category or ""),
            agent_tag,
            rel_badges,
            esc_html((mem.content or "")[:250]),
        )
