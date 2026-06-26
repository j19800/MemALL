#!/usr/bin/env python3
import sys
data = open(sys.argv[1], encoding="utf-8").read()
target = sys.argv[2] if len(sys.argv) > 2 else sys.argv[1]

# S0 kanban panel replacement
old_kanban_marker = '13 项待修复 | 预估 10 小时'
new_kanban_html = '''  <!-- S0 修复看板 -->
  <div class="panel">
    <h2>🔴 S0 Critical 修复看板 <span style="font-size:12px;color:var(--muted);font-weight:400;">5 项待修复（8/13 已修复）| 预估 5 小时</span></h2>
    <div class="kanban">
      <div class="kanban-col">
        <h3>待修复 <span class="cnt">5 项</span></h3>
        <div class="kanban-item">
          <span>S0-001 <span class="badge badge-data">bug</span> session_project NameError</span>
          <span class="tag">15m</span>
        </div>
        <div class="kanban-item">
          <span>S0-007 <span class="badge badge-data">data</span> UUID truncated</span>
          <span class="tag">5m</span>
        </div>
        <div class="kanban-item">
          <span>S0-009 <span class="badge badge-perf">perf</span> N+1 edge count</span>
          <span class="tag">1h</span>
        </div>
        <div class="kanban-item">
          <span>S0-011 <span class="badge badge-perf">perf</span> O(n²) adaptive.py</span>
          <span class="tag">2h</span>
        </div>
        <div class="kanban-item">
          <span>S0-012 <span class="badge badge-data">data</span> Memory dataclass mismatch</span>
          <span class="tag">15m</span>
        </div>
      </div>
      <div class="kanban-col">
        <h3>✅ 已修复 <span class="cnt">8 项</span></h3>
        <div class="kanban-item" style="opacity:0.6;">
          <span>S0-002 <span class="badge badge-data">data</span> PRAGMA FK OFF</span>
          <span class="tag">v0.1.11</span>
        </div>
        <div class="kanban-item" style="opacity:0.6;">
          <span>S0-003 <span class="badge badge-sec">sec</span> /api/* auth bypass</span>
          <span class="tag">v0.1.11</span>
        </div>
        <div class="kanban-item" style="opacity:0.6;">
          <span>S0-004 <span class="badge badge-auth">auth</span> /pair token leak</span>
          <span class="tag">v0.1.11</span>
        </div>
        <div class="kanban-item" style="opacity:0.6;">
          <span>S0-005 <span class="badge badge-sec">sec</span> int() crash</span>
          <span class="tag">v0.1.11</span>
        </div>
        <div class="kanban-item" style="opacity:0.6;">
          <span>S0-006 <span class="badge badge-sec">sec</span> MCP HTTP no auth</span>
          <span class="tag">v0.1.11</span>
        </div>
        <div class="kanban-item" style="opacity:0.6;">
          <span>S0-008 <span class="badge badge-perf">perf</span> O(n²) link.py</span>
          <span class="tag">v0.1.12</span>
        </div>
        <div class="kanban-item" style="opacity:0.6;">
          <span>S0-010 <span class="badge badge-perf">perf</span> enrich.py no LIMIT</span>
          <span class="tag">v0.1.12</span>
        </div>
        <div class="kanban-item" style="opacity:0.6;">
          <span>S0-013 <span class="badge badge-perf">perf</span> embedding silent fail</span>
          <span class="tag">v0.1.12</span>
        </div>
      </div>
    </div>
  </div>'''

# Find start and end of old kanban section
idx = data.find(old_kanban_marker)
if idx < 0:
    print("ERROR: kanban marker not found", file=sys.stderr)
    sys.exit(1)

# Find the enclosing <div class="panel"> by searching backwards
start = data.rfind("<!-- S0 修复看板 -->", 0, idx)
if start < 0:
    start = data.rfind('<div class="panel">', 0, idx)
end = data.find("</div>", idx)
end = data.find("</div>", end + 6) + 6  # close the kanban panel

# Replace
old_html = data[start:end]
data = data[:start] + new_kanban_html + data[end:]

