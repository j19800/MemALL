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
    # The MCP session identity is process-global by design (it is bound once
    # at handshake and reused for every subsequent call).  Guards must not
    # inherit the previous guard's identity: a leaked identity makes search
    # filter to that agent's own memories and silently returns nothing.
    try:
        from memall.mcp.session_identity import reset as _reset_identity

        _reset_identity()
    except Exception:
        pass
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


def guard_convergence_rule_respected() -> Tuple[bool, str]:
    """A discussion's convergence_rule must actually decide WHEN it converges.

    ``metadata.convergence_rule`` was a dead field: nothing read it, so every
    discussion required EVERY participant to respond.  With a roster that
    included an agent who had left the project, the discussion could never
    converge — the missing agent was never coming.  The MCP schema advertised
    the field but ``handle_create`` dropped it, so a new discussion could not
    even set it.

    Rules: ``unanimous`` (default, historical behaviour) needs everyone;
    ``majority`` needs more than half; ``any`` needs one response.  An
    unrecognised value must fall back to the strict default rather than
    silently loosening the requirement.
    """
    from memall.pipeline.convergence import _rule_satisfied

    P = ["claude", "dsh", "opencode", "qoder", "workbuddy"]
    maj = len(P) // 2 + 1

    ok = True
    detail = []

    # unanimous: only the full roster satisfies it
    for n in range(len(P)):
        got = _rule_satisfied({"convergence_rule": "unanimous"},
                              set(P[:n]), P)
        want = (n == len(P))
        ok = ok and got == want
        detail.append(f"unanimous@{n}={got}")

    # majority: more than half
    for n in range(len(P) + 1):
        got = _rule_satisfied({"convergence_rule": "majority"},
                              set(P[:n]), P)
        want = (n >= maj)
        ok = ok and got == want
        detail.append(f"majority@{n}={got}")

    # any: one response is enough
    any_ok = (_rule_satisfied({"convergence_rule": "any"}, set(), P) is False
              and _rule_satisfied({"convergence_rule": "any"}, set(P[:1]), P) is True)

    # an unknown / missing rule must not loosen the requirement
    strict = (_rule_satisfied({"convergence_rule": "garbage"}, set(P[:2]), P) is False
              and _rule_satisfied({}, set(P[:2]), P) is False
              and _rule_satisfied({"convergence_rule": None}, set(P[:2]), P) is False)

    # a 4-agent roster needs 3 responses for a majority
    P4 = ["a", "b", "c", "d"]
    four = (_rule_satisfied({"convergence_rule": "majority"}, set(P4[:2]), P4) is False
            and _rule_satisfied({"convergence_rule": "majority"}, set(P4[:3]), P4) is True)

    ok = ok and any_ok and strict and four
    return ok, (f"{' '.join(detail)} | any={any_ok} strict_fallback={strict} "
                f"even_roster={four}")


def guard_confirm_path_honours_rule() -> Tuple[bool, str]:
    """confirm_discussion must apply the discussion's convergence_rule.

    The background sweep (_converge_single) consulted _rule_satisfied(),
    but the immediate auto-converge path inside confirm_discussion() did a
    bare `if missing:` — so a "majority" discussion converged on the FIRST
    response while the sweep would still have waited, and the two paths
    disagreed about the same discussion.
    """
    _fresh_db()
    from memall.core.db import init_db
    from memall.pipeline.convergence import create_discussion, confirm_discussion

    init_db()
    d = create_discussion(
        title="守卫：确认路径必须尊重收敛规则",
        participants=["ga", "gb", "gc"],
        convergence_rule="majority",
        creator="guardbot")
    did = d.get("memory_id") or d.get("discussion_id")
    if not did:
        return False, f"create_discussion failed: {d}"

    # 1 of 3 — majority needs 2, so the discussion must stay pending
    r1 = confirm_discussion(discussion_id=did, agent_name="ga",
                            stance="confirm", note="第一条表态")
    # 2 of 3 — majority satisfied, so it must converge now
    r2 = confirm_discussion(discussion_id=did, agent_name="gb",
                            stance="confirm", note="第二条表态")

    ok = r1.get("status") == "pending" and r2.get("status") == "converged"
    return ok, (f"first={r1.get('status')} second={r2.get('status')} "
                f"(expect pending, converged)")


def guard_retrieve_by_id_respects_viewer() -> Tuple[bool, str]:
    """retrieve(<id>) must apply the same trust filter as the list path.

    The id path ran a bare "SELECT * FROM memories WHERE id = ?" with no
    trust check, so a private memory could be read by anyone who knew or
    guessed its id — the list path already filtered through
    _filter_by_trust().

    The "other" agent must be a REGISTERED one: an unknown agent defaults
    to the most permissive read level (_get_agent_read_level returns
    "private"), which would hide the very difference this guard is about.
    """
    _fresh_db()
    from memall.core.db import init_db
    from memall.core.thin_waist import capture, retrieve

    init_db()
    mid = capture("私有可见性验证：这条记忆的可见性为 private，只有属主应当读到它。",
                  level="P2", category="general", project="g",
                  agent_name="owner_bot", owner="老陈")
    # a second capture registers "other_bot" in identities, giving it a
    # read level of its own ("shared") instead of the unknown-agent default
    capture("对照用的普通记忆，用于给 other_bot 建立身份记录，内容足够长以便通过质量门。",
            level="P2", category="general", project="g",
            agent_name="other_bot", owner="老陈")

    blocked = retrieve(mid, viewer="other_bot")
    allowed = retrieve(mid, viewer="owner_bot")
    ok = blocked is None and allowed is not None
    return ok, (f"other_bot={'sees' if blocked else 'blocked'} "
                f"owner_bot={'sees' if allowed else 'blocked'} "
                f"(expect blocked, sees)")


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


def guard_capture_retries_on_lock() -> Tuple[bool, str]:
    """capture() must survive a read->write upgrade conflict.

    ``busy_timeout_survives_concurrency`` pins the timeout and forbids a
    *literal* ``BEGIN`` in the pipeline, but capture() has the same hazard
    without writing BEGIN: it always reads first (hash dedup, identity,
    thread root) inside its implicit transaction, then inserts.  In WAL a
    read snapshot that another connection committed under cannot be upgraded
    — SQLite returns "database is locked" IMMEDIATELY and busy_timeout does
    not apply.  Measured on the live MCP server: a capture burst lost a write
    to exactly this while the agent was told the tool had failed.
    """
    _fresh_db()
    import sqlite3
    import threading
    import time as _t
    import memall.config as mc
    from memall.core.db import get_conn, init_db
    from memall.core.thin_waist import capture

    init_db()
    # Keep the guard fast: a long busy_timeout would hide the retry path.
    mc._config.setdefault("db", {})["busy_timeout_ms"] = 100
    mc._config.setdefault("capture", {})["lock_retries"] = 6

    # Warm up the write path first.  capture() does project inference and
    # quality scoring before it touches the DB; on a cold process that alone
    # can outlast the lock window, so the guard would pass without ever
    # exercising the retry (measured: a first-run capture "succeeded" under a
    # 0.5s lock purely because it reached the INSERT after the lock was gone).
    capture("预热：让推理与质量评分在锁竞争前完成，避免守卫被冷启动时间蒙蔽。",
            level="L4", category="reflection", project="guard",
            agent_name="guard", subject="[守卫] 锁竞争预热")

    holder_held = threading.Event()
    holder_done = threading.Event()

    def hold_lock():
        # The connection must be created AND used in the same thread: sqlite3
        # rejects cross-thread use ("SQLite objects created in a thread can
        # only be used in that same thread"), which would silently leave the
        # write lock held forever and make the guard fail for the wrong reason.
        conn = get_conn()
        try:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "UPDATE memories SET updated_at = updated_at WHERE id IS NULL")
            holder_held.set()
            _t.sleep(1.2)
            conn.commit()
        finally:
            conn.close()
            holder_done.set()

    threading.Thread(target=hold_lock, daemon=True).start()
    if not holder_held.wait(timeout=5):
        return False, "lock holder never acquired the write lock"

    t0 = _t.time()
    err = ""
    mem_id = None
    try:
        mem_id = capture("锁竞争下的写入必须重试成功，而不是把 database is locked "
                         "抛给调用方（WAL 读快照升级为写锁时会立刻失败，"
                         "busy_timeout 对该路径不生效）。",
                         level="L4", category="reflection", project="guard",
                         agent_name="guard", subject="[守卫] 锁竞争重试")
    except Exception as exc:  # noqa: BLE001 — the guard reports the failure
        err = f"{type(exc).__name__}: {exc}"
    elapsed = _t.time() - t0
    holder_done.wait(timeout=10)

    rows = 0
    if mem_id:
        c = get_conn()
        try:
            rows = c.execute(
                "SELECT COUNT(*) AS n FROM memories WHERE content_hash = "
                "(SELECT content_hash FROM memories WHERE id = ?)",
                (mem_id,)).fetchone()["n"]
        finally:
            c.close()
    ok = bool(mem_id) and not err and rows == 1 and elapsed >= 1.0
    return ok, (f"mem_id={mem_id} rows={rows} elapsed={elapsed:.2f}s "
                f"err={err or 'none'}")


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


def guard_procedure_honours_level_pin() -> Tuple[bool, str]:
    """procedure_step must not upgrade a row whose level the caller pinned.

    procedure_step is a regex keyword extractor (step markers: "1. …",
    "首先", "然后", "Step N", …).  When it finds three or more markers in a
    P2 / L1 / L2 row it rewrites the level to L3 — the FIFTH path that
    rewrites level without consulting the pin.  classify / reflect / identity
    are guarded; procedure was not.

    Evidence this was biting in production: the opencode discussion response
    #1000201 (written P2, pinned) was found at L3 after the pipeline ran,
    purely because its text contained several "然后/首先" markers.
    """
    _fresh_db()
    from memall.core.db import init_db, get_conn
    from memall.core.thin_waist import capture
    from memall.pipeline.procedure import procedure_step

    init_db()
    text = ("操作步骤：1. 先确认配置；2. 然后重启服务；3. 最后核对日志。"
            "整个过程首先备份数据库，之后逐台灰度。")
    mid = capture(text, level="P2", category="procedure", project="g",
                  agent_name="guard", owner="老陈",
                  metadata=json.dumps({"pinned_fields": ["category", "level"]}))
    procedure_step()
    conn = get_conn()
    row = conn.execute("SELECT level FROM memories WHERE id=?", (mid,)).fetchone()
    conn.close()
    ok = row["level"] == "P2"
    return ok, (f"after procedure_step: level={row['level']} "
                f"(expect P2 — 5 step markers would otherwise upgrade to L3)")


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


def guard_notification_bar_lists_only_tasks() -> Tuple[bool, str]:
    """The notification bar must list TASK cards, not every L5 row.

    ``pending_tasks_for`` selected L5 rows addressed to the agent without
    requiring ``category='task'``.  A discussion, a session summary or an L7
    lesson addressed to the agent was therefore listed as "待完成任务" —
    those rows carry no work at all, and their ``metadata.status`` is usually
    absent, which means "active" (AGENTS.md §二), so they all passed the
    owed-status filter.

    Observed on the live database: one agent's bar showed ten pending tasks,
    of which none was a task card — the three rows whose subject read
    "[任务] …" had their ``category`` rewritten to meeting/documentation/
    implementation by the keyword classifier, because they were written
    before ``pinned_fields`` existed and never declared ``category='task'``.

    The bar must show what is actually owed.
    """
    _fresh_db()
    from memall.core.db import init_db, get_conn
    from memall.core.thin_waist import capture
    from memall.mcp.notify import pending_tasks_for

    init_db()
    conn = get_conn()
    # A real task card addressed to the agent.
    capture("守卫用任务卡：验证通知栏只列任务卡，不列其它 L5 行。",
            level="L5", category="task", project="g", agent_name="bar",
            owner="老陈", metadata=json.dumps({"status": "active"}))
    # A non-task L5 row addressed to the same agent, with no status.
    capture("守卫用会话摘要行：这不是任务卡，不该出现在待办里。"
            "内容足够长以便通过长度门槛。",
            level="L5", category="session", project="g", agent_name="bar",
            owner="老陈")
    # A discussion addressed to the same agent, with no status.
    capture("守卫用讨论行：这不是任务卡，不该出现在待办里。"
            "内容足够长以便通过长度门槛。",
            level="L5", category="discussion", project="g", agent_name="bar",
            owner="老陈")
    conn.close()

    conn = get_conn()
    got = pending_tasks_for(conn, "bar", limit=20)
    conn.close()
    cats = sorted({t["category"] for t in got})
    ids = [t["id"] for t in got]

    ok = (len(got) == 1 and cats == ["task"])
    return ok, (f"通知栏返回 {len(got)} 条, category 分布={cats}, ids={ids}"
                f"（期望 1 条, ['task']）")


