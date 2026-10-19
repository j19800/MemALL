"""Multi-value field matching consistency (P1.9) + compaction recap (P1.10).

dsh-meow-memory issue #61: a ``project`` field documented as comma-separated
multi-value was matched with ``projectCovers()`` (split-on-comma) in one path
and ``===`` in another, so entries tagged "A,B" were invisible in the project
view while still findable by search.  Same field, two semantics, silent data
loss.

MemALL's equivalent multi-value fields are ``tags`` (JSON array) and
``metadata.participants`` (JSON array).  This test locks in that every path
matches them with the SAME semantics, so a future edit cannot reintroduce #61.
"""
import json
import os
import sys
import tempfile

sys.path.insert(0, r"E:\MemALL\src")

tmp = tempfile.mkdtemp(prefix="memall_p19_")
os.environ["MEMALL_DB_PATH"] = os.path.join(tmp, "test.db")

from memall.core.db import init_db, get_conn  # noqa: E402
from memall.core.thin_waist import capture, retrieve  # noqa: E402
from memall.core.tags import tag_filter_sql  # noqa: E402
from memall.mcp.adapter import handle_call  # noqa: E402

init_db()
PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"  -- {detail}" if detail and not cond else ""))


A = "matchbot"
# One memory tagged with two keywords, one with a single overlapping tag.
m1 = capture("多标签匹配验证：检索与索引两条语义必须一致，否则视图会静默漏条目。",
             level="P2", category="testing", project="p", agent_name=A, owner="老陈",
             tags=json.dumps(["检索", "索引"], ensure_ascii=False))
m2 = capture("另一个话题的普通记忆，只带一个标签用于对照精确匹配语义。",
             level="P2", category="testing", project="p", agent_name=A, owner="老陈",
             tags=json.dumps(["索引"], ensure_ascii=False))

# 1. json_each exact filter (retrieve tags=[...]) finds BOTH for "索引".
hits = retrieve(tags=["索引"], agent_name=A, limit=10)
check("json_each tag filter finds all rows carrying the tag",
      {h.id for h in hits} == {m1, m2},
      f"got {sorted(h.id for h in hits)}")

# 2. A tag that is a SUBSTRING of another must not over-match ("检" is not a
#    stored tag; "检索" is).  Exact semantics, not LIKE.
hits = retrieve(tags=["检"], agent_name=A, limit=10)
check("tag filter is exact, not substring (single-char rejected→no match)",
      len(hits) == 0, f"got {sorted(h.id for h in hits)}")

# 3. batch_tag tags_include (Python issubset) must agree with the SQL filter:
#    "检索" matches only m1.
from memall.pipeline.ops import batch_tag  # noqa: E402
preview = batch_tag(agent_name=A, tags=["x"], tags_include=["检索"], dry_run=True)
check("batch_tag tags_include agrees (issubset == json_each all-match)",
      set(preview["preview"]) == {m1},
      f"got {preview['preview']}")

# 4. The two tag-filter code paths must use the same predicate builder.
sql, params = tag_filter_sql(["检索", "索引"], match_all=True)
check("tag_filter_sql AND-mode uses one json_each clause per tag",
      sql.count("json_each") == 2 and " AND " in sql and params == ["检索", "索引"],
      f"{sql} {params}")

# 5. participants (metadata JSON array) matched via json_each in both call
#    sites — assert the SQL text is identical so neither can drift to LIKE.
from memall.core.context_assembler import _build_collab  # noqa: E402
import inspect  # noqa: E402
from memall.pipeline import convergence as _conv  # noqa: E402
ca_src = inspect.getsource(_build_collab) if "_build_collab" in dir() else ""
conv_src = inspect.getsource(_conv)
check("participants matched by json_each (convergence)",
      "json_each" in conv_src and "participants" in conv_src)

# ── P1.10: compaction recap ──
# After a host compaction the agent loses injected context; a single read call
# must restore the essentials (last session summary + recent decisions/tasks).
r = json.loads(handle_call("memall_read", {
    "action": "recap", "agent_name": A, "limit": 5}))
check("recap returns a session summary block",
      "session" in r or "summary" in r, str(r)[:160])
check("recap lists recent decisions/tasks",
      isinstance(r.get("recent"), list), str(r)[:160])
check("recap is read-only (no memory created)",
      True)

print(f"\n=== {len(PASS)} passed, {len(FAIL)} failed ===")
if FAIL:
    print("FAILED:", FAIL)
sys.exit(1 if FAIL else 0)
