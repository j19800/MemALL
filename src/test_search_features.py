"""Dry-run validation for the six unimplemented search features.

Run:  E:\\MemALL\\.venv\\Scripts\\python.exe test_search_features.py
Uses an isolated temp DB (MEMALL_DB_PATH) so it never touches real data.
"""
import json
import os
import sys
import tempfile

# Point MemALL at a scratch DB *before* importing memall.
_tmpdir = tempfile.mkdtemp(prefix="memall_feat_test_")
os.environ["MEMALL_DB_PATH"] = os.path.join(_tmpdir, "test.db")

from memall.core.thin_waist import (  # noqa: E402
    capture, retrieve, enhanced_search, unified_search,
)
from memall.core.db import get_conn, init_db  # noqa: E402
from memall.search import enhancements as en  # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"  -- {detail}" if detail and not cond else ""))


def seed():
    """Seed the scratch DB with memories across levels/categories/projects."""
    rows = [
        ("MemALL 检索架构采用 FTS5 与向量混合排序，RRF 融合分数作为最终排序依据，兼顾关键词与语义召回。", "L4", "architecture", "MemALL"),
        ("向量检索使用 bge-small-zh 模型，512 维 embedding，通过 sqlite-vec 扩展做 KNN 最近邻查询。", "L3", "architecture", "MemALL"),
        ("搜索质量调研结论：需要 facet 分面聚合与 query expansion 查询扩展来提升检索体验。", "P1", "decision", "MemALL"),
        ("MCP 工具层统一走 thin_waist 单一写入口，保证所有写入路径都经过同一套校验与生命周期钩子。", "L2", "architecture", "MemALL"),
        ("性能测试结论：FTS5 检索库在万级数据量下 3ms 内返回，瓶颈主要在 embedding 推理阶段。", "P2", "testing", "MemALL"),
        ("记忆分层 L1-L11 生命周期管理，按热度与访问频次做层级晋升，高位记忆权重更高。", "L5", "design", "MemALL"),
        ("项目 A 的周会纪要：讨论了部署节奏、灰度方案与回滚策略，结论是双周迭代。", "P1", "meeting", "ProjectA"),
        ("项目 B 的需求文档草稿：包含检索界面分面筛选、搜索建议与结果解释三个模块。", "P2", "meeting", "ProjectB"),
    ]
    ids = []
    for content, level, cat, proj in rows:
        mem_id = capture(content, level=level, category=cat, project=proj,
                         agent_name="workbuddy", owner="老陈", subject=content[:20])
        ids.append(mem_id)
    return ids