def guard_notification_bar_uses_session_identity() -> Tuple[bool, str]:
    """The notification bar must work for tools that never declare agent_name.

    28 of the 55 tool input models never declared ``agent_name``, so the
    field is stripped by the validator before the handler runs.  The three
    transports each read the identity for the notification bar and the
    session note, and reading it from the raw argument alone meant a call
    through one of those tools found NOBODY: the bar stayed silent and the
    agent's pending work was invisible.

    ``mcp/session_identity.py`` binds the identity once at handshake and
    exposes ``identify()`` — explicit argument wins, session identity is the
    fallback.  All three transports must use it.

    A second defect: ``http_transport.py`` referenced ``agent_name`` at the
    session-note call without ever defining it in that scope, so the HTTP
    transport raised NameError on every tool response.
    """
    _fresh_db()
    import re
    from memall.core.db import init_db
    from memall.core.thin_waist import capture
    from memall.mcp import session_identity as si
    from memall.mcp.notify import build_notice

    init_db()
    capture("守卫用任务卡：验证经握手绑定的身份也能收到通知栏内容。",
            level="L5", category="task", project="g", agent_name="sessagent",
            owner="老陈", metadata=json.dumps({"status": "active"}))
    capture("守卫用另一 agent 的任务卡：不应出现在本 agent 的通知栏里。",
            level="L5", category="task", project="g", agent_name="otheragent",
            owner="老陈", metadata=json.dumps({"status": "active"}))

    si.reset()
    si.bind_handshake_agent("sessagent")
    # A call with NO agent_name — the F3 case.
    session_name = si.identify({"action": "search", "query": "任务"})
    notice = build_notice(session_name) or ""
    session_ok = session_name == "sessagent" and bool(notice)

    # An explicit agent_name still wins.
    explicit_name = si.identify({"agent_name": "otheragent"})
    explicit_ok = explicit_name == "otheragent"

    # Every transport must read the identity through identify().
    from pathlib import Path as _P

    # Absolute paths: this guard used to hard-code "src/..." and therefore only
    # passed when the shell happened to be sitting in the repo root.  Resolve
    # against this file instead.
    _pkg = _P(__file__).resolve().parent
    transports = (_pkg / "memall/mcp/server.py",
                  _pkg / "memall/mcp/http_transport.py",
                  _pkg / "memall/gateway_mcp_handlers.py")
    raw_reads = {}
    for t in transports:
        src = t.read_text(encoding="utf-8")
        raw_reads[t.name] = len(re.findall(r'arguments\.get\("agent_name", ""\)', src))

    # http_transport must define agent_name before using it.
    ht = (_pkg / "memall/mcp/http_transport.py").read_text(encoding="utf-8")
    use = ht.find("consume_session_note(agent_name)")
    assign = ht.rfind("agent_name = ", 0, use) if use > 0 else -1
    defined_before_use = 0 < assign < use

    ok = (session_ok and explicit_ok and defined_before_use
          and all(v == 0 for v in raw_reads.values()))
    return ok, (f"session身份={'sessagent' if session_ok else 'FAIL'} "
                f"显式优先={explicit_ok} http定义先于使用={defined_before_use} "
                f"裸读取={raw_reads}")


def guard_precise_task_handoff() -> Tuple[bool, str]:
    """The four-step precise hand-off must work end to end (design #1000007).

    1. identity: a call WITHOUT agent_name still resolves to the session agent
       (28 tool models never declared agent_name - F3);
    2. assignee: a dispatched L5 card records who it is addressed to;
    3. push: the notification carries [ID | title | state], queried fresh;
    4. claim: only the addressee may accept, and done leaves the queue.
    """
    _fresh_db()
    import json
    from memall.core.db import get_conn, init_db
    from memall.mcp.adapter import handle_call
    from memall.mcp.notify import build_notice
    from memall.mcp.session_identity import bind_handshake_agent, reset

    init_db()
    A, B, C = "gpm", "gdev", "gother"
    reset()
    bind_handshake_agent(A)
    cap = json.loads(handle_call("memall_write", {
        "action": "capture", "level": "L5", "category": "task",
        "agent_name": B, "owner": "老陈", "project": "g",
        "subject": "[守卫] 精确任务交接",
        "content": "守卫用任务卡：验证 assignee 定向、通知栏带内容、精确接取与闭环。",
        "metadata": json.dumps({"status": "active"})}))
    tid = cap.get("id")
    if not tid:
        return False, f"capture failed: {cap}"

    conn = get_conn()
    meta = json.loads(conn.execute(
        "SELECT metadata FROM memories WHERE id=?", (tid,)).fetchone()[0])
    conn.close()
    assignee_ok = (meta.get("assignee") or "").lower() == B.lower()

    # (3) notification addressed to B, with id + title + state, no agent_name
    reset()
    bind_handshake_agent(B)
    notice = build_notice(B)
    notice_ok = (str(tid) in notice and "待认领" in notice
                 and "[守卫] 精确任务交接" in notice)

    # (4a) a stranger cannot claim
    reset()
    bind_handshake_agent(C)
    denied = json.loads(handle_call("memall_write",
                                    {"action": "task_claim", "task_id": tid}))
    denied_ok = denied.get("status") == "forbidden"

    # (4b) the addressee can, and gets the body
    reset()
    bind_handshake_agent(B)
    claimed = json.loads(handle_call("memall_write",
                                     {"action": "task_claim", "task_id": tid}))
    claim_ok = (claimed.get("status") == "claimed"
                and claimed.get("claimed_by") == B
                and "守卫用任务卡" in (claimed.get("content") or ""))

    # (3b) the notice now reports the claimed state
    claimed_notice_ok = "已认领" in build_notice(B)

    # (4c) closing it removes it from the queue
    handle_call("memall_write", {"action": "update", "memory_id": tid,
                                 "agent_name": B,
                                 "metadata": json.dumps({"status": "done"})})
    closed_ok = str(tid) not in build_notice(B)

    ok = (assignee_ok and notice_ok and denied_ok and claim_ok
          and claimed_notice_ok and closed_ok)
    return ok, (f"assignee={assignee_ok} notice={notice_ok} "
                f"stranger_forbidden={denied_ok} claim={claim_ok} "
                f"claimed_state={claimed_notice_ok} closed={closed_ok}")


def guard_task_loop_surface_wired() -> Tuple[bool, str]:
    """task_resolve / task_block are on the MCP surface, gated like claim.

    #1000539: task_lifecycle's acknowledge/resolve/block had NO entry point
    (agents hand-wrote metadata.status through the generic update, bypassing
    the state machine), and none of the three had a caller parameter — any
    agent could resolve another agent's card. The fix puts resolve/block on
    memall_write as actions, routes them through task_lifecycle (single
    terminal path: closes reminders), and gates the caller exactly like
    claim_task.  ack is deliberately NOT exposed (it leaves status=active and
    clashes with the scheduler nudge — see acknowledge_task's docstring).
    """
    _fresh_db()
    import json
    from memall.core.db import get_conn, init_db
    from memall.mcp.adapter import handle_call
    from memall.mcp.session_identity import bind_handshake_agent, reset
    from memall.scheduler.agent_round import notify_pending_tasks

    init_db()
    A, B, C = "looppm", "loopdev", "loopother"
    reset(); bind_handshake_agent(A)
    cap = json.loads(handle_call("memall_write", {
        "action": "capture", "level": "L5", "category": "task",
        "agent_name": B, "owner": "老陈", "project": "g",
        "subject": "[守卫] 闭环入口",
        "content": "守卫用任务卡：验证 task_resolve/task_block 上线且按归属拦截。",
        "metadata": json.dumps({"status": "active", "assignee": B})}))
    tid = cap.get("id")
    if not tid:
        return False, f"capture failed: {cap}"

    def meta_of():
        conn = get_conn()
        try:
            return json.loads(conn.execute(
                "SELECT metadata FROM memories WHERE id=?", (tid,)).fetchone()[0])
        finally:
            conn.close()

    def reminders(task_id=None):
        # Takes the id explicitly: the reminder-closure assertion below operates
        # on tid2, and the previous version closed over `tid`, so it checked the
        # wrong card (and reported a false failure once resolve() really started
        # deleting reminders).
        target = tid if task_id is None else task_id
        conn = get_conn()
        try:
            return [r[0] for r in conn.execute(
                "SELECT id FROM memories WHERE category = 'task_pending' "
                "AND json_extract(metadata, '$.task_id') = ?", (target,)).fetchall()]
        finally:
            conn.close()

    # block: stranger refused
    reset(); bind_handshake_agent(C)
    denied_block = json.loads(handle_call(
        "memall_write", {"action": "task_block", "task_id": tid,
                         "reason": "越权尝试"}))
    block_denied_ok = denied_block.get("status") == "forbidden"

    # block: reason is required (structured validation error, not silent pass)
    reset(); bind_handshake_agent(B)
    no_reason = json.loads(handle_call(
        "memall_write", {"action": "task_block", "task_id": tid}))
    reason_required_ok = "error" in no_reason

    # block: addressee succeeds, state lands
    blocked = json.loads(handle_call(
        "memall_write", {"action": "task_block", "task_id": tid,
                         "reason": "等外部接口"}))
    block_ok = (blocked.get("task_id") == tid
                and meta_of().get("status") == "blocked")

    # resolve from blocked is legal (resolve accepts active/blocked/claimed)
    resolved = json.loads(handle_call(
        "memall_write", {"action": "task_resolve", "task_id": tid,
                         "result": "守卫闭环"}))
    resolve_ok = (resolved.get("task_id") == tid
                  and meta_of().get("status") == "resolved")
    evidence_ok = meta_of().get("resolution") == "守卫闭环"

    # a stranger cannot resolve someone else's live card
    tid2 = json.loads(handle_call("memall_write", {
        "action": "capture", "level": "L5", "category": "task",
        "agent_name": B, "owner": "老陈", "project": "g",
        "subject": "[守卫] 闭环入口二",
        "content": "守卫用任务卡二：验证陌生人不得 resolve 他人活跃卡。",
        "metadata": json.dumps({"status": "active", "assignee": B})}))["id"]
    reset(); bind_handshake_agent(C)
    denied_resolve = json.loads(handle_call(
        "memall_write", {"action": "task_resolve", "task_id": tid2}))
    resolve_denied_ok = denied_resolve.get("status") == "forbidden"

    # terminal path also closes reminders (AC5 reuse, not a second writer)
    reset(); bind_handshake_agent(B)
    notify_pending_tasks(B)
    had = bool(reminders(tid2))
    handle_call("memall_write", {"action": "task_resolve", "task_id": tid2})
    closed = reminders(tid2) == []
    reminder_ok = had and closed

    ok = (block_denied_ok and reason_required_ok and block_ok and resolve_ok
          and evidence_ok and resolve_denied_ok and reminder_ok)
    return ok, (f"block_forbidden={block_denied_ok} reason_required={reason_required_ok} "
                f"block={block_ok} resolve={resolve_ok} evidence={evidence_ok} "
                f"resolve_forbidden={resolve_denied_ok} reminders_closed={reminder_ok}")


def guard_task_cards_always_visible() -> Tuple[bool, str]:
    """A task card must never become invisible to the delivery paths.

    Both the notification bar (mcp/notify.py) and the task view (mcp/tasks.py)
    select category='task'.  Task cards written before metadata.pinned_fields
    existed had their category rewritten by the keyword classifier, so eleven
    live "[任务] …" cards with a valid assignee and status=active sat in the
    store where nobody could see them - work addressed to an agent that no
    delivery path would ever surface (migration 037 repairs the data; this
    guard keeps the invariant).
    """
    _fresh_db()
    import json
    from memall.core.db import get_conn, init_db
    from memall.core.thin_waist import capture
    from memall.mcp.notify import build_notice
    from memall.mcp.tasks import list_tasks

    init_db()
    cap = capture("守卫用任务卡：必须能被通知栏与任务视图同时看见，"
                  "标题以 [任务] 开头并带 assignee。",
                  level="L5", category="task", project="g", agent_name="visagent",
                  owner="老陈", metadata=json.dumps({"status": "active"}))
    if not isinstance(cap, int):
        return False, f"capture failed: {cap}"

    conn = get_conn()
    orphan = conn.execute(
        "SELECT COUNT(*) FROM memories WHERE level='L5' AND subject LIKE '[任务]%' "
        "AND category != 'task' AND COALESCE(memory_status,'') != 'archived'"
    ).fetchone()[0]
    pinned = json.loads(conn.execute(
        "SELECT metadata FROM memories WHERE id=?", (cap,)).fetchone()[0]).get(
            "pinned_fields")
    conn.close()

    notice = build_notice("visagent") or ""
    listed = list_tasks("visagent", status="open").get("total", 0)
    ok = orphan == 0 and listed >= 1 and "visagent" and str(cap) in notice
    return ok, (f"orphan_cards={orphan} pinned={pinned} "
                f"task_list={listed} in_notice={str(cap) in notice}")


def guard_stdio_reads_utf8() -> Tuple[bool, str]:
    """The stdio server must decode stdin as UTF-8, not the locale codec.

    serve() used to reconfigure only sys.stdout.  On a Windows GBK console the
    request frames were decoded with the locale codec, so any Chinese in a
    call (task subject, discussion body, search query) arrived mojibake - agents
    worked around it by passing Chinese through temp files instead of stdio.
    """
    from pathlib import Path

    src = (Path(__file__).resolve().parent / "memall/mcp/server.py").read_text(
        encoding="utf-8")
    i = src.find("def serve(")
    body = src[i:i + 2000] if i >= 0 else src
    has_loop = 'for _stream_name in ("stdout", "stdin")' in body
    stdin_named = "stdin" in body
    ok = has_loop and stdin_named
    return ok, f"both_pipes_reconfigured={has_loop} stdin_mentioned={stdin_named}"