# Sprint section
old_sprint_marker = '清除 6 项 S0'
new_sprint_html = '''    <h2>⚡ 下一轮推荐修复 <span style="font-size:12px;color:var(--muted);font-weight:400;">5 项 S0 剩余 | 预估 5 小时</span></h2>
    <table class="sprint-table">
      <tr><th>优先级</th><th>ID</th><th>问题</th><th>模块</th><th>类型</th><th>工时</th><th>影响</th></tr>
      <tr><td>🅿1</td><td>S0-009</td><td>N+1 edge count</td><td>classify.py:203</td><td>性能</td><td>1h</td><td>classify 逐条 COUNT</td></tr>
      <tr><td>🅿2</td><td>S0-011</td><td>O(n²) adaptive.py</td><td>adaptive.py:118</td><td>性能</td><td>2h</td><td>去重全表对比</td></tr>
      <tr><td>🅿3</td><td>S0-001</td><td>session_project NameError</td><td>session.py:770</td><td>崩溃</td><td>15m</td><td>L6 静默丢失</td></tr>
      <tr><td>🅿4</td><td>S0-012</td><td>Memory dataclass mismatch</td><td>models.py</td><td>数据</td><td>15m</td><td>对象字段缺失</td></tr>
      <tr><td>🅿5</td><td>S0-007</td><td>UUID truncation</td><td>session.py:353</td><td>数据</td><td>5m</td><td>碰撞风险</td></tr>
    </table>
    <div class="progress-bar"><div class="progress-fill" style="width:65%;"></div></div>
    <div style="margin-top:8px;font-size:13px;color:var(--muted);">整体 S0 修复进度：8 / 13（62%）— 剩余 5 项</div>'''

idx_sprint = data.find(old_sprint_marker)
if idx_sprint < 0:
    print("ERROR: sprint marker not found", file=sys.stderr)
    sys.exit(1)

sprint_start = data.rfind('<h2>', 0, idx_sprint)
sprint_end = data.find("</pre>", idx_sprint)
sprint_end = data.find("</div>", sprint_end) + 6

old_sprint = data[sprint_start:sprint_end]
data = data[:sprint_start] + new_sprint_html + data[sprint_end:]

# Trend chart
old_trend_marker = '0%</div>\n      </div>\n      <div style="flex:1;text-align:center;opacity:0.3;">'
new_trend = '''        <div style="height:100px;display:flex;align-items:flex-end;gap:8px;">
      <div style="flex:1;text-align:center;">
        <div style="height:50px;background:var(--accent);border-radius:2px 2px 0 0;width:100%;"></div>
        <div style="font-size:11px;color:var(--muted);margin-top:4px;">06-26 初扫</div>
        <div style="font-size:12px;font-weight:600;">0%</div>
      </div>
      <div style="flex:1;text-align:center;">
        <div style="height:62px;background:var(--green);border-radius:2px 2px 0 0;width:100%;"></div>
        <div style="font-size:11px;color:var(--muted);margin-top:4px;">06-26 修复</div>
        <div style="font-size:12px;font-weight:600;">62%</div>
      </div>
      <div style="flex:1;text-align:center;opacity:0.3;">
        <div style="height:4px;background:var(--border);border-radius:2px;width:100%;"></div>
        <div style="font-size:11px;color:var(--muted);margin-top:4px;">下一轮</div>
        <div style="font-size:12px;font-weight:600;">—</div>
      </div>
    </div>
    <div style="margin-top:12px;font-size:13px;color:var(--muted);">
      8/13 S0 已修复（v0.1.11~v0.1.12）。已修复：安全漏洞（S0-003~006）、数据完整性（S0-002）、性能（S0-008,010）、静默失败（S0-013）。
    </div>'''

idx_trend = data.find(old_trend_marker)
if idx_trend < 0:
    print("ERROR: trend marker not found", file=sys.stderr)
    sys.exit(1)

trend_start = data.rfind("height:100px", 0, idx_trend)
trend_start = data.rfind("<div", 0, trend_start)
trend_end = data.find("</div>", idx_trend)
trend_end = data.find("</div>", trend_end + 6)
trend_end = data.find("</div>", trend_end + 6) + 6

old_trend_section = data[trend_start:trend_end]
data = data[:trend_start] + new_trend + data[trend_end:]

# Update heatmap S0 counts
data = data.replace('>pipeline/</td><td><span class="sev sev-s0">5</span>', '>pipeline/</td><td><span class="sev sev-s0">3</span>')
data = data.replace('>mcp/</td><td><span class="sev sev-s0">3</span>', '>mcp/</td><td><span class="sev sev-s0">0</span>')
data = data.replace('>gateway</td><td><span class="sev sev-s0">3</span>', '>gateway</td><td><span class="sev sev-s0">0</span>')
data = data.replace('>core/</td><td><span class="sev sev-s0">2</span>', '>core/</td><td><span class="sev sev-s0">1</span>')
data = data.replace('>graph/</td><td><span class="sev sev-s0">1</span>', '>graph/</td><td><span class="sev sev-s0">0</span>')

with open(target, "w", encoding="utf-8") as f:
    f.write(data)
print("Updated successfully")