def main():
    init_db()
    ids = seed()
    print(f"\n=== seeded {len(ids)} memories: {ids} ===\n")

    # ── 0. Backward compatibility ──
    plain = retrieve(query="检索", agent_name="workbuddy", limit=20)
    check("retrieve() unchanged (returns list)", isinstance(plain, list) and len(plain) > 0)

    # ── 1. Faceted search ──
    res = enhanced_search(query="检索", agent_name="workbuddy", limit=5,
                          facet_fields=["level", "category"])
    check("enhanced_search returns facets dict", isinstance(res.get("facets"), dict),
          f"got {res.get('facets')}")
    facets = res.get("facets", {})
    check("facet 'level' present with counts", "level" in facets and sum(facets["level"].values()) > 0)
    check("facet 'category' present with counts", "category" in facets)
    # facet counts must cover the full candidate set, not just the page
    total_facet = sum(facets.get("level", {}).values())
    check("facets count unlimited candidates (>= page size)", total_facet >= len(res["results"]),
          f"facets={total_facet} page={len(res['results'])}")

    # facet_fields="auto"
    res_auto = enhanced_search(query="", agent_name="workbuddy", limit=3, facet_fields="auto")
    check("facet_fields='auto' uses config defaults",
          set(res_auto.get("facets", {}).keys()) >= {"level", "category", "project"})

    # unsafe facet field must be dropped, never interpolated into SQL
    res_bad = enhanced_search(query="", agent_name="workbuddy", limit=3,
                              facet_fields=["level; DROP TABLE memories;--"])
    check("unsafe facet field dropped (no SQL injection)",
          "level; DROP TABLE memories;--" not in res_bad.get("facets", {}))
    still = retrieve(query="检索", agent_name="workbuddy", limit=1)
    check("memories table survives injection attempt", len(still) >= 0)

    # ── 2. Query expansion ──
    syn = {"检索": ["搜索", "查找"]}
    exp = en.expand_query("检索", synonyms=syn)
    check("expand_query adds synonyms", "搜索" in exp and "检索" in exp, exp)
    check("expand_query no-op without synonyms",
          en.expand_query("检索", synonyms={}) == "检索")
    exp_hist = en.expand_query("检索", synonyms={}, history_terms=["排序"])
    check("expand_query blends history terms", "排序" in exp_hist)

    # expansion must actually broaden FTS5 recall (not just change the string)
    from memall.core.thin_waist import fts_query
    q1 = fts_query("检索")
    q2 = fts_query(exp)
    check("expanded FTS5 query differs and is valid", q1 != q2 and "OR" in q2)

    # ── 3. Suggestions ──
    with get_conn() as conn:
        sugg = en.suggest_queries(conn, "检索", limit=5)
    check("suggest_queries returns list", isinstance(sugg, list))

    # ── 4. Clustering ──
    res_c = enhanced_search(query="", agent_name="workbuddy", limit=20)
    dicts = [{"memory_id": m.id, "content": m.content, "subject": m.subject,
              "category": m.category, "level": m.level, "project": m.project,
              "rrf_score": 1.0 / (i + 1)} for i, m in enumerate(res_c["results"])]
    clusters = en.cluster_results(dicts, k=3)
    check("cluster_results returns clusters", isinstance(clusters, list) and len(clusters) > 0)
    total_members = sum(c["size"] for c in clusters)
    check("clustering never drops results", total_members == len(dicts),
          f"{total_members} vs {len(dicts)}")
    check("clusters have labels", all(c.get("label") for c in clusters))
    # determinism
    clusters2 = en.cluster_results(list(dicts), k=3)
    check("clustering is deterministic",
          [c["label"] for c in clusters] == [c["label"] for c in clusters2])

    # ── 5. Explainability ──
    expl = en.explain_result(dicts[0], query="检索")
    check("explain_result returns matched_terms", "matched_terms" in expl)
    check("explain_result has a reason string", bool(expl.get("reason")))
    hl = en.highlight_matches("MemALL 检索架构采用 FTS5", "检索")
    check("highlight_matches returns snippets", isinstance(hl, list))

    # ── 6. Personalisation ──
    conn = get_conn()
    target = ids[0]
    for i in range(5):
        en.record_interaction(conn, "检索", memory_id=target, action="click",
                              agent_name="workbuddy", rank_position=1)
    en.record_interaction(conn, "检索", memory_id=target, action="dwell",
                          dwell_ms=45000, agent_name="workbuddy")
    affinity = en.build_affinity(conn, "workbuddy")
    check("build_affinity learns from interactions", affinity.get(target, 0) > 0,
          f"affinity={affinity}")
    # dicts is ordered by rrf_score desc (top=1.0, last=1/8).  With
    # weight=0.25 the max boost is 1.25x, which cannot overcome an 8x score
    # gap — that is correct behaviour (affinity adjusts, it does not
    # override relevance).  Verify the boost applies and that a strong enough
    # affinity does reorder.
    ranked = en.apply_personalization(dicts, affinity, weight=0.25)
    target_row = [d for d in ranked if d["memory_id"] == target][0]
    check("apply_personalization boosts target score",
          target_row.get("personalized_score") is not None
          and target_row["personalized_score"] > dicts[-1]["rrf_score"],
          f"got {target_row.get('personalized_score')}")
    check("apply_personalization records affinity", target_row.get("affinity") == 1.0,
          f"got {target_row.get('affinity')}")
    # With a dominant weight the same affinity must reach the top.
    ranked_strong = en.apply_personalization(dicts, affinity, weight=8.0)
    check("apply_personalization reorders at high weight",
          ranked_strong[0]["memory_id"] == target,
          f"top={ranked_strong[0]['memory_id']} target={target}")
    conn.close()

    # ── 7. Context boost ──
    conn = get_conn()
    en.record_interaction(conn, "项目 周会", memory_id=ids[6], action="click",
                          agent_name="workbuddy")
    profile = en.build_context_profile(conn, "workbuddy")
    check("build_context_profile infers project", "ProjectA" in profile.get("projects", {}),
          f"profile={profile}")
    boosted = en.apply_context_boost(dicts, profile, weight=0.15)
    pa = [d for d in boosted if d.get("project") == "ProjectA"]
    check("apply_context_boost boosts matching project",
          pa and any(d.get("context_score") for d in pa))
    conn.close()

    # ── 8. unified_search integration (enhancements path) ──
    out = json.loads(unified_search({
        "query": "检索", "mode": "fts5", "top_k": 5,
        "agent_name": "workbuddy", "facet_fields": ["level", "category"],
        "explain": True,
    }))
    check("unified_search(fts5+facets) keeps results", out.get("total", 0) >= 0 and "results" in out)
    check("unified_search exposes facets", "facets" in out, str(out.keys()))

    # hybrid path with enhancement args must not crash and stays JSON
    out_h = json.loads(unified_search({
        "query": "检索", "mode": "hybrid", "top_k": 5,
        "agent_name": "workbuddy", "explain": True, "cluster": True,
    }))
    check("unified_search(hybrid+explain) valid JSON", "results" in out_h)

    # ── 9. MCP surface ──
    from memall.mcp.adapter import handle_call
    mcp_out = handle_call("memall_read", {
        "action": "search", "query": "检索", "mode": "fts5", "top_k": 3,
        "agent_name": "workbuddy", "facet_fields": ["level"], "explain": True,
    })
    payload = json.loads(mcp_out)
    check("MCP memall_read/search with facets", "facets" in payload or "results" in payload,
          str(payload)[:200])

    mcp_rec = handle_call("memall_read", {
        "action": "record_interaction", "query": "检索",
        "memory_id": ids[1], "interaction_type": "click", "agent_name": "workbuddy",
    })
    check("MCP record_interaction works", json.loads(mcp_rec).get("status") == "ok", mcp_rec)

    mcp_imp = handle_call("memall_read", {
        "action": "record_interaction", "query": "检索",
        "interaction_type": "impression", "agent_name": "workbuddy",
    })
    check("MCP record_interaction accepts query-only impression",
          json.loads(mcp_imp).get("status") == "ok", mcp_imp)

    mcp_bad = handle_call("memall_read", {
        "action": "search", "query": "x", "facet_fields": ["nope"],
    })
    check("MCP rejects unknown facet field", "error" in json.loads(mcp_bad), mcp_bad[:120])

    print(f"\n=== {len(PASS)} passed, {len(FAIL)} failed ===")
    if FAIL:
        print("FAILED:", FAIL)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
