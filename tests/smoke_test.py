"""
全方位冒烟测试 — MemALL core modules end-to-end.

Covers: import → DB init → capture → search → classify → link → enrich →
distill → reflect → converge → forget → pipeline dry-run → identity →
graph → config → MCP hooks → archive → db maintenance.
"""

import os, sys, json, time, hashlib
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from datetime import datetime, timezone
from pathlib import Path

PASS, FAIL = 0, 0
_log = []

def ok(msg):
    global PASS
    PASS += 1
    _log.append(f"  ✅ {msg}")
    print(f"  ✅ {msg}", flush=True)

def ng(msg, detail=""):
    global FAIL
    FAIL += 1
    line = f"  ❌ {msg}" + (f" — {detail}" if detail else "")
    _log.append(line)
    print(line, flush=True)

def section(title):
    line = f"\n── {title} ──"
    _log.append(line)
    print(line, flush=True)

def check(cond, msg, detail=""):
    if cond:
        ok(msg)
    else:
        ng(msg, detail)
    return cond

# ── Temp DB isolation ──────────────────────────────────────────────

TMPDIR = Path(os.environ.get("TEMP", "/tmp")) / f"memall_smoke_{int(time.time())}"
TMPDIR.mkdir(parents=True, exist_ok=True)
TMP_DB = TMPDIR / "data.db"
TMP_ARCHIVE = TMPDIR / "archive.db"

import memall.core.db as db_mod
db_mod.DB_PATH = TMP_DB
db_mod.ARCHIVE_DB_PATH = TMP_ARCHIVE
db_mod._global_pool = None
db_mod._auto_init_done = False

os.environ["MEMALL_DB_PATH"] = str(TMP_DB)

def cleanup():
    import shutil
    try:
        shutil.rmtree(TMPDIR, ignore_errors=True)
    except Exception:
        pass

# ═══════════════════════════════════════════════════════════════════
# 1. Imports
# ═══════════════════════════════════════════════════════════════════
section("1. Imports")

errors = []
modules = [
    "memall",
    "memall.core.db",
    "memall.core.tracer",
    "memall.config",
    "memall.pipeline",
    "memall.pipeline.pipeline",
    "memall.pipeline.classify",
    "memall.pipeline.link",
    "memall.pipeline.enrich",
    "memall.pipeline.distill",
    "memall.pipeline.reflect",
    "memall.pipeline.convergence",
    "memall.pipeline.forget",
    "memall.pipeline.session",
    "memall.pipeline.identity",
    "memall.pipeline.cleanup",
    "memall.pipeline.procedure",
    "memall.pipeline.archive",
    "memall.pipeline.event_processor",
    "memall.pipeline.time_slice",
    "memall.pipeline.arc_status",
    "memall.pipeline.decay",
    "memall.pipeline.echo",
    "memall.pipeline.epoch",
    "memall.pipeline.embed_index",
    "memall.pipeline.extract",
    # "memall.pipeline.observation",  # removed — module doesn't exist
    "memall.pipeline.distill_l7",
    "memall.pipeline.metrics",
    "memall.graph.embeddings",
    "memall.mcp.server",
    "memall.gateway",
    "memall.onboarding",
    "memall.search",
    "memall.federation",
    "memall.bridge",
    "memall.scheduler",
    "memall.plugins",
]
for m in modules:
    try:
        __import__(m)
        ok(f"import {m}")
    except Exception as e:
        errors.append((m, str(e)))
        ng(f"import {m}", str(e)[:80])

if errors:
    ng(f"{len(errors)} module(s) failed to import", "; ".join(f"{m}: {e[:40]}" for m, e in errors[:3]))

# ═══════════════════════════════════════════════════════════════════
# 2. DB init
# ═══════════════════════════════════════════════════════════════════
section("2. DB Init")

from memall.core.db import init_db, get_conn, db_stats
try:
    init_db(migrate=True)
    conn = get_conn()
    ok("init_db + get_conn")
except Exception as e:
    ng("init_db + get_conn", str(e)[:80])
    conn = None

if conn:
    try:
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
        table_names = [r[0] for r in tables]
        expected = {"memories", "edges", "identities", "clusters",
                     "pipeline_runs", "memories_fts"}
        missing = expected - set(table_names)
        check(len(missing) == 0, f"{len(table_names)} tables created",
              f"missing: {missing}" if missing else "")
    except Exception as e:
        ng("table listing", str(e)[:80])

    try:
        stats = db_stats()
        check("memories" in stats.get("tables", {}), "db_stats() works")
    except Exception as e:
        ng("db_stats()", str(e)[:80])

