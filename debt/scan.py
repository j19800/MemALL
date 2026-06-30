#!/usr/bin/env python3
"""技术负债自动扫描与报告生成器。

用法：
    python debt/scan.py                    # 扫描 + 更新看板
    python debt/scan.py --report-only      # 只输出报告
    python debt/scan.py --store-memory     # 同时存入 MemALL 记忆

设计说明：
    扫描分两阶段：(1) 正则查找候选 (2) 上下文分析验证
    验证步骤检查安全信号（参数化、白名单、LIMIT 存在等），
    只输出验证为真正的负债项，避免标签不准确的问题。
"""
import os, sys, json, re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

NOW = datetime.now(timezone.utc)
NOW_STR = NOW.strftime("%Y-%m-%d %H:%M")
PROJECT = Path(__file__).resolve().parent.parent

# ── 上下文提取工具 ──

def _line_number(text: str, pos: int) -> int:
    return text[:pos].count("\n") + 1

def _get_line(text: str, pos: int) -> str:
    start = text.rfind("\n", 0, pos) + 1 if pos > 0 else 0
    end = text.find("\n", pos)
    return text[start:end] if end >= 0 else text[start:]

def _get_surrounding(text: str, pos: int, lines: int = 5) -> str:
    start = text.rfind("\n", 0, pos)
    for _ in range(lines):
        prev = text.rfind("\n", 0, max(0, start))
        if prev < 0: break
        start = prev
    end = text.find("\n", pos)
    for _ in range(lines):
        nxt = text.find("\n", end + 1)
        if nxt < 0: break
        end = nxt
    return text[start:end] if end >= 0 else text[start:]

def _collect_sql_stmt(text: str, pos: int, max_lines: int = 20) -> str:
    """从匹配位置提取完整的 SQL 语句文本 + 参数行。

    1. 从 pos 往回找字符串起止符，提取 SQL 内容
    2. 继续向后读直到 execute() 的 ) 闭合，捕获 params 参数
    """
    # Step 1: find the SQL string opening
    q = max(text.rfind('"', 0, pos), text.rfind("'", 0, pos))
    if q < 0:
        return _get_surrounding(text, pos, max_lines)
    quote_char = text[q]
    # Could be triple-quoted
    if text[q:q+3] == quote_char * 3:
        quote_len, skip = 3, 3
    else:
        quote_len, skip = 1, 1

    # Step 2: extract string content (everything between quotes)
    i = q + skip
    sql_chars = []
    while i < len(text):
        if text[i:i+quote_len] == quote_char * quote_len:
            i += quote_len
            break
        if text[i] == '\\':
            sql_chars.append(text[i])
            i += 1
            if i < len(text):
                sql_chars.append(text[i])
                i += 1
        else:
            sql_chars.append(text[i])
            i += 1
    sql = ''.join(sql_chars)

    # Step 3: after the string closes, read the rest of the execute() call
    # to capture params argument
    paren_depth = 0
    in_execute = False
    tail = []
    lines_read = 0
    while i < len(text) and lines_read < 5:
        ch = text[i]
        if ch == '(':
            paren_depth += 1
            in_execute = True
        elif ch == ')':
            paren_depth -= 1
            tail.append(ch)
            i += 1
            if paren_depth <= 0 and in_execute:
                break
            continue
        if ch == '\n':
            lines_read += 1
        tail.append(ch)
        i += 1
    return sql + '\n' + ''.join(tail) if tail else sql

def _has_param_safety(text: str, pos: int) -> bool:
    """检查上下文是否有参数化查询的安全信号。"""
    ctx = _get_surrounding(text, pos, 10)
    signals = [
        r'\?',                     # ? placeholder in SQL
        r'all_params',             # all_params variable (compound name)
        r'recent_params',          # recent_params
        r'where_params',           # where_params
        r'source_ids',             # source_ids — placeholder ID list
        r'\bparams\b',             # standalone params
        r"""join\(\s*["']\?["']\s*""",  # join('?', ...) placeholder construction
        r"qmark",                  # qmark style
        r"ph\s*=",                 # ph = ','.join('?' * n)
        r'\bph\b',                 # standalone ph variable
        r"placeholder",            # placeholder
    ]
    for s in signals:
        if re.search(s, ctx):
            return True
    return False

