"""
记忆推理引擎 — 因果推理 + 时序推理 + 矛盾检测 + 模式发现 + 反事实推理。

支持:
- 多跳因果链 (A → B → C → D)
- 时序推理 (事件先后顺序)
- 矛盾检测 (冲突的记忆)
- 模式发现 (重复出现的模式)
- 反事实推理 ("如果...会怎样")
- 自然语言查询 (回答 "为什么" 问题)
"""

import re
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone, timedelta
from typing import Any, Optional

from memall.core.db import pool_conn, get_conn
from memall.core.entity_extractor import extract_entities, extract_triples, resolve_entity
from memall.pipeline.dream import _check_contradiction

logger = __import__("logging").getLogger(__name__)


# ══════════════════════════════════════════════════════════════
# 1. 因果模式 (支持中文 + 英文)
# ══════════════════════════════════════════════════════════════

_CAUSE_PATTERNS = [
    # 中文因果 — 确保有足够多的 capture group
    (r"(?:因为|由于)\s*(.+?)\s*(?:所以|因此|因而|导致|引起|引发|造成|使得|让)\s*(.+?)(?:[。，！？]|$)", "cause_effect"),
    (r"(?:导致|引起|引发|造成|使得|让)\s*(?:了)?\s*(?:的)?\s*(.+?)(?:[。，！？]|$)", "effect_only"),
    (r"(?:所以|因此|因而|于是)\s*(.+?)(?:[。，！？]|$)", "effect_only"),
    (r"(?:之所以)\s*(.+?)\s*(?:是因为|是由于)\s*(.+?)(?:[。，！？]|$)", "effect_cause"),
    (r"(?:为了|以便|旨在)\s*(.+?)(?:[。，！？]|$)", "purpose"),
    (r"(?:只有|只要)\s*(.+?)\s*(?:才|就|会)\s*(.+?)(?:[。，！？]|$)", "condition"),
    (r"(?:如果|假如|倘若)\s*(.+?)\s*(?:就|则|会|那么)\s*(.+?)(?:[。，！？]|$)", "conditional"),
    (r"(?:如果不|要不是|假如不)\s*(.+?)\s*(?:就|则|会)\s*(.+?)(?:[。，！？]|$)", "counterfactual"),
    (r"(?:先|首先)\s*(.+?)(?:然后|接着|再|随后|之后)\s*(.+?)(?:[。，！？]|$)", "sequence"),
    (r"(?:第一步|第二步|首先|然后|接着|最后)\s*(.+?)(?:[。，！？]|$)", "step"),

    # 英文因果
    (r"(?:because|since|as|due to)\s+(.+?)\s*(?:,|so|therefore|thus|hence)\s+(.+?)(?:[.?!]|$)", "cause_effect"),
    (r"(?:causes|leads to|results in|triggers|produces)\s+(.+?)(?:[.?!]|$)", "effect_only"),
    (r"(?:therefore|thus|hence|so|consequently)\s+(.+?)(?:[.?!]|$)", "effect_only"),
    (r"(?:if|when)\s+(.+?)\s*(?:then|,)\s*(.+?)(?:[.?!]|$)", "conditional"),
    (r"(?:first|then|after that|next|finally)\s+(.+?)(?:[.?!]|$)", "step"),
]


# ══════════════════════════════════════════════════════════════
# 2. 因果提取
# ══════════════════════════════════════════════════════════════

