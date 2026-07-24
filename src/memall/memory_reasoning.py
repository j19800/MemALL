"""
记忆推理 — 因果推理 + 时序推理 + 反事实推理。

基于知识图谱 (KG) 和实体关系，自动构建因果链。
"""

import re
from datetime import datetime, timezone, timedelta
from typing import Optional

from memall.core.db import pool_conn, get_conn
from memall.core.entity_extractor import extract_entities, extract_triples, resolve_entity


# ── 因果模式 ──

_CAUSE_PATTERNS = [
    (r"(?:因为|由于|由?于)\s*(.+?)\s*(?:所以|因此|因而|导致|引起|引发|造成)", "cause"),
    (r"(?:导致|引起|引发|造成)\s*(?:了)?\s*(?:的)?\s*(.+?)(?:。|，|$)", "effect"),
    (r"(?:所以|因此|因而)\s*(.+?)(?:。|，|$)", "effect"),
    (r"如果不(.+?)(?:就|则|会)(.+?)(?:。|，|$)", "counterfactual"),
    (r"如果(.+?)(?:就|则|会)(.+?)(?:。|，|$)", "conditional"),
    (r"(?:先|首先)\s*(.+?)(?:然后|接着|再|后)\s*(.+?)(?:。|，|$)", "sequence"),
]

_CAUSAL_RELATIONS = {"causes", "caused_by", "prevents", "enables", "leads_to", "sequence"}


def _extract_causal_triples(content: str) -> list[dict]:
    """从文本中提取因果三元组。

    Returns:
        [{cause, effect, relation_type, confidence}, ...]
    """
    triples = []
    for pat, kind in _CAUSE_PATTERNS:
        for m in re.finditer(pat, content):
            if kind == "cause":
                cause = m.group(1).strip()
                # effect is after the clause
                rest = content[m.end():]
                effect_match = re.search(r"(.+?)(?:。|！|？|$)", rest)
                effect = effect_match.group(1).strip() if effect_match else ""
                if cause and effect:
                    triples.append({
                        "cause": cause[:100],
                        "effect": effect[:100],
                        "relation_type": "causes",
                        "confidence": 0.7,
                    })
            elif kind == "effect":
                effect = m.group(1).strip()
                # cause is before the matched word
                before = content[:m.start()]
                cause_match = re.search(r"(.+?)(?:。|！|？|$)", before)
                cause = cause_match.group(1).strip() if cause_match else ""
                if cause and effect:
                    triples.append({
                        "cause": cause[:100],
                        "effect": effect[:100],
                        "relation_type": "caused_by",
                        "confidence": 0.6,
                    })
            elif kind == "counterfactual":
                condition = m.group(1).strip()
                result = m.group(2).strip()
                if condition and result:
                    triples.append({
                        "cause": condition[:100],
                        "effect": result[:100],
                        "relation_type": "prevents",
                        "confidence": 0.5,
                    })
    return triples


def extract_causal_relations(content: str, memory_id: int) -> int:
    """从内容中提取因果边并写入知识图谱。

    Returns:
        提取的因果边数量
    """
    triples = _extract_causal_triples(content)
    if not triples:
        return 0

    now = datetime.now(timezone.utc).isoformat()
    count = 0
    with pool_conn() as conn:
        for t in triples:
            cause_id = resolve_entity(t["cause"], "concept", conn)
            effect_id = resolve_entity(t["effect"], "concept", conn)
            if cause_id and effect_id:
                conn.execute(
                    "INSERT OR IGNORE INTO knowledge_triples "
                    "(subject_id, predicate, object_id, source_memory_id, confidence, weight, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (cause_id, t["relation_type"], effect_id, memory_id, t["confidence"], 1.0, now),
                )
                count += 1
        conn.commit()
    return count


def query_causal_chain(entity_name: str, depth: int = 2) -> dict:
    """查询因果链 — 从实体出发，沿因果边遍历。

    Returns:
        {entity, causes: [...], effects: [...], chain: [...]}
    """
    with pool_conn() as conn:
        # Find entity
        row = conn.execute(
            "SELECT id FROM entities WHERE LOWER(name) = LOWER(?) LIMIT 1",
            (entity_name,),
        ).fetchone()
        if not row:
            return {"entity": entity_name, "causes": [], "effects": [], "chain": []}

        eid = row["id"]
        visited = {eid}
        chain = []

        # BFS traversal along causal edges
        current = {eid}
        for _ in range(depth):
            if not current:
                break
            id_list = list(current)
            ph = ",".join("?" * len(id_list))

            triples = conn.execute(
                f"SELECT t.subject_id, t.predicate, t.object_id, t.confidence, "
                f"s.name as cause_name, o.name as effect_name "
                f"FROM knowledge_triples t "
                f"JOIN entities s ON t.subject_id = s.id "
                f"JOIN entities o ON t.object_id = o.id "
                f"WHERE t.predicate IN ('causes','caused_by','prevents','enables') "
                f"AND (t.subject_id IN ({ph}) OR t.object_id IN ({ph}))",
                *([id_list, id_list]),
            ).fetchall()

            new_ids = set()
            for t in triples:
                chain.append({
                    "cause": t["cause_name"],
                    "effect": t["effect_name"],
                    "relation": t["predicate"],
                    "confidence": t["confidence"],
                })
                new_ids.add(t["subject_id"])
                new_ids.add(t["object_id"])

            current = new_ids - visited
            visited.update(current)

        causes = [c for c in chain if c["effect"].lower() == entity_name.lower()]
        effects = [c for c in chain if c["cause"].lower() == entity_name.lower()]

        return {
            "entity": entity_name,
            "causes": causes,
            "effects": effects,
            "chain": chain,
        }


def query_why(query: str) -> dict:
    """回答 '为什么' 类型的问题 — 自动提取实体并查询因果链。

    Args:
        query: 自然语言查询，如 "为什么系统变慢了？"

    Returns:
        {query, entities: [...], causal_chains: [...], summary: str}
    """
    # 提取实体
    entities = extract_entities(query)
    if not entities:
        return {"query": query, "entities": [], "causal_chains": [], "summary": "未找到相关实体"}

    chains = []
    for ent in entities:
        chain = query_causal_chain(ent["name"])
        if chain["chain"]:
            chains.append(chain)

    if not chains:
        return {"query": query, "entities": entities, "causal_chains": [], "summary": "未找到因果链"}

    # 生成摘要
    parts = []
    for chain in chains:
        for c in chain["chain"]:
            parts.append(f"因为 {c['cause']}，所以 {c['effect']}（{c['relation']}）")

    return {
        "query": query,
        "entities": entities,
        "causal_chains": chains,
        "summary": "；".join(parts),
    }