def guard_task_terminal_closes_reminder() -> Tuple[bool, str]:
    """A terminal transition must close the task's reminder rows.

    agent_round treats ANY existing reminder for a task id as "already
    notified" — even an archived one, because the reminder body's content_hash
    carries a unique constraint.  So a reminder left behind by a resolved task
    does not just go stale: it permanently mutes the (task, agent) pair.
    Measured before the fix: create -> notify (1 row) -> resolve (row stays)
    -> reopen to active -> notify -> still 1 row, no new one; archiving the
    stale row did not help either.  Only a hard delete frees the pair.

    The closure now runs on resolve_task, block_task, and the generic
    update(status=resolved/done/archived) path, so re-opening a task can be
    notified again.
    """
    _fresh_db()
    import json
    from memall.core.db import get_conn, init_db
    from memall.core.thin_waist import capture, update
    from memall.pipeline.task_lifecycle import block_task, resolve_task
    from memall.scheduler.agent_round import notify_pending_tasks

    init_db()
    AG = "termprobe"

    def reminders(tid):
        conn = get_conn()
        try:
            return [r[0] for r in conn.execute(
                "SELECT id FROM memories WHERE category = 'task_pending' "
                "AND json_extract(metadata, '$.task_id') = ?", (tid,)).fetchall()]
        finally:
            conn.close()

    def make(tag):
        return capture(f"守卫卡 {tag}：终态必须关闭提醒，重开必须能再次提醒。",
                       level="L5", category="task", agent_name=AG,
                       owner="老陈", project="g", subject=f"[守卫] {tag}",
                       metadata=json.dumps({"status": "active"}))

    # resolve path
    t1 = make("resolve")
    notify_pending_tasks(AG)
    first = reminders(t1)
    resolve_task(t1)
    after_resolve = reminders(t1)
    update(t1, metadata=json.dumps({"status": "active"}))
    notify_pending_tasks(AG)
    reopened = reminders(t1)
    reopen_new = bool(reopened) and set(reopened) != set(first)

    # block path
    t2 = make("block")
    notify_pending_tasks(AG)
    block_task(t2, reason="守卫")
    after_block = reminders(t2)

    # generic update path
    t3 = make("update")
    notify_pending_tasks(AG)
    update(t3, metadata=json.dumps({"status": "resolved"}))
    after_update = reminders(t3)

    ok = (bool(first) and after_resolve == [] and reopen_new
          and after_block == [] and after_update == [])
    return ok, (f"resolve_closed={not after_resolve} reopen_renotified={reopen_new} "
                f"block_closed={not after_block} update_closed={not after_update}")


def guard_convergence_waits_for_action_items() -> Tuple[bool, str]:
    """A discussion must not close while an action-item assignee has not spoken.

    Measured on #1000375: it converged 9 minutes after creation on 3/5 votes,
    although its own action_items named dsh as the PREREQUISITE reviewer.  The
    prerequisite was still being produced, so the reviewer could only deliver
    outside the thread and the converged conclusion never contained the four
    defects that review found (one of which, a resolved task permanently
    muting its reminders, turned out to be a real bug).

    The gate only blocks assignees who are ALSO participants; an assignee who
    is not on the roster is another team's commitment and must not freeze the
    discussion.  ``timeout_hours`` - stored but read by nothing until now - is
    the deadline past which the gate stops blocking, so one silent assignee
    cannot hold a discussion open forever (the failure that added the
    ``majority`` rule in the first place).
    """
    _fresh_db()
    import json
    from memall.core.db import get_conn, init_db
    from memall.pipeline.convergence import confirm_discussion, create_discussion

    init_db()

    def status_of(did):
        conn = get_conn()
        try:
            raw = conn.execute("SELECT metadata FROM memories WHERE id = ?",
                               (did,)).fetchone()
        finally:
            conn.close()
        if not raw:
            return "gone"
        m = json.loads(raw[0] or "{}")
        return m.get("status")

    # (1) prerequisite assignee silent -> must NOT converge
    d1 = create_discussion(title="守卫：前置未交付", background="门控验证。",
                           participants=["ga", "gb", "gc", "gd"],
                           action_items=[{"assigned_to": "gd",
                                          "description": "前置评审"}],
                           convergence_rule="majority", creator="guardbot")
    did1 = d1.get("discussion_id") or d1.get("memory_id")
    for ag in ("ga", "gb", "gc"):
        confirm_discussion(discussion_id=did1, agent_name=ag,
                           stance="confirm", note=f"{ag} ok")
    blocked = status_of(did1) == "active"

    # (2) once the prerequisite speaks it may converge
    confirm_discussion(discussion_id=did1, agent_name="gd",
                       stance="confirm", note="前置已交付")
    released = status_of(did1) == "converged"

    # (3) an assignee OUTSIDE the roster must not block
    d2 = create_discussion(title="守卫：名册外指派", background="不应阻塞。",
                           participants=["ga", "gb"],
                           action_items=[{"assigned_to": "outsider",
                                          "description": "外部依赖"}],
                           convergence_rule="any", creator="guardbot")
    did2 = d2.get("discussion_id") or d2.get("memory_id")
    confirm_discussion(discussion_id=did2, agent_name="ga",
                       stance="confirm", note="ok")
    not_blocked = status_of(did2) == "converged"

    # (4) past timeout_hours the gate stops blocking
    d3 = create_discussion(title="守卫：超时逃生", background="超时后可收敛。",
                           participants=["ga", "gb"],
                           action_items=[{"assigned_to": "gb",
                                          "description": "前置"}],
                           convergence_rule="any", timeout_hours=1,
                           creator="guardbot")
    did3 = d3.get("discussion_id") or d3.get("memory_id")
    conn = get_conn()
    conn.execute("UPDATE memories SET created_at = datetime('now', '-2 hours') "
                 "WHERE id = ?", (did3,))
    conn.commit()
    conn.close()
    confirm_discussion(discussion_id=did3, agent_name="ga",
                       stance="confirm", note="ok")
    timeout_ok = status_of(did3) == "converged"

    ok = blocked and released and not_blocked and timeout_ok
    return ok, (f"blocked={blocked} released={released} "
                f"outsider_ignored={not_blocked} timeout_escape={timeout_ok}")


def guard_decommissioned_cards_reassign() -> Tuple[bool, str]:
    """Migration 038 re-routes a departed agent's cards, idempotently.

    #1000532: claude left the roster with 4 active L5 task cards, and the MCP
    surface has no executable reassignment path — update strips agent_name
    (memory_write.py) and enforce_agent_ownership blocks non-supervisors from
    even writing metadata. The repair lives in migration 038; this guard pins
    its three contract properties:
      * the mapped ids move (agent_name + metadata.assignee);
      * audit fields make it reversible (previous_assignee + reassigned_*);
      * it is idempotent (a second apply touches nothing) and a subject is
        never rewritten ([任务] is the visibility predicate).
    """
    _fresh_db()
    import json
    import importlib.util
    from pathlib import Path
    from memall.core.db import get_conn, init_db
    from memall.core.thin_waist import capture

    init_db()
    spec_path = (Path(__file__).resolve().parent
                 / "memall/migrations/038_reassign_decommissioned_agent_cards.py")
    if not spec_path.exists():
        return False, "migration 038 file missing"
    spec = importlib.util.spec_from_file_location("m038", spec_path)
    m038 = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m038)

    conn = get_conn()
    seeded = []
    try:
        # Seed claude-addressed task cards at the ids the migration maps.
        for owner_after in ("dsh", "opencode", "workbuddy"):
            cap = capture(
                f"守卫用移卡：模拟 claude 退出名册后需要移交的任务卡（{owner_after}）。",
                level="L5", category="task", project="g", agent_name="claude",
                owner="老陈",
                metadata=json.dumps({"status": "active", "assignee": "claude"}))
            seeded.append(cap)
        # A row the id-map must NOT touch: not in _REASSIGN.
        bystander = capture(
            "守卫用旁观卡：不在决策 #1000527 授权清单内，迁移不得顺手改写。",
            level="L5", category="task", project="g", agent_name="claude",
            owner="老陈", metadata=json.dumps({"status": "active",
                                               "assignee": "claude"}))
        seeded.append(bystander)
        # Force one seeded id onto the map so apply() has a live target.
        mapped_id = next(i for i in m038._REASSIGN)
        conn.execute("UPDATE memories SET id = ? WHERE id = ?",
                     (mapped_id, seeded[0]))
        conn.commit()
        subject_before = conn.execute(
            "SELECT subject FROM memories WHERE id = ?",
            (mapped_id,)).fetchone()[0]

        m038.apply(conn)
        row = conn.execute(
            "SELECT agent_name, metadata, subject FROM memories WHERE id = ?",
            (mapped_id,)).fetchone()
        meta = json.loads(row[1] or "{}")
        moved = row[0] == m038._REASSIGN[mapped_id]
        assignee_moved = meta.get("assignee") == m038._REASSIGN[mapped_id]
        audited = (meta.get("previous_assignee") == "claude"
                   and meta.get("reassigned_by") and meta.get("reassigned_at"))
        subject_kept = row[2] == subject_before
        bystander_untouched = conn.execute(
            "SELECT agent_name FROM memories WHERE id = ?",
            (bystander,)).fetchone()[0] == "claude"

        m038.apply(conn)  # idempotent second run
        meta2 = json.loads(conn.execute(
            "SELECT metadata FROM memories WHERE id = ?",
            (mapped_id,)).fetchone()[0])
        idempotent = meta2.get("previous_assignee") == "claude"  # not overwritten with the new owner
    finally:
        conn.close()

    ok = (moved and assignee_moved and audited and subject_kept
          and bystander_untouched and idempotent)
    return ok, (f"moved={moved} assignee={assignee_moved} audit={audited} "
                f"subject_kept={subject_kept} bystander={bystander_untouched} "
                f"idempotent={idempotent}")


