import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from memall.core.db import get_conn
from memall.core.thin_waist import retrieve
from memall.pipeline.persona import CERTAIN_KEYWORDS, UNCERTAIN_KEYWORDS, DECISION_KEYWORDS


class ContextAssembler:
    @staticmethod
    def ask(query: str, subject: str = "", mode: str = "stance", scope: str = "local", limit: int = 20) -> dict:
        subject = subject.strip().lower() if subject else ""
        mode = mode.lower()

        conn = get_conn()
        try:
            agent = None
            if subject:
                row = conn.execute(
                    "SELECT agent_name, profile_json, identity_profile FROM identities WHERE LOWER(agent_name) = LOWER(?)",
                    (subject,),
                ).fetchone()
                if row:
                    profile = json.loads(row["profile_json"]) if row["profile_json"] else {}
                    id_profile = json.loads(row["identity_profile"]) if row["identity_profile"] else {}
                    profile.update(id_profile)  # merge identity into profile for unified view
                    agent = {
                        "name": row["agent_name"],
                        "profile": profile,
                    }
                else:
                    # Fix Bug-1: 当 identities 查不到该 subject 时，从 memories.agent_name 反查最接近的
                    # 这样新注册的 agent（如 E2E 测试临时 agent）也能用 pattern/predict
                    mem_row = conn.execute(
                        "SELECT agent_name, COUNT(*) as cnt FROM memories WHERE LOWER(agent_name) = LOWER(?) GROUP BY agent_name ORDER BY cnt DESC LIMIT 1",
                        (subject,),
                    ).fetchone()
                    if mem_row:
                        agent = {
                            "name": mem_row["agent_name"],
                            "profile": {},  # 临时 agent 无 profile，pattern/predict 会用全局默认
                        }

            from memall.federation.family import search_family

            if scope == "family":
                fam = search_family(query, limit=limit)
                result = {
                    "answer": f"共享库结果（family.db）关于「{query}」：\n",
                    "citations": [],
                    "query": query,
                    "mode": mode,
                    "subject": subject or "",
                }
                if fam:
                    for f in fam:
                        result["answer"] += f"  [{f['source']}] #{f['original_id']} from {f['source_agent']}: {f['content'][:150]}...\n"
                        result["citations"].append(f"family:{f['original_id']}")
                else:
                    result["answer"] += "  没有找到相关结果。\n"
                _ensure_disclaimer(result)
                return result

            if mode == "stance":
                result = _ask_stance(conn, query, agent, limit)
            elif mode == "pattern":
                result = _ask_pattern(conn, query, agent, limit)
            elif mode == "predict":
                result = _ask_predict(conn, query, agent, limit)
            else:
                return {"error": f"unknown mode: {mode}", "query": query}

            if scope == "all":
                fam = search_family(query, limit=5)
                if fam:
                    result["family_results"] = fam
                    family_lines = "\n\n共享库结果（family.db）：\n"
                    for f in fam:
                        family_lines += f"  [{f['source']}] #{f['original_id']} from {f['source_agent']}: {f['content'][:100]}...\n"
                    result["answer"] += family_lines

            _ensure_disclaimer(result)

            return result

        finally:
            conn.close()


def _ensure_disclaimer(result: dict):
    result["disclaimer"] = "基于记忆推演，非真实决策"
    return result


def _search_memories(conn, query: str, agent_name: str = "", limit: int = 20):
    fts_where = ""
    fts_params = []
    if query and isinstance(query, str):
        from memall.core.thin_waist import fts_query
        q = fts_query(query)
        if q:
            fts_where = " AND m.id IN (SELECT rowid FROM memories_fts WHERE memories_fts MATCH ?)"
            fts_params.append(q)
    agent_where = ""
    agent_params = []
    if agent_name:
        agent_where = " AND LOWER(m.agent_name) = LOWER(?)"
        agent_params.append(agent_name)
    rows = conn.execute(
        "SELECT m.id, m.content, m.category, m.level, m.agent_name, m.occurred_at "
        "FROM memories m WHERE 1=1{}{} ORDER BY m.occurred_at DESC LIMIT ?".format(agent_where, fts_where),
        tuple(agent_params + fts_params + [limit]),
    ).fetchall()
    return rows


def _get_agent_edges(conn, agent_name: str, relation_type: str = ""):
    cond = " AND e.relation_type = ?" if relation_type else ""
    params = [agent_name]
    if relation_type:
        params.append(relation_type)
    return conn.execute(
        "SELECT e.source_id, e.target_id, e.relation_type, e.metadata "
        "FROM edges e "
        "JOIN memories m ON e.source_id = m.id "
        f"WHERE LOWER(m.agent_name) = LOWER(?){cond} "
        "ORDER BY e.id DESC LIMIT 200",
        tuple(params),
    ).fetchall()