def extract_causal_relations(content: str, memory_id: int = 0) -> list[dict]:
    """从文本中提取所有因果/时序/条件关系。

    Returns:
        [{subject, relation, object, confidence, pattern_type}, ...]
    """
    relations = []
    seen = set()

    for pat, pattern_type in _CAUSE_PATTERNS:
        for m in re.finditer(pat, content):
            groups = m.groups()

            if pattern_type == "cause_effect" and len(groups) >= 2:
                cause, effect = groups[0].strip(), groups[1].strip()
                if cause and effect and len(cause) > 3 and len(effect) > 3:
                    key = (cause[:40], "causes", effect[:40])
                    if key not in seen:
                        seen.add(key)
                        relations.append({
                            "subject": cause[:150],
                            "relation": "causes",
                            "object": effect[:150],
                            "confidence": 0.8,
                            "pattern_type": pattern_type,
                        })

            elif pattern_type == "effect_only" and len(groups) >= 1:
                effect = groups[0].strip()
                if effect and len(effect) > 3:
                    key = ("", "causes", effect[:40])
                    if key not in seen:
                        seen.add(key)
                        # Try to find cause from context (before the match)
                        context_before = content[:m.start()].strip()
                        if context_before and len(context_before) > 10:
                            cause = context_before[-100:].strip()
                        else:
                            cause = "[unknown]"
                        relations.append({
                            "subject": cause[:150],
                            "relation": "causes",
                            "object": effect[:150],
                            "confidence": 0.6,
                            "pattern_type": pattern_type,
                        })

            elif pattern_type == "effect_cause":
                effect, cause = groups[0].strip(), groups[1].strip()
                if cause and effect and len(cause) > 3 and len(effect) > 3:
                    key = (cause[:40], "causes", effect[:40])
                    if key not in seen:
                        seen.add(key)
                        relations.append({
                            "subject": cause[:150],
                            "relation": "causes",
                            "object": effect[:150],
                            "confidence": 0.85,
                            "pattern_type": pattern_type,
                        })

            elif pattern_type == "conditional":
                condition, result = groups[0].strip(), groups[1].strip()
                if condition and result and len(condition) > 3 and len(result) > 3:
                    key = (condition[:40], "enables", result[:40])
                    if key not in seen:
                        seen.add(key)
                        relations.append({
                            "subject": condition[:150],
                            "relation": "enables",
                            "object": result[:150],
                            "confidence": 0.5,
                            "pattern_type": pattern_type,
                        })

            elif pattern_type == "counterfactual":
                condition, result = groups[0].strip(), groups[1].strip()
                if condition and result and len(condition) > 3 and len(result) > 3:
                    key = (condition[:40], "prevents", result[:40])
                    if key not in seen:
                        seen.add(key)
                        relations.append({
                            "subject": condition[:150],
                            "relation": "prevents",
                            "object": result[:150],
                            "confidence": 0.7,
                            "pattern_type": pattern_type,
                        })

            elif pattern_type == "sequence":
                first, second = groups[0].strip(), groups[1].strip()
                if first and second and len(first) > 3 and len(second) > 3:
                    key = (first[:40], "sequence", second[:40])
                    if key not in seen:
                        seen.add(key)
                        relations.append({
                            "subject": first[:150],
                            "relation": "sequence",
                            "object": second[:150],
                            "confidence": 0.9,
                            "pattern_type": pattern_type,
                        })

    # 写入 KG 表
    if memory_id and relations:
        _persist_causal_relations(memory_id, relations)

    return relations


def _persist_causal_relations(memory_id: int, relations: list[dict]):
    """将因果边写入 knowledge_triples 表。"""
    now = datetime.now(timezone.utc).isoformat()
    with pool_conn() as conn:
        for r in relations:
            subj_id = resolve_entity(r["subject"], "concept", conn)
            obj_id = resolve_entity(r["object"], "concept", conn)
            if subj_id and obj_id:
                conn.execute(
                    "INSERT OR IGNORE INTO knowledge_triples "
                    "(subject_id, predicate, object_id, source_memory_id, confidence, weight, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (subj_id, r["relation"], obj_id, memory_id, r["confidence"], 1.0, now),
                )
        conn.commit()


# ══════════════════════════════════════════════════════════════
# 3. 多跳因果链推理
# ══════════════════════════════════════════════════════════════

