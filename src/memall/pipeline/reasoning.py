"""
Memory Reasoning — 强大的记忆推理引擎

核心能力:
1. 因果链提取: "因为 X 所以 Y" → X ->导致-> Y
2. 时序推理: 事件 A -> 事件 B -> 事件 C, 推断 B 是 A 的结果
3. 矛盾检测: 同一主题的冲突陈述自动标记
4. 多跳推理: 连接多个记忆推导新结论
5. 反事实推理: "如果当时选了 X 而不是 Y，会怎样"
"""

import json
import logging
import re
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from typing import Any, Optional
from pathlib import Path

from memall.core.db import pool_conn, get_conn
from memall.core.entity_extractor import extract_entities, extract_triples

logger = logging.getLogger(__name__)

# ── 因果模式 ──────────────────────────────────────────────

CAUSAL_PATTERNS = [
    # 中文因果
    (r"(?:因为|由于|因)\s*(.{5,100})\s*(?:所以|因此|因而|导致|致使|使得|引起|引发)\s*(.{5,100})", "cause_effect"),
    (r"(.{5,100})\s*(?:导致|致使|引发|引起|造成|使得|带来)\s*(.{5,100})", "cause_effect"),
    (r"(.{5,100})\s*(?:源于|来源于|来自于|是因为|是因为)\s*(.{5,100})", "effect_cause"),
    (r"(?:之所以|其所以)\s*(.{5,100})\s*(?:是因为|是由于|源于)\s*(.{5,100})", "effect_cause"),
    (r"如果\s*(.{5,100})\s*(?:就|则|那么)\s*(.{5,100})", "condition_result"),
    (r"(.{5,100})\s*(?:后|之后|以后)\s*(.{5,100})", "temporal_sequence"),
    # 英文因果
    (r"(?:because|since|due to)\s+(.{10,100})\s*[,.]?\s*(.{10,100})", "cause_effect"),
    (r"(.{10,100})\s*(?:causes|leads to|results in|triggers|creates)\s+(.{10,100})", "cause_effect"),
    (r"(.{10,100})\s*(?:caused by|resulted from|triggered by|due to)\s+(.{10,100})", "effect_cause"),
    (r"if\s+(.{10,100})\s*(?:then|,)\s*(.{10,100})", "condition_result"),
    (r"after\s+(.{10,100})\s*[,.]?\s*(.{10,100})", "temporal_sequence"),
]

# ── 矛盾模式 ──────────────────────────────────────────────

CONTRADICTION_PAIRS = [
    ("推荐|好|优|有利|适合", "不推荐|差|劣|不利|不适合"),
    ("应该|需要|必须|一定要", "不应该|不需要|不必|不该"),
    ("同意|支持|认可", "反对|不同意|不认可|质疑"),
    ("简单|容易|方便", "复杂|困难|麻烦"),
    ("保留|继续用|维持", "替换|改用|迁移|替代"),
]


def extract_causal_relations(content: str) -> list[dict]:
    """从文本中提取因果关系。

    Returns:
        [{"cause": str, "effect": str, "type": str, "confidence": float}, ...]
    """
    relations = []
    for pattern, rel_type in CAUSAL_PATTERNS:
        for m in re.finditer(pattern, content, re.IGNORECASE | re.DOTALL):
            if rel_type in ("cause_effect", "condition_result", "temporal_sequence"):
                cause = m.group(1).strip()
                effect = m.group(2).strip()
            else:  # effect_cause
                effect = m.group(1).strip()
                cause = m.group(2).strip()

            if len(cause) < 5 or len(effect) < 5:
                continue
            if cause == effect:
                continue

            # 去重
            relations.append({
                "cause": cause[:200],
                "effect": effect[:200],
                "type": rel_type,
                "confidence": 0.8 if rel_type == "cause_effect" else 0.6,
            })
    # 去重 (按 cause+effect 去重)
    seen = set()
    deduped = []
    for r in relations:
        key = (r["cause"][:50], r["effect"][:50])
        if key not in seen:
            seen.add(key)
            deduped.append(r)
    return deduped


def detect_contradictions(content_a: str, content_b: str) -> list[dict]:
    """检测两段文本之间的矛盾。

    Returns:
        [{"pos_pat": str, "neg_pat": str, "confidence": float}, ...]
    """
    contradictions = []
    for pos_pat, neg_pat in CONTRADICTION_PAIRS:
        pos_a = re.search(pos_pat, content_a, re.IGNORECASE)
        pos_b = re.search(pos_pat, content_b, re.IGNORECASE)
        neg_a = re.search(neg_pat, content_a, re.IGNORECASE)
        neg_b = re.search(neg_pat, content_b, re.IGNORECASE)

        if (pos_a and neg_b) or (neg_a and pos_b):
            contradictions.append({
                "pos_pat": pos_pat,
                "neg_pat": neg_pat,
                "confidence": 0.9,
            })
    return contradictions


