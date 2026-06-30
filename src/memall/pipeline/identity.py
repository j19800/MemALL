"""
Identity/preference extraction step (L1/L7).

Scans all memories for identity (L1: who I am) and preference (L7: what I like)
signals. Extracts relevant sentences and stores them in identities.profile_json,
bypassing the classify_step bottleneck (which only scans 'general' category).
"""

import json
import re
from datetime import datetime, timezone
from collections import defaultdict
from memall.core.db import get_conn

# L1 identity signals: self-description, role, skill, habit
_L1_PATTERNS = [
    (r'(?:我是|我叫|本人)[：:\s]*(.{5,60})', 'identity_statement'),
    (r'(?:我从事|我担任|我的角色|我的职位|我的职业)[：:\s]*(.{5,60})', 'role'),
    (r'(?:我擅长|我精通|我的优势|我的能力|我的技能|我会|我能|熟悉)[：:\s]*(.{5,60})', 'skill'),
    (r'(?:我的习惯|我习惯|我经常|我每周|我每天|我通常)[：:\s]*(.{5,60})', 'habit'),
    (r'(?:我认为|我相信|我的理念|我的原则|我看重|我的价值观)[：:\s]*(.{5,60})', 'belief'),
    (r'(?:我住在|我来自|我的家乡|我的背景|我的经历|我毕业于)[：:\s]*(.{5,60})', 'background'),
    (r'(?:我的生日|我的年龄|我出生于|生于)[：:\s]*(\d{4}.\d{1,2}.\d{1,2})', 'birth'),
    (r'(?:我叫|name|email|phone|contact)[：:\s]*(\S{2,60})', 'contact'),
]

# L7 preference signals: likes, dislikes, preferences
_L7_PATTERNS = [
    (r'(?:我喜欢|我偏好|我倾向于|我更[^过]|我更喜欢|偏爱)[：:\s]*(.{5,80})', 'preference'),
    (r'(?:我习惯用|我常用|我用得[多顺]|我主要用)[：:\s]*(.{5,60})', 'tool_preference'),
    (r'(?:我觉得更好|更方便|更高效|更舒服|更合适)[：:\s]*(.{5,60})', 'ergonomic'),
    (r'(?:我不喜欢|我排斥|我避免|我不用|我讨厌)[：:\s]*(.{5,80})', 'dislike'),
    (r'(?:使用场景|适用场景|主要用于)[：:\s]*(.{5,80})', 'use_case'),
    (r'(?:推荐|建议用|更推荐|优先选择)[：:\s]*(.{5,80})', 'recommendation'),
]


def _extract_matches(text: str, patterns: list) -> list:
    """Extract all (pattern_type, snippet) matches from text using relaxed matching."""
    results = []
    for pat, label in patterns:
        for m in re.finditer(pat, text[:8000]):  # limit to first 8000 chars
            snippet = m.group(1).strip()[:100]
            if snippet and len(snippet) > 3:
                results.append({"type": label, "snippet": snippet})
    return results


def identity_step() -> dict:
    """Scan all memories for L1/L7 signals, write extracted traits to identities."""
    conn = get_conn()
    try:
        now = datetime.now(timezone.utc).isoformat()
        rows = conn.execute(
            "SELECT id, content, agent_name, level FROM memories WHERE LENGTH(TRIM(content)) > 20 ORDER BY id"
        ).fetchall()

        # Per-agent trait accumulation
        agent_traits: dict = defaultdict(lambda: {"l1": [], "l7": [], "memory_ids": []})

        for r in rows:
            text = r["content"] or ""
            agent = r["agent_name"] or "unknown"
            l1_matches = _extract_matches(text, _L1_PATTERNS)
            l7_matches = _extract_matches(text, _L7_PATTERNS)

            if l1_matches or l7_matches:
                agent_traits[agent]["l1"].extend(l1_matches)
                agent_traits[agent]["l7"].extend(l7_matches)
                agent_traits[agent]["memory_ids"].append(r["id"])

                # Upgrade existing memory to L1 or L7 if not already terminal
                if l1_matches and r["level"] not in ("L3", "L4", "L5", "L6", "L9", "L10", "L11"):
                    conn.execute("UPDATE memories SET level = 'L1', updated_at = ? WHERE id = ?", (now, r["id"]))
                elif l7_matches and not l1_matches and r["level"] not in ("L3", "L4", "L5", "L6", "L9", "L10", "L11"):
                    conn.execute("UPDATE memories SET level = 'L7', updated_at = ? WHERE id = ?", (now, r["id"]))

        # Write aggregated traits to identities.profile_json
        updated_agents = 0
        for agent, traits in agent_traits.items():
            # Dedup by snippet
            seen = set()
            unique_l1 = []
            for t in traits["l1"]:
                if t["snippet"] not in seen:
                    seen.add(t["snippet"])
                    unique_l1.append(t)
            seen = set()
            unique_l7 = []
            for t in traits["l7"]:
                if t["snippet"] not in seen:
                    seen.add(t["snippet"])
                    unique_l7.append(t)

            # Read existing profile
            row = conn.execute(
                "SELECT profile_json FROM identities WHERE LOWER(agent_name) = LOWER(?)", (agent,)
            ).fetchone()
            profile = {}
            if row and row["profile_json"]:
                try:
                    profile = json.loads(row["profile_json"])
                except (json.JSONDecodeError, TypeError):
                    profile = {}

            # ID3: Merge with existing — keep old entries, append new, dedup, cap at 20
            existing_l1 = profile.get("l1_identity", [])
            existing_l7 = profile.get("l7_preferences", [])
            seen_l1 = {t["snippet"] for t in existing_l1}
            seen_l7 = {t["snippet"] for t in existing_l7}
            for t in unique_l1:
                if t["snippet"] not in seen_l1:
                    existing_l1.append(t)
                    seen_l1.add(t["snippet"])
            for t in unique_l7:
                if t["snippet"] not in seen_l7:
                    existing_l7.append(t)
                    seen_l7.add(t["snippet"])
            profile["l1_identity"] = existing_l1[:20]
            profile["l7_preferences"] = existing_l7[:20]
            profile["l1l7_updated_at"] = now

            if row:
                conn.execute(
                    "UPDATE identities SET identity_profile = ?, persona_updated_at = ? WHERE LOWER(agent_name) = LOWER(?)",
                    (json.dumps(profile, ensure_ascii=False), now, agent),
                )
            else:
                conn.execute(
                    "INSERT INTO identities (agent_name, agent_type, identity_profile, persona_updated_at, last_heartbeat) VALUES (?, 'ai', ?, ?, ?)",
                    (agent, json.dumps(profile, ensure_ascii=False), now, now),
                )
            updated_agents += 1

        conn.commit()
        return {
            "scanned": len(rows),
            "agents_with_traits": updated_agents,
            "l1_extracted": sum(len(t["l1"]) for t in agent_traits.values()),
            "l7_extracted": sum(len(t["l7"]) for t in agent_traits.values()),
        }
    finally:
        conn.close()