def query_causal_chain(entity_name: str, depth: int = 3) -> dict:
    """多跳因果链查询 — 从实体出发，沿因果边深度遍历。

    Args:
        entity_name: 起始实体名称
        depth: 遍历深度 (1-5)

    Returns:
        {entity, causes: [], effects: [], chain: [], summary: str}
    """
    depth = min(depth, 5)
    with pool_conn() as conn:
        row = conn.execute(
            "SELECT id FROM entities WHERE LOWER(name) = LOWER(?) LIMIT 1",
            (entity_name,),
        ).fetchone()
        if not row:
            return {"entity": entity_name, "causes": [], "effects": [], "chain": [], "summary": f"未找到实体 '{entity_name}'"}

        eid = row["id"]
        visited_entities = {eid}
        all_links = []
        current = {eid}

        for hop in range(depth):
            if not current:
                break
            id_list = list(current)
            ph = ",".join("?" * len(id_list))

            triples = conn.execute(
                f"SELECT t.subject_id, t.predicate, t.object_id, t.confidence, t.source_memory_id, "
                f"s.name as subj_name, o.name as obj_name, "
                f"m.content as mem_content "
                f"FROM knowledge_triples t "
                f"JOIN entities s ON t.subject_id = s.id "
                f"JOIN entities o ON t.object_id = o.id "
                f"LEFT JOIN memories m ON t.source_memory_id = m.id "
                f"WHERE t.predicate IN ('causes','caused_by','prevents','enables','sequence','leads_to') "
                f"AND (t.subject_id IN ({ph}) OR t.object_id IN ({ph}))",
                tuple(id_list + id_list),
            ).fetchall()

            new_ids = set()
            for t in triples:
                all_links.append({
                    "cause": t["subj_name"],
                    "effect": t["obj_name"],
                    "relation": t["predicate"],
                    "confidence": t["confidence"],
                    "hop": hop + 1,
                    "source_memory_id": t["source_memory_id"],
                    "evidence": (t["mem_content"] or "")[:100],
                })
                new_ids.add(t["subject_id"])
                new_ids.add(t["object_id"])

            current = new_ids - visited_entities
            visited_entities.update(current)

        # 组织结果
        direct_causes = [l for l in all_links if l["effect"].lower() == entity_name.lower() and l["hop"] == 1]
        direct_effects = [l for l in all_links if l["cause"].lower() == entity_name.lower() and l["hop"] == 1]
        full_chain = _build_chain_text(all_links, entity_name)

        return {
            "entity": entity_name,
            "direct_causes": direct_causes,
            "direct_effects": direct_effects,
            "full_chain": all_links,
            "summary": full_chain,
            "hops": max(l["hop"] for l in all_links) if all_links else 0,
            "total_relations": len(all_links),
        }


def _build_chain_text(links: list[dict], root: str) -> str:
    """将因果链转为自然语言描述。"""
    if not links:
        return f"未找到与 '{root}' 相关的因果链"

    parts = []
    seen = set()

    # 按跳数分组
    by_hop = defaultdict(list)
    for l in links:
        by_hop[l["hop"]].append(l)

    for hop in sorted(by_hop.keys()):
        for l in by_hop[hop]:
            rel_text = {"causes": "导致", "caused_by": "由...导致", "prevents": "阻止",
                       "enables": "使得", "sequence": "之后", "leads_to": "导致"}.get(l["relation"], l["relation"])
            text = f"因为 {l['cause']}，{rel_text} {l['effect']}"
            if text not in seen:
                seen.add(text)
                parts.append(text)

    return "；".join(parts[:10])  # 最多 10 条


# ══════════════════════════════════════════════════════════════
# 4. 时序推理
# ══════════════════════════════════════════════════════════════