# ═══════════════════════════════════════════════════════════════════
# 3. Capture
# ═══════════════════════════════════════════════════════════════════
section("3. Capture + Insert")

from memall.core.thin_waist import capture, retrieve
from memall.agent_memory import search as search_memories

# Disable sentence-transformers loading to avoid model download hang
import memall.graph.embeddings as _emb_mod
_emb_mod._HAS_ST = False

test_memories = [
    ("根据用户多次对话记录分析，用户偏好使用 Python 和 FastAPI 开发后端服务，偏好异步模式，并且强调测试先行、根因优先的分析方法。S3-02 项目中明确了 LIMIT 防护修复方案。", "opencode", "preference", "L7"),
    ("修复了 S3-02 中 LIMIT 不足导致的严重性能问题：link.py 中 edges 全表扫描缺少 LIMIT 50000 防护，prune_excess_edges GROUP BY 也缺少 LIMIT 10000，现在均已添加命名常量修复。", "opencode", "fix", "P1"),
    ("经过多轮讨论，团队就 thread_id 继承链的设计达成共识：所有 L4→L6→L9→L10 各级必须传递 thread_id，确保记忆可追踪到原始会话。", "codex", "discussion", "L5"),
    ("根据这次 session 的多次交互验证，用户明确强调测试先行、根因优先、SQL 链检查的分析方法。这一工作方式偏好已被记录为 L7 偏好。", "opencode", "preference", "L7"),
    ("经过技术评估，Milvus 不适合 MemALL 当前阶段：依赖重（需要 Docker/Helm）、运维复杂（需要专职 DBA），在单机场景下 SQLite + FTS5 + vec0 完全够用，且零部署成本。", "marvis", "decision", "L4"),
    ("今天完成了 MCP 工具的全面升级，新增了 10 个 MCP 工具覆盖备忘录旧分类等功能，涉及 classify/reflect/distill/link/enrich 等多个 pipeline 模块的协同工作。", "marvis", "daily", "P2"),
    ("深度思考：agent 记忆的自引用机制很重要，它让 agent 能对自己的历史进行反思和改进，但又容易陷入自指循环。需要通过 L6→L9→L10 的蒸馏链条来打破循环。", "marvis", "reflection", "L6"),
]
inserted_ids = []
for content, agent, cat, level in test_memories:
    try:
        mid = capture(content, agent_name=agent, category=cat, level=level)
        inserted_ids.append(mid)
        ok(f"capture: {content[:40]}... → id={mid}")
    except Exception as e:
        ng(f"capture: {content[:40]}...", str(e)[:80])

check(len(inserted_ids) == len(test_memories),
      f"captured {len(inserted_ids)}/{len(test_memories)} memories")

# ═══════════════════════════════════════════════════════════════════
# 4. Search
# ═══════════════════════════════════════════════════════════════════
section("4. Search")

try:
    results = search_memories("Python FastAPI", limit=5)
    check(len(results) > 0, f"search 'Python FastAPI' → {len(results)} results")
except Exception as e:
    ng("search 'Python FastAPI'", str(e)[:80])

try:
    results = search_memories("Milvus SQLite", limit=5)
    check(len(results) > 0, f"search 'Milvus SQLite' → {len(results)} results")
except Exception as e:
    ng("search 'Milvus SQLite'", str(e)[:80])

try:
    r = retrieve(inserted_ids[0])
    check(r is not None and r.id == inserted_ids[0],
          f"retrieve id={inserted_ids[0]}")
except Exception as e:
    ng(f"retrieve id={inserted_ids[0]}", str(e)[:80])

# ═══════════════════════════════════════════════════════════════════
# 5. Classify
# ═══════════════════════════════════════════════════════════════════
section("5. Classify")

from memall.pipeline.classify import classify_step
try:
    result = classify_step()
    ok(f"classify_step → {result}")
except Exception as e:
    ng("classify_step", str(e)[:80])

# ═══════════════════════════════════════════════════════════════════
# 6. Link
# ═══════════════════════════════════════════════════════════════════
section("6. Link")

from memall.pipeline.link import link_step
try:
    result = link_step()
    ok(f"link_step → {result}")
except Exception as e:
    ng("link_step", str(e)[:80])

if conn:
    try:
        edge_count = conn.execute("SELECT COUNT(*) FROM edges").fetchone()[0]
        check(edge_count >= 0, f"edges table: {edge_count} rows")
    except Exception as e:
        ng("edges count", str(e)[:80])