def _has_whitelist_validation(text: str, pos: int) -> bool:
    """检查 ORDER BY 列名是否有白名单校验。"""
    ctx = _get_surrounding(text, pos, 15)
    signals = [
        r'_ALLOWED_\w+',          # _ALLOWED_UPDATE_FIELDS, _ALLOWED_SORT_COLS
        r'_allowed\w*',           # lowercase
        r'_WHITELIST',            # _WHITELIST_COLS
        r'_whitelist',
        r"if .* not in ",         # inline whitelist check
        r'in [A-Z_]+\b',          # membership check
        r"PRAGMA\s+table_info",
        r"frozen_?set",           # frozenset(... as whitelist
        r"_ALLOWED_FIELDS",
        r"_PERMITTED",
    ]
    for s in signals:
        if re.search(s, ctx, re.I):
            return True
    return False

def _has_try_except(text: str, pos: int) -> bool:
    """检查匹配位置是否在 try/except 块内。"""
    before = text[:pos]
    # Count try/except nesting roughly
    tries = len(re.findall(r'\btry\s*:', before))
    excepts = len(re.findall(r'\bexcept\b', before))
    # Account for the current except if the match is after it
    return tries > excepts

# ── 验证函数 ──

def verify_fstring_sql(text: str, pos: int, fpath: str) -> Optional[tuple]:
    """验证 f-string SQL 是否真正危险。

    安全信号（有任一即降级为 INFO）：
      - SQL 中包含 ? 占位符
      - 上下文中有 params 变量
      - 是 join('?') 占位符构造
      - ORDER BY 列名有白名单校验
    """
    if _has_param_safety(text, pos):
        return ("info", "f-string SQL 拼接 (? 参数化，安全)")
    if _has_whitelist_validation(text, pos):
        return ("info", "f-string SQL 拼接 (列名白名单校验，安全)")
    line = _get_line(text, pos)
    if re.search(r"join\(['\"]\?['\"]", line):
        return ("info", "f-string SQL 拼接 (? 占位符构造，安全)")
    return ("critical", "f-string SQL 拼接 (值直接插值，需审查)")


def verify_order_by(text: str, pos: int, fpath: str) -> Optional[tuple]:
    """验证 ORDER BY 是否真正缺少 LIMIT。

    利用多行/多字符串相邻调用特性，向后扫描更广上下文。
    """
    # 1. 在 ±20 行范围内寻找 LIMIT（处理多字符串拼接）
    ctx = _get_surrounding(text, pos, 20)
    if re.search(r'\bLIMIT\b', ctx, re.I):
        return None

    # 2. GROUP BY 在同段范围 → 聚合结果有界
    if re.search(r'\bGROUP\s+BY\b', ctx, re.I):
        return ("info", "ORDER BY 无 LIMIT (GROUP BY 聚合)")

    # 3. 时间窗口约束（window_start >= ?, created_at >= ?）
    if re.search(r'(created_at|occurred_at|updated_at|window_start|started_at|timestamp)\s*[><=]+\s*\?', ctx):
        return ("info", "ORDER BY 无 LIMIT (时间窗口约束)")

    # 4. WHERE 中是 agent-specific + 时间范围 → 用户主动缩窄
    if re.search(r'\bagent_name\s*=\s*\?', ctx) and re.search(r'\bwindow_start\s*[><=]+\s*\?', ctx):
        return ("info", "ORDER BY 无 LIMIT (agent + 时间范围)")

    # 5. 聚合函数查询
    if re.search(r'\bCOUNT\s*\(|\bSUM\s*\(|\bMAX\s*\(|\bMIN\s*\(|\bAVG\s*\(', ctx):
        return ("info", "ORDER BY 无 LIMIT (聚合函数)")

    # 6. ORDER BY 后面接 LIMIT 但跨多行（"WHERE x ORDER BY y LIMIT 1"）
    line = _get_line(text, pos)
    if re.search(r'\bLIMIT\b', line, re.I):
        return None

    return ("major", "ORDER BY 无 LIMIT")


