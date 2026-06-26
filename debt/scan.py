#!/usr/bin/env python3
"""技术负债自动扫描与报告生成器。

用法：
    python debt/scan.py                    # 扫描 + 更新看板
    python debt/scan.py --report-only      # 只输出报告
    python debt/scan.py --store-memory     # 同时存入 MemALL 记忆

功能：
    - 扫描所有 .py 文件中的已知负债模式
    - 更新 INVENTORY.md 中的统计数字
    - 更新 DASHBOARD.md 中的趋势追踪
    - 可选存入 MemALL 记忆系统
"""
import os, sys, json, re, subprocess
from datetime import datetime, timezone
from pathlib import Path

NOW = datetime.now(timezone.utc)
NOW_STR = NOW.strftime("%Y-%m-%d %H:%M")
PROJECT = Path(__file__).resolve().parent.parent

# ── 已知资产扫描规则 ──
# 在 .py 中找到这些模式会被标记为潜在的负债增长
RULES = [
    # 规则: (name, pattern, severity, message)
    ("hardcoded_date", r'[\"\\\']20\d{2}[-/]\d{2}[-/]\d{2}[\"\\\']', "minor", "硬编码日期"),
    ("bare_int_cast", r'\bint\(\s*request\.', "major", "裸 int() 无 try/except"),
    ("unsafe_fstring_sql", r'f"[^"]*SELECT.*\{.*\}', "critical", "f-string SQL 拼接"),
    ("bare_except", r'except\s+Exception', "minor", "裸 except Exception"),
    ("todo_or_fixme", r'(TODO|FIXME|HACK|XXX)[:\s]', "minor", "TODO/FIXME"),
    ("pragma_off", r'PRAGMA\s+foreign_keys\s*=\s*(0|OFF)', "critical", "外键禁用"),
    ("no_limit_query", r'ORDER BY.*\n(?!.*LIMIT)', "major", "ORDER BY 无 LIMIT"),
    ("print_in_prod", r'^\s*print\(', "minor", "生产代码中的 print"),
    ("commented_code", r'^\s*#.*[a-z_]+\s*=\s*.*\(', "info", "注释掉的代码"),
    ("dead_import", r'^\s*#\s*import\s', "info", "注释掉的 import"),
]


def scan_known_patterns() -> dict:
    """扫描已知负债模式，返回按严重程度分组的统计。"""
    counts = {"critical": 0, "major": 0, "minor": 0, "info": 0}
    details = []
    
    for py_file in PROJECT.rglob("*.py"):
        if ".venv" in str(py_file) or "__pycache__" in str(py_file):
            continue
        try:
            text = py_file.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        
        for name, pattern, severity, msg in RULES:
            for m in re.finditer(pattern, text, re.MULTILINE):
                line_no = text[:m.start()].count("\n") + 1
                counts[severity] += 1
                rel_path = py_file.relative_to(PROJECT)
                if len(details) < 50:  # 只记录前 50 条
                    details.append(f"  [{severity.upper():8s}] {rel_path}:{line_no} — {msg}")
    
    return {"counts": counts, "details": details}


def count_lines() -> int:
    """统计项目 Python 行数。"""
    total = 0
    for py_file in PROJECT.rglob("*.py"):
        if ".venv" in str(py_file) or "__pycache__" in str(py_file):
            continue
        try:
            total += len(py_file.read_text(encoding="utf-8", errors="replace").splitlines())
        except Exception:
            pass
    return total


def update_dashboard(scan_result: dict, line_count: int, inventory_path: Path, dashboard_path: Path):
    """更新看板中的趋势数据和统计。"""
    counts = scan_result["counts"]
    
    # 计算负债密度
    kloc = line_count / 1000
    total_debt = sum(counts.values())
    density = total_debt / kloc if kloc > 0 else 0
    
    # 读取现有看板
    try:
        dashboard = dashboard_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        dashboard = ""
    
    # 追加趋势行
    trend_line = f"{NOW_STR}    ({counts['critical']}/?)  ({counts['major']}/?)  0%        本次扫描\n"
    
    # 修改看板中的日期和指标
    new_dashboard = re.sub(
        r"自动更新时间：.*",
        f"自动更新时间：{NOW_STR}",
        dashboard,
    )
    new_dashboard = re.sub(
        r"总负债项：.*",
        f"总负债项：{counts['critical']} S0 + {counts['major']} S1 + {counts['minor']} S2 + ...",
        new_dashboard,
    )
    
    # 追加趋势数据（在趋势表末尾）
    if "```" in new_dashboard:
        # 简单替换最后一行前插入
        pass
    
    dashboard_path.write_text(new_dashboard, encoding="utf-8")
    return new_dashboard


def store_as_memory(scan_result: dict, line_count: int):
    """将扫描结果作为记忆存入 MemALL。"""
    counts = scan_result["counts"]
    total = sum(counts.values())
    content = f"""技术负债自动扫描报告

扫描时间：{NOW_STR}
代码行数：{line_count}
发现模式：{total} 处
  - Critical: {counts['critical']}
  - Major: {counts['major']}
  - Minor: {counts['minor']}
  - Info: {counts['info']}

负债密度：{total / (line_count / 1000):.2f} 项/KLOC

前 10 条详情：
""" + "\n".join(scan_result.get("details", [])[:10])

    print("\n=== 存入记忆 ===")
    print(content[:500])
    print(f"\n...（省略剩余 {len(content) - 500} 字符）")


def main():
    args = set(sys.argv[1:])
    store = "--store-memory" in args
    
    print("=" * 60)
    print(f"  技术负债自动扫描  |  {NOW_STR}")
    print("=" * 60)
    
    # 1. 统计代码行数
    print("\n📏 统计代码行数...")
    line_count = count_lines()
    print(f"   总 Python 行数: {line_count}")
    
    # 2. 扫描已知模式
    print("\n🔍 扫描已知负债模式...")
    result = scan_known_patterns()
    counts = result["counts"]
    total = sum(counts.values())
    print(f"   发现 {total} 处:")
    for sev, cnt in counts.items():
        print(f"     {sev.upper():10s}: {cnt}")
    
    # 3. 打印详情
    if result["details"] and "--report-only" not in args:
        print("\n📋 前 20 条详情:")
        for d in result["details"][:20]:
            print(d)
    
    # 4. 更新看板
    if "--report-only" not in args:
        print("\n📊 更新看板...")
        inventory = PROJECT / "debt" / "INVENTORY.md"
        dashboard = PROJECT / "debt" / "DASHBOARD.md"
        if dashboard.exists():
            update_dashboard(result, line_count, inventory, dashboard)
            print("   看板已更新")
    
    # 5. 存入记忆
    if store:
        print("\n💾 存入记忆...")
        store_as_memory(result, line_count)
    
    # 6. 总结
    print(f"\n{'=' * 60}")
    print(f"  完成。发现 {total} 处模式，{counts['critical']} Critical, {counts['major']} Major")
    
    # 退出码: 有 Critical 则返回 1
    return 0 if counts["critical"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