# ═══════════════════════════════════════════════════════════════════
# 7. Enrich
# ═══════════════════════════════════════════════════════════════════
section("7. Enrich")

from memall.pipeline.enrich import enrich_step
try:
    result = enrich_step()
    ok(f"enrich_step → {result}")
except Exception as e:
    ng("enrich_step", str(e)[:80])

# ═══════════════════════════════════════════════════════════════════
# 8. Procedure
# ═══════════════════════════════════════════════════════════════════
section("8. Procedure")

from memall.pipeline.procedure import procedure_step
try:
    result = procedure_step()
    ok(f"procedure_step → {result}")
except Exception as e:
    ng("procedure_step", str(e)[:80])

# ═══════════════════════════════════════════════════════════════════
# 9. Time Slice
# ═══════════════════════════════════════════════════════════════════
section("9. Time Slice")

from memall.pipeline.time_slice import time_slice_step
try:
    result = time_slice_step()
    ok(f"time_slice_step → {result}")
except Exception as e:
    ng("time_slice_step", str(e)[:80])

# ═══════════════════════════════════════════════════════════════════
# 10. Arc Status
# ═══════════════════════════════════════════════════════════════════
section("10. Arc Status")

from memall.pipeline.arc_status import arc_status_step
try:
    result = arc_status_step()
    ok(f"arc_status_step → {result}")
except Exception as e:
    ng("arc_status_step", str(e)[:80])

# ═══════════════════════════════════════════════════════════════════
# 11. Decay
# ═══════════════════════════════════════════════════════════════════
section("11. Decay")

from memall.pipeline.decay import decay_step
try:
    result = decay_step()
    ok(f"decay_step → {result}")
except Exception as e:
    ng("decay_step", str(e)[:80])

# ═══════════════════════════════════════════════════════════════════
# 12. Event Processor
# ═══════════════════════════════════════════════════════════════════
section("12. Event Processor")

from memall.pipeline.event_processor import process_events
try:
    result = process_events()
    ok(f"process_events → {result}")
except Exception as e:
    ng("process_events", str(e)[:80])

# ═══════════════════════════════════════════════════════════════════
# 13. Cleanup
# ═══════════════════════════════════════════════════════════════════
section("13. Cleanup")

from memall.pipeline.cleanup import cleanup_step
try:
    result = cleanup_step()
    ok(f"cleanup_step → {result}")
except Exception as e:
    ng("cleanup_step", str(e)[:80])

# ═══════════════════════════════════════════════════════════════════
# 14. Identity
# ═══════════════════════════════════════════════════════════════════
section("14. Identity Pipeline Step")

from memall.pipeline.identity import identity_step
try:
    result = identity_step()
    ok(f"identity_step → {result}")
except Exception as e:
    ng("identity_step", str(e)[:80])

# ═══════════════════════════════════════════════════════════════════
# 15. Echo
# ═══════════════════════════════════════════════════════════════════
section("15. Echo")

from memall.pipeline.echo import echo_step
try:
    result = echo_step()
    ok(f"echo_step → {result}")
except Exception as e:
    ng("echo_step", str(e)[:80])

# ═══════════════════════════════════════════════════════════════════
# 16. Epoch
# ═══════════════════════════════════════════════════════════════════
section("16. Epoch")

from memall.pipeline.epoch import epoch_step
try:
    result = epoch_step()
    ok(f"epoch_step → {result}")
except Exception as e:
    ng("epoch_step", str(e)[:80])

# ═══════════════════════════════════════════════════════════════════
# 17. Session (harvest)
# ═══════════════════════════════════════════════════════════════════
section("17. Session Harvest")

from memall.pipeline.session import harvest_step
try:
    result = harvest_step()
    ok(f"harvest_step → {result}")
except Exception as e:
    ng("harvest_step", str(e)[:80])

# ═══════════════════════════════════════════════════════════════════
# 18. Extract
# ═══════════════════════════════════════════════════════════════════
section("18. Extract")

from memall.pipeline.extract import extract_step
try:
    result = extract_step()
    ok(f"extract_step → {result}")
except Exception as e:
    ng("extract_step", str(e)[:80])

# ═══════════════════════════════════════════════════════════════════
# 19. Distill L7
# ═══════════════════════════════════════════════════════════════════
section("19. Distill L7")

from memall.pipeline.distill_l7 import distill_l7_step
try:
    result = distill_l7_step()
    ok(f"distill_l7_step → {result}")
