"""Regression guards: one named check per fixed bug (P2.11).

Ported from dsh-meow-memory's scripts/check-fixes.mjs practice — every bug that
was ever fixed silently (no error, wrong results) gets a small permanent guard
here, so a future refactor that reintroduces it fails loudly in one command:

    python check_fixes.py            # run all guards
    python check_fixes.py --list     # show the manifest
    python check_fixes.py fts_cjk    # run selected guards

Each guard is a function returning (ok: bool, detail: str) against an isolated
temp DB.  The manifest is also documentation: bug -> root cause -> guard.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from typing import Callable, Dict, Tuple

SRC = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SRC)


def _fresh_db() -> str:
    """Point MemALL at a throwaway DB and return its path.

    Also isolates the deny-list: the deployment's
    ``security.disabled_agents`` patterns (which exist to keep test/audit
    identities out of the *production* store) must not disable the guards
    themselves — the guards write under agents like "guard" and would be
    rejected by their own ban list.  The ban lists are neutralised for the
    duration of the guard.
    """
    tmp = tempfile.mkdtemp(prefix="memall_guard_")
    path = os.path.join(tmp, "guard.db")
    # Drain capture-spawned background threads BEFORE repointing DB_PATH:
    # a still-running lightweight-pipeline thread calls get_conn() which
    # resolves the *module-global* path at run time — it would hit the half-
    # initialised new file ("no such table: memories" / "database is locked",
    # randomly poisoning whichever guard/suite ran next).
    try:
        from memall.core.bg_registry import wait_for_background

        left = wait_for_background(timeout=20.0)
        if left:
            print(f"note: {left} background thread(s) still alive after drain")
    except Exception:
        pass
    os.environ["MEMALL_DB_PATH"] = path
    # Reset cached singletons so the next import-time DB_PATH resolves fresh.
    import memall.config as mc
    mc.reset_config()
    sec = dict(mc.get_config("security") or {})
    sec["disabled_agents"] = []
    sec["disabled_projects"] = []
    mc._config["security"] = sec
    import memall.core.db as mdb
    # Close the previous pool's connections before dropping it.  Merely
    # dropping the reference leaves open handles on the *production* file
    # (and its WAL), which then make a later guard fail with "database is
    # locked" — a cross-guard artifact that looks like a product bug.
    try:
        old_pool = mdb._global_pool
        if old_pool is not None:
            old_pool.close_all()
    except Exception:
        pass
    mdb._global_pool = None
    mdb.DB_PATH = __import__("pathlib").Path(path)
    mdb._auto_init_done = False
    return path


# ── Guards ─────────────────────────────────────────────────────────────


def guard_fts_cjk() -> Tuple[bool, str]:
    """FTS5 CJK tokenisation: Chinese queries must return rows (was: 0 rows,
    silently — unicode61 treats a CJK run as one token)."""
    _fresh_db()
    from memall.core.db import init_db
    from memall.core.thin_waist import capture, retrieve

    init_db()
    capture("检索架构采用 FTS5 与向量混合排序，RRF 融合分数作为最终排序依据，"
            "兼顾关键词召回与语义匹配，已经过实测验证。",
            level="P2", category="architecture", project="g", agent_name="guard",
            owner="老陈")
    hits = retrieve("检索", agent_name="guard", limit=5)
    return len(hits) >= 1, f"retrieve(检索) -> {len(hits)} hits"


def guard_fts_tags_indexed() -> Tuple[bool, str]:
    """tags are FTS-indexed (was: tags column existed but never indexed/read)."""
    _fresh_db()
    from memall.core.db import init_db
    from memall.core.thin_waist import capture, retrieve

    init_db()
    marker = "qqzqxw"
    capture("一条正文不含任何特殊标记词的记忆，仅通过标签暴露其主题内容。",
            level="P2", category="general", project="g", agent_name="guard",
            owner="老陈", tags=json.dumps([marker]))
    hits = retrieve(marker, agent_name="guard", limit=5)
    return len(hits) == 1, f"tag-only token search -> {len(hits)} hits (expect 1)"


def guard_auto_capture_registered() -> Tuple[bool, str]:
    """auto-capture hook is registered (was: module never imported -> dead)."""
    import memall.mcp.adapter  # noqa: F401  (the import that wires it)
    from memall.core.lifecycle import HookRegistry, HOOK_POST_TOOL_USE

    hooks = [h for h in HookRegistry._hooks
             if h.hook_point == HOOK_POST_TOOL_USE and "Auto-capture" in h.description]
    return bool(hooks), f"{len(hooks)} auto-capture hook(s)"


def guard_classify_preserves_level() -> Tuple[bool, str]:
    """Background classify must not demote an explicit L6 nor clobber its
    category to "general" (was: unmatched text fell back to P2/general)."""
    _fresh_db()
    from memall.core.db import init_db, get_conn
    from memall.core.thin_waist import capture

    init_db()
    mid = capture("根因分析：契约校验器与处理器各存一份枚举导致漂移，所以必须同源，"
                  "这是经过确认的结论，后续改进按此执行。",
                  level="L6", category="reflection", project="g", agent_name="guard",
                  owner="老陈")
    # Run the inline classifier directly (the capture-triggered background pass
    # is racy in a short-lived process; the step function is the same code).
    from memall.pipeline.event_processor import _inline_classify
    conn = get_conn()
    row = conn.execute("SELECT * FROM memories WHERE id=?", (mid,)).fetchone()
    _inline_classify(conn, dict(row), {})
    conn.commit()
    after = conn.execute("SELECT level, category FROM memories WHERE id=?", (mid,)).fetchone()
    conn.close()
    # Level must be preserved.  Category may legitimately move when a rule
    # matches (that is classify's job) — the regression being guarded is the
    # silent clobber to "general" when NO rule matched.
    ok = after["level"] == "L6" and after["category"] != "general"
    return ok, f"after classify: {after['level']}/{after['category']}"


def guard_english_l6_accepted() -> Tuple[bool, str]:
    """English L6 reflections pass the quality gate (was: Chinese-only reasoning
    markers -> reasoning=0 -> every English L6 rejected)."""
    _fresh_db()
    from memall.core.db import init_db
    from memall.core.thin_waist import capture

    init_db()
    try:
        mid = capture("Root cause: the two copies of the enum drifted apart, so "
                      "the fix is one source of truth. Analysis confirmed by "
                      "measurement and the conclusion is therefore decided.",
                      level="L6", category="reflection", project="g",
                      agent_name="guard", owner="老陈")
        return mid > 0, f"id={mid}"
    except ValueError as e:
        return False, f"rejected: {e}"


def guard_ask_excludes_own_output() -> Tuple[bool, str]:
    """ask evidence pool excludes the system's own 推演 (feedback-loop guard)."""
    _fresh_db()
    from memall.core.db import init_db, get_conn
    from memall.pipeline.ask import _search_memories

    init_db()
    conn = get_conn()
    conn.execute(
        "INSERT INTO memories (content, content_hash, level, owner, agent_name, "
        "subject, project, category, summary, occurred_at, created_at, updated_at, "
        "metadata) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("关于检索的推演结论。", "hash-guard-ask", "L4", "老陈", "guard",
         "推演", "g", "decision", "", "2026-01-01", "2026-01-01", "2026-01-01",
         json.dumps({"source": "ask"})),
    )
    conn.commit()
    rows = _search_memories(conn, "检索", agent_name="guard", limit=10)
    conn.close()
    leaked = any("推演结论" in (r["content"] or "") for r in rows)
    return not leaked, f"evidence rows: {len(rows)}, leaked={leaked}"


def guard_distill_excludes_auto_rows() -> Tuple[bool, str]:
    """distill source query excludes auto-captured rows (speculation must not
    be promoted into L9 knowledge)."""
    import inspect
    from memall.pipeline import distill

    src = inspect.getsource(distill.distill_step)
    # The markers appear inside escaped SQL string literals; match the marker
    # words and count the exclusion clauses rather than exact quoting.
    n_excl = src.count("NOT LIKE")
    ok = n_excl >= 3 and all(m in src for m in ("ask", "reflect", "discussion"))
    return ok, f"{n_excl} exclusion clauses"


def guard_hash_hit_promotion() -> Tuple[bool, str]:
    """Re-capture at a higher level promotes the row (was: silently discarded)."""
    _fresh_db()
    from memall.core.db import init_db, get_conn
    from memall.core.thin_waist import capture

    init_db()
    text = ("同一条内容重复捕获时必须体现调用方的升级意图，而不是静默丢弃，"
            "根因是去重分支只加了计数没有比较层级。")
    mid = capture(text, level="P2", category="general", project="g",
                  agent_name="guard", owner="老陈")
    capture(text, level="L4", category="decision", project="g",
            agent_name="guard", owner="老陈")
    conn = get_conn()
    row = conn.execute("SELECT level FROM memories WHERE id=?", (mid,)).fetchone()
    n = conn.execute("SELECT COUNT(*) c FROM memories WHERE content=?",
                     (text,)).fetchone()["c"]
    conn.close()
    return row["level"] == "L4" and n == 1, f"level={row['level']} rows={n}"


def guard_mcp_contract_fields() -> Tuple[bool, str]:
    """Per-action sub_action fields exist and record_interaction is on the
    WRITE tool (was: one flat enum mixing 7 vocabularies; a write action behind
    readOnlyHint)."""
    import memall.mcp.adapter as _ad
    from memall.mcp.adapter import TOOL_DEFINITIONS

    # Tools register lazily so the stdio server can answer `initialize`
    # without the ~11s heavy import; reading TOOL_DEFINITIONS before this
    # yields an empty list.
    _ad._ensure_loaded()
    write = next(t for t in TOOL_DEFINITIONS if t["name"] == "memall_write")
    read = next(t for t in TOOL_DEFINITIONS if t["name"] == "memall_read")
    wprops = write["inputSchema"]["properties"]
    ractions = read["inputSchema"]["properties"]["action"]["enum"]
    ok = ("forget_sub_action" in wprops and "ops_sub_action" in wprops
          and "record_interaction" in wprops["action"]["enum"]
          and "record_interaction" not in ractions)
    return ok, "schema fields checked"


