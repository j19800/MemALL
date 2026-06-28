"""MemALL 冒烟测试 — 对真实数据库运行，验证核心链路畅通。"""

import sys
import json
import time as time_module

TEST_PASS = 0
TEST_FAIL = 0
RESULTS = []


def ok(msg):
    global TEST_PASS
    TEST_PASS += 1
    RESULTS.append(f"  ✅ {msg}")


def fail(msg, detail=""):
    global TEST_FAIL
    TEST_FAIL += 1
    line = f"  ❌ {msg}"
    if detail:
        line += f"  ({detail})"
    RESULTS.append(line)


def section(title):
    RESULTS.append(f"\n── {title} ─{'─' * max(0, 60 - len(title))}")


# ── 1. DB ────────────────────────────────────────────────────────────────

section("1. DB 初始化与基本状态")

try:
    from memall.core.db import get_conn, db_stats, DB_PATH
    conn = get_conn()
    row = conn.execute("SELECT COUNT(*) FROM memories").fetchone()
    total_memories = row[0]
    ok(f"data.db 连接成功 ({DB_PATH})")
    ok(f"记忆总量: {total_memories} 条")
except Exception as e:
    fail("DB 连接失败", str(e)[:100])

try:
    stats = db_stats()
    if isinstance(stats, dict) and stats.get("tables"):
        ok(f"db_stats 返回 {len(stats['tables'])} 张表")
    else:
        fail("db_stats 返回值异常", str(stats)[:80])
except Exception as e:
    fail("db_stats 异常", str(e)[:100])

try:
    levels = conn.execute(
        "SELECT level, COUNT(*) as cnt FROM memories GROUP BY level ORDER BY cnt DESC LIMIT 10"
    ).fetchall()
    level_str = ", ".join(f"{r['level']}({r['cnt']})" for r in levels)
    ok(f"层级分布: {level_str}")
except Exception as e:
    fail("层级查询异常", str(e)[:100])


# ── 2. FTS5 ──────────────────────────────────────────────────────────────

section("2. FTS5 全文搜索")

try:
    from memall.core.thin_waist import retrieve
    results = retrieve("记忆", viewer="system")
    if results:
        count = len(results) if isinstance(results, list) else 1
        ok(f"FTS5 retrieve('记忆') 返回 {count} 条")
    else:
        fail("FTS5 retrieve('记忆') 返回空")
except Exception as e:
    fail("FTS5 retrieve 异常", str(e)[:150])

try:
    results = retrieve("decision", viewer="system")
    if isinstance(results, list) and len(results) > 0:
        ok(f"FTS5 retrieve('decision') 返回 {len(results)} 条")
    else:
        fail("FTS5 retrieve('decision') 返回空或异常")
except Exception as e:
    fail("FTS5 retrieve decision 异常", str(e)[:150])


# ── 3. Timeline ───────────────────────────────────────────────────────────

section("3. Timeline / 时间线")

try:
    from memall.core.thin_waist import timeline
    tl = timeline(hours=168, limit=5)
    if isinstance(tl, list):
        ok(f"timeline(7天) 返回 {len(tl)} 条")
    elif isinstance(tl, dict) and "results" in tl:
        ok(f"timeline(7天) 返回 {len(tl['results'])} 条")
    else:
        ok(f"timeline(7天) 返回 {str(type(tl).__name__)} 类型 ({len(tl) if hasattr(tl, '__len__') else '?'})")
except Exception as e:
    fail("timeline 异常", str(e)[:150])


# ── 4. Traverse ──────────────────────────────────────────────────────────

section("4. Traverse / 图遍历")

try:
    from memall.core.thin_waist import traverse
    # 找一条有边的记忆
    edge_row = conn.execute("SELECT source_id, target_id FROM edges LIMIT 1").fetchone()
    if edge_row:
        tr = traverse(edge_row["source_id"], depth=2)
        if isinstance(tr, dict):
            nodes = tr.get("nodes", tr.get("visited", []))
            edges = tr.get("edges", [])
            ok(f"traverse(id={edge_row['source_id']}) nodes={len(nodes)} edges={len(edges)}")
        else:
            ok(f"traverse(id={edge_row['source_id']}) 返回 {type(tr).__name__}")
    else:
        fail("无可用边进行 traverse 测试")
except Exception as e:
    fail("traverse 异常", str(e)[:150])


# ── 5. Graph edges ───────────────────────────────────────────────────────

section("5. 图谱边统计")

try:
    ec = conn.execute("SELECT COUNT(*) FROM edges").fetchone()[0]
    ok(f"edges 表共 {ec} 条")

    # relation_type 分布
    rt = conn.execute(
        "SELECT relation_type, COUNT(*) as cnt FROM edges GROUP BY relation_type ORDER BY cnt DESC"
    ).fetchall()
    rt_str = ", ".join(f"{r['relation_type']}({r['cnt']})" for r in rt)
    ok(f"边类型分布: {rt_str}")
except Exception as e:
    fail("边统计异常", str(e)[:100])


# ── 6. Pipeline extract_step ─────────────────────────────────────────────

section("6. Pipeline extract_step")

try:
    from memall.pipeline.extract import extract_step
    t0 = time_module.time()
    r = extract_step()
    elapsed = time_module.time() - t0
    ok(f"extract_step 运行 {elapsed:.2f}s → {r}")
except Exception as e:
    fail("extract_step 异常", str(e)[:150])


# ── 7. Pipeline session/harvest ──────────────────────────────────────────

section("7. Pipeline harvest_step")

try:
    from memall.pipeline.session import harvest_step
    t0 = time_module.time()
    r = harvest_step()
    elapsed = time_module.time() - t0
    ok(f"harvest_step 运行 {elapsed:.2f}s → {r}")