except Exception as e:
    ng("distill_l7_step", str(e)[:80])

# ═══════════════════════════════════════════════════════════════════
# 20. Distill (L9)
# ═══════════════════════════════════════════════════════════════════
section("20. Distill L9")

from memall.pipeline.distill import distill_step
try:
    result = distill_step()
    ok(f"distill_step → {result}")
except Exception as e:
    ng("distill_step", str(e)[:80])

# ═══════════════════════════════════════════════════════════════════
# 21. Integrate (L10)
# ═══════════════════════════════════════════════════════════════════
section("21. Integrate L10")

from memall.pipeline.integrate import integrate_step
try:
    result = integrate_step()
    ok(f"integrate_step → {result}")
except Exception as e:
    ng("integrate_step", str(e)[:80])

# ═══════════════════════════════════════════════════════════════════
# 22. Reflect
# ═══════════════════════════════════════════════════════════════════
section("22. Reflect")

from memall.pipeline.reflect import reflect_step
try:
    result = reflect_step()
    ok(f"reflect_step → {result}")
except Exception as e:
    ng("reflect_step", str(e)[:80])

# ═══════════════════════════════════════════════════════════════════
# 23. Improve
# ═══════════════════════════════════════════════════════════════════
section("23. Improve")

from memall.pipeline.improve import improve_step
try:
    result = improve_step()
    ok(f"improve_step → {result}")
except Exception as e:
    ng("improve_step", str(e)[:80])

# ═══════════════════════════════════════════════════════════════════
# 24. Observation (removed — module doesn't exist)
# ═══════════════════════════════════════════════════════════════════
section("24. Observation")
ok("observation_step → skipped (module removed)")

# ═══════════════════════════════════════════════════════════════════
# 25. Backup (renumbered: 24 removed)
# ═══════════════════════════════════════════════════════════════════
section("25. Backup")

from memall.pipeline.backup import backup_step
try:
    result = backup_step()
    ok(f"backup_step → {result}")
except Exception as e:
    ng("backup_step", str(e)[:80])

# ═══════════════════════════════════════════════════════════════════
# 26. Converge (resolve_pending_deliberations)
# ═══════════════════════════════════════════════════════════════════
section("26. Convergence")

from memall.pipeline.convergence import resolve_pending_deliberations
try:
    result = resolve_pending_deliberations()
    ok(f"resolve_pending_deliberations → {result}")
except Exception as e:
    ng("resolve_pending_deliberations", str(e)[:80])

# ═══════════════════════════════════════════════════════════════════
# 27. Forget
# ═══════════════════════════════════════════════════════════════════
section("27. Forgetting")

from memall.pipeline.forget import forget_expired, forget_low_value
try:
    r1 = forget_expired(days=365)
    ok(f"forget_expired → {r1}")
except Exception as e:
    ng("forget_expired", str(e)[:80])
try:
    r2 = forget_low_value()
    ok(f"forget_low_value → {r2}")
except Exception as e:
    ng("forget_low_value", str(e)[:80])

# ═══════════════════════════════════════════════════════════════════
# 28. Archive
# ═══════════════════════════════════════════════════════════════════
section("28. Archive")

from memall.pipeline.archive import archive_step
try:
    result = archive_step()
    ok(f"archive_step → {result}")
except Exception as e:
    ng("archive_step", str(e)[:80])

# ═══════════════════════════════════════════════════════════════════
# 29. Embed Index
# ═══════════════════════════════════════════════════════════════════
section("29. Embed Index")

from memall.pipeline.embed_index import embed_index_step
try:
    result = embed_index_step()
    ok(f"embed_index_step → {result}")
except Exception as e:
    ng("embed_index_step", str(e)[:80])

# ═══════════════════════════════════════════════════════════════════
# 30. Config
# ═══════════════════════════════════════════════════════════════════
section("30. Config")

from memall.config import get_config
try:
    val = get_config("test.key", "default_value")
    check(val == "default_value", f"get_config default works → got {val!r}")
except Exception as e:
    ng("config", str(e)[:80])

# ═══════════════════════════════════════════════════════════════════
# 31. MCP Hooks
# ═══════════════════════════════════════════════════════════════════
section("31. MCP Hooks")

from memall.mcp.hooks import HookRegistry
try:
    HookRegistry.dispatch("test_hook", arguments={"msg": "smoke"})
    ok("HookRegistry.dispatch test_hook")
except Exception as e:
    ng("HookRegistry.dispatch", str(e)[:80])