def guard_unknown_tool_error() -> Tuple[bool, str]:
    """Unknown tool names a real tool list (was: 'unknown action ... Valid
    actions: <empty>')."""
    import memall.mcp.adapter as ad

    out = json.loads(ad.handle_call("memall_no_such_tool", {"action": "x"}))
    ok = out.get("error", "").startswith("unknown tool") and "memall_read" in out.get("valid_tools", "")
    return ok, str(out)[:120]


def guard_config_hot_reload() -> Tuple[bool, str]:
    """Editing ~/.memall/config.json takes effect without restart (was: cached
    forever in the long-lived MCP server)."""
    import memall.config as mc

    fake_home = tempfile.mkdtemp(prefix="memall_guard_home_")
    os.makedirs(os.path.join(fake_home, ".memall"), exist_ok=True)
    old_home = os.environ.get("USERPROFILE") or os.environ.get("HOME")
    os.environ["USERPROFILE"] = fake_home
    os.environ["HOME"] = fake_home
    try:
        mc.reset_config()
        before = mc.get_config("gateway.port")
        with open(os.path.join(fake_home, ".memall", "config.json"), "w") as f:
            json.dump({"gateway": {"port": 9911}}, f)
        mc._last_mtime_check = 0.0  # defeat the 2s throttle for the guard
        after = mc.get_config("gateway.port")
        return before == 9919 and after == 9911, f"{before} -> {after}"
    finally:
        os.environ["USERPROFILE"] = old_home or ""
        os.environ["HOME"] = old_home or ""
        mc.reset_config()


def guard_seen_never_drops() -> Tuple[bool, str]:
    """Seen tracking demotes but never excludes (the anti-drift rule)."""
    _fresh_db()
    from memall.core.db import init_db
    import memall.config as mc
    from memall.search import seen

    init_db()
    mc._config["search"]["seen_tracking"]["enabled"] = True
    items = [{"memory_id": i} for i in range(10)]
    seen.reset()
    seen.record("a", "s1", [0, 1, 2, 3, 4, 5])
    out = seen.reorder_novelty(list(items), "a", "s1")
    ok = len(out) == len(items)  # nothing dropped
    mc.reset_config()
    return ok, f"{len(items)} in, {len(out)} out"


def guard_pipeline_partial() -> Tuple[bool, str]:
    """A run with failed steps reports 'partial', not 'ok' (was: silent green)."""
    import inspect
    from memall.pipeline import pipeline

    src = inspect.getsource(pipeline.run_pipeline)
    ok = "failed_steps" in src and '"partial"' in src
    fsrc = inspect.getsource(pipeline._finalize_pipeline_run)
    return ok and "partial" in fsrc, "run_pipeline + finalize surface partial"


def guard_disabled_write_denylist() -> Tuple[bool, str]:
    """Disabled agents cannot write (test/audit identities pollute the store)."""
    _fresh_db()
    from memall.core.db import init_db
    from memall.core.thin_waist import capture
    import memall.config as mc

    init_db()
    mc._config["security"]["disabled_agents"] = ["banned_x"]
    try:
        capture("这条内容来自被停用的 agent，必须被拒绝写入以免污染长期记忆库，"
                "停名单就是为此而设的机制。",
                level="P2", category="general", project="g", agent_name="banned_x",
                owner="老陈")
        return False, "write was allowed"
    except ValueError as e:
        return "disabled" in str(e), str(e)[:80]
    finally:
        mc.reset_config()


def guard_tests_use_isolated_db() -> Tuple[bool, str]:
    """No test script may write the production store.

    The suites used to run against the live DB — test_auto_capture.py and
    test_m2_m3_m4.py each left rows behind (22 "test_agent" rows and ~7
    random-suffixed "autocap*" batches), indistinguishable from real
    history.  They now pin MEMALL_DB_PATH to a temp dir and neutralise the
    deny-list.  This guard keeps them that way.
    """
    import re as _re

    required = {
        "test_auto_capture.py": ["MEMALL_DB_PATH", "disabled_agents"],
        "test_match_consistency.py": ["MEMALL_DB_PATH", "disabled_agents"],
        "test_m2_m3_m4.py": ["MEMALL_DB_PATH", "disabled_agents"],
        "test_search_features.py": ["MEMALL_DB_PATH"],
    }
    problems = []
    for fname, needles in required.items():
        path = os.path.join(SRC, fname)
        if not os.path.exists(path):
            problems.append(f"{fname}: missing")
            continue
        src = open(path, encoding="utf-8").read()
        for needle in needles:
            if needle not in src:
                problems.append(f"{fname}: no {needle}")
        # The temp DB must be set BEFORE the memall import.
        if src.find("MEMALL_DB_PATH") > src.find("from memall"):
            problems.append(f"{fname}: MEMALL_DB_PATH set after the memall import")
    return not problems, "; ".join(problems) or "all suites isolated"


def guard_denylist_supports_globs() -> Tuple[bool, str]:
    """The deny-list matches globs (autocap*), because per-run suffixed
    identities cannot be enumerated by hand."""
    from memall.core.denylist import matches_any

    ok = (matches_any("autocapdimtsxcg", ["autocap*"])
          and not matches_any("workbuddy", ["autocap*"])
          and matches_any("mcp_audit", ["mcp_audit"])
          and matches_any("probe6", ["probe?"]))
    return ok, "glob/exact matching"


def guard_no_row_get_hazards() -> Tuple[bool, str]:
    """No pipeline step may call .get() on a sqlite3.Row.

    sqlite3.Row has no .get(); a row must become dict(r) first.  The
    convergence step shipped this bug for months (the failure only surfaced
    when a discussion actually converged), so it is worth a static check.

    Scope: a binding only counts if the .get() happens in the SAME function
    as the fetch — a later rebinding of the same name to a plain dict is a
    false positive otherwise.
    """
    import re as _re
    from pathlib import Path as _Path

    fetch = _re.compile(
        r"^\s*(\w+)\s*=\s*\w*conn\w*\s*\.\s*execute\s*\(", _re.M
    )
    get = _re.compile(r"^\s*.*\b(\w+)\.get\(", _re.M)
    func = _re.compile(r"^def\s+(\w+)|^    def\s+(\w+)", _re.M)

    problems = []
    for path in sorted(_Path(r"E:\MemALL\src\memall\pipeline").glob("*.py")):
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        # Per-function scope: {func_name: {var: line}}
        scopes: dict = {}
        cur = "<module>"
        bound: dict = {}
        scopes[cur] = bound
        for i, line in enumerate(lines, 1):
            fm = func.match(line)
            if fm:
                cur = fm.group(1) or fm.group(2)
                bound = {}
                scopes[cur] = bound
            m = fetch.match(line)
            if m:
                chunk = "\n".join(lines[i - 1:i + 6])
                if ".fetch" in chunk:
                    bound[m.group(1)] = i
            for gm in get.finditer(line):
                var = gm.group(1)
                if var in bound:
                    problems.append(f"{path.name}:{i} {var} (in {cur})")
    return not problems, "; ".join(problems) or "no Row.get() hazards"


def guard_pipeline_ignores_archived() -> Tuple[bool, str]:
    """Content-scanning pipeline steps must exclude archived rows.

    ``memory_status='archived'`` means soft-deleted.  A step that scans
    archived rows re-derives deleted material, and if the archived row
    belongs to a denied identity the write is refused — which failed the
    whole step ("distill_l7: capture: agent ... is disabled").
    """
    import inspect
    from memall.pipeline import distill, distill_l7, reflect, promote, link, enrich, classify

    checks = {
        "distill.distill_step": distill.distill_step,
        "distill_l7.distill_l7_step": distill_l7.distill_l7_step,
        "reflect.reflect_step": reflect.reflect_step,
        "promote.promote_step": promote.promote_step,
        "link.link_step": link.link_step,
        "enrich.enrich_step": enrich.enrich_step,
        "classify.classify_step": classify.classify_step,
    }
    missing = []
    for name, fn in checks.items():
        src = inspect.getsource(fn)
        if "memory_status" not in src or "archived" not in src:
            missing.append(name)
    return not missing, ", ".join(missing) or "all scanning steps filter archived"


def guard_denylist_before_normalize() -> Tuple[bool, str]:
    """Deny-list must run on the RAW identity, before normalize_agent_name.

    A name with 10+ consecutive digits (autocap2026..., probe123456789012)
    normalises to "system", which is not on the deny-list — checking after
    normalisation lets exactly the test identities the list exists to stop
    write straight through.
    """
    _fresh_db()
    import memall.config as mc
    from memall.core.db import init_db
    from memall.core.thin_waist import capture

    init_db()
    mc._config["security"]["disabled_agents"] = ["probe*"]
    try:
        mid = capture("这条内容用命中拒绝名单通配符且含十位以上连续数字的身份写入，"
                      "归一化会把它变成 system 从而尝试绕过拒绝名单的检查。",
                      level="P2", category="general", project="g",
                      agent_name="probe123456789012", owner="老陈")
        ok = False  # write succeeded -> bypass still open
        detail = f"WROTE id={mid}"
    except ValueError as e:
        ok = "disabled" in str(e)
        detail = str(e)[:80]
    finally:
        mc.reset_config()
    return ok, detail


def guard_search_ranks_by_bm25() -> Tuple[bool, str]:
    """FTS retrieve() must order by bm25 relevance, not recency.

    The IN-subquery form evaluates bm25() to a constant 0 (the MATCH is not
    in the outer FROM), so ORDER BY bm25 silently degenerates back to the
    recency tiebreak — verified by two rows that contain the full query
    phrase being outranked by noise matching only a 2-char bigram.
    """
    _fresh_db()
    from memall.core.db import init_db, pool_conn
    from memall.core.thin_waist import capture, retrieve

    init_db()
    noise = capture("这是一条只含测试两字的噪音记忆，用于验证 bm25 排序不会把无关结果排在前面，"
                    "因为最近写入的噪音曾经靠 occurred_at 挤掉了真正匹配的记忆。",
                    level="P2", category="testing", project="g", agent_name="bm25g",
                    owner="老陈")
    exact = capture("验收标记词组完整出现的记忆 alpha，用于被 bm25 排序提前，"
                    "这是验证检索相关性排序的核心用例。",
                    level="P2", category="testing", project="g", agent_name="bm25g",
                    owner="老陈")
    with pool_conn() as c:
        c.execute("UPDATE memories SET occurred_at = datetime('now') WHERE id = ?",
                  (noise,))
        c.execute("UPDATE memories SET occurred_at = '2020-01-01T00:00:00' "
                  "WHERE id = ?", (exact,))
        c.commit()
    hits = retrieve("验收标记词组", agent_name="bm25g", limit=2)
    order = [m.id for m in hits]
    return order and order[0] == exact, f"order={order} exact={exact}"


