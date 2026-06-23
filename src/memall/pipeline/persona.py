import json
import math
from datetime import datetime, timezone, timedelta
from collections import Counter
from memall.core.db import get_conn

COLORS = {
    "white": {"name": "白", "meaning": "结构"},
    "blue": {"name": "蓝", "meaning": "理解"},
    "black": {"name": "黑", "meaning": "行动力"},
    "red": {"name": "红", "meaning": "强度"},
    "green": {"name": "绿", "meaning": "连接"},
}

PROTOTYPES = {
    ("white",): {"en": "Anchor", "cn": "定锚"},
    ("blue",): {"en": "Rationalist", "cn": "理性者"},
    ("black",): {"en": "Maverick", "cn": "独行者"},
    ("red",): {"en": "Spark", "cn": "烈焰"},
    ("green",): {"en": "Weaver", "cn": "织网者"},
    ("white", "blue"): {"en": "Arbiter", "cn": "裁决者"},
    ("blue", "white"): {"en": "Magistrate", "cn": "衡鉴者"},
    ("white", "black"): {"en": "Custodian", "cn": "镇守者"},
    ("black", "white"): {"en": "Enforcer", "cn": "执律者"},
    ("white", "red"): {"en": "Herald", "cn": "宣道者"},
    ("red", "white"): {"en": "Crusader", "cn": "征伐者"},
    ("white", "green"): {"en": "Warden", "cn": "庇护者"},
    ("green", "white"): {"en": "Shepherd", "cn": "牧领者"},
    ("blue", "black"): {"en": "Strategist", "cn": "策略家"},
    ("black", "blue"): {"en": "Operator", "cn": "实操者"},
    ("blue", "green"): {"en": "Oracle", "cn": "洞见者"},
    ("green", "blue"): {"en": "Northstar", "cn": "引航者"},
    ("red", "blue"): {"en": "Innovator", "cn": "创想家"},
    ("blue", "red"): {"en": "Sparkmind", "cn": "灵焰"},
    ("black", "red"): {"en": "Vanguard", "cn": "先锋"},
    ("red", "black"): {"en": "Conqueror", "cn": "征服者"},
    ("black", "green"): {"en": "Founder", "cn": "奠基者"},
    ("green", "black"): {"en": "Coordinator", "cn": "统合者"},
    ("red", "green"): {"en": "Freeborn", "cn": "率性者"},
    ("green", "red"): {"en": "Wanderer", "cn": "寻归者"},
}

CERTAIN_KEYWORDS = ["必须", "一定", "确定", "结论是", "最终方案", "就这样"]
UNCERTAIN_KEYWORDS = ["也许", "可能", "考虑", "暂时", "先试试", "不确定", "待定"]
DECISION_KEYWORDS = ["决定", "选", "采用", "方案", "结论", "定为", "用", "选型"]


