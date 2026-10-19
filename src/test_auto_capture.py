"""Auto-capture and proactive-memory verification.

Run:  E:\\MemALL\\.venv\\Scripts\\python.exe test_auto_capture.py

Covers the auto-capture feature end to end plus the guardrails that keep the
system's own output from becoming its own evidence.

NOTE on agent names: ``normalize_agent_name`` maps any name containing a date
or 10+ consecutive digits to ``system``, so this test uses a letter-only suffix.
A timestamp suffix would silently redirect every probe row and make the
assertions look like failures.
"""
import json
import random
import string
import sys

sys.path.insert(0, r"E:\MemALL\src")
from memall.mcp import adapter  # noqa: E402,F401
from memall.mcp.adapter import handle_call  # noqa: E402
from memall.core.db import get_conn  # noqa: E402
from memall.core.lifecycle import HookRegistry, HOOK_POST_TOOL_USE  # noqa: E402
from memall.core.thin_waist import (  # noqa: E402
    capture, normalize_agent_name, _should_promote_level,
)

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"  -- {detail}" if detail and not cond else ""))


TAG = "".join(random.choices(string.ascii_lowercase, k=8))
AGENT = f"autocap{TAG}"
assert normalize_agent_name(AGENT) == AGENT


def q(sql, params=()):
    conn = get_conn()
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


def count_source(source, agent=AGENT):
    return q("SELECT COUNT(*) c FROM memories WHERE agent_name=? "
             "AND metadata LIKE ?", (agent, f'%"source": "{source}"%'))[0]["c"]


# ── Hook wiring ──
hooks = [h for h in HookRegistry._hooks if h.hook_point == HOOK_POST_TOOL_USE]
check("auto-capture hook is registered",
      any("Auto-capture" in h.description for h in hooks))

# ── Pure reads never capture ──
n0 = q("SELECT COUNT(*) c FROM memories")[0]["c"]
handle_call("memall_read", {"action": "search", "query": "\u68c0\u7d22", "limit": 3})
handle_call("memall_read", {"action": "timeline", "hours": 48, "limit": 3})
handle_call("memall_read", {"action": "retrieve", "query": "\u67b6\u6784", "limit": 3})
check("pure reads do not capture",
      q("SELECT COUNT(*) c FROM memories")[0]["c"] == n0)

# ── Explicit level survives the background classify pass ──
L6 = (f"Root cause analysis for {TAG}: the auto-capture hook was never "
      "imported, so the intent classifier never ran. Evidence: the hook "
      "registry held only three entries after the MCP adapter loaded. "
      "Because the feature was dead, every memory came from an explicit call.")
seed = json.loads(handle_call("memall_write", {
    "action": "capture", "content": L6, "level": "L6", "category": "reflection",
    "agent_name": AGENT, "owner": "\u8001\u9648"}))
cid = seed.get("id")
check("seed L6 reflection accepted", bool(cid), str(seed)[:140])

row = q("SELECT level, category FROM memories WHERE id=?", (cid,))
check("explicit L6/reflection survives background classify",
      bool(row) and row[0]["level"] == "L6" and row[0]["category"] == "reflection",
      f"got {dict(row[0]) if row else None}")

# ── reflect → auto-capture ──
ctx = (f"confirmed for {TAG}: the missing import was the whole reason the "
       "intent classifier never ran, because nothing registered the hook.")
n0 = count_source("reflect")
handle_call("memall_system", {
    "action": "reflect", "reflect_sub_action": "agree", "memory_id": cid,
    "context": ctx, "agent_name": AGENT})
n1 = count_source("reflect")
check("reflect auto-captures", n1 == n0 + 1, f"{n0} -> {n1}")

# ── dedup ──
handle_call("memall_system", {
    "action": "reflect", "reflect_sub_action": "agree", "memory_id": cid,
    "context": ctx, "agent_name": AGENT})
check("duplicate reflect is de-duplicated",
      count_source("reflect") == n1, f"{n1} -> {count_source('reflect')}")

# ── ask → auto-capture ──
a0 = count_source("ask")
handle_call("memall_persona", {
    "action": "ask", "question": f"\u68c0\u7d22\u67b6\u6784 {TAG} \u9700\u8981\u6539\u8fdb\u5417",
    "mode": "stance", "agent_name": AGENT})
a1 = count_source("ask")
check("ask auto-captures a stance", a1 == a0 + 1, f"{a0} -> {a1}")