def guard_facets_match_population() -> Tuple[bool, str]:
    """Facet counts must describe the SAME population as the results.

    An earlier version built the facet WHERE without the identity filter, so
    a 4-result page came back with 'L9: 259' — the whole store.
    """
    _fresh_db()
    from memall.core.db import init_db
    from memall.core.thin_waist import capture, enhanced_search

    init_db()
    for i in range(3):
        capture(f"分面对齐验证词条 alpha{i}：这条记忆属于本测试 agent，"
                "用于检查分面计数是否只统计候选集而不是全库。",
                level="P2", category="testing", project="facetproj",
                agent_name="facetg", owner="老陈")
    other = capture("分面对齐验证词条不属于该 agent：同查询词但不同 agent，"
                    "如果它进了分面计数就说明过滤集漂移了。",
                    level="L9", category="testing", project="other",
                    agent_name="otherg", owner="老陈")
    res = enhanced_search(query="分面对齐验证词条", agent_name="facetg",
                          limit=5, facet_fields=["level"])
    facets = res.get("facets", {}).get("level", {})
    total = sum(facets.values())
    n_res = len(res["results"])
    ok = (total == n_res == 3) and "L9" not in facets
    return ok, f"facet_total={total} results={n_res} levels={facets}"


def guard_hot_topics_functional() -> Tuple[bool, str]:
    """`hot` must return rows (the week_ago cutoff was set to now()) and
    must not surface archived rows."""
    _fresh_db()
    import json
    from memall.core.db import get_conn, init_db
    from memall.core.thin_waist import capture
    from memall.mcp.adapter import handle_call

    init_db()
    mid = capture("热门榜单验证记忆：这条记忆刚被访问过，应当出现在热榜里而不是被时间窗过滤掉。",
                  level="P2", category="testing", project="hotg", agent_name="hotg",
                  owner="老陈")
    conn = get_conn()
    conn.execute("UPDATE memories SET access_count = 9 WHERE id = ?", (mid,))
    conn.commit()
    conn.close()
    hot = json.loads(handle_call("memall_system", {"action": "hot"}))
    surfaced = any(h["id"] == mid for h in hot.get("hot_topics", []))

    # now archive it — must vanish from the hot list
    conn = get_conn()
    conn.execute("UPDATE memories SET memory_status='archived' WHERE id = ?", (mid,))
    conn.commit()
    conn.close()
    hot2 = json.loads(handle_call("memall_system", {"action": "hot"}))
    gone = not any(h["id"] == mid for h in hot2.get("hot_topics", []))
    return surfaced and gone, f"surfaced={surfaced} archived_gone={gone}"


def guard_digest_excludes_archived() -> Tuple[bool, str]:
    """digest aggregates live rows only (it's a planning surface, not a
    history read)."""
    _fresh_db()
    import json
    from memall.core.db import get_conn, init_db
    from memall.core.thin_waist import capture
    from memall.mcp.adapter import handle_call

    init_db()
    mid = capture("每日摘要排除验证：这条今天写入的记忆先计入摘要，归档后应当从摘要中消失，"
                  "因此这条内容本身需要足够长并通过质量门的实测。",
                  level="P2", category="acceptance2", project="dg", agent_name="dgg",
                  owner="老陈")
    if not mid:
        return False, "capture returned no id (quality gate?)"
    d1 = json.loads(handle_call("memall_system", {"action": "digest"}))
    in1 = "acceptance2" in json.dumps(d1, ensure_ascii=False)
    conn = get_conn()
    conn.execute("UPDATE memories SET memory_status='archived' WHERE id = ?", (mid,))
    conn.commit()
    conn.close()
    d2 = json.loads(handle_call("memall_system", {"action": "digest"}))
    gone = "acceptance2" not in json.dumps(d2, ensure_ascii=False)
    return in1 and gone, f"mid={mid} present={in1} archived_gone={gone}"


def guard_fts_query_survives_syntax() -> Tuple[bool, str]:
    """fts_query() output must be valid MATCH syntax for ANY user text.

    Pasted code/JSON (quotes, braces) was parsed as FTS5 query syntax and
    raised "unterminated string" on ~5% of real production content slices.
    AND: the escaped form must keep phrase adjacency — per-char quoted
    terms without an outer phrase are implicit-AND (measured: 394 candidates
    vs 284 for the phrase form), which silently floods results with noise.
    """
    import sqlite3

    nasty = ['tools/call: {"meth', 'quote " inside', 'search"]', "{brace}",
            '"unclosed', "a AND b OR c", "NOT 存在", "NEAR/3 靠近"]
    bad = []
    _fresh_db()
    from memall.core.db import init_db, pool_conn
    from memall.core.thin_waist import capture, fts_query

    init_db()
    # one row with the chars ADJACENT as tokens, one with other CJK tokens
    # BETWEEN them: the phrase form must find only the first, the
    # implicit-AND form finds both.  (Spacing the chars apart in raw text
    # does NOT help — CJK indexing already emits single-char tokens, so the
    # real test is other tokens between them.)
    capture("验收测试相邻性语料：这里验收两个字符相邻出现，用于区分短语与 AND 语义。",
            level="P2", category="testing", project="g", agent_name="syn", owner="老陈")
    capture("验证明天收工：字符验和收之间隔着其他单字 token，短语查询不应命中这条。",
            level="P2", category="testing", project="g", agent_name="syn", owner="老陈")
    with pool_conn() as conn:
        for q in nasty:
            try:
                fts = fts_query(q)
                conn.execute(
                    "SELECT 1 FROM memories_fts WHERE memories_fts MATCH ? LIMIT 1",
                    (fts,)).fetchone()
            except sqlite3.OperationalError as e:
                bad.append(f"{q!r}: {e}")
        # adjacency: 验 收 as outer-phrase vs bare implicit-AND
        n_phrase = conn.execute(
            "SELECT COUNT(*) FROM memories_fts WHERE memories_fts MATCH ?",
            ('"验 收"',)).fetchone()[0]
        n_implicit = conn.execute(
            "SELECT COUNT(*) FROM memories_fts WHERE memories_fts MATCH ?",
            ('"验" "收"',)).fetchone()[0]
        # a 2-char query must come back as ONE outer-quoted phrase
        phrase_form = fts_query("验收") == '"验 收"'
    ok = not bad and (n_phrase < n_implicit) and phrase_form
    return ok, ("; ".join(bad[:2]) or
                f"phrase={n_phrase} implicit={n_implicit} phrase_form={phrase_form}")


def guard_retrieve_excludes_archived() -> Tuple[bool, str]:
    """Live retrieval must never surface archived (soft-deleted) rows.

    The shared WHERE builder lacked the status predicate, so fts5/direct
    returned archived test rows while hybrid/vector did not — the same
    query gave different answers depending on mode (measured in production:
    three archived rows as the top-3 for "Promotion test").
    """
    _fresh_db()
    from memall.core.db import get_conn, init_db
    from memall.core.thin_waist import capture, retrieve

    init_db()
    mid = capture("归档不可见验证：这条记忆归档后绝不能再出现在任何检索模式中，"
                  "软删除对所有检索通路语义必须一致。",
                  level="P2", category="testing", project="g", agent_name="archg",
                  owner="老陈")
    conn = get_conn()
    conn.execute("UPDATE memories SET memory_status='archived' WHERE id=?", (mid,))
    conn.commit()
    conn.close()
    leak = any(m.id == mid for m in retrieve("归档不可见验证", limit=10))
    can_see = any(m.id == mid for m in retrieve(
        "归档不可见验证", limit=10, include_archived=True))
    return (not leak) and can_see, f"leak={leak} explicit_opt_in_visible={can_see}"


def guard_subjects_human_readable() -> Tuple[bool, str]:
    """capture() must sanitise caller-supplied subjects and reject
    machine-scaffolded titles on gated levels.

    The readability audit found 469+ live rows with template markers,
    embedded newlines, English fixed templates, or raw JSON in the title —
    every one of them arrived as an external subject that the generation
    path never saw. capture() now sanitises any supplied subject and the
    quality gate marks machine titles low-clarity.
    """
    _fresh_db()
    from memall.core.db import get_conn, init_db
    from memall.core.thin_waist import capture

    init_db()
    # caller-supplied marker/newline subject must arrive clean
    mid = capture("这条提供脏标题：[NOVEL:测试作品][FILE:章节]\n第二行才是正文要点，"
                  "标题不应携带模板标记或换行，应当被消毒。",
                  level="L4", category="reflection", project="g",
                  agent_name="subjbot", owner="老陈",
                  subject="[NOVEL:测试作品][FILE:章节]\n脏标题验证行")
    conn = get_conn()
    subj = conn.execute("SELECT subject FROM memories WHERE id=?", (mid,)).fetchone()[0]
    conn.close()
    clean = ("\n" not in subj and "[NOVEL:" not in subj and "[FILE:" not in subj)
    # gate level + machine subject -> must not score as before (marker
    # subjects get clarity<=3; an L4 with garbage body rejects outright)
    json_title_ok = False
    try:
        mid2 = capture('{"from": "a", "to": "b", "topic": "验收标题可读性", '
                       '"body": "正文保持 JSON 供消费者解析"}',
                       level="L4", category="message", project="g",
                       agent_name="subjbot", owner="老陈")
        conn = get_conn()
        s2 = conn.execute("SELECT subject FROM memories WHERE id=?", (mid2,)).fetchone()[0]
        conn.close()
        json_title_ok = s2 is not None and "from" not in s2[:3] and "{" not in s2
    except ValueError:
        json_title_ok = False
    return clean and json_title_ok, f"clean={clean} subj={subj[:44]!r} json={json_title_ok}"