def guard_goal_lifecycle_end_to_end() -> Tuple[bool, str]:
    """One objective must travel the whole chain and land verified.

    The requirement: 给一个 agent 发布任务 -> 它拆解 -> 与其他 agent 论证 ->
    分配各自的任务 -> 执行 -> 验收落地.  Nothing drove that sequence before:
    each agent could see its own queue but nothing knew what the whole
    objective needed next.

    This asserts the seven properties that make it real, in one pass:
      1. a goal starts at ``intake`` and the driver asks its owner to decompose;
      2. decomposition is rejected for a child without acceptance criteria;
      3. finalize is BLOCKED while the deliberation has not converged;
      4. once converged, finalize materialises cards with the dependency chain;
      5. a card whose prerequisites are unfinished cannot be claimed
         (``blocked_by_dependency``) and can be once they are done;
      6. the producer may not verify its own delivery (§五);
      7. a non-producer's approval closes the goal.
    """
    _fresh_db()
    import json
    from memall.core.db import get_conn, init_db
    from memall.mcp.adapter import handle_call
    from memall.pipeline.goals import goal_tick

    init_db()
    PM, W1, W2, VER = "gpm", "gw1", "gw2", "gvr"

    def call(tool, args):
        return json.loads(handle_call(tool, args))

    def phase(gid):
        conn = get_conn()
        try:
            raw = conn.execute("SELECT metadata FROM memories WHERE id=?",
                               (gid,)).fetchone()
        finally:
            conn.close()
        m = json.loads(raw[0] or "{}") if raw else {}
        return m.get("phase")

    # (1) intake + driver points at the owner
    created = call("memall_write", {"action": "goal_create", "agent_name": PM,
                                    "objective": "守卫目标：验证全链路",
                                    "acceptance": "可验证", "verifier": VER})
    gid = created.get("goal_id")
    if not gid:
        return False, f"goal_create failed: {created}"
    started = phase(gid) == "intake"
    tick0 = goal_tick()
    points_at_owner = any(a.get("goal_id") == gid and a.get("action") == "decompose"
                          for a in tick0.get("actions", []))

    # (2) a child without acceptance is rejected
    bad = call("memall_write", {"action": "goal_decompose", "agent_name": PM,
                                "goal_id": gid,
                                "children": [{"title": "无验收", "detail": "d",
                                              "assignee": W1, "depends_on_idx": []}]})
    rejected = bad.get("status") == "rejected"

    # (2b) a proper decomposition is accepted
    ok = call("memall_write", {"action": "goal_decompose", "agent_name": PM,
                               "goal_id": gid, "children": [
        {"title": "甲", "detail": "d", "assignee": W1, "acceptance": "甲达标",
         "depends_on_idx": []},
        {"title": "乙", "detail": "d", "assignee": W2, "acceptance": "乙达标",
         "depends_on_idx": [0]}]})
    decomposed = ok.get("status") == "decomposed"

    disc = call("memall_discussion", {"action": "create", "agent_name": PM,
                                      "title": "守卫目标分工论证",
                                      "background": "验证用",
                                      "participants": [PM, W1, W2, VER],
                                      "convergence_rule": "majority"})
    did = disc.get("discussion_id") or disc.get("memory_id")
    call("memall_write", {"action": "goal_deliberate", "agent_name": PM,
                          "goal_id": gid, "discussion_id": did})

    # (3) finalize blocked before convergence
    pre = call("memall_write", {"action": "goal_finalize", "agent_name": PM,
                                "goal_id": gid})
    blocked_before = pre.get("status") == "blocked"

    for ag in (W1, W2, VER):
        call("memall_discussion", {"action": "respond", "discussion_id": did,
                                   "agent_name": ag, "stance": "agree",
                                   "arguments": f"{ag} ok"})

    # (4) finalize materialises the chain
    fin = call("memall_write", {"action": "goal_finalize", "agent_name": PM,
                                "goal_id": gid})
    tasks = fin.get("tasks") or []
    chain_ok = (fin.get("status") == "assigned" and len(tasks) == 2
                and tasks[1].get("depends_on") == [tasks[0]["task_id"]])
    k0 = tasks[0]["task_id"] if tasks else 0
    k1 = tasks[1]["task_id"] if len(tasks) > 1 else 0

    # (5) dependency gate
    gate = call("memall_write", {"action": "task_claim", "agent_name": W2,
                                 "task_id": k1})
    gate_blocks = (gate.get("status") == "blocked_by_dependency"
                   and bool(gate.get("blocked_by")))

    call("memall_write", {"action": "task_claim", "agent_name": W1, "task_id": k0})
    call("memall_write", {"action": "update", "memory_id": k0, "agent_name": W1,
                          "metadata": json.dumps({"status": "done"})})
    gate_open = call("memall_write", {"action": "task_claim", "agent_name": W2,
                                      "task_id": k1}).get("status") == "claimed"
    call("memall_write", {"action": "update", "memory_id": k1, "agent_name": W2,
                          "metadata": json.dumps({"status": "done"})})

    req = call("memall_write", {"action": "goal_request_verification",
                                "agent_name": PM, "goal_id": gid})
    in_review = req.get("status") == "verifying"

    # (6) the producer cannot self-verify
    self_ver = call("memall_write", {"action": "goal_verify", "agent_name": W1,
                                     "goal_id": gid, "passed": True,
                                     "verdict": "self"})
    self_blocked = self_ver.get("status") == "forbidden"

    # (7) an independent verifier closes it
    done = call("memall_write", {"action": "goal_verify", "agent_name": VER,
                                 "goal_id": gid, "passed": True,
                                 "verdict": "checked"})
    closed = done.get("status") == "closed" and phase(gid) == "closed"

    ok_all = (started and points_at_owner and rejected and decomposed
              and blocked_before and chain_ok and gate_blocks and gate_open
              and in_review and self_blocked and closed)
    return ok_all, (f"intake={started} driver={points_at_owner} "
                    f"reject_no_acceptance={rejected} decomposed={decomposed} "
                    f"blocked_before_converge={blocked_before} chain={chain_ok} "
                    f"dep_gate={gate_blocks} dep_opens={gate_open} "
                    f"review={in_review} self_verify_blocked={self_blocked} "
                    f"closed={closed}")


def guard_agent_waker_is_safe() -> Tuple[bool, str]:
    """The agent waker must be off by default and never shell out.

    Waking an agent starts a process that can edit code, so the invariants are
    not optional:
      * disabled by default (and dry-run by default) — a fresh deployment must
        not begin spawning agents because it upgraded MemALL;
      * the prompt is ONE argv element and the command never uses shell=True:
        the prompt is model- or discussion-authored text, so treating it as a
        shell string would be a remote code execution path;
      * an agent on the write deny-list is never woken.

    Measured when this guard was written: hermes -z (the only headless CLI on
    this host) hangs — 90s with no output, no log line, no lock file — so the
    transport is unavailable even though the spawn pipeline is sound.  The
    waker is therefore left disabled with dry_run on; flipping those two flags
    against a working CLI is all that is needed to arm it.
    """
    _fresh_db()
    import json
    from memall.pipeline import agent_waker as W

    from pathlib import Path as _P

    _src = (_P(__file__).resolve().parent
            / "memall/pipeline/agent_waker.py").read_text(encoding="utf-8")

    # (1) the prompt stays a single argv element, even when it looks like shell
    evil = 'x"; rm -rf / # `whoami` $(id)'
    W._commands = lambda: {"probe": {"argv": ["prog", "-p", "{prompt}"],
                                     "env": {"MEMALL_AGENT_NAME": "{agent}"}}}
    argv, env, stdin_prompt, _rt = W.build_invocation("probe", evil)
    single_element = bool(argv) and sum(1 for a in argv if evil == a) == 1
    env_ok = env.get("MEMALL_AGENT_NAME") == "probe"
    no_shell = "shell" not in " ".join(argv) or True  # shell never comes from argv

    # The source must not pass shell=True to subprocess, and must pass
    # shell=False explicitly.  Match the ARGUMENT form, not the literal: the
    # module's own safety comment contains the words "never shell=True".
    import re
    from pathlib import Path

    src = (Path(__file__).resolve().parent / "memall/pipeline/agent_waker.py"
           ).read_text(encoding="utf-8")
    passes_shell_true = re.search(r"shell\s*=\s*True\s*[,)]", src) is not None
    passes_shell_false = re.search(r"shell\s*=\s*False\s*[,)]", src) is not None
    no_shell_true = (not passes_shell_true) and passes_shell_false

    # (2) The CODE defaults must be off: a deployment that has no
    # agents.wake block (or upgrades without one) must never start spawning
    # agents.  Check the source rather than the live config, because an
    # operator turning it on is a legitimate choice, not a defect.
    defaults_off = ('_cfg("enabled", False)' in _src
                    and '_cfg("dry_run", True)' in _src)
    unconfigured = W.build_invocation("nobody-configured", "x")[0] is None

    # (3) A command may only speak for the agent whose runtime it is.  A borrowed
    # CLI wearing another agent's name produces a throwaway session writing under
    # an identity that never saw the work — the opposite of dispatching to a
    # colleague.  Refusal must happen before any spawn.
    W._commands = lambda: {"impostor_target": {
        "runtime_agent": "someone_else",
        "argv": ["prog", "-p", "{prompt}"],
        "env": {"MEMALL_AGENT_NAME": "{agent}"}}}
    _saved_enabled, _saved_dry = W.enabled, W.dry_run
    W.enabled = lambda: True
    W.dry_run = lambda: False
    try:
        refused = W.wake_agent("impostor_target", "x", goal_id=0, action="t")
    finally:
        W.enabled, W.dry_run = _saved_enabled, _saved_dry
    refused_impostor = (refused.get("woke") is False
                        and "runtime_agent" in (refused.get("reason") or ""))

    ok = single_element and env_ok and no_shell and no_shell_true \
        and defaults_off and unconfigured and refused_impostor
    return ok, (f"prompt_single_argv={single_element} env_injected={env_ok} "
                f"no_shell=True:{no_shell_true} defaults_off={defaults_off} "
                f"unconfigured_produces_nothing={unconfigured} "
                f"impostor_refused={refused_impostor}")


def guard_wake_claimed_in_db() -> Tuple[bool, str]:
    """Two processes racing to wake the same (agent, goal, action) must
    spawn exactly ONE headless run (P1-3, memory #1000942).

    Every stdio MCP client session spawns its own server process, each with
    a fallback timer calling goal_tick -> wake_agent.  The in-process
    cooldown cannot see across processes, so the same agent was spawnable
    once per live session — two woken instances would run concurrently
    against the same store.  The claim now lives in the ``wake_claims``
    table (migration 040), keyed (agent, goal_id, action).

    The second process is simulated by clearing the waker's process memory
    (a fresh server process starts with empty ``_last_wake``/``_HOURLY``)
    and calling ``wake_agent`` again against the same throwaway DB.  It must
    be refused with a claim/cooldown reason, while a different goal_id is an
    independent claim and must still go through.
    """
    _fresh_db()
    import sys as _sys
    import memall.config as mc
    from memall.core.db import get_conn, init_db
    from memall.pipeline import agent_waker as W

    init_db()
    # Don't fight the config hot-reloader: pin the waker entry points
    # directly.  The config path itself is already covered by
    # guard_agent_waker_is_safe; here we only need wake to be "on" with a
    # spawnable probe command so the DB-claim behaviour is what's exercised.
    # dry_run is pinned too: the claim sits AFTER the dry_run branch, so a
    # config with dry_run=true (the safe default) would make this guard return
    # "dry_run" before ever reaching wake_claims — it must exercise the claim
    # path regardless of the deployment's dry_run setting.
    _orig_enabled = W.enabled
    _orig_commands = W._commands
    _orig_dry_run = W.dry_run
    W.enabled = lambda: True
    W._commands = lambda: {"probe_wa": {"argv": [_sys.executable, "-c", "pass"]}}
    W.dry_run = lambda: False
    try:
        W._last_wake.clear()
        W._HOURLY.clear()
        W._wake_log.clear()

        # Process 1: claims and spawns.
        r1 = W.wake_agent("probe_wa", "第一进程", goal_id=7, action="decompose")
        # Process 2: fresh process memory (its own server process), same DB.
        W._last_wake.clear()
        W._HOURLY.clear()
        W._wake_log.clear()
        r2 = W.wake_agent("probe_wa", "第二进程", goal_id=7, action="decompose")
        # A different goal_id is an independent decision — must still claim.
        W._last_wake.clear()
        W._HOURLY.clear()
        r3 = W.wake_agent("probe_wa", "第三进程", goal_id=8, action="decompose")

        conn = get_conn()
        n = conn.execute("SELECT COUNT(*) FROM wake_claims").fetchone()[0]
        conn.close()

        reason2 = r2.get("reason", "")
        claim_r2 = ("claim" in reason2) or ("cooldown" in reason2)
        ok = (r1.get("woke") is True and r2.get("woke") is False and claim_r2
              and r3.get("woke") is True and n == 2)
        result = (ok, (f"r1={r1.get('woke')} r2={r2.get('woke')} "
                       f"reason={reason2!r} r3={r3.get('woke')} db_rows={n}"))
    finally:
        W.enabled = _orig_enabled
        W._commands = _orig_commands
        W.dry_run = _orig_dry_run
    return result


def guard_goal_tick_on_heartbeat_cadence() -> Tuple[bool, str]:
    """P0-①: goal hand-offs must ride the 300s tick, not the 6h pipeline.

    ``goal_tick`` used to run only inside the fallback pipeline (21600s), so a
    goal waiting on its owner could sit half a day before anyone was told.
    The scheduler's fast tick now runs
    ``agent_round`` -> ``deliver_goal_prompts`` -> ``goal_tick``.

    Three properties, asserted behaviourally through the REAL ``_tick``:
      1. cadence — the fast tick is not slower than the heartbeat, and is far
         faster than the pipeline;
      2. idempotency — two consecutive ticks with no state change leave ONE
         session note for the goal, not two (reconcile, not a firehose);
      3. isolation — a raising goal driver must not break ``agent_round`` or
         the scheduler tick; the heartbeat keeps beating.
    """
    _fresh_db()
    import time as _time
    from pathlib import Path
    import memall.config as mc
    import memall.scheduler.scheduler as S
    from memall.core.db import get_conn, init_db
    from memall.mcp import shared as SH
    from memall.mcp.adapter import handle_call
    from memall.pipeline import fallback as FB

    init_db()

    # Wake spawns a CLI process.  The guard must not depend on whatever the
    # deployment (or an earlier guard in the same process) left in
    # agents.wake, and must put it back afterwards.
    agents_cfg = mc._config.setdefault("agents", {})
    prev_wake = agents_cfg.get("wake")
    agents_cfg["wake"] = {"enabled": False, "dry_run": True}
    mc._last_mtime_check = float("inf")

    prev_log = S.HEARTBEAT_LOG
    S.HEARTBEAT_LOG = Path(tempfile.mkdtemp(prefix="memall_hb_")) / "heartbeat.log"
    try:
        owner = "gowner"
        created = json.loads(handle_call("memall_write", {
            "action": "goal_create", "agent_name": owner,
            "objective": "守卫：心跳节奏驱动目标推进（P0-①）",
            "acceptance": "可验证", "verifier": "gver"}))
        gid = created.get("goal_id")
        if not gid:
            return False, f"goal_create failed: {created}"

        conn = get_conn()
        conn.execute(
            "INSERT OR IGNORE INTO identities (agent_name, agent_type, status) "
            "VALUES (?, 'ai', 'active')", (owner,))
        conn.commit()
        conn.close()

        SH._PENDING_SESSION_NOTES.clear()
        SH._NOTE_DEDUP.clear()
        SH._NOTE_ACKED.clear()

        sched = S.Scheduler()
        now_t = _time.time()
        # Pin every slow timer to "just ran": one _tick() then exercises
        # exactly the heartbeat + agent_round (fast) path.
        for attr in ("_last_pipeline", "_last_doctor", "_last_forget",
                     "_last_security", "_last_lifecycle", "_last_dream"):
            setattr(sched, attr, now_t)
        sched._last_agent_round = 0  # fast tick is due

        sched._tick()
        after_one = SH.consume_session_note(owner) or ""
        sched._last_agent_round = 0
        sched._tick()
        after_two = SH.consume_session_note(owner) or ""

        marker = f"[目标 #{gid}]"
        first_prompt = marker in after_one
        repeat_delta = after_two.count(marker) - after_one.count(marker)

        # Isolation: the driver blows up, the loop must not.
        def _boom(*a, **k):
            raise RuntimeError("guard-injected goal driver failure")

        from memall.scheduler.agent_round import agent_round
        orig_driver = FB.deliver_goal_prompts
        FB.deliver_goal_prompts = _boom
        try:
            try:
                agent_round()
                round_ok = True
            except Exception:
                round_ok = False
        finally:
            FB.deliver_goal_prompts = orig_driver

        FB.deliver_goal_prompts = _boom
        try:
            sched._last_agent_round = 0
            sched._tick()
            tick_ok = True
        except Exception:
            tick_ok = False
        finally:
            FB.deliver_goal_prompts = orig_driver

        conn = get_conn()
        hb = conn.execute(
            "SELECT last_heartbeat FROM identities WHERE agent_name = ?",
            (owner,)).fetchone()
        conn.close()
        heartbeat_alive = bool(hb and hb["last_heartbeat"])

        cadence_ok = (S.INTERVAL_PROXY_SESSION <= S.INTERVAL_HEARTBEAT
                      and S.INTERVAL_PROXY_SESSION < S.INTERVAL_PIPELINE)
        ok = (cadence_ok and first_prompt and repeat_delta == 0
              and round_ok and tick_ok and heartbeat_alive)
        return ok, (f"cadence={S.INTERVAL_PROXY_SESSION}s<=hb({S.INTERVAL_HEARTBEAT}s)"
                    f"<pipeline({S.INTERVAL_PIPELINE})s:{cadence_ok} "
                    f"first_prompt={first_prompt} repeat_delta={repeat_delta} "
                    f"round_survives={round_ok} tick_survives={tick_ok} "
                    f"heartbeat_alive={heartbeat_alive}")
    finally:
        S.HEARTBEAT_LOG = prev_log
        if prev_wake is None:
            agents_cfg.pop("wake", None)
        else:
            agents_cfg["wake"] = prev_wake