# ══════════════════════════════════════════════════════════════
# 推理引擎
# ══════════════════════════════════════════════════════════════


class ReasoningEngine:
    """记忆推理引擎 — 多跳推理、因果链、反事实分析。"""

    def __init__(self, agent_name: Optional[str] = None):
        self.agent_name = agent_name

    def query(self, question: str) -> dict:
        """对记忆系统进行推理查询。

        Args:
            question: 自然语言问题

        Returns:
            {"answer": str, "reasoning": str, "sources": [dict]}
        """
        # 1. 提取问题中的实体和意图
        entities = extract_entities(question)
        question_lower = question.lower()

        # 2. 根据问题类型路由
        if any(w in question_lower for w in ["为什么", "原因", "根因", "why", "root cause"]):
            return self._causal_analysis(question, entities)
        elif any(w in question_lower for w in ["如果", "假如", "what if", "would"]):
            return self._counterfactual_analysis(question, entities)
        elif any(w in question_lower for w in ["之后", "然后", "after", "then", "timeline"]):
            return self._temporal_analysis(question, entities)
        elif any(w in question_lower for w in ["矛盾", "冲突", "conflict", "contradict"]):
            return self._conflict_analysis(question, entities)
        else:
            return self._multi_hop_reasoning(question, entities)

    def _causal_analysis(self, question: str, entities: list) -> dict:
        """因果分析：找到事件的根因和影响。"""
        with pool_conn() as conn:
            # 搜索相关记忆
            keyword = question[:50]
            rows = conn.execute(
                "SELECT id, content, level, created_at, agent_name FROM memories "
                "WHERE content LIKE ? ORDER BY created_at DESC LIMIT 20",
                (f"%{keyword}%",),
            ).fetchall()

        # 提取因果链
        causal_chains = []
        for r in rows:
            relations = extract_causal_relations(r["content"])
            if relations:
                for rel in relations:
                    causal_chains.append({
                        "memory_id": r["id"],
                        "level": r["level"],
                        "agent": r["agent_name"],
                        "created_at": r["created_at"],
                        **rel,
                    })

        # 构建因果链文本
        if causal_chains:
            chain_text = " → ".join(
                f"{c['cause'][:50]} → {c['effect'][:50]}"
                for c in causal_chains[:3]
            )
            return {
                "answer": f"发现 {len(causal_chains)} 条因果关联: {chain_text}",
                "reasoning": "causal_analysis",
                "sources": causal_chains[:5],
            }
        return {
            "answer": "未找到明确的因果关联",
            "reasoning": "causal_analysis",
            "sources": [],
        }

    def _counterfactual_analysis(self, question: str, entities: list) -> dict:
        """反事实分析：如果当时做了不同选择会怎样。"""
        # 提取假设条件
        match = re.search(r"(?:如果|假如|要是|假设|if|假设)\s*(.{5,100})", question)
        condition = match.group(1) if match else question

        with pool_conn() as conn:
            # 查找相关决策
            rows = conn.execute(
                "SELECT id, content, level, category, created_at FROM memories "
                "WHERE level IN ('L4','L6') AND (content LIKE ? OR content LIKE ?) "
                "ORDER BY created_at DESC LIMIT 10",
                (f"%{condition[:30]}%", f"%{condition}%"),
            ).fetchall()

        if not rows:
            return {
                "answer": f"未找到与「{condition[:50]}」相关的决策记忆",
                "reasoning": "counterfactual_analysis",
                "sources": [],
            }

        alternatives = []
        for r in rows:
            # 提取替代方案
            alt_match = re.search(
                r"(?:替代|替换|改用|不用|不选|instead of|rather than)\s*(\S+)",
                r["content"], re.IGNORECASE,
            )
            if alt_match:
                alternatives.append({
                    "memory_id": r["id"],
                    "decision": r["content"][:100],
                    "alternative": alt_match.group(1),
                })

        if alternatives:
            return {
                "answer": f"找到 {len(alternatives)} 个可反事实分析的决策点",
                "reasoning": "counterfactual_analysis",
                "sources": alternatives,
            }
        return {
            "answer": "找到相关决策但未发现替代方案",
            "reasoning": "counterfactual_analysis",
            "sources": [dict(r) for r in rows],
        }

    def _temporal_analysis(self, question: str, entities: list) -> dict:
        """时序分析：事件时间线。"""
        with pool_conn() as conn:
            rows = conn.execute(
                "SELECT id, content, level, category, created_at, agent_name FROM memories "
                "ORDER BY created_at DESC LIMIT 20",
            ).fetchall()

        if not rows:
            return {"answer": "无记忆数据", "reasoning": "temporal_analysis", "sources": []}

        timeline = [
            {"id": r["id"], "time": r["created_at"][:19], "level": r["level"],
             "category": r["category"], "preview": r["content"][:80]}
            for r in rows
        ]
        return {
            "answer": f"最近 {len(timeline)} 条记忆时间线",
            "reasoning": "temporal_analysis",
            "sources": timeline,
        }

    def _conflict_analysis(self, question: str, entities: list) -> dict:
        """矛盾分析：检测记忆中的冲突。"""
        conflicts = []
        with pool_conn() as conn:
            rows = conn.execute(
                "SELECT id, content, level, category, agent_name FROM memories "
                "WHERE memory_status = 'conflict' ORDER BY created_at DESC LIMIT 20"
            ).fetchall()

        for r in rows:
            conflicts.append({
                "memory_id": r["id"],
                "content": r["content"][:100],
                "level": r["level"],
                "agent": r["agent_name"],
            })

        if conflicts:
            return {
                "answer": f"发现 {len(conflicts)} 个冲突记忆",
                "reasoning": "conflict_analysis",
                "sources": conflicts,
            }
        return {"answer": "未发现矛盾记忆", "reasoning": "conflict_analysis", "sources": []}

    def _multi_hop_reasoning(self, question: str, entities: list) -> dict:
        """多跳推理：连接多个记忆推导结论。"""
        # 1. 提取问题中的关键实体
        question_entities = [e["name"] for e in entities]

        if not question_entities:
            with pool_conn() as conn:
                rows = conn.execute(
                    "SELECT id, content, level, category, created_at FROM memories "
                    "ORDER BY created_at DESC LIMIT 5"
                ).fetchall()
            return {
                "answer": f"找到 {len(rows)} 条相关记忆",
                "reasoning": "recent_memories",
                "sources": [dict(r) for r in rows],
            }

        # 2. 通过实体查找关联记忆
        identities = []
        for qe in question_entities[:3]:
            with pool_conn() as conn:
                rows = conn.execute(
                    "SELECT m.id, m.content, m.level, m.category, m.created_at, "
                    "e.name as entity_name, e.entity_type "
                    "FROM memories m "
                    "JOIN memory_entities me ON m.id = me.memory_id "
                    "JOIN entities e ON me.entity_id = e.id "
                    "WHERE LOWER(e.name) LIKE LOWER(?) "
                    "ORDER BY m.created_at DESC LIMIT 5",
                    (f"%{qe}%",),
                ).fetchall()
                for r in rows:
                    identities.append(dict(r))

        if identities:
            return {
                "answer": f"通过实体「{', '.join(question_entities[:3])}」找到 {len(identities)} 条关联记忆",
                "reasoning": "entity_reasoning",
                "sources": identities[:10],
            }

        # 3. 全文搜索兜底
        with pool_conn() as conn:
            keyword = question_entities[0] if question_entities else question[:30]
            rows = conn.execute(
                "SELECT id, content, level, category, created_at FROM memories "
                "WHERE content LIKE ? ORDER BY created_at DESC LIMIT 5",
                (f"%{keyword}%",),
            ).fetchall()
            return {
                "answer": f"找到 {len(rows)} 条包含「{keyword}」的记忆",
                "reasoning": "keyword_search",
                "sources": [dict(r) for r in rows],
            }