def guard_caller_pins_classification() -> Tuple[bool, str]:
    """A caller-chosen category/level must survive BOTH classifier paths.

    Audit P1-16: the keyword-rule classifiers rewrote explicitly-chosen
    values — every task written through capture() became fix/testing within
    60s (notify_pending_tasks then returned zero) and L10 reports were
    demoted to L6.  Both the per-event path (_inline_classify) and the
    batch path (classify_step) must honour metadata.pinned_fields.
    """
    _fresh_db()
    from memall.core.db import get_conn, init_db
    from memall.core.thin_waist import capture
    from memall.pipeline.event_processor import process_events
    from memall.pipeline.classify import classify_step

    init_db()
    mid = capture("契约类别验证：这条记忆显式声明 category=task 与 level=L5，"
                  "两条分类器路径跑完后必须原样保留，不得被关键词规则改写。",
                  level="L5", category="task", project="g", agent_name="pinbot",
                  owner="老陈")
    # pipeline_cursors is created by classify_step itself; run it once to
    # create the table, then rewind the cursor so the batch pass covers the
    # row under test.
    classify_step()
    conn = get_conn()
    conn.execute("UPDATE pipeline_cursors SET cursor_id = ? WHERE step='classify'",
                 (mid + 1,))
    conn.commit()
    conn.close()

    process_events()          # the per-write path
    classify_step()           # the batch path

    conn = get_conn()
    row = conn.execute("SELECT level, category FROM memories WHERE id=?", (mid,)).fetchone()
    conn.execute("UPDATE pipeline_cursors SET cursor_id = 0 WHERE step='classify'")
    conn.commit()
    conn.close()
    ok = bool(row) and row["level"] == "L5" and row["category"] == "task"
    return ok, f"level={row['level'] if row else '?'} category={row['category'] if row else '?'}"


def guard_capture_audit_hook_fires() -> Tuple[bool, str]:
    """The capture audit hook must actually fire.

    Audit P2-9: the hook's matcher was "capture" (the core function name)
    while the adapter dispatches the MCP tool name "memall_write" — so the
    audit trail was believed to exist and did not.  The matcher now names
    the dispatched tool, and the handler logs only real writes.
    """
    _fresh_db()
    import memall.mcp.hooks_builtin  # noqa: F401  (registers the hooks)
    from memall.core.lifecycle import _match_tool
    from memall.mcp.adapter import handle_call

    matchers = [h.matcher for h in
                getattr(__import__("memall.mcp.hooks", fromlist=["HookRegistry"]),
                        "HookRegistry")._hooks
                if "audit" in (getattr(h, "description", "") or "").lower()]
    fires = any(_match_tool(m, "memall_write") for m in matchers)

    seen = []
    import memall.mcp.hooks_builtin as hb
    orig = hb._hook_capture_audit

    def spy(tool_name, arguments, result="", **kw):
        seen.append((arguments or {}).get("action", ""))
        return orig(tool_name, arguments, result, **kw)

    # the registry holds the function object registered at import time
    from memall.mcp.hooks import HookRegistry
    for h in HookRegistry._hooks:
        if "audit" in (getattr(h, "description", "") or "").lower():
            h.handler = spy
    try:
        handle_call("memall_write", {
            "action": "capture",
            "content": "审计钩子验证：这条真实写入应当让 capture 审计钩子被触发并记录。",
            "agent_name": "auditbot", "owner": "老陈", "project": "g"})
    finally:
        for h in HookRegistry._hooks:
            if "audit" in (getattr(h, "description", "") or "").lower():
                h.handler = orig
    return (fires and "capture" in seen), f"matchers={matchers} fired_actions={seen}"


def guard_background_threads_bounded() -> Tuple[bool, str]:
    """Background work must run on a BOUNDED pool, not one thread per call.

    Audit P0-3: every capture spawned bare threads (embedding, dream_scan,
    lightweight pipeline, discussion check) — writing 100 memories meant
    100+ threads each loading the ONNX model and contending for the SQLite
    write lock.  Acceptance: peak threads <= resident + 4.
    """
    _fresh_db()
    import threading
    from memall.core import bg_registry

    before = threading.active_count()
    bg_registry.set_max_workers(3)

    done = []
    for i in range(20):
        bg_registry.spawn(lambda i=i: done.append(i))
    # give the pool a moment to pick up the burst
    import time as _t
    _t.sleep(0.4)
    peak = threading.active_count()
    bg_registry.wait_for_background(timeout=10.0)

    limit = before + 4
    ok = peak <= limit and len(done) == 20
    return ok, f"before={before} peak={peak} limit={limit} completed={len(done)}"


def guard_search_filters_pushed_to_sql() -> Tuple[bool, str]:
    """Hybrid/vector search must filter in SQL and support project.

    Audit P1-5/P1-6: hybrid filtered candidates IN MEMORY after cutting the
    pool to 2x top_k (a category filter usually left 0 rows) and had no
    project parameter at all; vector_search could not isolate by project,
    category, level or agent — cross-project content leaked into every
    answer.
    """
    _fresh_db()
    from memall.core.db import init_db
    from memall.core.thin_waist import capture
    from memall.core.thin_waist_search import hybrid_search, vector_search

    init_db()
    for i in range(3):
        capture(f"过滤下推验证条目 alpha{i}：这条记忆属于 projA 项目，用于验证检索过滤是下推到 SQL 的。",
                level="P2", category="testing", project="projA",
                agent_name="agentA", owner="老陈")
    capture("过滤下推验证条目 beta：这条记忆属于 projB 项目，用于验证项目隔离确实生效。",
            level="P2", category="decision", project="projB",
            agent_name="agentB", owner="老陈")

    r = hybrid_search(query="过滤下推验证", top_k=10, project="projA")
    projs = {x.get("project") for x in r.get("results", [])}
    ok_a = bool(r.get("results")) and projs == {"projA"}

    r2 = hybrid_search(query="过滤下推验证", top_k=10, category="decision")
    cats = {x.get("category") for x in r2.get("results", [])}
    ok_b = bool(r2.get("results")) and cats == {"decision"}

    r3 = vector_search(query="过滤下推验证", top_k=8, project="projA")
    v_projs = {x.get("project") for x in r3.get("results", [])}
    ok_c = (not r3.get("results")) or v_projs == {"projA"}

    return (ok_a and ok_b and ok_c), (
        f"project_filter={ok_a}({projs}) category_filter={ok_b}({cats}) "
        f"vector_project={ok_c}({v_projs})")


def guard_intent_reaches_semantic() -> Tuple[bool, str]:
    """A long Chinese question must be able to reach the SEMANTIC branch.

    Audit P1-8: the "cjk >= 3 -> HYBRID" check came first, so every
    Chinese query returned HYBRID and the pure-vector path was dead code
    in production.
    """
    from memall.search.intent_router import classify, resolve_mode
    from memall.search.intent_router import SearchIntent

    long_cn = "验收测试标记记忆的分面检索候选集统计与排序实现细节"
    intent = classify(long_cn)
    ok = intent is SearchIntent.SEMANTIC_SEARCH
    short_cn = classify("验收测试")
    ok2 = short_cn is SearchIntent.HYBRID_SEARCH
    return ok and ok2, (f"long_cn->{resolve_mode(intent)} short_cn->{resolve_mode(short_cn)}")


def guard_access_count_means_reads() -> Tuple[bool, str]:
    """access_count counts retrievals; duplicate writes count separately.

    Audit P1-4: a hash hit (re-writing the same content) incremented
    access_count, so duplicate WRITES inflated the hot-topic ranking, the
    decay score and the zero-access health statistic; and retrieval itself
    never incremented it (only 21% of the store had ever been read).
    """
    _fresh_db()
    from memall.core.db import get_conn, init_db
    from memall.core.thin_waist import capture, retrieve

    init_db()
    text = "读写计数验证：这条记忆被检索一次 access_count 加一，重复写入只增加 dedup_hits。"
    mid = capture(text, level="P2", category="testing", project="g",
                  agent_name="cntbot", owner="老陈")
    conn = get_conn()
    before = conn.execute("SELECT access_count FROM memories WHERE id=?", (mid,)).fetchone()[0]
    conn.close()

    retrieve("读写计数验证", agent_name="cntbot", limit=3)
    conn = get_conn()
    after_read = conn.execute("SELECT access_count FROM memories WHERE id=?", (mid,)).fetchone()[0]
    conn.close()

    capture(text, level="P2", category="testing", project="g",
            agent_name="cntbot", owner="老陈")
    conn = get_conn()
    row = conn.execute("SELECT access_count, dedup_hits FROM memories WHERE id=?", (mid,)).fetchone()
    conn.close()
    ok = (before == 0 and after_read == 1
          and row["access_count"] == 1 and row["dedup_hits"] >= 1)
    return ok, (f"before={before} after_read={after_read} "
                f"after_dup=access{row['access_count']}/dedup{row['dedup_hits']}")


def guard_batch_search_is_semantic() -> Tuple[bool, str]:
    """batch_search must go through the unified (hybrid) path.

    Audit P1-7: it called retrieve() — the plain FTS5 path — while the
    docstring claimed "unified search", so a batch semantic query behaved
    like a keyword query and never recalled keyword-disjoint memories.
    """
    _fresh_db()
    import json
    from memall.core.db import init_db
    from memall.core.thin_waist import capture
    from memall.mcp.adapter import handle_call

    init_db()
    capture("批量语义检索验证：这条记忆与查询措辞不同但语义相关，用于验证批量搜索走了向量通路。",
            level="P2", category="testing", project="g", agent_name="batchbot",
            owner="老陈")
    out = json.loads(handle_call("memall_read", {
        "action": "batch_search", "queries": ["语义相关的另一种问法"], "limit": 3}))
    items = out.get("results", [])
    has_rrf = any(r.get("rrf_score") for item in items for r in item.get("results", []))
    return has_rrf, f"rrf_score_present={has_rrf} processed={out.get('processed')}"