def guard_empty_identity_rejected_everywhere() -> Tuple[bool, str]:
    """An empty identity must be refused at every goal-surface gate (P1-1).

    goals.py's decompose/attach_discussion/finalize used
    ``if agent_name and owner and ...`` — an empty agent_name short-circuited
    the check and let an anonymous caller decompose/attach/finalise any goal.
    memory_write.handle_update had the same hole: ``if caller and ...`` skipped
    the ownership gate for caller="".

    There is no legitimate empty-identity caller on the goal surface (MCP
    injects session_identity; the gateway reads params/clientInfo), so an empty
    identity is now rejected outright.

    The guards call the pipeline handlers DIRECTLY rather than through the
    MCP adapter: the adapter re-attaches the bound session identity (F3), so
    an empty agent_name never reaches the handler via MCP — that is the first
    line of defence.  This guard tests the second line (the handler itself).
    """
    _fresh_db()
    import json
    from memall.core.db import get_conn, init_db
    from memall.mcp.tools.memory_write import handle_update
    from memall.pipeline import goals as G

    init_db()
    PM, W1 = "gpm", "gw1"

    created = G.create_goal("空身份守卫目标", PM, acceptance="可验证", verifier="gvr")
    gid = created.get("goal_id")
    if not gid:
        return False, f"create_goal failed: {created}"
    children = [{"title": "甲", "detail": "d", "assignee": W1,
                 "acceptance": "甲达标", "depends_on_idx": []}]

    r_decompose = G.decompose(gid, "", children)
    r_attach = G.attach_discussion(gid, "", 1)
    r_finalize = G.finalize(gid, "")
    ok_decompose = r_decompose.get("status") == "forbidden"
    ok_attach = r_attach.get("status") == "forbidden"
    ok_finalize = r_finalize.get("status") == "forbidden"

    # memory_write.handle_update: an explicitly empty agent_name must be
    # rejected rather than silently skipping the ownership gate.  A missing
    # agent_name (caller is None) is the trusted-internal path and stays open.
    conn = get_conn()
    try:
        vid = conn.execute(
            "INSERT INTO memories (content, content_hash, level, owner, agent_name, "
            "subject, category, project, summary, occurred_at, created_at, updated_at, "
            "supersedes, confidence, visibility, metadata, arc_status) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("x" * 60, "h" * 64, "L5", PM, W1, "[任务] 他人卡", "task", "memall",
             "", "2026-01-01T00:00:00+00:00", "2026-01-01T00:00:00+00:00",
             "2026-01-01T00:00:00+00:00", "[]", 0.5, "private", "{}", "open")
        ).lastrowid
    finally:
        conn.close()
    r_update = json.loads(handle_update({"memory_id": vid, "agent_name": "",
                                         "content": "hijack"}))
    ok_update = "empty identity" in json.dumps(r_update, ensure_ascii=False)

    ok = ok_decompose and ok_attach and ok_finalize and ok_update
    return ok, (f"decompose={ok_decompose} attach={ok_attach} "
                f"finalize={ok_finalize} update={ok_update}")


def guard_verifying_requires_verifier() -> Tuple[bool, str]:
    """request_verification must refuse an empty verifier (P1-2).

    The old code advanced to ``verifying`` with an empty verifier; the driver
    then produced an empty-agent delivery that fallback.py silently dropped, so
    the goal hung forever with no escalation.
    """
    _fresh_db()
    import json
    from memall.core.db import init_db
    from memall.mcp.adapter import handle_call

    init_db()
    PM, W1, VER = "gpm", "gw1", "gvr"

    def call(tool, args):
        return json.loads(handle_call(tool, args))

    import time as _t

    def step(payload, tool="memall_write", attempts=5):
        """Run one lifecycle step, retrying ONLY the documented conflict.

        ``_save_meta`` is a compare-and-set against the metadata blob, and two
        writers legitimately share a goal: the background agent_round driver
        (``goal_tick``) refreshes the same row while an agent calls
        decompose/deliberate/finalize.  The contract is "re-read and retry",
        so a guard that ignores the intermediate status is flaky by
        construction — measured: the same flow passed 3/3 in isolation and
        intermittently failed with "'decomposed'" when the tick wrote between
        attach_discussion's read and its write.
        """
        out = {}
        for _ in range(attempts):
            out = call(tool, payload)
            if out.get("status") != "conflict":
                return out
            _t.sleep(0.2)
        return out

    created = step({"action": "goal_create", "agent_name": PM,
                    "objective": "空验收人目标",
                    "acceptance": "可验证"})  # no verifier
    gid = created.get("goal_id")
    if not gid:
        return False, f"create failed: {created}"
    dec = step({"action": "goal_decompose", "agent_name": PM, "goal_id": gid,
                "children": [{"title": "甲", "detail": "d", "assignee": W1,
                              "acceptance": "甲达标", "depends_on_idx": []}]})
    if dec.get("phase") != "decomposed":
        return False, f"decompose failed: {dec}"
    disc = call("memall_discussion", {"action": "create", "agent_name": PM,
                                      "title": "t", "background": "b",
                                      "participants": [PM, W1, VER],
                                      "convergence_rule": "majority"})
    did = disc.get("discussion_id") or disc.get("memory_id")
    if not did:
        return False, f"discussion create failed: {disc}"
    delib = step({"action": "goal_deliberate", "agent_name": PM,
                  "goal_id": gid, "discussion_id": did})
    if delib.get("status") != "deliberating":
        return False, f"deliberate failed: {delib}"
    for ag in (PM, W1, VER):
        call("memall_discussion", {"action": "respond", "discussion_id": did,
                                   "agent_name": ag, "stance": "agree"})
    fin = step({"action": "goal_finalize", "agent_name": PM, "goal_id": gid})
    if fin.get("status") != "assigned":
        return False, f"finalize failed: {fin}"
    k0 = (fin.get("tasks") or [{}])[0].get("task_id")
    call("memall_write", {"action": "task_resolve", "agent_name": W1, "task_id": k0})
    r = call("memall_write", {"action": "goal_request_verification",
                              "agent_name": PM, "goal_id": gid})
    ok = r.get("status") == "error" and "verifier" in json.dumps(r, ensure_ascii=False)
    return ok, f"status={r.get('status')} err={r.get('error','')[:100]}"


def guard_forward_dependency_rejected() -> Tuple[bool, str]:
    """decompose must reject a child that depends on a later sibling (P2-1)."""
    _fresh_db()
    import json
    from memall.core.db import init_db
    from memall.mcp.adapter import handle_call

    init_db()
    PM, W1 = "gpm", "gw1"

    def call(tool, args):
        return json.loads(handle_call(tool, args))

    created = call("memall_write", {"action": "goal_create", "agent_name": PM,
                                    "objective": "前向依赖目标",
                                    "acceptance": "可验证", "verifier": "gvr"})
    gid = created.get("goal_id")
    # child 0 depends on child 1 (di > i) — must be rejected
    r = call("memall_write", {"action": "goal_decompose", "agent_name": PM,
                              "goal_id": gid, "children": [
        {"title": "甲", "detail": "d", "assignee": W1, "acceptance": "甲达标",
         "depends_on_idx": [1]},
        {"title": "乙", "detail": "d", "assignee": W1, "acceptance": "乙达标",
         "depends_on_idx": []}]})
    ok = r.get("status") == "rejected"
    return ok, f"status={r.get('status')} problems={r.get('problems')}"


def guard_unresolved_dependency_fails_loudly() -> Tuple[bool, str]:
    """finalize must fail loudly on an unresolved forward dependency (P2-1).

    Second line of defence: decompose already rejects di > i, but a plan
    written before that fix can still carry a forward edge.  Materialising it
    would silently drop the dependency — this asserts it errors instead.
    """
    _fresh_db()
    import json
    from memall.core.db import get_conn, init_db
    from memall.mcp.adapter import handle_call

    init_db()
    PM, W1, VER = "gpm", "gw1", "gvr"

    def call(tool, args):
        return json.loads(handle_call(tool, args))

    created = call("memall_write", {"action": "goal_create", "agent_name": PM,
                                    "objective": "未解析依赖目标",
                                    "acceptance": "可验证", "verifier": VER})
    gid = created.get("goal_id")
    call("memall_write", {"action": "goal_decompose", "agent_name": PM, "goal_id": gid,
                          "children": [{"title": "甲", "detail": "d", "assignee": W1,
                                        "acceptance": "甲达标", "depends_on_idx": []},
                                       {"title": "乙", "detail": "d", "assignee": W1,
                                        "acceptance": "乙达标", "depends_on_idx": [0]}]})
    # hand-craft a plan whose child 0 depends on index 1 (a later sibling).
    conn = get_conn()
    try:
        raw = conn.execute("SELECT metadata FROM memories WHERE id=?", (gid,)).fetchone()
        meta = json.loads(raw[0] or "{}")
        meta["plan"] = [
            {"idx": 0, "title": "甲", "detail": "d", "assignee": W1,
             "acceptance": "甲达标", "depends_on_idx": [1]},
            {"idx": 1, "title": "乙", "detail": "d", "assignee": W1,
             "acceptance": "乙达标", "depends_on_idx": []},
        ]
        conn.execute("UPDATE memories SET metadata=? WHERE id=?",
                     (json.dumps(meta, ensure_ascii=False), gid))
        conn.commit()
    finally:
        conn.close()
    disc = call("memall_discussion", {"action": "create", "agent_name": PM,
                                      "title": "t", "background": "b",
                                      "participants": [PM, W1, VER],
                                      "convergence_rule": "majority"})
    did = disc.get("discussion_id") or disc.get("memory_id")
    call("memall_write", {"action": "goal_deliberate", "agent_name": PM,
                          "goal_id": gid, "discussion_id": did})
    for ag in (PM, W1, VER):
        call("memall_discussion", {"action": "respond", "discussion_id": did,
                                   "agent_name": ag, "stance": "agree"})
    r = call("memall_write", {"action": "goal_finalize", "agent_name": PM, "goal_id": gid})
    ok = r.get("status") == "error" and "unresolved" in json.dumps(r, ensure_ascii=False)
    return ok, f"status={r.get('status')} err={r.get('error','')[:120]}"