# ── ask must not treat its own output as evidence ──
from memall.pipeline.ask import _search_memories  # noqa: E402
ask_ids = {r["id"] for r in q(
    "SELECT id FROM memories WHERE agent_name=? "
    "AND metadata LIKE '%\"source\": \"ask\"%'", (AGENT,))}
conn = get_conn()
try:
    ev = _search_memories(conn, f"\u68c0\u7d22\u67b6\u6784 {TAG}", agent_name=AGENT, limit=50)
finally:
    conn.close()
check("ask evidence pool excludes its own 推演",
      not ({r["id"] for r in ev} & ask_ids),
      f"overlap={sorted({r['id'] for r in ev} & ask_ids)}")

# ── distill must not promote the system's own output ──
conn = get_conn()
try:
    src = {r["id"] for r in conn.execute(
        "SELECT id FROM memories WHERE category != '' AND category IS NOT NULL "
        "AND LENGTH(TRIM(content)) > 20 "
        "AND level NOT IN ('P0','P1','P2','P3','P4','L6','L9','L10','L11') "
        "AND stream = 'knowledge' "
        "AND COALESCE(metadata, '') NOT LIKE '%\"source\": \"ask\"%' "
        "AND COALESCE(metadata, '') NOT LIKE '%\"source\": \"reflect\"%' "
        "AND COALESCE(metadata, '') NOT LIKE '%\"source\": \"discussion\"%'").fetchall()}
finally:
    conn.close()
check("distill source excludes auto-captured rows",
      not (src & ask_ids), f"overlap={sorted(src & ask_ids)}")

# ── discussion → auto-capture ──
disc = json.loads(handle_call("memall_discussion", {
    "action": "create", "title": f"autocapture verification {TAG}",
    "background": "verify discussion stances are captured",
    "options": ["A", "B"], "participants": [AGENT]}))
did = disc.get("discussion_id") or disc.get("memory_id")
d0 = count_source("discussion")
if did:
    handle_call("memall_discussion", {
        "action": "respond", "discussion_id": did, "agent_name": AGENT,
        "stance": "agree",
        "arguments": f"option A for {TAG} keeps one source of truth and "
                     "avoids the drift we just reproduced."})
    check("discussion stance auto-captured",
          count_source("discussion") == d0 + 1,
          f"{d0} -> {count_source('discussion')}")
else:
    check("discussion create returned an id", False, str(disc)[:120])

# ── hash-hit promotion semantics ──
PRIORITY = {"P0": 3, "P1": 2, "P2": 1}
LIFE = {f"L{i}": i for i in range(1, 12)}


def promote(new, old):
    return _should_promote_level(new, old, PRIORITY, LIFE)


check("cross-band re-capture wins (P2 -> L4)", promote("L4", "P2"))
check("cross-band re-capture wins (L4 -> P1)", promote("P1", "L4"))
check("same-band never demotes (P1 !-> P2)", not promote("P2", "P1"))
check("same-band promotes upward (P1 -> P0)", promote("P0", "P1"))
check("lifecycle never demotes (L9 !-> L4)", not promote("L4", "L9"))
check("lifecycle promotes upward (L4 -> L9)", promote("L9", "L4"))
check("identical level is a no-op", not promote("L4", "L4"))

# ── hash-hit promotion reaches the database ──
PROMO = (f"Promotion probe {TAG}: identical content re-captured at a more "
         "significant level must upgrade the stored row instead of silently "
         "discarding the caller's intent. Root cause matters here.")
pid = capture(PROMO, level="P2", category="general", agent_name=AGENT,
              owner="\u8001\u9648")
conn = get_conn()
try:
    first = conn.execute("SELECT level FROM memories WHERE id=?", (pid,)).fetchone()["level"]
finally:
    conn.close()
capture(PROMO, level="L4", category="decision", agent_name=AGENT,
        owner="\u8001\u9648")
conn = get_conn()
try:
    second = conn.execute("SELECT level FROM memories WHERE id=?", (pid,)).fetchone()["level"]
    rows = conn.execute("SELECT COUNT(*) c FROM memories WHERE content=?",
                        (PROMO,)).fetchone()["c"]
finally:
    conn.close()
check("re-capture at higher level upgrades the row",
      first == "P2" and second == "L4", f"{first} -> {second}")
check("promotion does not duplicate the row", rows == 1, f"rows={rows}")

print()
print(f"=== {len(PASS)} passed, {len(FAIL)} failed ===")
if FAIL:
    print("FAILED:", FAIL)
sys.exit(1 if FAIL else 0)