def guard_capture_ignores_foreign_fields() -> Tuple[bool, str]:
    """action=capture must not crash on schema fields of other actions, and
    must REPORT them instead of silently dropping them (audit P0-2)."""
    _fresh_db()
    import json
    from memall.core.db import init_db
    from memall.mcp.adapter import handle_call

    init_db()
    out = json.loads(handle_call("memall_write", {
        "action": "capture",
        "content": "schema 收口验证：这条写入带上了别的 action 的字段，必须被忽略而不是崩溃。",
        "agent_name": "schemabot", "owner": "老陈", "project": "g",
        "dedup_threshold": 0.9, "memory_id": 123, "interaction_type": "click",
    }))
    ok = out.get("status") == "ok" and out.get("id")
    ignored = set(out.get("ignored_fields") or [])
    reported = {"dedup_threshold", "memory_id", "interaction_type"} <= ignored
    return ok and reported, f"status={out.get('status')} ignored={sorted(ignored)}"


def guard_session_cooldown_persisted() -> Tuple[bool, str]:
    """The auto-start cooldown must survive a process restart.

    Audit P2-13: the cooldown lived in process memory, so restarting the
    MCP server reset it and every agent immediately re-opened a session
    (the sessions table piled up duplicates — one agent had 7 in a day).
    It now lives in the session_cooldowns table.
    """
    _fresh_db()
    from memall.core.db import get_conn, init_db
    from memall.mcp.shared import ensure_session_started, _last_start

    init_db()
    ensure_session_started("cdbot", auto_inject=False)
    persisted = bool(_last_start("cdbot"))
    conn = get_conn()
    n1 = conn.execute(
        "SELECT COUNT(*) FROM sessions WHERE agent_name = 'cdbot'").fetchone()[0]
    conn.close()
    ensure_session_started("cdbot", auto_inject=False)
    conn = get_conn()
    n2 = conn.execute(
        "SELECT COUNT(*) FROM sessions WHERE agent_name = 'cdbot'").fetchone()[0]
    conn.close()
    ok = persisted and n1 == n2
    return ok, f"persisted={persisted} sessions {n1}->{n2}"


def guard_session_note_acknowledged() -> Tuple[bool, str]:
    """A session note must survive until it is DELIVERED (audit P2-12).

    consume_session_note() used to pop the note on read, so a response that
    never reached the client lost the session context for good.  Reading
    must not consume; only ack_session_note() does.
    """
    _fresh_db()
    from memall.mcp import shared

    shared._PENDING_SESSION_NOTES.clear()
    shared._PENDING_SESSION_NOTES["ackbot"] = "上下文内容"
    first = shared.consume_session_note("ackbot")
    second = shared.consume_session_note("ackbot")
    still = shared._PENDING_SESSION_NOTES.get("ackbot")
    acked = shared.ack_session_note("ackbot")
    gone = "ackbot" not in shared._PENDING_SESSION_NOTES
    ok = first == "上下文内容" and second == "上下文内容" and still and acked and gone
    return ok, (f"first={first!r} second={second!r} still_pending={bool(still)} "
                f"acked={acked} dropped={gone}")


def guard_session_note_not_shared_across_agents() -> Tuple[bool, str]:
    """A session note must reach its OWN agent and nobody else.

    The note carries one agent's persona, pending tasks and reflections.
    ``consume_session_note()`` used to fall back to "the oldest pending
    note" when the caller passed no name, and ``ack_session_note()`` dropped
    that same oldest note — so a call arriving without an identity delivered
    another agent's context in full AND destroyed the rightful owner's copy
    (the owner would never receive it).  That is an identity violation
    (AGENTS.md §三.4 身份锁定), not a delivery quirk.

    An unnamed caller must now get nothing, and a named caller must get
    exactly its own note.
    """
    from memall.mcp import shared

    shared._PENDING_SESSION_NOTES.clear()
    shared._NOTE_ACKED.clear()
    shared._PENDING_SESSION_NOTES["owner"] = "owner 的上下文"
    shared._PENDING_SESSION_NOTES["other"] = "other 的上下文"

    # An unnamed caller must not receive anyone's note.
    unnamed = shared.consume_session_note("")
    unnamed_ack = shared.ack_session_note("")

    # A named caller gets its own note, and only its own.
    own = shared.consume_session_note("owner")
    other_seen = shared.consume_session_note("other")
    other_still = shared._PENDING_SESSION_NOTES.get("other")

    # The rightful owner acks; the other agent's note is untouched.
    acked = shared.ack_session_note("owner")
    owner_gone = "owner" not in shared._PENDING_SESSION_NOTES
    other_intact = shared._PENDING_SESSION_NOTES.get("other") == "other 的上下文"

    ok = (unnamed is None and unnamed_ack is False
          and own == "owner 的上下文" and other_seen == "other 的上下文"
          and other_still == "other 的上下文"
          and acked and owner_gone and other_intact)
    return ok, (f"unnamed={unnamed!r} unnamed_ack={unnamed_ack} own={own!r} "
                f"other={other_seen!r} owner_dropped={owner_gone} "
                f"other_intact={other_intact}（期望 unnamed=None/False, "
                f"own=owner, other 完好）")


def guard_fallback_produces_self_evolution() -> Tuple[bool, str]:
    """The in-process fallback must be ABLE to produce L9/L10.

    Audit P0-1: run_lightweight_pipeline skips distill/integrate and the
    only periodic full run lived in the daemon, so a serve-only deployment
    never produced an L9 (distilled) or L10 (cross-domain) memory — the
    11-layer model stopped at L7.  This checks the fallback's plumbing
    (pending-work detection + claim) without running the expensive steps.
    """
    _fresh_db()
    from memall.core import bg_registry
    from memall.core.db import get_conn, init_db
    from memall.core.thin_waist import capture
    from memall.pipeline import fallback

    # No background pipeline may start while this guard runs: one starting
    # between the quiesce below and claim_run() writes a fresh "running" row
    # and the claim is (correctly) refused, making the guard flaky.  Captures
    # still work — only the fire-and-forget pipeline is suppressed.
    prior = bg_registry.spawn
    bg_registry.set_enabled(False)
    try:
        init_db()
        capture("兜底定时器验证：这条知识记忆应当被识别为可蒸馏输入，"
                "让进程内的自进化兜底有活可干。",
                level="L4", category="decision", project="g", agent_name="fb",
                owner="老陈")

        work = fallback.has_distillable_input()
        events = fallback.pending_event_count()

        # Start from a QUIESCENT database: a "running" row left by a killed
        # process is correctly treated as in-progress by claim_run, which
        # made this guard depend on external state.  Production recovers
        # such a row via recover_stale_pipeline_runs(); do the same.
        conn = get_conn()
        conn.execute("UPDATE pipeline_runs SET status='completed', "
                     "ended_at=datetime('now') WHERE status='running'")
        conn.commit()
        conn.close()

        # claim must succeed on a quiet DB with no recent run
        claimed = fallback.claim_run(min_age_seconds=0)
        # and a second claim must be refused while the first one is "running"
        second = fallback.claim_run(min_age_seconds=0)

        conn = get_conn()
        running = conn.execute(
            "SELECT COUNT(*) FROM pipeline_runs WHERE status='running'").fetchone()[0]
        # Do not leave a phantom "running" row behind: it would block the
        # real fallback (and the next run of this guard) for the process.
        conn.execute("UPDATE pipeline_runs SET status='completed', "
                     "ended_at=datetime('now') WHERE status='running'")
        conn.commit()
        conn.close()
        ok = work and events >= 1 and claimed and (not second) and running >= 1
        return ok, (f"work={work} events={events} claimed={claimed} "
                    f"second_refused={not second} running_rows={running}")
    finally:
        bg_registry.set_enabled(True)
        del prior