def guard_duplicate_objective_rejected() -> Tuple[bool, str]:
    """create_goal must not mint a second goal for the same objective (P2-2)."""
    _fresh_db()
    import json
    from memall.core.db import init_db
    from memall.mcp.adapter import handle_call

    init_db()
    PM = "gpm"

    def call(tool, args):
        return json.loads(handle_call(tool, args))

    first = call("memall_write", {"action": "goal_create", "agent_name": PM,
                                  "objective": "重复目标：验证去重",
                                  "acceptance": "可验证", "verifier": "gvr"})
    gid1 = first.get("goal_id")
    # same objective, different verifier — defeats the content_hash backstop
    second = call("memall_write", {"action": "goal_create", "agent_name": PM,
                                   "objective": "重复目标：验证去重",
                                   "acceptance": "可验证", "verifier": "gvx"})
    dup = second.get("status") == "duplicate" and second.get("goal_id") == gid1
    # a different objective must still create
    third = call("memall_write", {"action": "goal_create", "agent_name": PM,
                                  "objective": "另一个目标",
                                  "acceptance": "可验证", "verifier": "gvr"})
    created = third.get("status") == "created" and bool(third.get("goal_id"))
    # A CLOSED goal must not block a fresh one with the same objective.  The
    # dedup only looks at status='active'; verify_goal closes by writing
    # phase=closed/status=done (goals.py:810-811), so mirror that exactly
    # instead of inventing a status the lifecycle never produces.
    from memall.core.db import get_conn
    _c = get_conn()
    try:
        _row = _c.execute("SELECT metadata FROM memories WHERE id = ?",
                          (gid1,)).fetchone()
        _m = json.loads(_row["metadata"])
        _m["phase"] = "closed"
        _m["status"] = "done"
        _c.execute("UPDATE memories SET metadata = ? WHERE id = ?",
                   (json.dumps(_m, ensure_ascii=False), gid1))
        _c.commit()
    finally:
        _c.close()
    again = call("memall_write", {"action": "goal_create", "agent_name": PM,
                                  "objective": "重复目标：验证去重",
                                  "acceptance": "可验证", "verifier": "gvr"})
    reopened = (again.get("status") == "created"
                and again.get("goal_id") not in (None, gid1))
    ok = dup and created and reopened
    return ok, (f"dup={dup} gid1={gid1} gid2={second.get('goal_id')} "
                f"created={created} closed_recreate={reopened} "
                f"cycle={again.get('cycle')} prev={again.get('previous_goal_id')}")


def guard_depends_on_points_at_task_card() -> Tuple[bool, str]:
    """A depends_on that points at a non-task memory must be diagnosed, not
    silently frozen (task #1001343).

    Measured: card #1000947 had depends_on=[#1000945], where #1000945 was an
    L6 reflection whose status is always ``active``.  The claim gate treated
    it as a normal blocking dependency, so the card was blocked forever — a
    silent deadlock, not "not done yet".

    The claim gate now classifies a non-task depends_on target as
    ``malformed_dependency`` (with the target's level/category) instead of
    letting it hang.  Real dependencies are unaffected: a genuine
    task-to-task edge still blocks until the prerequisite resolves.
    """
    _fresh_db()
    import hashlib
    import json
    from memall.core.db import get_conn, init_db
    from memall.mcp.tasks import claim_task

    init_db()
    AG = "dsh"

    def mk(content, level, category, agent, subject, meta):
        """Direct INSERT: bypasses capture's quality gate, which rejects short
        test fixtures (reasoning=0 → min=0 → rejected)."""
        h = hashlib.sha256(content.encode("utf-8")).hexdigest()
        conn = get_conn()
        try:
            cur = conn.execute(
                "INSERT INTO memories (content, content_hash, level, owner, "
                "agent_name, subject, category, project, summary, occurred_at, "
                "created_at, updated_at, supersedes, confidence, visibility, "
                "metadata, arc_status) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (content, h, level, agent, agent, subject, category, "memall",
                 "", "2026-01-01T00:00:00+00:00", "2026-01-01T00:00:00+00:00",
                 "2026-01-01T00:00:00+00:00", "[]", 0.5, "private",
                 json.dumps(meta, ensure_ascii=False), "open"))
            conn.commit()
            return cur.lastrowid
        finally:
            conn.close()

    # A reflection whose status can never reach a terminal state.
    ref_id = mk(
        "管线观察（2026-10-29）：当前记忆分布 L6×1427、L4×1341、L9×1037、"
        "L11×811、P2×693、L5×438、L10×199、L7×185。L6 反思共 1269 条，其中"
        "高质量 1 条，近 24 小时新增 432 条，聚合组 3 个。告警：L6 高质量反思"
        "占比偏低；活跃领域从 110 缩至 11，领域多样性下降。",
        "L6", "reflection", "system", "管线观察（2026-10-29）",
        {"status": "active"})

    # (1) depends_on pointing at the reflection -> malformed_dependency.
    bad = mk("设计阶段：验证 depends_on 指向非任务记忆时的诊断行为",
             "L5", "task", AG, "[任务] 依赖指向 reflection 的卡",
             {"status": "active", "assignee": AG, "depends_on": [ref_id]})
    r_bad = claim_task(bad, AG)
    md0 = (r_bad.get("malformed_dependencies") or [{}])[0]
    ok_malformed = (r_bad.get("status") == "malformed_dependency"
                    and md0.get("level") == "L6"
                    and md0.get("category") == "reflection")

    # (2) a real task-to-task edge still blocks until the prerequisite resolves.
    a = mk("设计阶段：产出测试方案与用例设计文档，含测试目标、用例设计、环境需求、验收标准",
           "L5", "task", AG, "[任务] 前置卡 A",
           {"status": "active", "assignee": AG, "depends_on": []})
    b = mk("执行阶段：搭建环境、执行用例、记录结果、输出验收报告",
           "L5", "task", AG, "[任务] 后置卡 B",
           {"status": "active", "assignee": AG, "depends_on": [a]})
    r_block = claim_task(b, AG)
    ok_block = r_block.get("status") == "blocked_by_dependency"
    # resolve A, then B must claim successfully (existing behaviour unchanged).
    conn = get_conn()
    try:
        am = json.loads(conn.execute("SELECT metadata FROM memories WHERE id=?",
                                     (a,)).fetchone()[0] or "{}")
        am["status"] = "resolved"
        conn.execute("UPDATE memories SET metadata=? WHERE id=?",
                     (json.dumps(am, ensure_ascii=False), a))
        conn.commit()
    finally:
        conn.close()
    r_ok = claim_task(b, AG)
    ok_release = r_ok.get("status") in ("claimed", "reclaimed")

    ok = ok_malformed and ok_block and ok_release
    return ok, (f"malformed={ok_malformed} block={ok_block} "
                f"release={ok_release}")


def guard_write_action_surface_in_sync() -> Tuple[bool, str]:
    """Every memall_write action the validator accepts must be ADVERTISED.

    This costs hours if it drifts.  The goal lifecycle was fully wired -
    dispatcher branch present, validator mapping present, handlers present -
    but the action was missing from the tool's ``inputSchema.action.enum``.
    A client validates against the schema it received, so it never makes the
    call at all: the woken agent tried ``goal_decompose``, was told
    "'goal_decompose' is not one of [...]", guessed at ``update`` instead, got
    HTTP 200, and reported success while the goal had not moved.

    The drift happened because the enum line is a single long literal that
    several agents rewrote in the same session: a later rewrite of that line
    silently dropped another agent's additions.

    The invariant: validator mapping ⊆ advertised enum, for both tools.
    """
    _fresh_db()
    import memall.mcp.adapter as ad
    from memall.mcp import validator as V

    ad._ensure_loaded()
    problems = []
    for tool in ("memall_write", "memall_read"):
        published = {}
        for t in ad.TOOL_DEFINITIONS:
            if t["name"] == tool:
                published = (t["inputSchema"].get("properties", {})
                             .get("action", {}).get("enum", []))
                break
        if not published:
            problems.append(f"{tool}: no action enum advertised")
            continue
        accepted = {a for (t, a) in getattr(V, "_ACTION_MODELS", {}) if t == tool}
        missing = sorted(accepted - set(published))
        if missing:
            problems.append(f"{tool}: accepted-but-not-advertised={missing}")

    ok = not problems
    return ok, ("surface in sync" if ok else "; ".join(problems))


def guard_goal_children_accepts_json_string() -> Tuple[bool, str]:
    """A decomposition sent as a JSON STRING must still be accepted.

    Models routinely serialise a nested array as a JSON-encoded string.  Before
    this, ``children`` was declared ``Optional[List[Dict]]`` only, so a
    well-formed decomposition arrived as the text ``[{"title": ...}]`` and
    pydantic rejected it with "children: must be a valid list".

    Measured on a live woken agent: it burned its entire turn guessing at the
    format — retrying with different quoting, shorter strings, fewer fields —
    and never decomposed the goal, then exited claiming nothing was wrong.
    The GOAL stayed at intake with an empty plan.

    The contract must stay strict in the other direction: a string that is not
    JSON is still an error, and it must say so instead of silently becoming
    "no children".
    """
    _fresh_db()
    import json as _json
    from memall.mcp.models import GoalWriteInput

    sample = [{"title": "t0", "acceptance": "a0"},
              {"title": "t1", "acceptance": "a1", "depends_on_idx": [0]}]
    from_string = GoalWriteInput(goal_id=1, children=_json.dumps(sample)).children
    from_list = GoalWriteInput(goal_id=1, children=sample).children
    csv_participants = GoalWriteInput(participants="dsh,qoder").participants

    string_ok = (isinstance(from_string, list) and len(from_string) == 2
                 and from_string[1]["title"] == "t1")
    list_ok = isinstance(from_list, list) and len(from_list) == 2
    participants_ok = csv_participants == ["dsh", "qoder"]

    rejected = False
    detail = ""
    try:
        GoalWriteInput(goal_id=1, children="not json at all")
    except Exception as exc:
        rejected = True
        detail = str(exc)
    # The message must name the field so the caller can fix the call.
    names_field = "children" in detail

    ok = string_ok and list_ok and participants_ok and rejected and names_field
    return ok, (f"json_string_parsed={string_ok} native_list={list_ok} "
                f"csv_participants={participants_ok} bad_string_rejected={rejected} "
                f"error_names_field={names_field}")


def guard_partial_finalize_leaves_goal_in_deliberating() -> Tuple[bool, str]:
    """A failed child must stop finalize in 'deliberating', not look 'assigned'.

    P2-3.  ``finalize`` used to swallow a failed ``capture`` (``if isinstance(tid,
    int)``) and advance the goal to ``assigned`` anyway, so the plan's missing
    card was invisible: the driver saw "waiting for assignees" forever, and a
    retry re-created every card it had already made.  This asserts the four
    properties of the fix:
      1. the goal stays in ``deliberating``;
      2. the response is an explicit ``partial`` naming created / failed /
         not-attempted children, and the same record is persisted;
      3. dependents are not attempted against a missing prerequisite;
      4. a retry completes and REUSES the card from the first attempt
         (no duplicate card per plan item).
    """
    _fresh_db()
    import json
    from memall.core import thin_waist
    from memall.core.db import get_conn, init_db
    from memall.pipeline import goals

    init_db()
    PM, VER = "pfm", "pfv"
    made = goals.create_goal("部分失败守卫目标", PM, acceptance="全部完成",
                             verifier=VER)
    gid = made.get("goal_id")
    if not gid:
        return False, f"create_goal failed: {made}"

    dec = goals.decompose(gid, PM, [
        {"title": "甲", "detail": "d", "assignee": "pfw1", "acceptance": "甲达标"},
        {"title": "乙", "detail": "d", "assignee": "pfw2", "acceptance": "乙达标",
         "depends_on_idx": [0]},
        {"title": "丙", "detail": "d", "assignee": "pfw3", "acceptance": "丙达标",
         "depends_on_idx": [1]},
    ])
    if dec.get("status") != "decomposed":
        return False, f"decompose failed: {dec}"
    # discussion_id 0 keeps the convergence gate out of this guard's way; the
    # real converge path is covered by guard_goal_lifecycle_end_to_end.
    att = goals.attach_discussion(gid, PM, 0)
    if att.get("status") != "deliberating":
        return False, f"attach_discussion failed: {att}"

    real_capture = thin_waist.capture
    calls = {"n": 0}

    def flaky(*a, **kw):
        calls["n"] += 1
        if calls["n"] == 2:
            raise ValueError("quality gate rejected (injected by guard)")
        return real_capture(*a, **kw)

    thin_waist.capture = flaky
    try:
        first = goals.finalize(gid, PM)
    finally:
        thin_waist.capture = real_capture

    def _meta(gid):
        conn = get_conn()
        try:
            row = conn.execute(
                "SELECT metadata FROM memories WHERE id = ?", (gid,)).fetchone()
        finally:
            conn.close()
        return json.loads(row["metadata"] or "{}") if row else {}

    def _cards(gid):
        conn = get_conn()
        try:
            rows = conn.execute(
                "SELECT id FROM memories WHERE level = 'L5' "
                "AND category = 'task' "
                "AND json_extract(metadata, '$.goal_id') = ?", (gid,)).fetchall()
        finally:
            conn.close()
        return [r["id"] for r in rows]

    after = _meta(gid)
    stayed = after.get("phase") == "deliberating"
    partial_ok = (first.get("status") == "partial"
                  and [c["idx"] for c in first.get("created") or []] == [0]
                  and [f["idx"] for f in first.get("failed") or []] == [1]
                  and first.get("not_attempted") == [2])
    recorded = bool((after.get("partial_finalize") or {}).get("failed"))
    lease_cleared = "finalize_inflight" not in after
    cards_after = _cards(gid)
    one_card = len(cards_after) == 1

    retry = goals.finalize(gid, PM)
    final = _meta(gid)
    assigned = retry.get("status") == "assigned" and final.get("phase") == "assigned"
    reused_first = any(r.get("idx") == 0 for r in retry.get("reused") or [])
    cards_final = _cards(gid)
    no_duplicates = (len(cards_final) == 3
                     and set(cards_after).issubset(set(cards_final)))
    cleaned = "partial_finalize" not in final and "finalize_inflight" not in final

    ok = (stayed and partial_ok and recorded and lease_cleared and one_card
          and assigned and reused_first and no_duplicates and cleaned)
    return ok, (f"stays_deliberating={stayed} partial_shape={partial_ok} "
                f"persisted={recorded} lease_released={lease_cleared} "
                f"one_card_after_failure={one_card} retry_assigned={assigned} "
                f"reused_first={reused_first} no_duplicates={no_duplicates} "
                f"state_cleaned={cleaned}")


