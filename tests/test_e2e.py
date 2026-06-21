"""
End-to-End Test for MemALL Core.

Tests the complete operational flow by calling the tool layer directly
(via handle_call), bypassing HTTP transport complexity.

  1. init_db, capture 3 memories
  2. retrieve
  3. connect (graph edge)
  4. traverse (1-hop)
  5. timeline
  6. session_start → capture → session_end → summary
  7. memall_smart_store (dedup)
  8. memall_vector_search
  9. memall_db stats
  10. memall_persona
  11. memall_onboarding status
  12. memall_forget stats (readonly)
  13. memall_ops stats (readonly)
  14. memall_security check (readonly)
  15. memall_adaptive report (readonly)
  16. memall_index_rebuild (forces embedding index)
  17. Error: unknown tool
  18. memall_run_pipeline (full pipeline)

Usage:
    python tests/test_e2e.py
"""
import json
import os
import sys
import time
import traceback
from pathlib import Path

SRC_DIR = os.path.join(os.path.dirname(__file__), "..", "src")
TEST_DIR = os.path.join(os.path.dirname(__file__))
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)
if TEST_DIR not in sys.path:
    sys.path.insert(0, TEST_DIR)

PASS = 0
FAIL = 0


def ok(name, msg=""):
    global PASS; PASS += 1
    print(f"  \u2713 {name}{' ' * max(0, 60 - len(name))}{msg}")


def fail(name, detail=""):
    global FAIL; FAIL += 1
    print(f"  \u2717 {name}{' ' * max(0, 60 - len(name))}{detail}")


# ── Boot ────────────────────────────────────────────────────────────

from test_helpers import init_temp_db, cleanup_temp_db
db_path, patcher = init_temp_db()
print(f"Temp DB: {db_path}")

# Import AFTER db_path is patched
from memall.mcp.adapter import handle_call

TOOL_CACHE = {}  # cache for long-running tool results


def tool(name, arguments=None, *, cache=False, timeout=120):
    """Call a tool and return parsed JSON result. Retries on BUSY."""
    key = (name, json.dumps(arguments or {}, sort_keys=True))
    if cache and key in TOOL_CACHE:
        return TOOL_CACHE[key]
    for attempt in range(3):
        try:
            raw = handle_call(name, arguments or {})
            data = json.loads(raw)
            # Tool returns list directly (retrieve, timeline) — return as-is
            if isinstance(data, list):
                return data
            # Tool returned error — retry on BUSY, otherwise raise
            if isinstance(data, dict) and data.get("status") == "error":
                err_msg = str(data.get("error", ""))
                if "locked" in err_msg and attempt < 2:
                    time.sleep(1)
                    continue
                raise RuntimeError(f"Tool {name} error: {err_msg}")
            if cache:
                TOOL_CACHE[key] = data
            return data
        except Exception as e:
            if attempt < 2 and "locked" in str(e):
                time.sleep(1)
                continue
            raise RuntimeError(f"Tool {name} failed: {e}") from e
    raise RuntimeError(f"Tool {name} failed after 3 retries")


SKIP = set()  # tool names to skip (e.g. BUSY-affected)


# ── Tests ───────────────────────────────────────────────────────────