def guard_busy_timeout_survives_concurrency() -> Tuple[bool, str]:
    """Concurrent writers must not fail with 'database is locked'.

    Audit P0-3: a 200-write burst raced the background pipeline.  Two
    causes, both fixed: (a) a 5s busy_timeout was too short, and
    (b) pipeline steps opened DEFERRED transactions (BEGIN), whose
    read->write lock upgrade returns SQLITE_BUSY immediately and ignores
    busy_timeout entirely.  This guard pins both.
    """
    _fresh_db()
    import sqlite3
    import threading
    from memall.core.db import get_conn, init_db

    init_db()
    # A deferred BEGIN must not appear in the pipeline package any more.
    import re
    from pathlib import Path

    bad = []
    for p in sorted(Path(r"E:\MemALL\src\memall\pipeline").glob("*.py")):
        src = p.read_text(encoding="utf-8", errors="replace")
        if re.search(r'execute\(\s*"BEGIN"\s*\)', src):
            bad.append(p.name)

    # Two concurrent writers, one holding the lock briefly.
    errors: list[str] = []

    def writer(n: int) -> None:
        try:
            conn = get_conn()
            try:
                conn.execute("BEGIN IMMEDIATE")
                conn.execute(
                    "INSERT INTO memories (content, content_hash, level, owner, "
                    "agent_name, category, occurred_at, created_at, updated_at) "
                    "VALUES (?,?,'P2','o','conc','testing',datetime('now'),"
                    "datetime('now'),datetime('now'))",
                    (f"并发写入 {n}", f"conchash{n}-{id(conn)}"))
                conn.commit()
            finally:
                conn.close()
        except sqlite3.Error as e:
            errors.append(f"{type(e).__name__}: {e}")

    threads = [threading.Thread(target=writer, args=(i,)) for i in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    ok = not bad and not errors
    return ok, (f"deferred_BEGIN_files={bad or 'none'} "
                f"concurrent_errors={errors[:2] or 'none'}")


def guard_update_can_complete_tasks() -> Tuple[bool, str]:
    """update must be able to write metadata (the L5 task status).

    The task loop was broken at its LAST step: an executor marks a task
    done by writing metadata.status='done', but UpdateInput never declared
    `metadata`, so the validator stripped it before the handler ran and
    update() answered "no updatable fields provided".  The task stayed
    status='active' forever and kept reappearing in pending_tasks — the
    hand-off could never be closed.  (Found while auditing the memall-bus
    end-to-end collaboration flow.)
    """
    _fresh_db()
    import json
    from memall.core.db import get_conn, init_db
    from memall.mcp.adapter import handle_call

    init_db()
    cap = json.loads(handle_call("memall_write", {
        "action": "capture", "level": "L5", "agent_name": "taskbot",
        "category": "task", "project": "g", "owner": "老陈",
        "subject": "[任务] update 闭环验证",
        "content": "验证 update 能否把 L5 任务标记为 done，这条内容需要足够长以通过质量门。",
        "metadata": json.dumps({"status": "active"}),
    }))
    mid = cap.get("id")
    if not mid:
        return False, f"capture failed: {cap}"
    upd = json.loads(handle_call("memall_write", {
        "action": "update", "memory_id": mid, "agent_name": "taskbot",
        "metadata": json.dumps({"status": "done"}),
    }))
    conn = get_conn()
    meta = json.loads(conn.execute(
        "SELECT metadata FROM memories WHERE id=?", (mid,)).fetchone()[0])
    conn.close()
    ok = upd.get("updated") is True and meta.get("status") == "done"
    return ok, f"updated={upd.get('updated')} status={meta.get('status')}"


def guard_lazy_tools_always_loadable() -> Tuple[bool, str]:
    """Every tools/list reader must trigger the lazy tool registration.

    Tools are registered lazily (adapter._ensure_loaded) so the stdio server
    answers `initialize` without the ~11s heavy import that made MCP clients
    time out with "Not connected".  That win only holds if EVERY code path
    that serves a tool list calls it first — otherwise an HTTP/SSE client
    gets an empty list.  This guard pins the server-side readers.
    """
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parent / "memall"
    readers = {
        "mcp/server.py": 'method == "tools/list"',
        "mcp/http_transport.py": 'method == "tools/list"',
        "gateway_mcp_handlers.py": 'method == "tools/list"',
    }
    offenders = []
    for rel, anchor in readers.items():
        p = root / rel
        if not p.exists():
            offenders.append(f"{rel}: missing")
            continue
        src = p.read_text(encoding="utf-8", errors="replace")
        i = src.find(anchor)
        if i == -1:
            offenders.append(f"{rel}: no tools/list branch")
            continue
        # the call must appear in the same branch (next ~600 chars)
        if "_ensure_loaded()" not in src[i:i + 600]:
            offenders.append(f"{rel}: tools/list without _ensure_loaded()")

    # and the registry must actually be empty until the loader runs
    import memall.mcp.adapter as ad
    from memall.mcp.adapter import TOOL_DEFINITIONS

    ad._ensure_loaded()
    n = len(TOOL_DEFINITIONS)
    ok = not offenders and n >= 7
    return ok, f"tools={n} offenders={offenders or 'none'}"


def guard_reflect_merges_metadata() -> Tuple[bool, str]:
    """reflect_step must MERGE into metadata, never replace the column.

    Was: reflect.py wrote a fresh {"l6_source", "quality"} dict over the whole
    column, erasing metadata.pinned_fields, metadata.status and every
    caller-supplied key.  metadata.status is what notify_pending_tasks()
    selects on, so any L5 task row reflect touched vanished from the task
    chain — and the pin protection (audit P1-16) survived exactly one
    pipeline cycle before erasing itself.
    """
    _fresh_db()
    from memall.core.db import init_db, get_conn
    from memall.core.thin_waist import capture
    from memall.pipeline.reflect import reflect_step

    init_db()
    # reflect skips agents with < 20 memories (cold start), so pad first.
    for i in range(21):
        capture(f"填充用的普通记忆 {i}，内容足够长以便通过长度门槛，这里不含任何触发词。",
                level="P2", category="general", project="g", agent_name="guard",
                owner="老陈")
    # No `level` argument on purpose: capture() records "level" as an explicit
    # choice whenever the caller passes it at all (even the default "P2"),
    # so passing it would pin the level and reflect would — rightly — skip
    # the row, leaving the merge path untested.  category="decision" is a
    # contract category, so only the category is pinned.
    mid = capture("这里需要修正一下，之前写的实现有偏差，所以重新确认一遍结论。",
                  category="decision", project="g", agent_name="guard",
                  owner="老陈",
                  metadata=json.dumps({"my_custom_key": "keep-me", "status": "active"}))
    conn = get_conn()
    row = conn.execute("SELECT metadata, level FROM memories WHERE id=?", (mid,)).fetchone()
    conn.close()
    before = json.loads(row["metadata"] or "{}")
    if "pinned_fields" in before and "level" in before["pinned_fields"]:
        return False, f"precondition failed: level unexpectedly pinned: {before}"

    reflect_step()

    conn = get_conn()
    row = conn.execute("SELECT metadata, level FROM memories WHERE id=?", (mid,)).fetchone()
    conn.close()
    md = json.loads(row["metadata"] or "{}")
    kept = (md.get("my_custom_key") == "keep-me"
            and md.get("status") == "active"
            and md.get("pinned_fields") == before.get("pinned_fields")
            and md.get("l6_source") == "reflect_step"
            and md.get("quality") is not None)
    return kept, f"after reflect: level={row['level']} metadata={md}"


def guard_reflect_honours_level_pin() -> Tuple[bool, str]:
    """reflect_step must not relabel a row whose level the caller pinned.

    Was: reflect changed level to L6 without checking
    memall.core.pins.is_field_pinned — the third path that writes level,
    after classify_step and event_processor._inline_classify, which both
    honour the pin.
    """
    _fresh_db()
    from memall.core.db import init_db, get_conn
    from memall.core.thin_waist import capture
    from memall.pipeline.reflect import reflect_step

    init_db()
    for i in range(21):
        capture(f"填充用的普通记忆 {i}，内容足够长以便通过长度门槛，这里不含任何触发词。",
                level="P2", category="general", project="g", agent_name="guard",
                owner="老陈")
    mid = capture("这里需要修正一下，之前写的实现有偏差，所以重新确认一遍结论。",
                  level="L4", category="decision", project="g", agent_name="guard",
                  owner="老陈")
    conn = get_conn()
    conn.execute("UPDATE memories SET metadata=? WHERE id=?",
                 (json.dumps({"pinned_fields": ["category", "level"]}), mid))
    conn.commit()
    conn.close()

    result = reflect_step()

    conn = get_conn()
    row = conn.execute("SELECT level, metadata FROM memories WHERE id=?", (mid,)).fetchone()
    conn.close()
    md = json.loads(row["metadata"] or "{}")
    ok = row["level"] == "L4" and md.get("pinned_fields") == ["category", "level"]
    return ok, (f"after reflect: level={row['level']} (expect L4), "
                f"skipped_pinned={result.get('skipped_pinned')}")


def guard_capture_declares_supported_fields() -> Tuple[bool, str]:
    """CaptureInput must declare every field capture.py supports.

    Pydantic v2 drops undeclared fields and validator.py hands the handler
    only the validated subset, so a field the MCP schema advertises but the
    model omits silently never reaches the store.  `tags` was the loudest
    case: the schema advertises it as array<string>, and a caller that set
    it got auto-derived keywords instead.
    """
    from memall.mcp.models import CaptureInput
    from memall.mcp.tools.capture import _CAPTURE_FIELDS

    declared = set(CaptureInput.model_fields.keys())
    wanted = set(_CAPTURE_FIELDS) - {"action"}
    missing = sorted(wanted - declared)
    return not missing, (f"declared={len(declared)} supported={len(wanted)} "
                         f"missing={missing or 'none'}")


def guard_capture_tags_survive_validator() -> Tuple[bool, str]:
    """A caller-supplied tag must reach the store through the MCP validator.

    Was: the validator stripped `tags` before the handler ran, so the
    caller's tags were replaced by auto-derived keywords — which also made
    memall_read's exact-tag filter useless for MCP-written memories.
    """
    _fresh_db()
    from memall.core.db import init_db, get_conn
    from memall.mcp.validator import validate_tool_input
    from memall.mcp.tools.capture import handle

    init_db()
    marker = "zzqxtag"
    args = {"action": "capture", "content": "一条用于验证标签能否穿过校验器的记忆正文。",
            "level": "L4", "category": "decision", "agent_name": "guard",
            "project": "g", "tags": [marker]}
    ok_valid, validated, err = validate_tool_input("memall_write", args)
    if not ok_valid:
        return False, f"validation failed: {err}"
    raw = handle(validated)
    payload = json.loads(raw)
    if not payload.get("id"):
        return False, f"capture rejected: {raw[:120]}"
    conn = get_conn()
    row = conn.execute("SELECT tags FROM memories WHERE id=?", (payload["id"],)).fetchone()
    conn.close()
    stored = json.loads(row["tags"] or "[]")
    ok = marker in stored
    return ok, f"stored tags={stored} (expect {marker!r} present)"


def guard_inject_cache_refreshes_tasks() -> Tuple[bool, str]:
    """auto_inject's cache must never serve a stale task list.

    Was: the cache was keyed by agent with a 300s TTL and no invalidation,
    so a task card created or completed moments earlier was invisible for up
    to five minutes.  Worse, handle_call runs ensure_session_started
    (auto_inject) BEFORE the tool, so the cache was warmed with the task
    list as it was *before* the capture that followed it — the agent could
    never see a freshly dispatched card.
    """
    _fresh_db()
    from memall.core.db import init_db, get_conn
    from memall.core.thin_waist import capture
    from memall.mcp.federation_tools import auto_inject, invalidate_inject_cache

    init_db()
    # Warm the cache with an empty task list, exactly like handle_call does
    # before dispatching the tool.
    auto_inject("guard")
    invalidate_inject_cache("guard")
    auto_inject("guard")

    mid = capture("一张刚建的任务卡，用于验证缓存是否会把它藏起来。",
                  level="L5", category="task", project="g", agent_name="guard",
                  owner="老陈", metadata=json.dumps({"status": "active"}))
    conn = get_conn()
    conn.execute("UPDATE memories SET metadata=? WHERE id=?",
                 (json.dumps({"status": "active"}), mid))
    conn.commit()
    conn.close()

    injected = auto_inject("guard")
    pending = injected.get("pending_tasks", [])
    seen = any(t.get("id") == mid for t in pending)

    # And the other direction: a completed task must disappear immediately.
    from memall.core.thin_waist import update
    update(mid, metadata=json.dumps({"status": "done"}))
    injected2 = auto_inject("guard")
    still = any(t.get("id") == mid for t in injected2.get("pending_tasks", []))

    ok = seen and not still
    return ok, (f"新卡可见={seen} (expect True), 完成后仍列={still} (expect False), "
                f"pending={len(pending)}")


def guard_discussion_stays_l5() -> Tuple[bool, str]:
    """A discussion must stay L5/discussion through a classifier pass.

    respond_discussion() looks a discussion up as
    level='L5' AND category='discussion'.  A long review body matches the L6
    markers, so the keyword classifier could rewrite the level, after which
    the lookup failed and the review could never converge — observed on
    discussion #999964, which turned into L6/conflict hours after creation
    while #999948 stayed L5.  create_discussion() now pins category+level.
    """
    _fresh_db()
    import json
    from memall.core.db import get_conn, init_db
    from memall.pipeline.classify import classify_step
    from memall.pipeline.convergence import create_discussion

    init_db()
    d = create_discussion(
        title="守卫：讨论不得被降级",
        background=(
            "刻意包含 L6 诱饵词：反思 教训 认知 元反思 架构决策 系统性 模式识别 "
            "洞察 归纳 抽象 长期 演化 认知偏差 归因 复盘 心智模型 范式 反模式 "
            "权衡 取舍 根因 依赖 分层 边界 抽象层次。"),
        participants=["g1", "g2"], creator="guardbot")
    did = d.get("discussion_id") or d.get("memory_id")
    if not did:
        return False, f"create_discussion failed: {d}"

    # classify_step() creates pipeline_cursors on first run; run it once so
    # the rewind below has a table to update.
    classify_step()
    conn = get_conn()
    conn.execute("UPDATE pipeline_cursors SET cursor_id = ? WHERE step='classify'",
                 (did + 1,))
    conn.commit()
    conn.close()
    classify_step()

    conn = get_conn()
    row = conn.execute(
        "SELECT level, category, metadata FROM memories WHERE id=?", (did,)).fetchone()
    meta = json.loads(row["metadata"] or "{}")
    conn.execute("UPDATE pipeline_cursors SET cursor_id = 7 WHERE step='classify'")
    conn.execute("DELETE FROM memories WHERE id=?", (did,))
    conn.commit()
    conn.close()
    ok = row["level"] == "L5" and row["category"] == "discussion"
    return ok, (f"level={row['level']} category={row['category']} "
                f"pinned={meta.get('pinned_fields')}")


def guard_task_reminder_no_hash_collision() -> Tuple[bool, str]:
    """The task-reminder round must not collide with the UNIQUE content_hash.

    The reminder body is deterministic per (task, agent), so its content_hash
    is deterministic too — but content_hash carries a PERMANENT UNIQUE
    constraint.  The old dedup used a 6-hour TIME WINDOW, so past the window
    the INSERT re-used the same hash and raised
    `UNIQUE constraint failed: memories.content_hash`, aborting the round
    (audit F6).  The body must carry the task id so two different tasks with
    the same subject still get their own reminder.

    The dedup key is the task id in the reminder's metadata, not the body's
    hash — see guard_task_reminder_one_per_task for why the hash is the wrong
    key.
    """
    _fresh_db()
    from memall.core.db import init_db, get_conn
    from memall.core.thin_waist import capture
    from memall.scheduler.agent_round import notify_pending_tasks

    init_db()
    mid = capture("一张用于验证提醒去重的任务卡，内容足够长以便通过长度门槛。",
                  level="L5", category="task", project="g", agent_name="guard",
                  owner="老陈", metadata=json.dumps({"status": "active"}))

    first = notify_pending_tasks("guard")
    conn = get_conn()
    rows1 = conn.execute(
        "SELECT COUNT(*) FROM memories WHERE category='task_pending'").fetchone()[0]
    conn.close()

    # Second round: must be a no-op, not an UNIQUE violation.
    second = notify_pending_tasks("guard")
    conn = get_conn()
    rows2 = conn.execute(
        "SELECT COUNT(*) FROM memories WHERE category='task_pending'").fetchone()[0]
    # Simulate the old behaviour: age the reminder past any window and re-run.
    conn.execute("UPDATE memories SET created_at='2020-01-01T00:00:00' "
                 "WHERE category='task_pending'")
    conn.commit()
    conn.close()
    third = notify_pending_tasks("guard")
    conn = get_conn()
    rows3 = conn.execute(
        "SELECT COUNT(*) FROM memories WHERE category='task_pending'").fetchone()[0]
    conn.close()

    ok = (len(first) == 1 and rows1 == 1
          and len(second) == 0 and rows2 == 1
          and len(third) == 0 and rows3 == 1)
    return ok, (f"首轮 {len(first)} 条/库中 {rows1}；第二轮 {len(second)}/{rows2}；"
                f"老化后再跑 {len(third)}/{rows3}（期望 1/1, 0/1, 0/1）")


def guard_task_reminder_one_per_task() -> Tuple[bool, str]:
    """A task must get exactly ONE reminder, whatever the body format is.

    The reminder's identity is the (task, agent) pair.  Keying the dedup on
    the body's content_hash ties that identity to the body FORMAT: when the
    body gained its task-id suffix, every reminder written in the old format
    hashed differently, so each of those tasks got a SECOND reminder row —
    the duplicate delivery the reminder table exists to prevent.

    The key must therefore be the task id in the reminder's metadata, which
    does not change when the body format does.
    """
    _fresh_db()
    import hashlib
    from memall.core.db import init_db, get_conn
    from memall.core.thin_waist import capture
    from memall.scheduler.agent_round import notify_pending_tasks

    init_db()
    capture("一张用于验证提醒唯一性的任务卡，内容足够长以便通过长度门槛。",
            level="L5", category="task", project="g", agent_name="guard",
            owner="老陈", metadata=json.dumps({"status": "active"}))

    notify_pending_tasks("guard")
    conn = get_conn()
    row = conn.execute(
        "SELECT id, content, agent_name, subject, metadata FROM memories "
        "WHERE category='task_pending' ORDER BY id LIMIT 1").fetchone()
    conn.close()
    if not row:
        return False, "首轮没有生成提醒"

    # Simulate a reminder written under an OLDER body format: same task id in
    # the metadata, different content (and therefore different hash).
    old_body = row["content"].replace(" (task #", " [old-format] task #")
    meta = json.loads(row["metadata"])
    meta["subject"] = meta.get("subject", "") + " [old-format]"
    h = hashlib.sha256(old_body.encode("utf-8")).hexdigest()
    from memall.core.db import get_conn as _gc
    conn = _gc()
    conn.execute(
        "INSERT INTO memories (content, content_hash, level, owner, agent_name, "
        "subject, category, project, summary, occurred_at, created_at, updated_at, "
        "supersedes, confidence, visibility, metadata, arc_status) "
        "VALUES (?,?,'P2',?,?,'旧格式提醒','task_pending','','',?,?,?,'[]',0.5,"
        "'private',?,'open')",
        (old_body, h, row["agent_name"], row["agent_name"],
         "2026-10-22T00:00:00+00:00", "2026-10-22T00:00:00+00:00",
         "2026-10-22T00:00:00+00:00", json.dumps(meta, ensure_ascii=False)))
    conn.commit()
    conn.close()

    # The next round must NOT add a third reminder for the same task.
    added = notify_pending_tasks("guard")
    conn = get_conn()
    before = [r[0] for r in conn.execute(
        "SELECT COUNT(*) FROM memories WHERE category='task_pending' "
        "AND json_extract(metadata,'$.task_id') IS NOT NULL "
        "GROUP BY json_extract(metadata,'$.task_id')"
    ).fetchall()]
    conn.close()

    # The duplicate that already exists is the migration's job: collapsing it
    # must leave exactly one reminder per task.
    import importlib.util
    import pathlib
    mig_path = (pathlib.Path(__file__).parent / "memall" / "migrations" /
                "036_collapse_duplicate_task_reminders.py")
    spec = importlib.util.spec_from_file_location("m036_guard", mig_path)
    mig = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mig)
    conn = get_conn()
    mig.apply(conn)
    conn.commit()
    counts = [r[0] for r in conn.execute(
        "SELECT COUNT(*) FROM memories WHERE category='task_pending' "
        "AND json_extract(metadata,'$.task_id') IS NOT NULL "
        "GROUP BY json_extract(metadata,'$.task_id')"
    ).fetchall()]
    conn.close()

    ok = (len(added) == 0 and before == [2] and counts == [1])
    return ok, (f"旧格式提醒存在时新一轮新增 {len(added)} 条；"
                f"迁移前每 task 提醒数={before}、迁移后={counts}"
                f"（期望 0 条新增, [2], [1]）")