def query_timeline(entity_name: str, days: int = 90) -> dict:
    """时序推理 — 查询实体相关事件的时间线。

    Returns:
        {entity, events: [{date, content, level, memory_id}, ...], timeline_summary: str}
    """
    with pool_conn() as conn:
        # 1. 找到实体
        row = conn.execute(
            "SELECT id FROM entities WHERE LOWER(name) = LOWER(?) LIMIT 1",
            (entity_name,),
        ).fetchone()
        if not row:
            return {"entity": entity_name, "events": [], "timeline_summary": f"未找到实体 '{entity_name}'"}

        eid = row["id"]
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

        # 2. 找到关联此实体的记忆 (按时间排序)
        events = conn.execute(
            "SELECT m.id, m.content, m.subject, m.level, m.category, m.created_at, m.occurred_at "
            "FROM memories m "
            "JOIN memory_entities me ON m.id = me.memory_id "
            "WHERE me.entity_id = ? AND m.created_at >= ? "
            "ORDER BY m.created_at ASC",
            (eid, cutoff),
        ).fetchall()

        # 3. 找到因果链
        causal = conn.execute(
            "SELECT t.subject_id, t.predicate, t.object_id, t.created_at, "
            "s.name as subj_name, o.name as obj_name "
            "FROM knowledge_triples t "
            "JOIN entities s ON t.subject_id = s.id "
            "JOIN entities o ON t.object_id = o.id "
            "WHERE (t.subject_id = ? OR t.object_id = ?) "
            "AND t.created_at >= ? "
            "ORDER BY t.created_at",
            (eid, eid, cutoff),
        ).fetchall()

    formatted = []
    for e in events:
        formatted.append({
            "date": e["occurred_at"] or e["created_at"],
            "content": (e["content"] or "")[:200],
            "subject": e["subject"],
            "level": e["level"],
            "category": e["category"],
            "memory_id": e["id"],
        })

    # 生成时间线摘要
    summary_parts = []
    if formatted:
        summary_parts.append(f"'{entity_name}' 在 {days} 天内共有 {len(formatted)} 条相关记忆")
        if len(formatted) >= 3:
            earliest = formatted[0]["date"][:10]
            latest = formatted[-1]["date"][:10]
            summary_parts.append(f"时间范围: {earliest} 至 {latest}")

    for c in causal:
        rel_text = {"causes": "导致", "caused_by": "由...导致"}.get(c["predicate"], c["predicate"])
        summary_parts.append(f"检测到因果关系: {c['subj_name']} {rel_text} {c['obj_name']}")

    return {
        "entity": entity_name,
        "events": formatted,
        "causal_relations": [{"cause": c["subj_name"], "effect": c["obj_name"], "relation": c["predicate"]} for c in causal],
        "timeline_summary": "。".join(summary_parts) if summary_parts else f"未找到 '{entity_name}' 的事件时间线",
    }


# ══════════════════════════════════════════════════════════════
# 5. 矛盾检测
# ══════════════════════════════════════════════════════════════

