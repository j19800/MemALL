"""
Agent routing — 能力感知的参与者推荐。

解决 MemALL 多 Agent 协作中的"该请谁参与"问题：过去 create_discussion
的 participants 全靠手动指定，没有数据支撑。本模块基于每个 agent 的
身份画像（identities.profile_json 中的 L1 身份 / L7 偏好）+ 历史讨论参与度，
用 TF-IDF 余弦相似度计算与讨论主题的匹配度，给出可解释的推荐名单。

设计原则：纯只读建议函数，不修改任何状态；作为 create_discussion 的可选
增强与独立 MCP 工具存在，绝不强制自动邀请（保持行为向后兼容）。
"""

import json
import logging
from typing import Optional

from memall.core.db import get_conn
from memall.core.nlp import tokenize, compute_tfidf, cosine_sim, tokenize_cjk

logger = logging.getLogger(__name__)

# 人类 owner 通常不需要被"推荐"为技术讨论参与者；但可作为兜底保留。
_HUMAN_TYPES = {"human"}


def _agent_profile_text(profile_json: str) -> str:
    """Flatten an agent's identity/preference signals into a single text blob."""
    try:
        profile = json.loads(profile_json or "{}")
    except (json.JSONDecodeError, TypeError):
        return ""
    parts: list[str] = []
    for t in profile.get("l1_identity", []):
        if isinstance(t, dict):
            parts.append(t.get("snippet", ""))
        elif isinstance(t, str):
            parts.append(t)
    for t in profile.get("l7_preferences", []):
        if isinstance(t, dict):
            parts.append(t.get("snippet", ""))
        elif isinstance(t, str):
            parts.append(t)
    return " ".join(p for p in parts if p)


def _participation_boost(conn, agent_name: str) -> int:
    """Count how many discussions this agent has historically responded to.

    Agents with a track record of contributing get a small relevance boost,
    so the router favours experienced participants for a topic it is relevant to.
    """
    try:
        row = conn.execute(
            "SELECT COUNT(DISTINCT e.source_id) AS c FROM edges e "
            "JOIN memories m ON m.id = e.target_id "
            "WHERE m.agent_name = ? AND m.level = 'P2' "
            "AND m.category = 'discussion_response'",
            (agent_name,),
        ).fetchone()
        return row["c"] if row else 0
    except Exception:
        return 0


def suggest_participants(
    title: str,
    background: str = "",
    options: Optional[list] = None,
    k: int = 3,
    exclude: Optional[list] = None,
    conn=None,
) -> list[dict]:
    """Recommend agents to invite to a discussion, ranked by topic relevance.

    Scoring blends two signals:
      1. Profile relevance — TF-IDF cosine between the discussion topic text and
         the agent's identity/preference profile (semantic match to its expertise).
      2. Participation track record — a small additive boost for agents that have
         historically engaged (经验权重).

    Args:
        title: Discussion title.
        background: Problem description / facts.
        options: Candidate solution options.
        k: Max number of recommendations to return.
        exclude: Agent names to never recommend (e.g. the creator).
        conn: Optional open connection (for test injection).

    Returns:
        List of ``{"agent": str, "score": float, "reason": str}`` sorted by score
        descending. Only agents with a positive relevance score are returned.
    """
    topic_text = " ".join(
        [title or "", background or "", " ".join(options or [])]
    ).strip()
    if not topic_text:
        return []

    own_conn = conn is None
    if own_conn:
        conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT agent_name, agent_type, profile_json FROM identities"
        ).fetchall()

        # Build the corpus: [topic] + [profile_text per agent]
        profiles = []
        agents = []
        agent_types = {}
        valid = []
        for r in rows:
            agent = r["agent_name"]
            if exclude and agent in exclude:
                continue
            ptext = _agent_profile_text(r["profile_json"] or "")
            if not ptext.strip():
                continue  # skip agents with no profile signal
            profiles.append(ptext)
            agents.append(agent)
            agent_types[agent] = (r["agent_type"] or "ai").lower()
            valid.append(r)

        if not profiles:
            return []

        docs = [topic_text] + profiles
        try:
            # tokenize_cjk adds character bigrams so Chinese topics match
            # Chinese agent profiles on shared 2-grams (e.g. "文档", "技术").
            tfidf = compute_tfidf(docs, tokenizer=tokenize_cjk)
            topic_vec = tfidf[0]
        except Exception:
            return []

        scored = []
        for i, agent in enumerate(agents):
            sim = cosine_sim(topic_vec, tfidf[i + 1])
            if sim <= 0:
                continue
            boost = min(_participation_boost(conn, agent), 5) * 0.01
            score = round(sim + boost, 4)
            reason = (
                "身份/偏好与主题相关" + ("（历史参与度加成）" if boost else "")
            )
            scored.append({"agent": agent, "score": score, "reason": reason})

        scored.sort(key=lambda x: x["score"], reverse=True)
        return scored[:k]
    finally:
        if own_conn:
            conn.close()
