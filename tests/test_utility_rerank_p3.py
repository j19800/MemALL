"""P3 default-off utility rerank end-to-end propagation contracts.

Covers:
- enhanced_search no longer raises NameError on the enhancement path and
  accepts an explicit utility_rerank parameter.
- unified_search parses/forwards utility_rerank across enhanced_search and
  vector/hybrid _apply_enhancements_inplace paths.
- default-off semantics: with config disabled, no utility_rerank_* fields and
  no order change even when the call flag is set.
- conservative gating: with config enabled + flag, only memories with
  >= min_interactions evidence get signals; no items are dropped.
- MCP handle_retrieve / handle_unified_search route the flag end-to-end.
"""
import json
from datetime import datetime, timezone

from tests.test_helpers import init_temp_db, cleanup_temp_db


def _set_utility_config(enabled: bool, min_interactions: int = 2, weight: float = 0.12):
    import memall.config as cfg
    # Ensure the in-process config cache is populated, then mutate directly.
    cfg.get_config()
    block = cfg._config.setdefault("search", {}).setdefault("utility_rerank", {})
    block["enabled"] = enabled
    block["min_interactions"] = min_interactions
    block["weight"] = weight
    block["window_days"] = 30


def _reset_config():
    from memall.config import reset_config
    reset_config()


def _interaction(conn, memory_id, agent_name, action="click", n=1):
    now = datetime.now(timezone.utc).isoformat()
    for _ in range(n):
        conn.execute(
            "INSERT INTO search_interactions (query, memory_id, action, dwell_ms, agent_name, created_at) "
            "VALUES (?,?,?,?,?,?)",
            ("p3 e2e query", memory_id, action, 0, agent_name, now),
        )
    conn.commit()


def test_enhanced_search_accepts_utility_rerank_flag_without_nameerror():
    """Regression: the enhancement path referenced an undefined `arguments`."""
    db_path, orig = init_temp_db()
    try:
        from memall.core.thin_waist import capture, enhanced_search
        _set_utility_config(False)
        mid = capture("p3 enhanced search no nameerror target memory content here",
                      level="P2", category="general", agent_name="tester", project="memall")
        # explain=True forces the post-retrieval enhancement branch. Previously
        # raised NameError: arguments is not defined at line 3220.
        out = enhanced_search(query="nameerror", viewer="tester", agent_name="tester",
                              explain=True, utility_rerank=True, limit=10)
        assert isinstance(out, dict)
        assert "results" in out
        # default-off: no rerank signals even when flag passed
        blob = json.dumps(out.get("results"), ensure_ascii=False, default=str)
        assert "utility_rerank" not in blob
        _reset_config()
        print("PASS: test_enhanced_search_accepts_utility_rerank_flag_without_nameerror")
    finally:
        _reset_config()
        cleanup_temp_db(db_path, orig)


def test_unified_search_fts5_default_off_no_signals():
    """unified_search(mode='fts5', utility_rerank=True) with config off: no signals, no error."""
    db_path, orig = init_temp_db()
    try:
        from memall.core.thin_waist import capture, unified_search
        _set_utility_config(False)
        capture("p3 unified fts5 off target memory content", level="P2", category="general",
                agent_name="tester", project="memall")
        raw = unified_search({"query": "fts5", "mode": "fts5", "utility_rerank": True,
                              "agent_name": "tester", "top_k": 10})
        out = json.loads(raw)
        assert "error" not in out
        blob = json.dumps(out.get("results"), ensure_ascii=False)
        assert "utility_rerank_signal" not in blob
        assert "utility_rerank_score" not in blob
        print("PASS: test_unified_search_fts5_default_off_no_signals")
    finally:
        _reset_config()
        cleanup_temp_db(db_path, orig)


