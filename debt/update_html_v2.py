#!/usr/bin/env python3
"""Update dashboard.html with current S0 stats (0 remaining, 13/13 fixed)."""
import sys

path = sys.argv[1]
data = open(path, encoding="utf-8").read()

# Summary cards
data = data.replace('">5</div>\n      <div class="label">🔴 S0 Critical (8 已修复)</div>',
                    '">0</div>\n      <div class="label">🔴 S0 Critical (13/13 全部修复)</div>')
data = data.replace('<div class="bar" style="width:10%;"></div>\n    </div>\n    <div class="card card-s1">',
                    '<div class="bar" style="width:0%;"></div>\n    </div>\n    <div class="card card-s1">')
data = data.replace('<div class="num">75</div>', '<div class="num">67</div>')
data = data.replace('<div class="num">62%</div>\n      <div class="label">✅ S0 修复率</div>',
                    '<div class="num">100%</div>\n      <div class="label">✅ S0 修复率</div>')
data = data.replace('<div class="bar" style="width:62%;"></div>', '<div class="bar" style="width:100%;"></div>')
data = data.replace('~50h', '~45h')
data = data.replace('<div class="bar" style="width:50%;"></div>', '<div class="bar" style="width:45%;"></div>')

# Pie chart: S0 = 0
data = data.replace(
    '<!-- S0 0/67 = 0% => 0° -->\n          <circle cx="50" cy="50" r="40" fill="none" stroke="#ff4444" stroke-width="20"\n            stroke-dasharray="0 100" stroke-dashoffset="25" transform="rotate(-90,50,50)"/>',
    '<!-- S0 0/67 = 0% => 0° -->\n          <circle cx="50" cy="50" r="40" fill="none" stroke="#ff4444" stroke-width="20"\n            stroke-dasharray="0 100" stroke-dashoffset="25" transform="rotate(-90,50,50)"/>'
)
# Update pie total
data = data.replace('<!-- S0 5/75 = 6.67% => 24° -->', '<!-- S0 0/67 = 0% => 0° -->')
data = data.replace('stroke-dasharray="6.67 93.33"', 'stroke-dasharray="0 100"')
data = data.replace('<!-- S1 33/75 = 44%', '<!-- S1 33/67 = 49.3%')
data = data.replace('stroke-dasharray="44 56" stroke-dashoffset="-6.33"', 'stroke-dasharray="49.25 50.75" stroke-dashoffset="-25"')
data = data.replace('<!-- S2 24/75 = 32%', '<!-- S2 24/67 = 35.8%')
data = data.replace('stroke-dasharray="32 68" stroke-dashoffset="-50.33"', 'stroke-dasharray="35.82 64.18" stroke-dashoffset="-74.25"')
data = data.replace('<!-- S3 13/75 = 17.33%', '<!-- S3 13/67 = 19.4%')
data = data.replace('stroke-dasharray="17.33 82.67" stroke-dashoffset="-82.33"', 'stroke-dasharray="19.4 80.6" stroke-dashoffset="-110.07"')
data = data.replace('font-weight="700">75<', 'font-weight="700">67<')
data = data.replace('S0 Critical<span class="val">5</span>', 'S0 Critical<span class="val">0</span>')
# Re-count pie percentages
data = data.replace('font-weight:400;">5 项待修复（8/13 已修复）| 预估 5 小时</span>',
                    'font-weight:400;">0 项待修复（13/13 ✅ 全部修复）</span>')

# Sprint section
data = data.replace('⚡ 下一轮推荐修复 <span style="font-size:12px;color:var(--muted);font-weight:400;">5 项 S0 剩余 | 预估 5 小时</span>',
                    '⚡ S0 已全部关闭 <span style="font-size:12px;color:var(--muted);font-weight:400;">13/13 已修复，转战 S1 性能优化</span>')
data = data.replace('<div class="progress-bar"><div class="progress-fill" style="width:65%;"></div></div>\n    <div style="margin-top:8px;font-size:13px;color:var(--muted);">整体 S0 修复进度：8 / 13（62%）— 剩余 5 项</div>',
                    '<div class="progress-bar"><div class="progress-fill" style="width:100%;background:var(--green);"></div></div>\n    <div style="margin-top:8px;font-size:13px;color:var(--muted);">整体 S0 修复进度：13 / 13（100%）✅ 全部关闭</div>')

# Heatmap S0 column values
data = data.replace('>pipeline/</td><td><span class="sev sev-s0">3</span>', '>pipeline/</td><td><span class="sev sev-s0">0</span>')
data = data.replace('>mcp/</td><td><span class="sev sev-s0">0</span>', '>mcp/</td><td>0')
data = data.replace('>gateway</td><td><span class="sev sev-s0">0</span>', '>gateway</td><td>0')
data = data.replace('>core/</td><td><span class="sev sev-s0">1</span>', '>core/</td><td><span class="sev sev-s0">0</span>')
data = data.replace('>graph/</td><td><span class="sev sev-s0">0</span>', '>graph/</td><td>0')

# Trend bars
data = data.replace('06-26 修复', '06-26 清零')
data = data.replace('62%', '100%', 1)  # trend bar value
data = data.replace('height:62px;background:var(--green)', 'height:80px;background:var(--green)')
data = data.replace('8/13 S0 已修复（v0.1.11~v0.1.12）', '13/13 S0 已全部修复（v0.1.11~v0.1.13）')

# Kanban: remove "待修复" column, rename "已修复"
data = data.replace('5 项待修复（8/13 已修复）', '0 项待修复（13/13 ✅ 全部修复）')

open(path, "w", encoding="utf-8").write(data)
print("done")
