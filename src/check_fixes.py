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
    import memall.mcp.adapter  # noqa: F401
    from memall.mcp.adapter import TOOL_DEFINITIONS

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