def extract_features(agent_name: str, time_range: str = "all") -> dict:
    """Extract cognitive features for an agent.

    Args:
        agent_name: Name of the agent to analyze.
        time_range: ``"all"`` (all memories) or ``"recent_7d"`` (last 7 days)
                    or ``"recent_Nd"`` where N is a number of days.

    Returns dict of feature scores, or ``{"error": ...}`` if no memories found.
    """
    conn = get_conn()
    try:
        # Time filter for dynamic profile
        time_filter = ""
        if time_range and time_range != "all":
            days = 7
            if time_range.startswith("recent_"):
                try:
                    days = int(time_range.replace("recent_", "").replace("d", ""))
                except ValueError:
                    days = 7
            time_filter = f" AND created_at > datetime('now', '-{days} days')"

        rows = conn.execute(
            "SELECT id, content, category, occurred_at, created_at, updated_at, level FROM memories "
            "WHERE LOWER(agent_name) = LOWER(?)" + time_filter + " ORDER BY created_at",
            (agent_name,),
        ).fetchall()
        if not rows:
            return {"error": f"no memories for {agent_name}", "sample_size": 0}

        n = len(rows)
        timestamps = [_parse_ts(r["created_at"]) for r in rows if r["created_at"]]
        timestamps = [t for t in timestamps if t is not None]
        timestamps.sort()

        # L1/L7 identity signal: count of identity and preference memories
        l1_count = sum(1 for r in rows if r["level"] == "L1")
        l7_count = sum(1 for r in rows if r["level"] == "L7")
        identity_signal = min(1.0, (l1_count + l7_count) / 5.0)  # normalized: 5+ L1/L7 → 1.0
        # L6 reflection signal (第三刀 第10点)
        l6_count = sum(1 for r in rows if r["level"] == "L6")
        l6_signal = min(1.0, l6_count / 10.0)  # normalized: 10 L6 → 1.0

        categories = [r["category"] or "" for r in rows]
        contents = [r["content"] for r in rows if r["content"]]

        cat_counter = Counter(categories)
        domain_breadth = len([c for c in cat_counter if c])

        if timestamps and len(timestamps) > 1:
            intervals = [(timestamps[i + 1] - timestamps[i]).total_seconds() for i in range(len(timestamps) - 1)]
            total_span_days = max(1, (timestamps[-1] - timestamps[0]).total_seconds() / 86400)
            capture_frequency = n / total_span_days if total_span_days > 0 else n
            burst_count = sum(1 for iv in intervals if iv < 300)
            burst_ratio = burst_count / len(intervals) if intervals else 0
            capture_regularity = _time_entropy(timestamps)
        else:
            capture_frequency = n if n > 0 else 0
            burst_ratio = 0
            capture_regularity = 1.0

        cat_depths = {}
        for c, cnt in cat_counter.most_common(10):
            if c:
                avgs = conn.execute(
                    "SELECT AVG(LENGTH(content)) FROM memories WHERE LOWER(agent_name) = LOWER(?) AND category = ?",
                    (agent_name, c),
                ).fetchone()[0]
                cat_depths[c] = {"count": cnt, "avg_length": avgs or 0}
        domain_depth = sum(d["count"] * min(1, d["avg_length"] / 200) for d in cat_depths.values()) / max(1, len(cat_depths))

        if timestamps and len(timestamps) > 1:
            recent_30d = timestamps[-1] - timedelta(days=30)
            recent_cats = set()
            old_cats = set()
            for i, r in enumerate(rows):
                ts = _parse_ts(r["created_at"])
                if ts:
                    cat = r["category"] or ""
                    if ts >= recent_30d:
                        recent_cats.add(cat)
                    else:
                        old_cats.add(cat)
            new_domain_rate = len(recent_cats - old_cats) / max(1, len(recent_cats)) if recent_cats else 0
        else:
            new_domain_rate = 0

        knowledge_gap = sum(1 for c in categories if not c) / max(1, n)

        edge_counts = conn.execute(
            "SELECT relation_type, COUNT(*) as cnt FROM edges WHERE source_id IN (SELECT id FROM memories WHERE LOWER(agent_name) = LOWER(?)) GROUP BY relation_type",
            (agent_name,),
        ).fetchall()
        edge_dict = {r["relation_type"]: r["cnt"] for r in edge_counts}
        contradiction_count = edge_dict.get("contradicts", 0)
        derived_count = edge_dict.get("derived_from", 0)

        contradiction_rows = conn.execute(
            "SELECT e.source_id, e.target_id, e.metadata FROM edges e WHERE e.relation_type = 'contradicts' AND e.source_id IN (SELECT id FROM memories WHERE LOWER(agent_name) = LOWER(?))",
            (agent_name,),
        ).fetchall()
        resolved = 0
        for cr in contradiction_rows:
            meta = json.loads(cr["metadata"]) if cr["metadata"] and cr["metadata"] != "{}" else {}
            if meta.get("resolved"):
                resolved += 1
        contradiction_resolution = resolved / max(1, contradiction_count)

        question_ratio = sum(1 for c in contents if "?" in c) / max(1, len(contents))
        certain_count = sum(1 for c in contents if any(kw in c for kw in CERTAIN_KEYWORDS))
        uncertain_count = sum(1 for c in contents if any(kw in c for kw in UNCERTAIN_KEYWORDS))
        certainty_score = certain_count / max(1, certain_count + uncertain_count)
        decision_ratio_val = sum(1 for c in contents if any(kw in c for kw in DECISION_KEYWORDS)) / max(1, len(contents))

        return {
            "sample_size": n,
            "l6_reflection_count": l6_count,
            "l6_signal": round(l6_signal, 3),
            "capture_frequency": round(capture_frequency, 3),
            "burst_ratio": round(burst_ratio, 3),
            "capture_regularity": round(capture_regularity, 3),
            "domain_breadth": domain_breadth,
            "domain_depth": round(domain_depth, 3),
            "new_domain_rate": round(new_domain_rate, 3),
            "knowledge_gap": round(knowledge_gap, 3),
            "contradiction_count": contradiction_count,
            "contradiction_resolution": round(contradiction_resolution, 3),
            "derived_count": derived_count,
            "question_ratio": round(question_ratio, 3),
            "decision_ratio": round(decision_ratio_val, 3),
            "certainty_score": round(certainty_score, 3),
            "identity_signal": round(identity_signal, 3),
        }
    finally:
        conn.close()