def guard_addressed_to_single_impl() -> Tuple[bool, str]:
    """One implementation of "may this caller act on this card" (P2-4).

    The predicate lived twice (``mcp/tasks.py`` and
    ``pipeline/task_lifecycle.py``) and ``security.supervisor_agents`` was
    looked up in three places, so a policy change on one side silently did not
    apply on the other.  This asserts both halves of the contract:
      * no local ``_addressed_to`` definition is left anywhere in ``src``, and
        both callers import the shared gate;
      * the policy is per-operation: the addressee wins over the card's
        ``agent_name`` column, a supervisor may resolve/block but NOT claim,
        and an anonymous (``None``) internal caller may do both.
    """
    _fresh_db()
    import json
    import re
    from pathlib import Path
    from memall.core import task_identity as TI
    from memall.core.db import init_db
    from memall.core.thin_waist import capture
    from memall.mcp import tasks as T
    from memall.pipeline import task_lifecycle as TL

    init_db()
    src_dir = Path(__file__).resolve().parent / "memall"
    stray = [p.name for p in src_dir.rglob("*.py")
             if re.search(r"^\s*def\s+_addressed_to\b",
                          p.read_text(encoding="utf-8"), re.M)]
    no_copy = not stray
    both_import = ("from memall.core.task_identity import"
                   in Path(T.__file__).read_text(encoding="utf-8")
                   and "from memall.core.task_identity import"
                   in Path(TL.__file__).read_text(encoding="utf-8"))

    # Addressee resolution: assignee wins over the column, trimmed + casefolded.
    meta = {"assignee": "  AgentA "}
    assignee_wins = (TI.is_addressee(meta, "columnname", "agenta")
                     and not TI.is_addressee(meta, "columnname", "columnname"))
    fallback = TI.is_addressee({}, "colname", "colname")
    no_identity = not TI.can_claim({"assignee": "a"}, "a", "")

    def _card(subject):
        body = (f"{subject}：用于验证任务卡的身份门只允许被指派人操作，"
                f"以及监督者可以结卡但不能代认领。")
        return capture(body, level="L5", category="task",
                       agent_name="w1", subject=subject,
                       metadata=json.dumps({"status": "active", "assignee": "w1"},
                                           ensure_ascii=False))

    tid = _card("守卫卡片：单一身份实现")
    tid2 = _card("守卫卡片：内部调用方")
    orig_sup = TI.is_supervisor
    TI.is_supervisor = lambda a: (a or "").strip().lower() == "boss"
    try:
        sup_claim = T.claim_task(tid, "boss")
        other_resolve = TL.resolve_task(tid, caller="w2")
        mine = T.claim_task(tid, "w1")
        sup_resolve = TL.resolve_task(tid, caller="boss")
        anon_block = TL.block_task(tid2, "x", caller=None)
    finally:
        TI.is_supervisor = orig_sup

    sup_cannot_claim = sup_claim.get("status") == "forbidden"
    stranger_cannot_close = other_resolve.get("status") == "forbidden"
    addressee_claims = mine.get("status") in ("claimed", "reclaimed")
    sup_can_close = bool(sup_resolve.get("resolved_at"))
    anon_allowed = bool(anon_block.get("blocked_at"))

    ok = (no_copy and both_import and assignee_wins and fallback and no_identity
          and sup_cannot_claim and stranger_cannot_close and addressee_claims
          and sup_can_close and anon_allowed)
    return ok, (f"no_local_copy={no_copy} {stray or ''} both_import={both_import} "
                f"assignee_wins={assignee_wins} column_fallback={fallback} "
                f"empty_identity_refused={no_identity} "
                f"supervisor_cannot_claim={sup_cannot_claim} "
                f"stranger_cannot_close={stranger_cannot_close} "
                f"addressee_claims={addressee_claims} "
                f"supervisor_can_close={sup_can_close} "
                f"internal_caller_allowed={anon_allowed}")


def guard_metadata_cas() -> Tuple[bool, str]:
    """A stale goal transition must be refused, not written (P2-5).

    The driver tick and an agent share a goal row.  ``_save_meta`` used to
    write unconditionally, so a tick that had read ``deliberating`` could
    write its stale copy back after finalize advanced to ``assigned``,
    dropping ``task_ids`` and stranding the cards it had just created.  Now
    every transition is a compare-and-set on the blob it read: the first
    write wins, later writes on the same read return False, and a fresh read
    works again.
    """
    _fresh_db()
    import json
    import re
    from pathlib import Path
    from memall.core.db import get_conn, init_db
    from memall.pipeline import goals

    init_db()
    made = goals.create_goal("CAS 守卫目标", "casp", acceptance="可验证",
                             verifier="casv")
    gid = made.get("goal_id")
    if not gid:
        return False, f"create_goal failed: {made}"

    conn = get_conn()
    try:
        read = goals._get_goal(conn, gid)
        advanced = dict(read["meta"])
        advanced["phase"] = "assigned"
        first = goals._save_meta(conn, gid, advanced, read["raw"])
        conn.commit()
        # (a) the same stale read tries again — must not be applied
        replay = goals._save_meta(conn, gid, advanced, read["raw"])
        # (b) a tick holding the PRE-transition copy must not roll it back
        stale_tick = dict(read["meta"])
        stale_tick["phase"] = "intake"
        rollback = goals._save_meta(conn, gid, stale_tick, read["raw"])
        conn.commit()
        row = conn.execute("SELECT metadata FROM memories WHERE id = ?",
                           (gid,)).fetchone()
        fresh = goals._get_goal(conn, gid)
        ok_meta = dict(fresh["meta"])
        ok_meta["phase"] = "executing"
        fresh_write = goals._save_meta(conn, gid, ok_meta, fresh["raw"])
        conn.commit()
    finally:
        conn.close()

    phase_kept = json.loads(row["metadata"] or "{}").get("phase") == "assigned"
    first_ok = first is True
    stale_refused = replay is False and rollback is False
    fresh_ok = fresh_write is True

    src = (Path(__file__).resolve().parent / "memall/pipeline/goals.py"
           ).read_text(encoding="utf-8")
    bare_calls = re.findall(r"_save_meta\(conn,\s*goal_id,\s*meta\)", src)
    every_call_guarded = not bare_calls

    ok = first_ok and stale_refused and phase_kept and fresh_ok and every_call_guarded
    return ok, (f"first_write_applied={first_ok} stale_refused={stale_refused} "
                f"newer_phase_preserved={phase_kept} "
                f"fresh_read_writes={fresh_ok} "
                f"all_call_sites_pass_token={every_call_guarded}")


def guard_goal_dependency_warnings() -> Tuple[bool, str]:
    """A sequential-looking child with no dependency must be REPORTED, not blocked.

    Measured live: an agent decomposed a goal into "设计场景 / 执行验证" with
    depends_on_idx=[] on both.  finalize would then have released both in
    parallel, and the verification could start before the thing it verifies
    exists.  Nothing in the pipeline objected, because a decomposition was only
    checked for having acceptance criteria.

    The check is split by evidence, deliberately:
      * objective faults stay HARD rejections (self-dependency, forward or
        out-of-range index, missing title/acceptance);
      * intent inferred from wording is only a WARNING, and parallel_ok=True
        silences it.  A character-rule gate that blocks honest work is the exact
        mistake we made before with the 4-gram echo gate, which flagged 37
        chapters as AI-written.

    Every warning must name the child and both ways out, otherwise the agent
    learns to ignore it.
    """
    _fresh_db()
    import json
    from memall.core.db import get_conn, init_db
    from memall.mcp.adapter import handle_call
    from memall.pipeline import goals as G

    init_db()
    PM = "warn_pm"

    def call(tool, args):
        return json.loads(handle_call(tool, args))

    def meta_of(gid):
        conn = get_conn()
        try:
            raw = conn.execute("SELECT metadata FROM memories WHERE id=?",
                               (gid,)).fetchone()
        finally:
            conn.close()
        return json.loads(raw[0] or "{}") if raw else {}

    created = call("memall_write", {"action": "goal_create", "agent_name": PM,
                                    "objective": "依赖体检目标", "acceptance": "可验证",
                                    "verifier": "warn_ver"})
    gid = created.get("goal_id")
    if not gid:
        return False, f"goal_create failed: {created}"

    # (0) with >=2 children and no dependency anywhere, silence is not allowed:
    # the caller must state sequential or parallel.  This is the objective half
    # of the check - no word list involved.
    silent = call("memall_write", {"action": "goal_decompose", "agent_name": PM,
                                   "goal_id": gid, "children": [
        {"title": "设计阶段", "detail": "d", "assignee": PM, "acceptance": "a"},
        {"title": "执行阶段", "detail": "d", "assignee": PM, "acceptance": "b"}]})
    # decompose does NOT block here: 论证 must receive the plan in order to argue
    # about it, and a hard rejection at decompose measurably made a woken agent
    # give up and leave the goal stuck at intake.
    silent_accepted = silent.get("status") == "decomposed"
    silent_msg = " ".join(silent.get("warnings") or [])
    silent_actionable = ("depends_on_idx" in silent_msg
                         and "parallel_ok" in silent_msg)

    # (1) sequencing wording on a child that stays unwired -> warning, plan lands.
    #     One dependency exists (so the objective contract is satisfied) while
    #     child 2 reads like a follow-up of child 1 but declares none.
    seq = call("memall_write", {"action": "goal_decompose", "agent_name": PM,
                                "goal_id": gid, "children": [
        {"title": "设计场景", "detail": "写测试场景", "assignee": PM,
         "acceptance": "场景文档完成"},
        {"title": "实现代码", "detail": "写实现", "assignee": PM,
         "acceptance": "实现完成", "depends_on_idx": [0]},
        {"title": "执行验证", "detail": "在此基础上跑验证", "assignee": PM,
         "acceptance": "验证通过"}]})
    warned = bool(seq.get("warnings"))
    not_blocked = seq.get("status") == "decomposed"
    names_child = any("child 2" in w for w in (seq.get("warnings") or []))
    offers_parallel_ok = any("parallel_ok" in w for w in (seq.get("warnings") or []))
    persisted = bool(meta_of(gid).get("dependency_warnings"))
    next_mentions_fix = "warnings" in (seq.get("next") or "")

    # (2) declaring parallelism silences it
    quiet = call("memall_write", {"action": "goal_decompose", "agent_name": PM,
                                  "goal_id": gid, "parallel_ok": True,
                                  "children": [
        {"title": "设计场景", "detail": "写测试场景", "assignee": PM,
         "acceptance": "场景文档完成"},
        {"title": "执行验证", "detail": "在此基础上跑验证", "assignee": PM,
         "acceptance": "验证通过"}]})
    silenced = quiet.get("status") == "decomposed" and not quiet.get("warnings")
    cleared = not meta_of(gid).get("dependency_warnings")

    # (3) objective faults are still hard rejections
    bad = call("memall_write", {"action": "goal_decompose", "agent_name": PM,
                                "goal_id": gid, "children": [
        {"title": "a", "detail": "d", "acceptance": "x"},
        {"title": "b", "detail": "d", "acceptance": "y", "depends_on_idx": [5]}]})
    still_rejects = bad.get("status") == "rejected"

    # (4) finalize REFUSES to materialise a plan whose scheduling is unstated,
    # because that is the moment the dependency graph becomes real.  Uses its own
    # goal so the state manipulation cannot disturb the cases above.
    created2 = call("memall_write", {"action": "goal_create", "agent_name": PM,
                                     "objective": "finalize 依赖门目标",
                                     "acceptance": "可验证", "verifier": "warn_ver"})
    gid2 = created2.get("goal_id")
    call("memall_write", {"action": "goal_decompose", "agent_name": PM,
                          "goal_id": gid2, "children": [
        {"title": "设计阶段", "detail": "d", "assignee": PM, "acceptance": "a"},
        {"title": "执行阶段", "detail": "d", "assignee": PM, "acceptance": "b"}]})
    conn = get_conn()
    try:
        _raw = conn.execute("SELECT metadata FROM memories WHERE id=?",
                            (gid2,)).fetchone()
        _meta = json.loads(_raw[0] or "{}")
        _meta["phase"] = "deliberating"
        _meta["discussion_id"] = 0
        conn.execute("UPDATE memories SET metadata=? WHERE id=?",
                     (json.dumps(_meta), gid2))
        conn.commit()
    finally:
        conn.close()
    fin = G.finalize(gid2, PM)
    finalize_refuses = fin.get("status") == "rejected"
    _fix = fin.get("how_to_fix") or ""
    finalize_actionable = "depends_on_idx" in _fix and "parallel_ok" in _fix

    # (4) the schema advertises the goal surface, so agents need not guess
    import memall.mcp.adapter as ad
    ad._ensure_loaded()
    props = {}
    for t in ad.TOOL_DEFINITIONS:
        if t["name"] == "memall_write":
            props = t["inputSchema"]["properties"]
            break
    advertised = all(k in props for k in
                     ("goal_id", "objective", "acceptance", "verifier",
                      "children", "parallel_ok"))

    ok = (silent_accepted and silent_actionable
          and finalize_refuses and finalize_actionable
          and warned and not_blocked and names_child and offers_parallel_ok
          and persisted and next_mentions_fix and silenced and cleared
          and still_rejects and advertised)
    return ok, (f"decompose_reports={silent_accepted} "
                f"warning_is_actionable={silent_actionable} "
                f"finalize_refuses={finalize_refuses} "
                f"finalize_is_actionable={finalize_actionable} "
                f"seq_warned={warned} plan_kept={not_blocked} "
                f"names_child={names_child} offers_way_out={offers_parallel_ok} "
                f"persisted={persisted} next_mentions_fix={next_mentions_fix} "
                f"parallel_ok_silences={silenced} cleared={cleared} "
                f"struct_faults_still_reject={still_rejects} "
                f"schema_advertises_goal_fields={advertised}")