def _get_contradiction_pairs(conn, agent_name: str):
    edges = _get_agent_edges(conn, agent_name, "contradicts")
    pairs = []
    for e in edges:
        src = retrieve(e["source_id"])
        tgt = retrieve(e["target_id"])
        meta = json.loads(e["metadata"]) if e["metadata"] and e["metadata"] != "{}" else {}
        pairs.append({
            "source_id": e["source_id"],
            "source_content": src.content if src else "",
            "target_id": e["target_id"],
            "target_content": tgt.content if tgt else "",
            "resolved": meta.get("resolved", False),
        })
    return pairs


def _get_domain_distribution(conn, agent_name: str):
    rows = conn.execute(
        "SELECT category, COUNT(*) as cnt FROM memories WHERE LOWER(agent_name) = LOWER(?) AND category != '' AND category IS NOT NULL GROUP BY category ORDER BY cnt DESC",
        (agent_name,),
    ).fetchall()
    total = sum(r["cnt"] for r in rows) or 1
    return {r["category"]: {"count": r["cnt"], "ratio": round(r["cnt"] / total, 3)} for r in rows}


def _ask_stance(conn, query: str, agent: dict, limit: int) -> dict:
    agent_name = agent["name"] if agent else ""
    mems = _search_memories(conn, query, agent_name, limit)
    if not mems:
        return {"answer": f"没有找到与「{query}」相关的记忆。", "citations": [], "query": query, "mode": "stance"}

    evidence = []
    certain = 0
    uncertain = 0
    decisions = 0
    pos_keywords = ["支持", "采用", "推荐", "肯定", "好", "优", "选择"]
    neg_keywords = ["反对", "不用", "避免", "问题", "不好", "劣", "放弃"]
    stance_pos = 0
    stance_neg = 0
    neutral = 0

    for m in mems:
        content = m["content"] or ""
        cid = m["id"]
        cat = m["category"] or ""
        certain += sum(1 for kw in CERTAIN_KEYWORDS if kw in content)
        uncertain += sum(1 for kw in UNCERTAIN_KEYWORDS if kw in content)
        decisions += sum(1 for kw in DECISION_KEYWORDS if kw in content)
        pos_match = sum(1 for kw in pos_keywords if kw in content)
        neg_match = sum(1 for kw in neg_keywords if kw in content)
        if pos_match > neg_match:
            stance_pos += 1
        elif neg_match > pos_match:
            stance_neg += 1
        evidence.append({"id": cid, "content": content, "category": cat})

    total = len(mems)
    stance_label = "支持" if stance_pos > stance_neg else ("反对" if stance_neg > stance_pos else "中立或未明确表态")
    certainty_label = "高自信" if certain > uncertain else ("低自信" if uncertain > certain else "中等")

    answer = (
        f"关于「{query}」的立场分析（{agent_name or '全局'}）：\n"
        f"总体立场偏向「{stance_label}」（{stance_pos}条支持 / {stance_neg}条反对 / {total - stance_pos - stance_neg}条中立）\n"
        f"涉及 {total} 条相关记忆，决策关键词出现 {decisions} 次，{certainty_label}（确定{certain}次 / 不确定{uncertain}次）\n"
    )
    if stance_pos > 0:
        answer += f"\n支持证据（{stance_pos}条）：\n"
        for e in evidence:
            if any(kw in e["content"] for kw in pos_keywords):
                answer += f"  - #{e['id']} [{e['category']}] {e['content'][:100]}...\n"
    if stance_neg > 0:
        answer += f"\n反对证据（{stance_neg}条）：\n"
        for e in evidence:
            if any(kw in e["content"] for kw in neg_keywords):
                answer += f"  - #{e['id']} [{e['category']}] {e['content'][:100]}...\n"

    citations = [e["id"] for e in evidence]

    if agent:
        feat = agent.get("profile", {}).get("features", {})
        proto = agent.get("profile", {}).get("prototype", {})
        answer += (
            f"\n人格上下文：{agent['name']} 是 {proto.get('cn','?')}（{proto.get('en','?')}）\n"
            f"  certainty_score={feat.get('certainty_score','?')} / decision_ratio={feat.get('decision_ratio','?')}\n"
            f"  contradiction_count={feat.get('contradiction_count','?')} / resolution={feat.get('contradiction_resolution','?')}"
        )

    return {"answer": answer, "citations": citations, "total_memories": total,
            "certain": certain, "uncertain": uncertain, "decisions": decisions,
            "query": query, "mode": "stance", "subject": agent_name}