def _parse_ts(ts_str: str):
    if not ts_str:
        return None
    tz_utc = timezone.utc
    for fmt in ("%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            dt = datetime.strptime(ts_str.rstrip("Z"), fmt.rstrip("z"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=tz_utc)
            return dt
        except (ValueError, AttributeError):
            continue
    try:
        ts = ts_str.replace("Z", "+00:00")
        return datetime.fromisoformat(ts)
    except (ValueError, TypeError):
        return None


def _time_entropy(timestamps):
    if len(timestamps) < 2:
        return 1.0
    hours = [t.hour + t.minute / 60 for t in timestamps]
    buckets = [0] * 24
    for h in hours:
        idx = min(int(h), 23)
        buckets[idx] += 1
    total = sum(buckets)
    if total == 0:
        return 1.0
    entropy = -sum((c / total) * math.log2(c / total) for c in buckets if c > 0)
    max_entropy = math.log2(24)
    return round(entropy / max_entropy, 3) if max_entropy > 0 else 1.0


def features_to_colors(features: dict) -> dict:
    scores = {}
    f = features
    ident_w = f.get("identity_signal", 0)
    l6_w = f.get("l6_signal", 0)  # 第三刀 第10点: L6 自反联动
    white = f.get("certainty_score", 0) * 0.4 + f.get("decision_ratio", 0) * 0.3 + (1 - f.get("capture_regularity", 0)) * 0.3
    scores["white"] = min(1.0, max(0.0, white * (1 + ident_w * 0.5)))
    blue = f.get("domain_depth", 0) * 0.4 + f.get("domain_breadth", 0) / 20 * 0.3 + f.get("new_domain_rate", 0) * 0.3
    scores["blue"] = min(1.0, max(0.0, blue))
    black = f.get("capture_frequency", 0) / 20 * 0.4 + f.get("burst_ratio", 0) * 0.3 + f.get("contradiction_resolution", 0) * 0.3
    scores["black"] = min(1.0, max(0.0, black))
    red = f.get("contradiction_count", 0) / 20 * 0.4 + f.get("question_ratio", 0) * 0.3 + f.get("knowledge_gap", 0) * 0.3
    scores["red"] = min(1.0, max(0.0, red * (1 + l6_w * 0.4)))  # L6 高频修正 → 红色增强
    green = f.get("derived_count", 0) / 20 * 0.4 + f.get("new_domain_rate", 0) * 0.3 + (1 - f.get("knowledge_gap", 0)) * 0.3
    scores["green"] = min(1.0, max(0.0, green * (1 + ident_w * 0.5) * (1 + l6_w * 0.3)))  # 反思闭环 → 绿色增强
    total = sum(scores.values()) or 1
    return {k: round(v / total, 3) for k, v in scores.items()}


def colors_to_prototype(color_ratios: dict) -> dict:
    sorted_colors = sorted(color_ratios.items(), key=lambda x: (-x[1], x[0]))
    primary = sorted_colors[0][0]
    secondary = sorted_colors[1][0] if len(sorted_colors) > 1 and sorted_colors[1][1] > 0.05 else None
    key = (primary, secondary) if secondary else (primary,)
    if key not in PROTOTYPES:
        key = (primary,)
    proto = PROTOTYPES.get(key, {"en": "Unknown", "cn": "未知"})
    return {
        "en": proto["en"], "cn": proto["cn"],
        "primary_color": COLORS[primary],
        "secondary_color": COLORS.get(secondary) if secondary else None,
        "color_ratios": color_ratios,
    }


def generate_persona(agent_name: str, time_range: str = "all") -> dict:
    """Generate a cognitive persona (color profile + prototype) for an agent.

    Args:
        agent_name: Name of the agent to analyze.
        time_range: ``"all"`` (full history) or ``"recent_7d"`` (last 7 days).

    Returns dict with keys ``features``, ``color_ratios``, ``prototype``,
    ``interpretation``, ``generated_at``.
    """
    features = extract_features(agent_name, time_range=time_range)
    if "error" in features:
        return features
    color_ratios = features_to_colors(features)
    prototype = colors_to_prototype(color_ratios)
    interpretations = {
        "white": "结构化思维者：有明确的决策框架和确定性偏好",
        "blue": "深度理解者：注重领域知识的深度和广度",
        "black": "行动驱动者：高频率、高密度地产生内容",
        "red": "矛盾发现者：善于从冲突和问题中学习",
        "green": "关系建设者：擅长建立连接和拓展新领域",
    }
    primary = prototype.get("primary_color", {}).get("name", "").lower()
    interp = interpretations.get(primary, "")
    return {"features": features, "color_ratios": color_ratios, "prototype": prototype,
            "interpretation": interp, "time_range": time_range,
            "generated_at": datetime.now(timezone.utc).isoformat()}


def generate_dual_persona(agent_name: str, dynamic_days: int = 7) -> dict:
    """Generate both static (full history) and dynamic (recent N days) persona.

    Returns::

        {
            "agent_name": str,
            "static": { ... full-history persona ... },
            "dynamic": { ... recent-N-days persona ... },
            "delta": {
                "prototype_shift": str,        # e.g. "Stabilizer → Explorer"
                "color_deltas": { ... },       # per-color change (+0.05, -0.02)
                "activity_ratio": float,       # recent memories / total
            },
            "generated_at": str,
        }

    The ``delta`` field highlights how the agent's cognitive profile has
    shifted in the recent window compared to its long-term baseline.
    """
    static = generate_persona(agent_name, time_range="all")
    if "error" in static:
        return {"agent_name": agent_name, "error": static["error"]}

    dynamic = generate_persona(agent_name, time_range=f"recent_{dynamic_days}d")
    if "error" in dynamic:
        # Fall back to static if no recent memories
        dynamic_copy = dict(static)
        dynamic_copy["note"] = f"no memories in last {dynamic_days}d, using static as dynamic"
        dynamic = dynamic_copy

    # Compute delta
    static_colors = static.get("color_ratios", {})
    dynamic_colors = dynamic.get("color_ratios", {})
    color_deltas: dict[str, float] = {}
    all_keys = set(static_colors.keys()) | set(dynamic_colors.keys())
    for k in all_keys:
        sv = static_colors.get(k, 0)
        dv = dynamic_colors.get(k, 0)
        delta = round(dv - sv, 3)
        if abs(delta) >= 0.01:
            color_deltas[k] = delta

    static_proto = static.get("prototype", {}).get("cn", "?")
    dynamic_proto = dynamic.get("prototype", {}).get("cn", "?")
    if static_proto != dynamic_proto:
        prototype_shift = f"{static_proto} → {dynamic_proto}"
    else:
        prototype_shift = "stable"

    s_feat = static.get("features", {})
    d_feat = dynamic.get("features", {})
    activity_ratio = round(
        d_feat.get("sample_size", 0) / max(1, s_feat.get("sample_size", 1)), 3
    )

    return {
        "agent_name": agent_name,
        "dynamic_window_days": dynamic_days,
        "static": static,
        "dynamic": dynamic,
        "delta": {
            "prototype_shift": prototype_shift,
            "color_deltas": color_deltas,
            "activity_ratio": activity_ratio,
        },
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def save_persona(agent_name: str) -> dict:
    profile = generate_persona(agent_name)
    if "error" in profile:
        return profile
    conn = get_conn()
    try:
        now = datetime.now(timezone.utc).isoformat()
        conn.execute("UPDATE identities SET profile_json = ?, persona_updated_at = ? WHERE agent_name = ?",
                     (json.dumps(profile, ensure_ascii=False), now, agent_name))
        if conn.execute("SELECT 1 FROM identities WHERE agent_name = ?", (agent_name,)).fetchone() is None:
            conn.execute("INSERT INTO identities (agent_name, agent_type, profile_json, persona_updated_at, last_heartbeat) VALUES (?, 'ai', ?, ?, ?)",
                         (agent_name, json.dumps(profile, ensure_ascii=False), now, now))
        conn.commit()
        return profile
    finally:
        conn.close()


def persona_step() -> dict:
    conn = get_conn()
    try:
        agents = conn.execute("SELECT DISTINCT LOWER(agent_name) as aname FROM memories WHERE agent_name != '' AND agent_name IS NOT NULL").fetchall()
        seen = set()
        results = {}
        skipped = 0
        for row in agents:
            agent = row["aname"]
            if agent in seen:
                continue
            seen.add(agent)

            # Incremental: skip agents with no new memories since last persona update
            last_update = conn.execute(
                "SELECT persona_updated_at FROM identities WHERE LOWER(agent_name) = LOWER(?)",
                (agent,),
            ).fetchone()
            if last_update and last_update["persona_updated_at"]:
                new_count = conn.execute(
                    "SELECT COUNT(*) FROM memories WHERE LOWER(agent_name) = LOWER(?) AND created_at > ?",
                    (agent, last_update["persona_updated_at"]),
                ).fetchone()[0]
                if new_count == 0:
                    skipped += 1
                    continue

            profile = save_persona(agent)
            if "error" not in profile:
                proto = profile.get("prototype", {})
                results[agent] = {
                    "prototype_cn": proto.get("cn", "?"),
                    "prototype_en": proto.get("en", "?"),
                    "sample_size": profile["features"].get("sample_size", 0),
                }
        result = {"agents_processed": len(results), "agents": results, "skipped": skipped}
        if skipped:
            result["note"] = f"{skipped} agents had no new memories since last persona update"
        return result
    finally:
        conn.close()


# ══════════════════════════════════════════════════════════════════
# Evolution time series (GAP-9)
# ══════════════════════════════════════════════════════════════════

def get_evolution(agent_name: str, window_days: int = 30) -> dict:
    """Build a time series of persona snapshots for evolution tracking.

    Divides memories into time windows and computes persona features per window,
    then produces a trend summary showing how the agent's cognitive profile
    has evolved over time.

    Usage: memall persona <agent> --evolution [--window 30]
    """
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT id, content, category, occurred_at, created_at FROM memories "
            "WHERE LOWER(agent_name) = LOWER(?) ORDER BY created_at",
            (agent_name,),
        ).fetchall()

        if not rows:
            return {"error": f"no memories for {agent_name}"}

        # Parse all timestamps
        entries = []
        for r in rows:
            ts = _parse_ts(r["created_at"]) or _parse_ts(r["occurred_at"])
            if ts:
                entries.append({
                    "id": r["id"],
                    "content": r["content"] or "",
                    "category": r["category"] or "",
                    "ts": ts,
                })

        if not entries:
            return {"error": f"no timestamped memories for {agent_name}"}

        entries.sort(key=lambda e: e["ts"])
        start = entries[0]["ts"]
        end = entries[-1]["ts"]

        # Build windows
        window_delta = timedelta(days=window_days)
        windows = []
        current_start = start
        while current_start < end:
            current_end = current_start + window_delta
            window_entries = [e for e in entries if current_start <= e["ts"] < current_end]
            if window_entries:
                windows.append({
                    "label": current_start.strftime("%Y-%m-%d"),
                    "count": len(window_entries),
                    "categories": list(set(e["category"] for e in window_entries if e["category"])),
                    "certain": sum(1 for e in window_entries if any(kw in e["content"] for kw in CERTAIN_KEYWORDS)),
                    "uncertain": sum(1 for e in window_entries if any(kw in e["content"] for kw in UNCERTAIN_KEYWORDS)),
                    "decisions": sum(1 for e in window_entries if any(kw in e["content"] for kw in DECISION_KEYWORDS)),
                })
            current_start = current_end

        # Trend indicators
        if len(windows) >= 2:
            first = windows[0]
            last = windows[-1]
            trend = {
                "activity": "increasing" if last["count"] > first["count"] else "decreasing",
                "certainty": "increasing" if last["certain"] > first["certain"] else "decreasing",
                "decision_making": "increasing" if last["decisions"] > first["decisions"] else "decreasing",
            }
        else:
            trend = {"activity": "stable", "certainty": "stable", "decision_making": "stable"}

        # Current persona for context
        current_profile = generate_persona(agent_name)

        return {
            "agent_name": agent_name,
            "total_windows": len(windows),
            "window_days": window_days,
            "span_days": (end - start).days,
            "windows": windows,
            "trend": trend,
            "current_persona": current_profile.get("prototype", {}) if "error" not in current_profile else {},
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }
    finally:
        conn.close()


def compare_personas(agents: list[str]) -> dict:
    """Compare persona features across multiple agents (GAP-9).

    Returns a comparison matrix: each agent's features side-by-side.

    Usage: memall persona --compare agent1 agent2 agent3
    """
    results = {}
    for agent in agents:
        profile = generate_persona(agent)
        if "error" in profile:
            results[agent] = {"error": profile["error"]}
            continue
        proto = profile.get("prototype", {})
        feat = profile.get("features", {})
        colors = profile.get("color_ratios", {})
        results[agent] = {
            "prototype_cn": proto.get("cn", "?"),
            "prototype_en": proto.get("en", "?"),
            "primary_color": proto.get("primary_color", {}).get("name", "?"),
            "sample_size": feat.get("sample_size", 0),
            "certainty_score": feat.get("certainty_score", 0),
            "decision_ratio": feat.get("decision_ratio", 0),
            "domain_breadth": feat.get("domain_breadth", 0),
            "domain_depth": feat.get("domain_depth", 0),
            "contradiction_resolution": feat.get("contradiction_resolution", 0),
            "color_ratios": colors,
        }

    # Compute pairwise similarities
    similarities = {}
    agent_list = [a for a in agents if "error" not in results[a]]
    for i in range(len(agent_list)):
        for j in range(i + 1, len(agent_list)):
            a1, a2 = agent_list[i], agent_list[j]
            c1 = results[a1].get("color_ratios", {})
            c2 = results[a2].get("color_ratios", {})
            if c1 and c2:
                common = set(c1.keys()) & set(c2.keys())
                if common:
                    dist = sum(abs(c1.get(k, 0) - c2.get(k, 0)) for k in common) / len(common)
                    sim = round(1 - dist, 3)
                    similarities[f"{a1}↔{a2}"] = sim

    return {
        "agents": results,
        "similarities": similarities,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


# ══════════════════════════════════════════════════════════════════
# Phase 10: Layer 2 — Network Topology Profile
# ══════════════════════════════════════════════════════════════════

def extract_topology(agent_name: str) -> dict:
    """Analyze agent's memory network topology: degree, clustering, bridges."""
    conn = get_conn()
    try:
        # Get agent's memory IDs
        agent_ids = [r[0] for r in conn.execute(
            "SELECT id FROM memories WHERE LOWER(agent_name) = LOWER(?)",
            (agent_name,),
        ).fetchall()]
        if not agent_ids:
            return {"error": f"no memories for {agent_name}", "memory_count": 0}

        n = len(agent_ids)

        # Out-degree: edges where agent's memory is source
        out_degrees = conn.execute(
            f"SELECT source_id, COUNT(*) FROM edges WHERE source_id IN ({','.join('?'*n)}) GROUP BY source_id",
            agent_ids,
        ).fetchall()
        out_deg_map = {r[0]: r[1] for r in out_degrees}
        out_values = list(out_deg_map.values())
        avg_out = sum(out_values) / max(1, len(out_values))
        max_out = max(out_values) if out_values else 0

        # In-degree: edges where agent's memory is target
        in_degrees = conn.execute(
            f"SELECT target_id, COUNT(*) FROM edges WHERE target_id IN ({','.join('?'*n)}) GROUP BY target_id",
            agent_ids,
        ).fetchall()
        in_deg_map = {r[0]: r[1] for r in in_degrees}
        in_values = list(in_deg_map.values())
        avg_in = sum(in_values) / max(1, len(in_values))
        max_in = max(in_values) if in_values else 0

        # Total edges involving agent's memories (both directions)
        total_out = sum(out_values)
        total_in = sum(in_values)

        # Internal edges: both source and target are agent's memories (self-reference)
        internal = conn.execute(
            f"SELECT COUNT(*) FROM edges WHERE source_id IN ({','.join('?'*n)}) AND target_id IN ({','.join('?'*n)})",
            agent_ids + agent_ids,
        ).fetchone()[0]

        # External edges: edges from agent to others
        external_out = total_out - internal

        # Global edge count for normalization
        global_edges = conn.execute("SELECT COUNT(*) FROM edges").fetchone()[0]

        # Clustering coefficient (local): triadic closure among agent's memories
        triad_count = conn.execute(
            f"""SELECT COUNT(*) FROM edges e1
                JOIN edges e2 ON e1.target_id = e2.source_id
                WHERE e1.source_id IN ({','.join('?'*n)})
                  AND e2.target_id IN ({','.join('?'*n)})
                  AND e1.target_id IN ({','.join('?'*n)})""",
            agent_ids * 3,
        ).fetchone()[0]

        # Possible triads: choose 3 from agent_ids where edges exist between them
        possible_triads = 0
        if n >= 3:
            # Count pairs connected by edges within agent's memories
            pair_edges = conn.execute(
                f"""SELECT COUNT(*) FROM edges
                    WHERE source_id IN ({','.join('?'*n)})
                      AND target_id IN ({','.join('?'*n)})
                      AND source_id < target_id""",
                agent_ids + agent_ids,
            ).fetchone()[0]
            # Each pair can potentially form triads with (n-2) other nodes
            possible_triads = pair_edges * (n - 2)

        clustering = round(triad_count / max(1, possible_triads), 4) if possible_triads > 0 else 0.0

        # Bridge detection: agent's memories that connect different relation types
        # A node is a bridge if it participates in multiple relation types across clusters
        bridge_scores = conn.execute(
            f"""SELECT source_id, COUNT(DISTINCT relation_type) as rel_types,
                       COUNT(DISTINCT target_id) as out_targets
                FROM edges
                WHERE source_id IN ({','.join('?'*n)})
                GROUP BY source_id
                HAVING rel_types >= 3
                ORDER BY out_targets DESC LIMIT 10""",
            agent_ids,
        ).fetchall()
        bridges = [{"memory_id": r[0], "relation_types": r[1], "out_targets": r[2]} for r in bridge_scores]

        # Contradiction self-index
        contradictions = conn.execute(
            f"SELECT COUNT(*) FROM edges WHERE relation_type = 'contradicts' AND source_id IN ({','.join('?'*n)})",
            agent_ids,
        ).fetchone()[0]

        # Network leverage: how much agent connects to others vs self-contained
        leverage = round(external_out / max(1, total_out), 3) if total_out > 0 else 0.0

        return {
            "memory_count": n,
            "degree": {
                "avg_out": round(avg_out, 2),
                "max_out": max_out,
                "avg_in": round(avg_in, 2),
                "max_in": max_in,
                "total_out_edges": total_out,
                "total_in_edges": total_in,
            },
            "internal_edges": internal,
            "external_edges": external_out,
            "network_leverage": leverage,
            "clustering_coefficient": clustering,
            "global_edge_share": round((total_out + total_in) / max(1, global_edges * 2), 5),
            "contradiction_self_index": contradictions,
            "bridge_nodes": bridges[:5],
            "bridge_count": len(bridges),
        }
    finally:
        conn.close()


# ══════════════════════════════════════════════════════════════════
# Phase 10: Layer 3 — Behavioral Patterns
# ══════════════════════════════════════════════════════════════════

def extract_behavioral(agent_name: str) -> dict:
    """Analyze agent's behavioral patterns: time rhythm, domain flow, bursts."""
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT id, content, category, created_at FROM memories "
            "WHERE LOWER(agent_name) = LOWER(?) ORDER BY created_at",
            (agent_name,),
        ).fetchall()
        if not rows:
            return {"error": f"no memories for {agent_name}"}

        entries = []
        for r in rows:
            ts = _parse_ts(r["created_at"])
            if ts:
                entries.append({
                    "id": r["id"],
                    "category": r["category"] or "",
                    "content": r["content"] or "",
                    "ts": ts,
                })

        if not entries:
            return {"error": f"no timestamped memories for {agent_name}"}

        entries.sort(key=lambda e: e["ts"])

        # 1. Hour rhythm: activity by hour of day
        hour_buckets = [0] * 24
        for e in entries:
            hour_buckets[e["ts"].hour] += 1
        peak_hour = hour_buckets.index(max(hour_buckets))
        active_hours = sum(1 for h in hour_buckets if h > 0)

        # Day-of-week rhythm
        dow_buckets = [0] * 7
        for e in entries:
            dow_buckets[e["ts"].weekday()] += 1
        peak_dow = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"][dow_buckets.index(max(dow_buckets))]

        # 2. Domain flow: category transition sequence
        categories = [e["category"] for e in entries if e["category"]]
        transitions = []
        for i in range(len(categories) - 1):
            if categories[i] != categories[i + 1]:
                transitions.append(f"{categories[i]}→{categories[i+1]}")
        trans_counter = Counter(transitions)
        top_transitions = trans_counter.most_common(5)

        # Domain stickiness: consecutive same-category ratio
        same_count = sum(1 for i in range(len(categories) - 1) if categories[i] == categories[i + 1])
        stickiness = round(same_count / max(1, len(categories) - 1), 3)

        # 3. Burst analysis: detect activity spikes
        if len(entries) > 1:
            intervals = [(entries[i + 1]["ts"] - entries[i]["ts"]).total_seconds()
                         for i in range(len(entries) - 1)]
            avg_interval = sum(intervals) / len(intervals)
            # Burst: consecutive intervals below 25% of average
            burst_threshold = avg_interval * 0.25
            bursts = []
            current_burst = 0
            for iv in intervals:
                if iv < burst_threshold:
                    current_burst += 1
                else:
                    if current_burst >= 3:
                        bursts.append(current_burst + 1)
                    current_burst = 0
            if current_burst >= 3:
                bursts.append(current_burst + 1)
        else:
            avg_interval = 0
            bursts = []

        # 4. Category entropy over time (how spread is domain exploration)
        cat_entropy = 0.0
        if categories:
            cat_counts = Counter(categories)
            total = sum(cat_counts.values())
            cat_entropy = round(-sum((c / total) * math.log2(c / total) for c in cat_counts.values() if c > 0), 3)

        # 5. Session-like grouping: detect natural session boundaries (gap > 2 hours)
        sessions = []
        session_start = entries[0]["ts"]
        session_memories = 1
        for i in range(1, len(entries)):
            gap = (entries[i]["ts"] - entries[i-1]["ts"]).total_seconds()
            if gap > 7200:  # 2 hours
                sessions.append({
                    "start": session_start.isoformat(),
                    "memories": session_memories,
                    "duration_min": round((entries[i-1]["ts"] - session_start).total_seconds() / 60, 1),
                })
                session_start = entries[i]["ts"]
                session_memories = 1
            else:
                session_memories += 1
        # Last session
        sessions.append({
            "start": session_start.isoformat(),
            "memories": session_memories,
            "duration_min": round((entries[-1]["ts"] - session_start).total_seconds() / 60, 1),
        })

        avg_session_len = round(sum(s["memories"] for s in sessions) / max(1, len(sessions)), 1)
        avg_session_dur = round(sum(s["duration_min"] for s in sessions) / max(1, len(sessions)), 1)

        return {
            "time_rhythm": {
                "hour_distribution": hour_buckets,
                "peak_hour": peak_hour,
                "active_hours": active_hours,
                "peak_day": peak_dow,
                "dow_distribution": dow_buckets,
            },
            "domain_flow": {
                "category_entropy": cat_entropy,
                "stickiness": stickiness,
                "top_transitions": [{"from_to": t[0], "count": t[1]} for t in top_transitions],
            },
            "bursts": {
                "avg_interval_seconds": round(avg_interval, 1),
                "burst_count": len(bursts),
                "burst_sizes": bursts[:10],
                "max_burst": max(bursts) if bursts else 0,
            },
            "sessions": {
                "total_sessions": len(sessions),
                "avg_memories_per_session": avg_session_len,
                "avg_duration_min": avg_session_dur,
            },
            "total_interactions": len(entries),
            "span_days": (entries[-1]["ts"] - entries[0]["ts"]).days if len(entries) > 1 else 0,
        }
    finally:
        conn.close()


# ══════════════════════════════════════════════════════════════════
# Phase 10: Unified 3-Layer Profile
# ══════════════════════════════════════════════════════════════════

def generate_profile_3layer(agent_name: str) -> dict:
    """Generate a complete 3-layer Agent Profile.

    Layer 1: Cognitive Features (colors + prototype)
    Layer 2: Network Topology (degree, clustering, bridges)
    Layer 3: Behavioral Patterns (rhythm, flow, bursts, sessions)
    """
    # Layer 1
    cognitive = generate_persona(agent_name)
    if "error" in cognitive:
        return {"error": cognitive["error"], "agent_name": agent_name}

    # Layer 2
    topology = extract_topology(agent_name)

    # Layer 3
    behavioral = extract_behavioral(agent_name)

    return {
        "agent_name": agent_name,
        "layer_1_cognitive": {
            "prototype": cognitive.get("prototype", {}),
            "color_ratios": cognitive.get("color_ratios", {}),
            "features": cognitive.get("features", {}),
        },
        "layer_2_topology": topology,
        "layer_3_behavioral": behavioral,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def persona_profile_cmd(agent_name: str, layer: str = "all") -> dict:
    """CLI entry: memall persona profile <agent> [--layer 1|2|3|all]"""
    if layer == "1" or layer == "all":
        profile = generate_profile_3layer(agent_name)
    elif layer == "2":
        profile = {"layer_2_topology": extract_topology(agent_name)}
    elif layer == "3":
        profile = {"layer_3_behavioral": extract_behavioral(agent_name)}
    else:
        return {"error": f"invalid layer: {layer}"}
    return profile