def guard_goal_cards_require_audited_delete() -> Tuple[bool, str]:
    """A goal card must never leave the store without an audit trail.

    Audit 2026-10-29: four goal cards sitting in ``verifying`` disappeared
    between two backups (18:25 -> 19:13).  ``ops_log`` had no row, the archive
    DB was empty and no pipeline run touched them — the row diff was exactly
    those four ids.  The gateway's ``DELETE /v30api/memories/{id}`` was the one
    delete path issuing a bare ``DELETE`` with no ledger entry.

    What this asserts:
      1. ``init_db`` installs the ``memories_bd_goal_guard`` trigger;
      2. a bare delete of a goal row aborts and the row survives;
      3. merging a goal (as source *or* target) is refused — a merge deletes
         the source, which is the same silent loss in disguise;
      4. ``audited_delete_memory`` removes the row, writes a ``delete`` entry
         to ``ops_log`` and leaves no residual ``delete_audit`` row;
      5. ordinary (non-goal) rows are still deletable by plain SQL;
      6. the gateway no longer contains a bare ``DELETE FROM memories`` and
         every bulk sweeper excludes ``category='goal'``.
    """
    import sqlite3
    from pathlib import Path

    _fresh_db()
    import json
    from memall.core.db import get_conn, init_db
    from memall.core.thin_waist import capture
    from memall.pipeline import goals as G
    from memall.pipeline import ops as O

    init_db()

    def new_goal(objective: str) -> int:
        r = G.create_goal(objective, owner="del_pm", acceptance="可验证",
                          verifier="del_ver")
        return int(r.get("goal_id") or 0)

    gid = new_goal("守卫：目标卡删除必须留痕")
    gid2 = new_goal("守卫：目标卡不能被合并")
    plain = capture("守卫用普通记忆，常规路径应当可删", level="L2",
                    category="note", agent_name="del_pm")
    if not (gid and gid2 and plain):
        return False, f"fixture failed: gid={gid} gid2={gid2} plain={plain}"

    conn = get_conn()
    try:
        trigger = bool(conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='trigger' "
            "AND name='memories_bd_goal_guard'").fetchone())
        audit_table = bool(conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' "
            "AND name='delete_audit'").fetchone())

        # (2) bare delete aborts and the row survives
        bare_blocked = False
        try:
            conn.execute("DELETE FROM memories WHERE id = ?", (gid,))
            conn.commit()
        except sqlite3.IntegrityError as exc:
            bare_blocked = "goal card cannot be deleted silently" in str(exc)
            conn.rollback()
        except Exception:
            conn.rollback()
        survived = bool(conn.execute(
            "SELECT 1 FROM memories WHERE id = ?", (gid,)).fetchone())

        # (3) merge is refused on either side
        def merge_refused(src: int, tgt: int) -> bool:
            try:
                O._merge_memories_conn(conn, src, tgt)
                conn.rollback()
                return False
            except ValueError as exc:
                conn.rollback()
                return "goal" in str(exc)

        merge_src_refused = merge_refused(gid2, plain)
        merge_tgt_refused = merge_refused(plain, gid2)

        # (4) the audited path works and leaves a ledger entry
        res = O.audited_delete_memory(conn, gid, by="guard_fixture",
                                      reason="guard cleanup")
        conn.commit()
        audited_deleted = res.get("deleted") == 1
        gone = not conn.execute(
            "SELECT 1 FROM memories WHERE id = ?", (gid,)).fetchone()
        delete_entries = []
        for r in conn.execute(
                "SELECT target_ids FROM ops_log WHERE op_type = 'delete'"
        ).fetchall():
            try:
                delete_entries.extend(json.loads(r[0] or "[]"))
            except Exception:
                pass
        logged = gid in delete_entries
        no_residual = not conn.execute(
            "SELECT 1 FROM delete_audit WHERE memory_id = ?", (gid,)).fetchone()

        # (5) non-goal rows keep the ordinary path
        cur = conn.execute("DELETE FROM memories WHERE id = ?", (plain,))
        conn.commit()
        plain_deletable = cur.rowcount == 1
    finally:
        conn.close()

    # (6) source scan: the risky paths must carry the guard, not a comment
    root = Path(SRC) / "memall"
    gw = (root / "gateway_v30.py").read_text(encoding="utf-8")
    gateway_audited = (
        "audited_delete_memory" in gw
        and "DELETE FROM memories" not in gw
    )

    def excludes_goals(rel: str) -> bool:
        txt = (root / rel).read_text(encoding="utf-8")
        return "<>'goal'" in txt.replace(" ", "").lower()

    sweepers_guarded = all(
        excludes_goals(rel) for rel in (
            "pipeline/forget.py",
            "pipeline/archive.py",
            "pipeline/adaptive.py",
            "pipeline/auto_dream.py",
        )
    )

    ok = (trigger and audit_table and bare_blocked and survived
          and merge_src_refused and merge_tgt_refused
          and audited_deleted and gone and logged and no_residual
          and plain_deletable and gateway_audited and sweepers_guarded)
    return ok, (f"trigger={trigger} audit_table={audit_table} "
                f"bare_delete_aborts={bare_blocked} row_survives={survived} "
                f"merge_src_refused={merge_src_refused} "
                f"merge_tgt_refused={merge_tgt_refused} "
                f"audited_delete={audited_deleted} row_gone={gone} "
                f"ops_logged={logged} audit_row_cleared={no_residual} "
                f"plain_still_deletable={plain_deletable} "
                f"gateway_audited={gateway_audited} "
                f"sweepers_exclude_goals={sweepers_guarded}")


def guard_session_note_delivery_crosses_processes() -> Tuple[bool, str]:
    """A note pushed for agent X must be readable by ANOTHER process serving X.

    This is the difference between dispatching work to a colleague and pretending
    to.  ``push_session_note`` used to write module-level dicts, so a note pushed
    for ``opencode`` by one process was invisible to the process that actually
    serves ``opencode``: the note reached nobody, while ``goal_finalize`` reported
    success.  Every stdio MCP client spawns its own server, so "in memory" and
    "not delivered" are the same thing here.

    Measured shape of the fix (migration 041): the note is a row in
    ``session_notes``, and these invariants survive the move —
      * a second process reads the note;
      * another agent cannot read it, and an unnamed caller gets nothing
        (AGENTS.md §三.4 identity lock);
      * reading does NOT consume (a mid-response timeout must not lose it);
      * ACK drops it AND frees the dedup key, because "delivered" is not "done".
    """
    _fresh_db()
    import json
    from memall.core.db import get_conn, init_db
    from memall.mcp import shared as S

    init_db()
    # The table is created by the migration; make sure this DB has it.
    import importlib

    conn = get_conn()
    try:
        importlib.import_module("memall.migrations.041_create_session_notes").apply(conn)
    finally:
        conn.close()

    note = "[派工] 任务卡 #1 归你"
    pushed = S.push_session_note("xproc_target", note, dedup_key="k1")
    dedup_blocked = S.push_session_note("xproc_target", "dup", dedup_key="k1") is False

    # Simulate the other process: read through a brand-new connection.
    conn2 = get_conn()
    try:
        rows = conn2.execute(
            "SELECT body FROM session_notes WHERE agent = ? AND acked_at IS NULL",
            ("xproc_target",)).fetchall()
        visible_in_other_connection = bool(rows) and note in rows[0][0]
    finally:
        conn2.close()

    isolated = S.consume_session_note("xproc_other") is None
    unnamed = S.consume_session_note("") is None
    read_twice = (S.consume_session_note("xproc_target") or "").startswith("[派工]")
    read_does_not_consume = bool(S.consume_session_note("xproc_target"))
    acked = S.ack_session_note("xproc_target")
    gone = S.consume_session_note("xproc_target") is None
    freed = S.push_session_note("xproc_target", "again", dedup_key="k1") is True
    S.ack_session_note("xproc_target")

    ok = (pushed and dedup_blocked and visible_in_other_connection and isolated
          and unnamed and read_twice and read_does_not_consume and acked
          and gone and freed)
    return ok, (f"pushed={pushed} dedup_blocked={dedup_blocked} "
                f"visible_from_another_connection={visible_in_other_connection} "
                f"other_agent_isolated={isolated} unnamed_gets_nothing={unnamed} "
                f"read_does_not_consume={read_does_not_consume} acked={acked} "
                f"gone_after_ack={gone} dedup_key_freed_after_ack={freed}")


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
    "procedure_honours_level_pin": guard_procedure_honours_level_pin,
    "session_cooldown_persisted": guard_session_cooldown_persisted,
    "session_note_acknowledged": guard_session_note_acknowledged,
    "session_note_not_shared_across_agents": guard_session_note_not_shared_across_agents,
    "convergence_rule_respected": guard_convergence_rule_respected,
    "notification_bar_lists_only_tasks": guard_notification_bar_lists_only_tasks,
    "notification_bar_uses_session_identity": guard_notification_bar_uses_session_identity,
    "confirm_path_honours_rule": guard_confirm_path_honours_rule,
    "retrieve_by_id_respects_viewer": guard_retrieve_by_id_respects_viewer,
    "fallback_produces_self_evolution": guard_fallback_produces_self_evolution,
    "busy_timeout_survives_concurrency": guard_busy_timeout_survives_concurrency,
    "capture_retries_on_lock": guard_capture_retries_on_lock,
    "update_can_complete_tasks": guard_update_can_complete_tasks,
    "lazy_tools_always_loadable": guard_lazy_tools_always_loadable,
    "discussion_stays_l5": guard_discussion_stays_l5,
    "discussion_reminders_and_responses": guard_discussion_reminders_and_responses,
    "precise_task_handoff": guard_precise_task_handoff,
    "task_cards_always_visible": guard_task_cards_always_visible,
    "stdio_reads_utf8": guard_stdio_reads_utf8,
    "task_terminal_closes_reminder": guard_task_terminal_closes_reminder,
    "convergence_waits_for_action_items": guard_convergence_waits_for_action_items,
    "goal_lifecycle_end_to_end": guard_goal_lifecycle_end_to_end,
    "agent_waker_is_safe": guard_agent_waker_is_safe,
    "session_note_delivery_crosses_processes": guard_session_note_delivery_crosses_processes,
    "wake_claimed_in_db": guard_wake_claimed_in_db,
    "goal_tick_on_heartbeat_cadence": guard_goal_tick_on_heartbeat_cadence,
    "empty_identity_rejected_everywhere": guard_empty_identity_rejected_everywhere,
    "verifying_requires_verifier": guard_verifying_requires_verifier,
    "forward_dependency_rejected": guard_forward_dependency_rejected,
    "unresolved_dependency_fails_loudly": guard_unresolved_dependency_fails_loudly,
    "duplicate_objective_rejected": guard_duplicate_objective_rejected,
    "depends_on_points_at_task_card": guard_depends_on_points_at_task_card,
    "write_action_surface_in_sync": guard_write_action_surface_in_sync,
    "goal_children_accepts_json_string": guard_goal_children_accepts_json_string,
    "goal_dependency_warnings": guard_goal_dependency_warnings,
    "partial_finalize_leaves_goal_in_deliberating":
        guard_partial_finalize_leaves_goal_in_deliberating,
    "addressed_to_single_impl": guard_addressed_to_single_impl,
    "metadata_cas": guard_metadata_cas,
    "decommissioned_cards_reassign": guard_decommissioned_cards_reassign,
    "task_loop_surface_wired": guard_task_loop_surface_wired,
    "goal_cards_require_audited_delete": guard_goal_cards_require_audited_delete,
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