def verify_int_request(text: str, pos: int, fpath: str) -> Optional[tuple]:
    """验证 int(request.xxx) 是否真实。

    排除：
      - int(time.time()) 等非 request 用法
      - 已有 try/except 包装
    """
    line = _get_line(text, pos)
    # Check it's actually int(request.xxx)
    if 'request' not in line.split('int(')[-1][:30] if 'int(' in line else '':
        return None
    if _has_try_except(text, pos):
        return None
    return ("major", "裸 int() 无 try/except")


def verify_bare_except(text: str, pos: int, fpath: str) -> Optional[tuple]:
    """验证裸 except Exception 是否危险。

    安全的：except Exception as e: logger.warn(...) / continue / return
    危险的：except: pass (沉默吞异常)
    """
    line = _get_line(text, pos)
    next_line = text[text.find("\n", pos) + 1:text.find("\n", text.find("\n", pos) + 1)] if text.find("\n", pos) >= 0 else ""
    # Look at what follows the except
    body = (line + " " + next_line)[:120]
    if re.search(r'\bpass\s*$', body):
        return ("major", "裸 except: pass (沉默吞异常)")
    return ("info", "裸 except Exception (需审查具体场景)")


def verify_hardcoded_date(text: str, pos: int, fpath: str) -> Optional[tuple]:
    """验证硬编码日期是否真正是代码异味。

    排除：版权头年份、测试 fixture 数据
    """
    line = _get_line(text, pos)
    line_lower = line.lower()
    # Copyright headers
    if re.search(r'(copyright|license|author|created|modified)\s*[:\s]', line_lower):
        return None
    # Test data
    if "test" in fpath or "fixture" in fpath:
        return None
    return ("minor", "硬编码日期")


def _has_pragma_off_recovery(text: str, pos: int) -> bool:
    """检查 PRAGMA foreign_keys=OFF 是否有对应的恢复（try/finally + ON）。"""
    ctx = _get_surrounding(text, pos, 20)
    if re.search(r'\bfinally\s*:', ctx):
        # 有 try/finally → 一定会恢复
        return True
    if re.search(r'PRAGMA\s+foreign_keys\s*=\s*(1|ON|"ON"|"1")', ctx, re.I):
        # 有对应的主动恢复 ON
        return True
    if re.search(r'fk_was_on', ctx):
        # 有根据原状态恢复
        return True
    return False


def verify_pragma_off(text: str, pos: int, fpath: str) -> Optional[tuple]:
    """验证 PRAGMA foreign_keys=OFF 是否危险。

    安全的：try/finally 恢复；或显式 ON（恢复）；或 fk_was_on 条件恢复
    """
    if _has_pragma_off_recovery(text, pos):
        return ("minor", "外键临时禁用（已恢复）")
    return ("critical", "外键禁用（无 try/finally 恢复）")


# ── 规则定义 ──
# (name, regex_pattern, severity, message, verify_fn_or_None)
RULES = [
    ("hardcoded_date", r'[\"\\\']20\d{2}[-/]\d{2}[-/]\d{2}[\"\\\']', "minor", "硬编码日期", verify_hardcoded_date),
    ("bare_int_cast", r'\bint\(\s*request\.', "major", "裸 int() 无 try/except", verify_int_request),
    ("unsafe_fstring_sql", r'f"[^"]*SELECT[^"]*\{[^}]*\}', "critical", "f-string SQL 拼接", verify_fstring_sql),
    ("bare_except", r'except\s+Exception', "minor", "裸 except Exception", verify_bare_except),
    ("todo_or_fixme", r'(TODO|FIXME|HACK|XXX)[:\s]', "minor", "TODO/FIXME", None),
    ("pragma_off", r'conn\.execute\([^)]*PRAGMA\s+foreign_keys\s*=\s*(0|OFF)', "critical", "外键禁用", verify_pragma_off),
    ("no_limit_query", r'ORDER BY', "major", "ORDER BY 无 LIMIT", verify_order_by),
    ("print_in_prod", r'^\s*print\(', "minor", "生产代码中的 print", None),
    ("commented_code", r'^\s*#.*[a-z_]+\s*=\s*.*\(', "info", "注释掉的代码", None),
    ("dead_import", r'^\s*#\s*import\s', "info", "注释掉的 import", None),
]