def guard_discussion_reminders_and_responses() -> Tuple[bool, str]:
    """A discussion must be able to REACH its participants and COUNT replies.

    Three defects made the review mechanism silently unworkable (found while
    auditing discussion #999964):
      1. confirm_discussion wrote replies with level='P2' but no pinned_fields,
         and counted them with "AND level='P2'".  The classifier rewrote the
         level (11 of 30 live replies had drifted, one to L1), so an agent that
         HAD answered looked like it never had — convergence waited forever.
      2. check_pending_discussions skipped the reminder for EVERY participant
         as soon as ANY reply existed, so the first responder muted everyone.
      3. its reminder INSERT passed supersedes=None against a NOT NULL column
         inside INSERT OR IGNORE, so the row was never written while the
         caller was told reminder_captured=True.
    """
    _fresh_db()
    import json
    from memall.core.db import get_conn, init_db
    from memall.pipeline.classify import classify_step
    from memall.pipeline.convergence import (
        check_pending_discussions, confirm_discussion, create_discussion,
    )

    init_db()
    d = create_discussion(title="守卫：讨论可达且可统计",
                          background="验证回执计数与提醒投递。",
                          participants=["ra", "rb"], creator="guardbot")
    did = d.get("discussion_id") or d.get("memory_id")
    if not did:
        return False, f"create failed: {d}"

    # (3) a reminder must actually be written
    pend_a = check_pending_discussions("ra")
    conn = get_conn()
    got_reminder = conn.execute(
        "SELECT COUNT(*) FROM memories WHERE category='discussion_pending' "
        "AND agent_name='ra' AND json_extract(metadata,'$.discussion_id')=?",
        (did,)).fetchone()[0]
    conn.close()

    # (1) a reply must survive reclassification and still be counted
    confirm_discussion(discussion_id=did, agent_name="ra", stance="agree",
                       note="ra 的表态：内容足够长以便分类器有发挥空间，"
                            "包含反思 教训 架构 认知 模式 洞察 归因 复盘 等L6诱饵词。")
    classify_step()
    conn = get_conn()
    row = conn.execute(
        "SELECT level, category FROM memories WHERE category='discussion_response' "
        "AND agent_name='ra' ORDER BY id DESC LIMIT 1").fetchone()
    conn.close()

    # (2) rb has not replied, so rb must still be reminded; ra must not be
    pend_b = [x.get("discussion_id") for x in check_pending_discussions("rb")]
    pend_a2 = [x.get("discussion_id") for x in check_pending_discussions("ra")]

    ok = (got_reminder > 0 and row is not None and row["level"] == "P2"
          and did in pend_b and did not in pend_a2)
    return ok, (f"reminder_rows={got_reminder} reply_level="
                f"{row['level'] if row else None} rb_reminded={did in pend_b} "
                f"ra_muted_after_reply={did not in pend_a2}")