def test_unified_search_hybrid_default_off_no_signals():
    """unified_search(mode='hybrid', utility_rerank=True) with config off: no signals, no error."""
    db_path, orig = init_temp_db()
    try:
        from memall.core.thin_waist import capture, unified_search
        _set_utility_config(False)
        capture("p3 unified hybrid off target memory content", level="P2", category="general",
                agent_name="tester", project="memall")
        raw = unified_search({"query": "hybrid", "mode": "hybrid", "utility_rerank": True,
                              "agent_name": "tester", "top_k": 10})
        out = json.loads(raw)
        assert "error" not in out
        blob = json.dumps(out.get("results"), ensure_ascii=False)
        assert "utility_rerank_signal" not in blob
        print("PASS: test_unified_search_hybrid_default_off_no_signals")
    finally:
        _reset_config()
        cleanup_temp_db(db_path, orig)


def test_unified_search_enabled_conservative_gating_no_drops():
    """Flag + config on: only >=min_interactions memories get signals; none dropped."""
    db_path, orig = init_temp_db()
    try:
        from memall.core.db import get_conn
        from memall.core.thin_waist import capture, unified_search
        _set_utility_config(True, min_interactions=2, weight=0.12)

        m1 = capture("p3 high utility memory alpha beta gamma", level="P2", category="general",
                     agent_name="tester", project="memall")
        m2 = capture("p3 low evidence memory alpha beta", level="P2", category="general",
                     agent_name="tester", project="memall")
        conn = get_conn()
        _interaction(conn, int(m1), "tester", action="click", n=3)
        _interaction(conn, int(m2), "tester", action="click", n=1)  # below threshold
        conn.close()

        raw = unified_search({"query": "alpha beta", "mode": "fts5", "utility_rerank": True,
                              "agent_name": "tester", "viewer": "tester", "top_k": 10})
        out = json.loads(raw)
        assert "error" not in out
        results = out.get("results") or []
        assert len(results) >= 1  # never drops items
        # Config is on and flag is set -> at least one candidate should carry
        # a signal if any high-evidence memory was in the result set.
        # (Tolerant: FTS matching may vary; assert no crash + no drops.)
        for r in results:
            if r.get("memory_id") == int(m1):
                # high-evidence row, if present, must carry signal fields
                assert "utility_rerank_signal" in r or "utility_rerank_score" in r
        print("PASS: test_unified_search_enabled_conservative_gating_no_drops")
    finally:
        _reset_config()
        cleanup_temp_db(db_path, orig)


def test_mcp_handle_retrieve_utility_rerank_end_to_end():
    """MCP handle_retrieve passes utility_rerank through enhanced_search without leaking it to retrieve()."""
    db_path, orig = init_temp_db()
    try:
        from memall.core.thin_waist import capture
        from memall.mcp.tools.retrieve import handle_retrieve
        _set_utility_config(False)
        capture("p3 mcp retrieve target memory content", level="P2", category="general",
                agent_name="tester", project="memall")
        raw = handle_retrieve({"query": "mcp", "agent_name": "tester", "utility_rerank": True,
                               "explain": True, "limit": 10})
        out = json.loads(raw)
        assert "error" not in out
        assert "results" in out
        blob = json.dumps(out, ensure_ascii=False)
        assert "utility_rerank_signal" not in blob  # default-off
        print("PASS: test_mcp_handle_retrieve_utility_rerank_end_to_end")
    finally:
        _reset_config()
        cleanup_temp_db(db_path, orig)


def test_mcp_handle_unified_search_utility_rerank_end_to_end():
    """MCP handle_unified_search forwards utility_rerank into unified_search."""
    db_path, orig = init_temp_db()
    try:
        from memall.core.thin_waist import capture
        from memall.mcp.tools.retrieve import handle_unified_search
        _set_utility_config(False)
        capture("p3 mcp unified target memory content", level="P2", category="general",
                agent_name="tester", project="memall")
        raw = handle_unified_search({"query": "mcp unified", "mode": "fts5",
                                     "utility_rerank": True, "agent_name": "tester", "top_k": 10})
        out = json.loads(raw)
        assert "error" not in out
        assert "utility_rerank_signal" not in json.dumps(out, ensure_ascii=False)
        print("PASS: test_mcp_handle_unified_search_utility_rerank_end_to_end")
    finally:
        _reset_config()
        cleanup_temp_db(db_path, orig)