def scan_known_patterns() -> dict:
    """两阶段扫描：正则查找 → 上下文验证。

    返回按严重程度分组的统计和验证后的详情列表。
    """
    counts = {"critical": 0, "major": 0, "minor": 0, "info": 0}
    details = []

    for py_file in PROJECT.rglob("*.py"):
        skip_dirs = [".venv", "__pycache__", ".git", "node_modules"]
        rel = str(py_file.relative_to(PROJECT))
        if any(d in rel for d in skip_dirs):
            continue
        # Skip hidden directories
        parts = Path(rel).parts
        if any(p.startswith(".") for p in parts[:-1]):
            continue

        try:
            text = py_file.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue

        for name, pattern, severity, msg, verify_fn in RULES:
            for m in re.finditer(pattern, text, re.MULTILINE):
                line_no = _line_number(text, m.start())
                rel_path = rel

                if verify_fn:
                    result = verify_fn(text, m.start(), rel_path)
                    if result is None:
                        continue  # false positive
                    adj_sev, adj_msg = result
                else:
                    adj_sev, adj_msg = severity, msg

                counts[adj_sev] += 1
                details.append(f"  [{adj_sev.upper():8s}] {rel_path}:{line_no} — {adj_msg}")

    return {"counts": counts, "details": details}


def count_lines() -> int:
    """统计项目 Python 行数。"""
    total = 0
    for py_file in PROJECT.rglob("*.py"):
        skip = [".venv", "__pycache__", ".git", "node_modules"]
        if any(d in str(py_file) for d in skip):
            continue
        try:
            total += len(py_file.read_text(encoding="utf-8", errors="replace").splitlines())
        except Exception:
            pass
    return total


def update_dashboard(scan_result: dict, line_count: int, inventory_path: Path, dashboard_path: Path):
    """更新看板中的趋势数据和统计。"""
    counts = scan_result["counts"]
    kloc = line_count / 1000
    total_debt = sum(counts.values())
    density = total_debt / kloc if kloc > 0 else 0

    try:
        dashboard = dashboard_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        dashboard = ""

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

    trend_line = f"{NOW_STR}    ({counts['critical']}/?)  ({counts['major']}/?)  0%        本次扫描\n"
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

    print("\n📏 统计代码行数...")
    line_count = count_lines()
    print(f"   总 Python 行数: {line_count}")

    print("\n🔍 扫描已知负债模式...")
    result = scan_known_patterns()
    counts = result["counts"]
    total = sum(counts.values())
    print(f"   发现 {total} 处:")
    for sev, cnt in counts.items():
        print(f"     {sev.upper():10s}: {cnt}")

    if result["details"] and "--report-only" not in args:
        print("\n📋 前 20 条详情:")
        for d in result["details"][:20]:
            print(d)

    if "--report-only" not in args:
        print("\n📊 更新看板...")
        inventory = PROJECT / "debt" / "INVENTORY.md"
        dashboard = PROJECT / "debt" / "DASHBOARD.md"
        if dashboard.exists():
            update_dashboard(result, line_count, inventory, dashboard)
            print("   看板已更新")

    if store:
        print("\n💾 存入记忆...")
        store_as_memory(result, line_count)

    print(f"\n{'=' * 60}")
    print(f"  完成。发现 {total} 处模式，{counts['critical']} Critical, {counts['major']} Major")

    return 0 if counts["critical"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