def main():
    global PASS, FAIL

    # ── 1. Capture 3 memories ──
    print("\n\u2500\u2500 Capture \u2500\u2500")
    mem_ids = []
    captions = [
        "MemALL E2E: HTTP transport stability with thread pool",
        "MemALL E2E: graph edge between memories",
        "MemALL E2E: vector search and session tracking",
    ]
    for i, text in enumerate(captions):
        try:
            r = tool("capture", {
                "content": text,
                "agent_name": "e2e_agent",
                "category": "test",
                "level": "P0",
                "project": "MemALL",
            })
            assert r.get("status") == "ok", f"capture failed: {r}"
            mid = r.get("id", r.get("memory_id", 0))
            mem_ids.append(mid)
            ok(f"capture #{i+1}", f"id={mid}")
        except Exception as e:
            fail(f"capture #{i+1}", str(e))

    # ── 2. Retrieve ──
    print("\n\u2500\u2500 Retrieve \u2500\u2500")
    try:
        raw = handle_call("retrieve", {"query": "HTTP transport thread pool", "limit": 5})
        r = json.loads(raw)
        assert isinstance(r, list), f"expected list, got {type(r)}"
        ok("retrieve", f"{len(r)} results")
    except Exception as e:
        fail("retrieve", str(e))

    # ── 3. Timeline ──
    print("\n\u2500\u2500 Timeline \u2500\u2500")
    try:
        raw = handle_call("timeline", {"hours": 24, "limit": 10})
        r = json.loads(raw)
        assert isinstance(r, list), f"expected list, got {type(r)}"
        ok("timeline", f"{len(r)} entries")
    except Exception as e:
        fail("timeline", str(e))

    # ── 4. Connect ──
    print("\n\u2500\u2500 Connect \u2500\u2500")
    if len(mem_ids) >= 2:
        try:
            r = tool("connect", {
                "source_id": mem_ids[0],
                "target_id": mem_ids[1],
                "relation_type": "refines",
            })
            assert r.get("status") == "ok"
            ok("connect", f"{mem_ids[0]} \u2192 {mem_ids[1]}")
        except Exception as e:
            fail("connect", str(e))
    else:
        fail("connect", "not enough memory ids")

    # ── 5. Traverse ──
    print("\n\u2500\u2500 Traverse \u2500\u2500")
    if mem_ids:
        try:
            r = tool("traverse", {"node_id": mem_ids[0], "depth": 1})
            ok("traverse", f"from node {mem_ids[0]}")
        except Exception as e:
            fail("traverse", str(e))

    # ── 6. Session lifecycle ──
    print("\n\u2500\u2500 Session Lifecycle \u2500\u2500")
    session_id = None
    try:
        raw = handle_call("memall_session_start", {"agent_name": "e2e_agent"})
        r = json.loads(raw)
        if isinstance(r, dict) and r.get("status") == "error":
            err = r.get("error", "")
            if "locked" in err:
                SKIP.add("session")
                fail("session_start (BUSY, known issue)")
            else:
                fail("session_start", err)
        else:
            session_id = r.get("session_id", "")
            assert session_id
            ok("session_start", f"id={session_id[:16]}...")
    except Exception as e:
        fail("session_start", str(e))

    if session_id:
        try:
            r = tool("capture", {
                "content": "E2E: memory during active session",
                "agent_name": "e2e_agent",
                "category": "test",
            })
            ok("capture (in-session)", f'id={r.get("id", "?")}')
        except Exception as e:
            fail("capture (in-session)", str(e))
        try:
            raw = handle_call("memall_session_end", {"session_id": session_id})
            r = json.loads(raw)
            assert r.get("status") == "ended"
            ok("session_end")
        except Exception as e:
            fail("session_end", str(e))
        try:
            r = tool("memall_session_summary", {"session_id": session_id})
            ok("session_summary",
               f'ids={len(r.get("memory_ids", []))}')
        except Exception as e:
            fail("session_summary", str(e))
    elif "session" not in SKIP:
        fail("session_end", "no session_id")
        fail("session_summary", "no session_id")

    # ── 7. Smart store ──
    print("\n\u2500\u2500 Smart Store \u2500\u2500")
    try:
        r = tool("memall_smart_store", {
            "content": "E2E: testing smart store dedup mechanism",
            "agent_name": "e2e_agent",
        })
        ok("memall_smart_store", f'id={r.get("id", "?")}')
    except Exception as e:
        fail("memall_smart_store", str(e))

    # ── 8. Vector search ──
    print("\n\u2500\u2500 Vector Search \u2500\u2500")
    try:
        r = tool("memall_vector_search", {"query": "memory thread pool", "top_k": 5})
        ok("memall_vector_search", f'{len(r) if isinstance(r, list) else 1} results')
    except Exception as e:
        fail("memall_vector_search", str(e))

    # ── 9. DB stats ──
    print("\n\u2500\u2500 DB Stats \u2500\u2500")
    try:
        r = tool("memall_db", {"action": "stats"})
        cnt = r.get("memories") or r.get("memory_count") or r.get("total", "?")
        ok("memall_db stats", f"memories={cnt}")
    except Exception as e:
        fail("memall_db stats", str(e))

    # ── 10. Identity ──
    print("\n\u2500\u2500 Identity \u2500\u2500")
    try:
        r = tool("memall_identity", {"agent_name": "e2e_agent"})
        ok("memall_identity", "ok")
    except Exception as e:
        fail("memall_identity", str(e))

    # ── 11. Persona ──
    print("\n\u2500\u2500 Persona \u2500\u2500")
    try:
        r = tool("memall_persona", {"agent_name": "e2e_agent"})
        ok("memall_persona", f"type={type(r).__name__}")
    except Exception as e:
        fail("memall_persona", str(e))

    # ── 12. Onboarding ──
    print("\n\u2500\u2500 Onboarding \u2500\u2500")
    try:
        r = tool("memall_onboarding", {"action": "status"})
        ok("memall_onboarding", f'step={r.get("current_step", r.get("step", "?"))}')
    except Exception as e:
        fail("memall_onboarding", str(e))

    # ── 13. Forget stats (readonly) ──
    print("\n\u2500\u2500 Forget Stats \u2500\u2500")
    try:
        r = tool("memall_forget", {"action": "stats"})
        ok("memall_forget stats", "ok")
    except Exception as e:
        fail("memall_forget stats", str(e))

    # ── 14. Security check ──
    print("\n\u2500\u2500 Security Check \u2500\u2500")
    try:
        r = tool("memall_security", {"action": "check", "agent_name": "e2e_agent"})
        ok("memall_security check", "ok")
    except Exception as e:
        fail("memall_security check", str(e))

    # ── 15. Adaptive report ──
    print("\n\u2500\u2500 Adaptive Report \u2500\u2500")
    try:
        r = tool("memall_adaptive", {"action": "report"})
        ok("memall_adaptive report", "ok")
    except Exception as e:
        fail("memall_adaptive report", str(e))

    # ── 16. Error: unknown tool ──
    print("\n\u2500\u2500 Error Handling \u2500\u2500")
    try:
        raw = handle_call("nonexistent_tool_xyz", {})
        data = json.loads(raw)
        if isinstance(data, dict) and data.get("status") == "error":
            ok("unknown tool error", data.get("error", ""))
        else:
            fail("unknown tool", f"unexpected response: {data}")
    except Exception as e:
        fail("unknown tool", str(e))

    # ── 17. Pipeline (full run) ──
    print("\n\u2500\u2500 Pipeline (full run) \u2500\u2500")
    try:
        r = tool("memall_run_pipeline", {
            "include_reflect": True,
            "include_distill": True,
            "include_integrate": True,
            "include_persona": True,
        })
        ok("memall_run_pipeline", f'status={r.get("status", "?")}')
    except Exception as e:
        fail("memall_run_pipeline", str(e))

    # ── 18. Index rebuild ──
    print("\n\u2500\u2500 Index Rebuild \u2500\u2500")
    try:
        r = tool("memall_index_rebuild", {"force": False})
        ok("memall_index_rebuild", f'status={r.get("status", "?")}')
    except Exception as e:
        fail("memall_index_rebuild", str(e))

    # ── 19. Vector search after index rebuild ──
    print("\n\u2500\u2500 Vector Search (post-index) \u2500\u2500")
    try:
        r = tool("memall_vector_search", {"query": "HTTP transport thread pool", "top_k": 5})
        ok("memall_vector_search (post-index)", f'{len(r) if isinstance(r, list) else 1} results')
    except Exception as e:
        fail("memall_vector_search (post-index)", str(e))

    # ── 20. Ops dedup ──
    print("\n\u2500\u2500 Memall Ops Dedup \u2500\u2500")
    try:
        r = tool("memall_ops", {"action": "dedup", "agent_name": "e2e_agent", "threshold": 0.95})
        ok("memall_ops dedup", "ok")
    except Exception as e:
        fail("memall_ops dedup", str(e))

    # ── Summary ──
    total = PASS + FAIL
    print(f"\n{'='*60}")
    print(f"  Results: {PASS}/{total} passed, {FAIL}/{total} failed")
    print(f"{'='*60}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    try:
        rc = main()
    finally:
        cleanup_temp_db(db_path, patcher)
    sys.exit(rc)
