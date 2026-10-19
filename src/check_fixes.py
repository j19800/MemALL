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
    """Point MemALL at a throwaway DB and return its path."""
    tmp = tempfile.mkdtemp(prefix="memall_guard_")
    path = os.path.join(tmp, "guard.db")
    os.environ["MEMALL_DB_PATH"] = path
    # Reset cached singletons so the next import-time DB_PATH resolves fresh.
    import memall.config as mc
    mc.reset_config()
    import memall.core.db as mdb
    mdb.DB_PATH = __import__("pathlib").Path(path)
    mdb._auto_init_done = False
    try:
        mdb._global_pool = None
    except AttributeError:
        pass
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