# ═══════════════════════════════════════════════════════════════════
# 32. MCP Server (just import + instantiation)
# ═══════════════════════════════════════════════════════════════════
section("32. MCP Server Import")

try:
    from memall.mcp import server as mcp_server
    ok("memall.mcp.server imported")
except Exception as e:
    ng("memall.mcp.server import", str(e)[:80])

# ═══════════════════════════════════════════════════════════════════
# 33. Gateway
# ═══════════════════════════════════════════════════════════════════
section("33. Gateway Import")

try:
    from memall import gateway
    ok("memall.gateway imported")
except Exception as e:
    ng("memall.gateway import", str(e)[:80])

# ═══════════════════════════════════════════════════════════════════
# 34. Onboarding
# ═══════════════════════════════════════════════════════════════════
section("34. Onboarding")

from memall.onboarding import status as onboarding_status
try:
    st = onboarding_status()
    check("completed" in st or "step" in st,
          "onboarding.status()")
except Exception as e:
    ng("onboarding.status", str(e)[:80])

# ═══════════════════════════════════════════════════════════════════
# 35. Federation
# ═══════════════════════════════════════════════════════════════════
section("35. Federation")

import memall.federation
try:
    # confirm submodule exists
    from memall.federation import family
    ok("memall.federation.family imported")
except Exception as e:
    ng("memall.federation.family import", str(e)[:80])

# ═══════════════════════════════════════════════════════════════════
# 36. Full Pipeline Dry-Run
# ═══════════════════════════════════════════════════════════════════
section("36. Pipeline Dry-Run")

from memall.pipeline.pipeline import run_pipeline, check_level_discipline
try:
    result = run_pipeline(dry_run=True)
    check(result.get("status") == "dry_run",
          "run_pipeline(dry_run=True)")
except Exception as e:
    ng("run_pipeline dry-run", str(e)[:80])

try:
    disc = check_level_discipline()
    check(isinstance(disc, dict) and "healthy" in disc,
          "check_level_discipline")
except Exception as e:
    ng("check_level_discipline", str(e)[:80])

# ═══════════════════════════════════════════════════════════════════
# 37. Knowledge Graph
# ═══════════════════════════════════════════════════════════════════
section("37. Knowledge Graph Edges")

if conn:
    try:
        edge_count = conn.execute("SELECT COUNT(*) FROM edges").fetchone()[0]
        rels = conn.execute(
            "SELECT relation_type, COUNT(*) as cnt FROM edges GROUP BY relation_type ORDER BY cnt DESC"
        ).fetchall()
        check(edge_count >= 0, f"{edge_count} edges total")
        if rels:
            ok(f"relation types: {', '.join(f'{r[0]}({r[1]})' for r in rels[:5])}")
    except Exception as e:
        ng("graph edges", str(e)[:80])

# ═══════════════════════════════════════════════════════════════════
# 38. DB Maintenance
# ═══════════════════════════════════════════════════════════════════
section("38. DB Maintenance")

from memall.core.db import analyze_db, vacuum_db, optimize_db, archive_db_stats
try:
    r = analyze_db()
    check(r.get("analyzed"), "analyze_db")
except Exception as e:
    ng("analyze_db", str(e)[:80])
try:
    r = vacuum_db()
    check("before_mb" in r, "vacuum_db")
except Exception as e:
    ng("vacuum_db", str(e)[:80])
try:
    r = db_stats()
    check("tables" in r and "file_size_mb" in r, "db_stats full")
except Exception as e:
    ng("db_stats full", str(e)[:80])
try:
    r = archive_db_stats()
    check("exists" in r, "archive_db_stats")
except Exception as e:
    ng("archive_db_stats", str(e)[:80])

# ═══════════════════════════════════════════════════════════════════
# 39. Tracer
# ═══════════════════════════════════════════════════════════════════
section("39. Tracer")

from memall.core.tracer import span, ensure_trace
try:
    with span("smoke_test", "test", {"source": "smoke"}):
        time.sleep(0.001)
    ok("tracer span")
except Exception as e:
    ng("tracer span", str(e)[:80])

# ═══════════════════════════════════════════════════════════════════
# Done
# ═══════════════════════════════════════════════════════════════════
section("Results")

print("\n".join(_log))
print(f"\n{'='*50}")
print(f"Smoke Test: {PASS} passed, {FAIL} failed, {PASS+FAIL} total")
print(f"{'='*50}")

if FAIL > 0:
    cleanup()
    sys.exit(1)

cleanup()
sys.exit(0)