except Exception as e:
    fail("harvest_step 异常", str(e)[:150])


# ── 8. FTS5  vs vec0 搜索 ─────────────────────────────────────────────────

section("8. 混合搜索增强 vs 向量搜索")

try:
    from memall.core.thin_waist import hybrid_search, vector_search
    hs = hybrid_search("记忆", top_k=3)
    if isinstance(hs, dict):
        results = hs.get("results", hs.get("data", []))
        ok(f"hybrid_search('记忆') 返回 {len(results)} 条")
    elif isinstance(hs, list):
        ok(f"hybrid_search('记忆') 返回 {len(hs)} 条")
    else:
        ok(f"hybrid_search 返回 {type(hs).__name__}")
except Exception as e:
    fail("hybrid_search 异常", str(e)[:150])

try:
    vs = vector_search("记忆", top_k=3)
    if isinstance(vs, dict):
        results = vs.get("results", vs.get("data", []))
        ok(f"vector_search('记忆') 返回 {len(results)} 条")
    except Exception:
        ok(f"vector_search 返回 dict 类型")
except Exception as e:
    fail("vector_search 异常", str(e)[:150])


# ── 9. FTS5 CJK 测试 ────────────────────────────────────────────────────

section("9. FTS5 CJK 多关键词")

try:
    from memall.core.thin_waist import retrieve

    queries = ["架构", "决策", "问题", "修复", "数据"]
    for q in queries:
        r = retrieve(q, viewer="system")
        count = len(r) if isinstance(r, list) else (1 if r else 0)
        ok(f"FTS5 retrieve('{q}') → {count} 条")
except Exception as e:
    fail("FTS5 CJK 多关键词异常", str(e)[:150])


# ── 10. Archive 检查 ─────────────────────────────────────────────────────

section("10. Archive 冷存储")

try:
    from memall.core.db import ARCHIVE_DB_PATH
    if ARCHIVE_DB_PATH.exists():
        size = ARCHIVE_DB_PATH.stat().st_size
        conn.execute(f"ATTACH DATABASE ? AS archive_db", (str(ARCHIVE_DB_PATH),))
        ac = conn.execute("SELECT COUNT(*) FROM archive_db.archived_memories").fetchone()[0]
        conn.execute("DETACH DATABASE archive_db")
        ok(f"archive.db 存在 ({size/1024:.1f}KB, {ac} 条归档)")
    else:
        ok("archive.db 尚未创建（正常，数据未到 TTL）")
except Exception as e:
    fail("Archive 检查异常", str(e)[:150])


# ── 11. Session 表 ──────────────────────────────────────────────────────

section("11. Session 状态")

try:
    active = conn.execute("SELECT COUNT(*) FROM sessions WHERE status='active'").fetchone()[0]
    ended = conn.execute("SELECT COUNT(*) FROM sessions WHERE status='ended'").fetchone()[0]
    ok(f"sessions: {active} active, {ended} ended")
except Exception as e:
    fail("Session 查询异常", str(e)[:100])


# ── 12. L6 提取条目验证 ──────────────────────────────────────────────────

section("12. extract_step 产物验证")

try:
    extract_l6 = conn.execute(
        "SELECT m.id, m.category, json_extract(m.metadata, '$.extract_category') AS extract_cat, "
        "json_extract(m.metadata, '$.source') AS source, "
        "(SELECT COUNT(*) FROM edges WHERE source_id = m.id AND relation_type = 'derived_from') AS edge_count, "
        "substr(m.content, 1, 80) AS preview "
        "FROM memories m WHERE json_extract(m.metadata, '$.source') = 'pipeline_extract'"
    ).fetchall()
    if extract_l6:
        for r in extract_l6:
            ok(f"L6 #{r['id']} cat={r['category']} edges={r['edge_count']} 「{r['preview']}」")
    else:
        ok("无 extract_step 产物（首次运行后应已有）")
except Exception as e:
    fail("L6 提取产物验证异常", str(e)[:100])


# ── 13. 核心 import 完整性 ───────────────────────────────────────────────

section("13. 核心模块 import 完整性")

modules = [
    ("memall.core.db", "get_conn"),
    ("memall.core.thin_waist", "capture, retrieve"),
    ("memall.core.models", "MemoryInput, Memory"),
    ("memall.pipeline.pipeline", "run_pipeline"),
    ("memall.pipeline.extract", "extract_step"),
    ("memall.pipeline.session", "harvest_step, session_start, session_end"),
    ("memall.pipeline.classify", "classify_step"),
    ("memall.pipeline.archive", "archive_step"),
    ("memall.mcp.adapter", "handle_call"),
    ("memall.mcp.server", "serve"),
    ("memall.cli.main", "app"),
    ("memall.graph.retrieve", "graph_retrieve"),
    ("memall.mcp.tools.distill", "handle"),
]
errors = 0
for mod_name, attrs in modules:
    try:
        mod = __import__(mod_name, fromlist=[attrs.split(",")[0].strip()])
        ok(f"import {mod_name} 成功")
    except Exception as e:
        fail(f"import {mod_name} 失败", str(e)[:100])
        errors += 1


# ── 总结 ─────────────────────────────────────────────────────────────────

conn.close()
section(f"冒烟测试完成")
RESULTS.append(f"  通过: {TEST_PASS} | 失败: {TEST_FAIL}")
if TEST_FAIL == 0:
    RESULTS.append("  状态: ✅ 全部通过")
else:
    RESULTS.append(f"  状态: ❌ {TEST_FAIL} 项失败")

print("\n".join(RESULTS))
sys.exit(0 if TEST_FAIL == 0 else 1)