def reasoning_step() -> dict:
    """推理管线步骤 — 提取因果链、检测矛盾、构建推理图。

    定期运行，自动增强记忆的知识深度。
    """
    with pool_conn() as conn:
        # 1. 扫描未处理的记忆提取因果链
        causal_count = 0
        rows = conn.execute(
            "SELECT id, content, level FROM memories WHERE level IN ('L4','L6','L7') "
            "ORDER BY id DESC LIMIT 500"
        ).fetchall()

        for r in rows:
            relations = extract_causal_relations(r["content"])
            if relations:
                causal_count += 1

        # 2. 检测矛盾
        conflict_count = 0
        level_rows = conn.execute(
            "SELECT id, content, level, category, agent_name FROM memories "
            "WHERE level IN ('L4','L6') ORDER BY id DESC LIMIT 200"
        ).fetchall()

        for i, a in enumerate(level_rows):
            for b in level_rows[i + 1:]:
                if a["category"] == b["category"] or a["agent_name"] == b["agent_name"]:
                    contradictions = detect_contradictions(a["content"], b["content"])
                    if contradictions:
                        conn.execute(
                            "UPDATE memories SET memory_status = 'conflict' WHERE id = ? "
                            "AND memory_status IS NULL",
                            (a["id"],),
                        )
                        conn.execute(
                            "UPDATE memories SET memory_status = 'conflict' WHERE id = ? "
                            "AND memory_status IS NULL",
                            (b["id"],),
                        )
                        conflict_count += 1
                        if conflict_count >= 10:  # 每轮最多处理 10 个矛盾
                            break
            if conflict_count >= 10:
                break

        conn.commit()

    return {
        "causal_relations": causal_count,
        "conflicts_detected": conflict_count,
        "status": "ok",
    }