def _ask_pattern(conn, query: str, agent: dict, limit: int) -> dict:
    agent_name = agent["name"] if agent else ""
    if not agent_name:
        return {"answer": "pattern 模式需要指定 subject（agent 名称）。", "citations": [], "query": query, "mode": "pattern"}

    pairs = _get_contradiction_pairs(conn, agent_name)
    domains = _get_domain_distribution(conn, agent_name)
    feat = agent.get("profile", {}).get("features", {})
    proto = agent.get("profile", {}).get("prototype", {})

    top_domains = sorted(domains.items(), key=lambda x: -x[1]["count"])[:5]

    contradictions_resolved = sum(1 for p in pairs if p["resolved"])
    contradictions_unresolved = sum(1 for p in pairs if not p["resolved"])

    answer = f"关于「{query}」的模式分析（{agent_name}）：\n"
    answer += f"\n知识领域分布（Top 5）：\n"
    for d_name, d_info in top_domains:
        answer += f"  {d_name}: {d_info['count']}条 ({d_info['ratio']*100:.1f}%)\n"

    answer += f"\n矛盾分析（共 {len(pairs)} 对）：\n"
    answer += f"  已解决：{contradictions_resolved} / 未解决：{contradictions_unresolved}\n"
    answer += f"  矛盾解决率：{feat.get('contradiction_resolution', 0)*100:.1f}%\n"

    if pairs:
        answer += "\n矛盾示例：\n"
        for p in pairs[:5]:
            status = "✓ 已解决" if p["resolved"] else "✗ 未解决"
            answer += f"  #{p['source_id']} 与 #{p['target_id']} [{status}]\n"
            answer += f"    A: {p['source_content'][:80]}...\n"
            answer += f"    B: {p['target_content'][:80]}...\n"

    answer += (
        f"\n认知特征：\n"
        f"  原型：{proto.get('cn','?')}（{proto.get('en','?')}）\n"
        f"  决策密度：{feat.get('decision_ratio',0)*100:.1f}%\n"
        f"  提问倾向：{feat.get('question_ratio',0)*100:.1f}%\n"
        f"  自信指数：{feat.get('certainty_score',0)*100:.1f}%\n"
        f"  知识广度：{feat.get('domain_breadth',0)} 个领域\n"
        f"  综合思维：{feat.get('derived_count',0)} 次融合引用\n"
        f"  跨域学习：{feat.get('new_domain_rate',0)*100:.1f}% 新领域占比"
    )

    citations = []
    for p in pairs[:5]:
        if p["source_id"]:
            citations.append(p["source_id"])
        if p["target_id"]:
            citations.append(p["target_id"])

    return {"answer": answer, "citations": citations, "total_memories": feat.get("sample_size", 0),
            "contradictions_total": len(pairs), "contradictions_resolved": contradictions_resolved,
            "query": query, "mode": "pattern", "subject": agent_name}


def _ask_predict(conn, query: str, agent: dict, limit: int) -> dict:
    agent_name = agent["name"] if agent else ""
    if not agent_name:
        return {"answer": "predict 模式需要指定 subject（agent 名称）。", "citations": [], "query": query, "mode": "predict"}

    domains = _get_domain_distribution(conn, agent_name)
    feat = agent.get("profile", {}).get("features", {})
    proto = agent.get("profile", {}).get("prototype", {})

    top_domains = sorted(domains.items(), key=lambda x: -x[1]["count"])[:5]

    burst = feat.get("burst_ratio", 0)
    regularity = feat.get("capture_regularity", 0.5)
    certainty = feat.get("certainty_score", 0.5)
    decision = feat.get("decision_ratio", 0.3)
    depth = feat.get("domain_depth", 0)
    breadth = feat.get("domain_breadth", 0)
    resolution = feat.get("contradiction_resolution", 0)
    derived = feat.get("derived_count", 0)

    approach = []
    if burst > 0.5:
        approach.append("倾向于快速迭代、冲动式探索，先试再说")
    else:
        approach.append("倾向于审慎规划、系统性推进")
    if depth > breadth:
        approach.append("偏好深度钻研而非广度覆盖")
    else:
        approach.append("偏好广泛探索而非单点深挖")
    if certainty > 0.6:
        approach.append("决策时表现出高自信，倾向于给出明确结论")
    else:
        approach.append("决策时较为谨慎，倾向于保留余地")
    if decision > 0.3:
        approach.append("决策密度高，习惯频繁做出选择并记录")
    else:
        approach.append("决策密度低，花更多时间在调研和思考上")
    if resolution > 0.5:
        approach.append("矛盾解决能力强，善于从冲突中收敛到最终方案")
    else:
        approach.append("矛盾解决能力弱，部分冲突可能仍未收敛")
    if derived > 5:
        approach.append("善于综合多条记忆形成新结论")
    else:
        approach.append("较少进行记忆综合，偏向原子式思考")

    answer = f"关于「{query}」的推演分析（{agent_name}）：\n\n推演基础（{feat.get('sample_size',0)} 条记忆样本）：\n"
    for d_name, d_info in top_domains:
        answer += f"  {d_name}: {d_info['count']}条 ({d_info['ratio']*100:.1f}%)\n"

    answer += f"\n如果由 {agent_name} 来解决「{query}」，基于其认知模式推断：\n"
    for a in approach:
        answer += f"  • {a}\n"
    answer += f"  分类上最可能归入 {top_domains[0][0] if top_domains else 'general'} 领域\n"

    answer += (
        f"\n人格上下文：{agent_name} 是 {proto.get('cn','?')}（{proto.get('en','?')}）\n"
        f"  sample_size={feat.get('sample_size','?')} / depth={depth} / breadth={breadth}\n"
        f"  burst={burst} / certainty={certainty} / decision={decision}"
    )

    return {"answer": answer, "citations": ["N/A - 基于 persona 特征推演"],
            "total_memories": feat.get("sample_size", 0),
            "query": query, "mode": "predict", "subject": agent_name}