def detect_contradictions(entity_name: str = "", days: int = 90) -> dict:
    """检测记忆中的矛盾 — 针对同一实体或主题的矛盾陈述。

    Args:
        entity_name: 如果指定，只检测该实体的矛盾
        days: 时间范围

    Returns:
        {contradictions: [{text_a, text_b, stance, similarity, memory_id_a, memory_id_b}, ...],
         total: int}
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    contradictions = []

    with pool_conn() as conn:
        if entity_name:
            rows = conn.execute(
                "SELECT DISTINCT m.id, m.content, m.level, m.created_at "
                "FROM memories m "
                "JOIN memory_entities me ON m.id = me.memory_id "
                "JOIN entities e ON me.entity_id = e.id "
                "WHERE LOWER(e.name) = LOWER(?) AND m.created_at >= ? "
                "ORDER BY m.created_at DESC LIMIT 100",
                (entity_name, cutoff),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, content, level, created_at FROM memories "
                "WHERE created_at >= ? AND LENGTH(TRIM(content)) > 20 "
                "ORDER BY created_at DESC LIMIT 500",
                (cutoff,),
            ).fetchall()

        # 两两比较矛盾
        for i in range(len(rows)):
            for j in range(i + 1, len(rows)):
                if rows[i]["id"] == rows[j]["id"]:
                    continue
                # 只有同 level 或同 category 才比
                if rows[i]["level"] != rows[j]["level"]:
                    continue

                text_a = (rows[i]["content"] or "")[:500]
                text_b = (rows[j]["content"] or "")[:500]
                if len(text_a) < 20 or len(text_b) < 20:
                    continue

                if _check_contradiction(text_a, text_b):
                    contradictions.append({
                        "text_a": text_a[:200],
                        "text_b": text_b[:200],
                        "memory_id_a": rows[i]["id"],
                        "memory_id_b": rows[j]["id"],
                        "level": rows[i]["level"],
                        "created_a": rows[i]["created_at"],
                        "created_b": rows[j]["created_at"],
                    })
                    if len(contradictions) >= 20:
                        break
            if len(contradictions) >= 20:
                break

    return {
        "entity": entity_name or "all",
        "contradictions": contradictions,
        "total": len(contradictions),
        "scanned": len(rows),
    }


# ══════════════════════════════════════════════════════════════
# 6. 模式发现
# ══════════════════════════════════════════════════════════════

def discover_patterns(agent_name: str = "", min_occurrences: int = 3) -> dict:
    """发现重复出现的模式 — 高频主题/因果/教训。

    Returns:
        {recurring_topics: [], causal_patterns: [], lesson_patterns: [],
         entity_cooccurrence: [], summary: str}
    """
    with pool_conn() as conn:
        where = ""
        params = []
        if agent_name:
            where = "WHERE LOWER(m.agent_name) = LOWER(?)"
            params.append(agent_name)

        # 1. 高频因果模式
        causal_patterns = conn.execute(
            f"SELECT t.predicate, s.name as subj, o.name as obj, COUNT(*) as cnt "
            f"FROM knowledge_triples t "
            f"JOIN entities s ON t.subject_id = s.id "
            f"JOIN entities o ON t.object_id = o.id "
            f"JOIN memories m ON t.source_memory_id = m.id "
            f"{where} "
            f"GROUP BY t.predicate, s.name, o.name "
            f"HAVING cnt >= ? ORDER BY cnt DESC LIMIT 10",
            *([params + [min_occurrences]]),
        ).fetchall()

        # 2. 高频共现实体
        cooccurrence = conn.execute(
            f"SELECT e1.name as e1, e2.name as e2, COUNT(*) as cnt "
            f"FROM memory_entities me1 "
            f"JOIN memory_entities me2 ON me1.memory_id = me2.memory_id AND me1.entity_id < me2.entity_id "
            f"JOIN entities e1 ON me1.entity_id = e1.id "
            f"JOIN entities e2 ON me2.entity_id = e2.id "
            f"JOIN memories m ON me1.memory_id = m.id "
            f"{where} "
            f"GROUP BY e1.name, e2.name "
            f"HAVING cnt >= ? ORDER BY cnt DESC LIMIT 10",
            *([params + [min_occurrences]]),
        ).fetchall()

        # 3. 高频教训模式 (L6/L7 关键词)
        lesson_words = Counter()
        lesson_params = []
        level_filter = "WHERE level IN ('L6','L7')"
        if agent_name:
            level_filter += " AND LOWER(agent_name) = LOWER(?)"
            lesson_params.append(agent_name)
        try:
            lessons = conn.execute(
                f"SELECT content FROM memories {level_filter} LIMIT 200",
                lesson_params,
            ).fetchall()
            for r in lessons:
                for word in re.findall(r'[一-鿿]{2,6}', r["content"] or ""):
                    lesson_words[word] += 1
        except Exception:
            logger.warning("discover_patterns: lesson scan failed", exc_info=True)

        top_lesson_words = [{"word": w, "count": c} for w, c in lesson_words.most_common(10) if c >= min_occurrences]

    return {
        "causal_patterns": [{"cause": r["subj"], "effect": r["obj"], "relation": r["predicate"], "count": r["cnt"]}
                          for r in causal_patterns],
        "entity_cooccurrence": [{"entity_a": r["e1"], "entity_b": r["e2"], "count": r["cnt"]}
                               for r in cooccurrence],
        "lesson_keywords": top_lesson_words,
        "summary": f"发现 {len(causal_patterns)} 个因果模式, {len(cooccurrence)} 个实体共现模式, {len(top_lesson_words)} 个高频教训词",
    }


# ══════════════════════════════════════════════════════════════
# 7. 自然语言查询入口
# ══════════════════════════════════════════════════════════════

def query_reason(query: str) -> dict:
    """自然语言推理查询 — 自动理解查询意图并返回推理结果。

    支持:
    - "为什么 X" → 因果链查询
    - "X 和 Y 有什么关系" → 关系查询
    - "X 的时间线" → 时序查询
    - "X 有什么矛盾" → 矛盾检测
    - "X 的模式" → 模式发现

    Args:
        query: 自然语言查询

    Returns:
        {query, type, result, summary}
    """
    entities = extract_entities(query)
    entity_names = [e["name"] for e in entities]

    # 1. 检测查询类型
    if re.search(r"(?:为什么|为何|怎么|原因|理由|how|why)", query):
        qtype = "causal"
        target = entity_names[0] if entity_names else query
        result = query_causal_chain(target, depth=3)
        return {
            "query": query,
            "type": qtype,
            "target": target,
            "result": result,
            "summary": result.get("summary", f"未找到与 '{target}' 相关的因果链"),
        }

    elif re.search(r"(?:关系|关联|联系|relation|connection|between)", query):
        qtype = "relation"
        if len(entity_names) >= 2:
            a, b = entity_names[0], entity_names[1]
            chain_a = query_causal_chain(a, depth=2)
            chain_b = query_causal_chain(b, depth=2)
            # 找共同链接
            common = []
            for link_a in chain_a.get("full_chain", []):
                for link_b in chain_b.get("full_chain", []):
                    if link_a["cause"].lower() == link_b["cause"].lower() or \
                       link_a["effect"].lower() == link_b["effect"].lower():
                        common.append(link_a)
            result = {
                "entity_a": a,
                "entity_b": b,
                "chain_a": chain_a,
                "chain_b": chain_b,
                "common_links": common[:5],
            }
            return {
                "query": query,
                "type": qtype,
                "target": f"{a} ↔ {b}",
                "result": result,
                "summary": f"'{a}' 和 '{b}' 之间存在 {len(common)} 个共同因果链接",
            }
        else:
            target = entity_names[0] if entity_names else query
            result = query_causal_chain(target, depth=2)
            return {"query": query, "type": "relation", "target": target, "result": result,
                    "summary": result.get("summary", "")}

    elif re.search(r"(?:时间线|时间|timeline|历史|history|发展|演变)", query):
        qtype = "timeline"
        target = entity_names[0] if entity_names else query
        result = query_timeline(target, days=90)
        return {
            "query": query,
            "type": qtype,
            "target": target,
            "result": result,
            "summary": result.get("timeline_summary", ""),
        }

    elif re.search(r"(?:矛盾|冲突|contradict|conflict|不一致)", query):
        qtype = "contradiction"
        target = entity_names[0] if entity_names else ""
        result = detect_contradictions(target, days=90)
        return {
            "query": query,
            "type": qtype,
            "target": target or "all",
            "result": result,
            "summary": f"发现 {result['total']} 条矛盾记忆（扫描 {result['scanned']} 条）",
        }

    elif re.search(r"(?:模式|pattern|趋势|规律|重复)", query):
        qtype = "pattern"
        target = entity_names[0] if entity_names else ""
        result = discover_patterns(target, min_occurrences=2)
        return {
            "query": query,
            "type": qtype,
            "target": target or "all",
            "result": result,
            "summary": result.get("summary", ""),
        }

    else:
        # 默认：尝试因果链
        qtype = "auto"
        target = entity_names[0] if entity_names else query
        result = query_causal_chain(target, depth=2)
        return {
            "query": query,
            "type": qtype,
            "target": target,
            "result": result,
            "summary": result.get("summary", f"查询 '{query}' 未能找到匹配的推理结果"),
        }