def guard_identity_honours_level_pin() -> Tuple[bool, str]:
    """identity_step must not relabel a row whose level the caller pinned.

    identity_step is a keyword classifier (first-person preference phrases),
    so it is the same class as classify_step and reflect_step — and it must
    consult memall.core.pins.is_field_pinned like they do.  It did not: its
    guard was a hardcoded list of "terminal" levels that left P2 unprotected,
    so a discussion_response (written at P2) whose text happened to contain
    "我建议…" / "我觉得更好…" was demoted to L1 — mislabelling a discussion
    reply as an identity signal and feeding that text into the agent's
    persona traits.
    """
    _fresh_db()
    from memall.core.db import init_db, get_conn
    from memall.core.thin_waist import capture
    from memall.pipeline.identity import identity_step

    init_db()
    # A first-person preference phrase — exactly what identity_step hunts for.
    mid = capture("我建议用 SQLite 存储，零依赖部署是最省心的做法，强烈推荐。",
                  level="P2", category="discussion_response", project="g",
                  agent_name="guard", owner="老陈",
                  metadata=json.dumps({"pinned_fields": ["category", "level"]}))
    conn = get_conn()
    conn.execute("UPDATE memories SET metadata=? WHERE id=?",
                 (json.dumps({"pinned_fields": ["category", "level"],
                              "stance": "confirm", "agent_name": "guard"}), mid))
    conn.commit()
    conn.close()

    identity_step()

    conn = get_conn()
    row = conn.execute("SELECT level FROM memories WHERE id=?", (mid,)).fetchone()
    conn.close()
    ok = row["level"] == "P2"
    return ok, f"after identity_step: level={row['level']} (expect P2, was demoted to L1)"


def guard_msg_tool_wired() -> Tuple[bool, str]:
    """memall_msg must be registered, validated, and fail cleanly without a bus.

    The bus is an independent resident process, so the guard cannot require it
    to be running — it checks the three things that must hold offline:
      1. the tool is in tools/list (so an agent can discover it),
      2. the validator accepts its actions (an undeclared action would be
         rejected before the handler ever runs — the P0-2 defect class),
      3. with no bus running the handler returns a structured error with
         guidance, not a traceback, and the main chain (auto_inject) is
         unaffected.
    """
    import memall.mcp.adapter  # noqa: F401
    from memall.mcp.adapter import TOOL_DEFINITIONS
    from memall.mcp.validator import validate_tool_input
    from memall.mcp.tools.msg import handle

    memall.mcp.adapter._ensure_loaded()
    names = {d.get("name") for d in TOOL_DEFINITIONS}
    registered = "memall_msg" in names

    ok_valid, _data, err = validate_tool_input(
        "memall_msg", {"action": "send", "agent_name": "x", "to": "y",
                        "payload": {"memory_id": 1}})
    validated = ok_valid

    # Point the client at a port nothing listens on -> must be a clean error.
    old = os.environ.get("MEMALL_BUS_PORT")
    os.environ["MEMALL_BUS_PORT"] = "9"
    try:
        raw = handle({"action": "send", "agent_name": "x", "to": "y",
                      "payload": {"memory_id": 1}})
    finally:
        if old is None:
            os.environ.pop("MEMALL_BUS_PORT", None)
        else:
            os.environ["MEMALL_BUS_PORT"] = old
    try:
        payload = json.loads(raw)
    except Exception:
        payload = {}
    clean_error = (payload.get("status") == "error"
                   and "bus unavailable" in str(payload.get("error", "")).lower()
                   and payload.get("delivered") is False)

    ok = registered and validated and clean_error
    return ok, (f"registered={registered}, validator_accepts={validated} "
                f"(err={err!r}), clean_error_without_bus={clean_error}")


GUARDS: Dict[str, Callable[[], Tuple[bool, str]]] = {
    "fts_cjk": guard_fts_cjk,
    "fts_tags_indexed": guard_fts_tags_indexed,
    "auto_capture_registered": guard_auto_capture_registered,
    "classify_preserves_level": guard_classify_preserves_level,
    "english_l6_accepted": guard_english_l6_accepted,
    "ask_excludes_own_output": guard_ask_excludes_own_output,
    "distill_excludes_auto_rows": guard_distill_excludes_auto_rows,
    "hash_hit_promotion": guard_hash_hit_promotion,
    "mcp_contract_fields": guard_mcp_contract_fields,
    "unknown_tool_error": guard_unknown_tool_error,
    "config_hot_reload": guard_config_hot_reload,
    "seen_never_drops": guard_seen_never_drops,
    "pipeline_partial": guard_pipeline_partial,
    "disabled_write_denylist": guard_disabled_write_denylist,
    "tests_use_isolated_db": guard_tests_use_isolated_db,
    "denylist_supports_globs": guard_denylist_supports_globs,
    "no_row_get_hazards": guard_no_row_get_hazards,
    "pipeline_ignores_archived": guard_pipeline_ignores_archived,
    "denylist_before_normalize": guard_denylist_before_normalize,
    "search_ranks_by_bm25": guard_search_ranks_by_bm25,
    "facets_match_population": guard_facets_match_population,
    "hot_topics_functional": guard_hot_topics_functional,
    "digest_excludes_archived": guard_digest_excludes_archived,
    "fts_query_survives_syntax": guard_fts_query_survives_syntax,
    "retrieve_excludes_archived": guard_retrieve_excludes_archived,
    "subjects_human_readable": guard_subjects_human_readable,
    "caller_pins_classification": guard_caller_pins_classification,
    "capture_audit_hook_fires": guard_capture_audit_hook_fires,
    "background_threads_bounded": guard_background_threads_bounded,
    "search_filters_pushed_to_sql": guard_search_filters_pushed_to_sql,
    "intent_reaches_semantic": guard_intent_reaches_semantic,
    "access_count_means_reads": guard_access_count_means_reads,
    "batch_search_is_semantic": guard_batch_search_is_semantic,
    "capture_ignores_foreign_fields": guard_capture_ignores_foreign_fields,
    "reflect_merges_metadata": guard_reflect_merges_metadata,
    "reflect_honours_level_pin": guard_reflect_honours_level_pin,
    "capture_declares_supported_fields": guard_capture_declares_supported_fields,
    "capture_tags_survive_validator": guard_capture_tags_survive_validator,
    "inject_cache_refreshes_tasks": guard_inject_cache_refreshes_tasks,
    "task_reminder_no_hash_collision": guard_task_reminder_no_hash_collision,
    "task_reminder_one_per_task": guard_task_reminder_one_per_task,
    "identity_honours_level_pin": guard_identity_honours_level_pin,
    "msg_tool_wired": guard_msg_tool_wired,
    "session_cooldown_persisted": guard_session_cooldown_persisted,
    "session_note_acknowledged": guard_session_note_acknowledged,
    "session_note_not_shared_across_agents": guard_session_note_not_shared_across_agents,
    "fallback_produces_self_evolution": guard_fallback_produces_self_evolution,
    "busy_timeout_survives_concurrency": guard_busy_timeout_survives_concurrency,
    "update_can_complete_tasks": guard_update_can_complete_tasks,
    "lazy_tools_always_loadable": guard_lazy_tools_always_loadable,
    "discussion_stays_l5": guard_discussion_stays_l5,
    "discussion_reminders_and_responses": guard_discussion_reminders_and_responses,
}

# Full test files run as subprocesses when no guard name is given.
SUITE_FILES = [
    "test_search_features.py",
    "test_auto_capture.py",
    "test_match_consistency.py",
    "test_m2_m3_m4.py",
]


def main(argv: list) -> int:
    if "--list" in argv:
        for name in GUARDS:
            print(f"  {name}")
        return 0

    selected = [a for a in argv if not a.startswith("-")]
    names = selected or list(GUARDS)
    failed = []
    for name in names:
        fn = GUARDS.get(name)
        if fn is None:
            print(f"FAIL  {name}  (unknown guard)")
            failed.append(name)
            continue
        try:
            ok, detail = fn()
        except Exception as e:
            ok, detail = False, f"raised {e!r}"[:160]
        print(f"{'PASS' if ok else 'FAIL'}  {name:28s} {detail}")
        if not ok:
            failed.append(name)

    if not selected:  # also run the full suites
        print("\n--- suites ---")
        # A guard's capture may have scheduled a lightweight pipeline on the
        # bounded pool; it keeps writing to the test DB for up to a minute.
        # Launching the suite subprocesses while that runs made
        # test_auto_capture.py fail intermittently (it passes standalone).
        try:
            from memall.core.bg_registry import wait_for_background

            left = wait_for_background(timeout=120.0)
            if left:
                print(f"NOTE  {left} background task(s) still running; "
                      f"suites may contend with them")
        except Exception:
            pass
        for f in SUITE_FILES:
            path = os.path.join(SRC, f)
            if not os.path.exists(path):
                print(f"SKIP  {f} (missing)")
                continue
            r = subprocess.run([sys.executable, path], cwd=SRC,
                               capture_output=True, text=True, timeout=900)
            ok = r.returncode == 0
            print(f"{'PASS' if ok else 'FAIL'}  {f}")
            if not ok:
                failed.append(f)

    print(f"\n=== check_fixes: {len(names) - len([f for f in failed if f in names])}/"
          f"{len(names)} guards" + (", suites included" if not selected else "") + " ===")
    if failed:
        print("FAILED:", ", ".join(failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
