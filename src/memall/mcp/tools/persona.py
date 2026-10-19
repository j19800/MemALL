import json
from memall.pipeline.persona import generate_persona, generate_dual_persona, get_evolution as persona_evolution, generate_profile_3layer
from memall.pipeline.ask import ContextAssembler
from memall.core.db import get_conn
from datetime import datetime, timedelta, timezone


def _resolve_agent(arguments: dict) -> str:
    """Resolve the effective agent identity for persona-family actions.

    Policy (uniform across every tool): ``agent_name`` is optional.  When it
    is omitted or empty we fall back to the configured human owner
    (``identity.human_owner``) — the same invariant the write path uses, where
    owner is ALWAYS a human.  Returning ``""`` would instead query "all
    unowned memories", which silently produced misleading personas; raising an
    error forced callers to guess.  The fallback makes both outcomes
    predictable.

    Returns the resolved identity (never empty in practice — the config
    default is "老陈").
    """
    agent = (arguments.get("agent_name") or "").strip()
    if agent:
        return agent
    from memall.config import get_config

    return str(get_config("identity.human_owner", "老陈") or "老陈")


def handle_persona(arguments: dict) -> str:
    agent = _resolve_agent(arguments)
    mode = arguments.get("mode", "static")

    if mode == "dual":
        dynamic_days = arguments.get("dynamic_window_days", 7)
        profile = generate_dual_persona(agent, dynamic_days=dynamic_days)
    elif mode == "dynamic":
        dynamic_days = arguments.get("dynamic_window_days", 7)
        profile = generate_persona(agent, time_range=f"recent_{dynamic_days}d")
    else:
        profile = generate_persona(agent, time_range="all")

    if arguments.get("evolution"):
        evolution = persona_evolution(agent, window_days=arguments.get("window_days", 30))
        profile["evolution"] = evolution
    return json.dumps(profile, ensure_ascii=False, default=str)


def handle_persona_profile(arguments: dict) -> str:
    agent = _resolve_agent(arguments)
    layer = str(arguments.get("layer", "all"))
    profile = generate_profile_3layer(agent)
    if layer != "all":
        key = f"layer_{layer}"
        profile = {f"layer_{layer}_{'cognitive' if layer=='1' else 'topology' if layer=='2' else 'behavioral'}": profile.get(key, {})}
    return json.dumps(profile, ensure_ascii=False, default=str)


def handle_ask(arguments: dict) -> str:
    if not arguments.get("question"):
        return json.dumps({"error": "question is required"}, ensure_ascii=False)
    # ``subject`` is the persona to answer as; fall back to ``agent_name``,
    # then to the configured human owner (uniform empty-identity policy).
    subject = (arguments.get("subject") or "").strip() or _resolve_agent(arguments)
    result = ContextAssembler.ask(
        query=arguments["question"],
        subject=subject,
        mode=arguments.get("mode", "stance"),
    )
    return json.dumps(result, ensure_ascii=False, default=str)


def handle_identity(arguments: dict) -> str:
    agent = _resolve_agent(arguments)
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT identity_profile, profile_json, persona_updated_at FROM identities WHERE LOWER(agent_name) = LOWER(?)",
            (agent,),
        ).fetchone()
        if not row:
            return json.dumps({"agent_name": agent, "error": "not found",
                               "hint": "No identity record yet — run a pipeline pass or "
                                       "session_start to create one."})
        id_profile = json.loads(row["identity_profile"]) if isinstance(row["identity_profile"], str) and row["identity_profile"] else {}
        pj = json.loads(row["profile_json"]) if isinstance(row["profile_json"], str) and row["profile_json"] else {}
        result = {
            "agent_name": agent,
            "l1_identity": id_profile.get("l1_identity", []) if isinstance(id_profile, dict) else [],
            "l7_preferences": id_profile.get("l7_preferences", []) if isinstance(id_profile, dict) else [],
            "prototype": pj.get("prototype", {}) if isinstance(pj, dict) else {},
            "features": pj.get("features", {}) if isinstance(pj, dict) else {},
            "updated_at": row["persona_updated_at"],
        }
        return json.dumps(result, ensure_ascii=False, default=str)
    finally:
        conn.close()


def handle_profile_preload(arguments: dict) -> str:
    """Preload user profile into a flat structure for fast retrieval.
    Implements Memobase-inspired profile caching.
    """
    agent = _resolve_agent(arguments)
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT identity_profile, profile_json FROM identities WHERE LOWER(agent_name) = LOWER(?)",
            (agent,),
        ).fetchone()
        if not row:
            return json.dumps({"agent_name": agent, "profile": {}, "cached": False})

        id_profile = json.loads(row["identity_profile"]) if isinstance(row["identity_profile"], str) and row["identity_profile"] else {}
        pj = json.loads(row["profile_json"]) if isinstance(row["profile_json"], str) and row["profile_json"] else {}

        # Build flat profile with key-value pairs for fast lookup
        flat = {
            "name": agent,
            "prototype": pj.get("prototype", {}),
            "features": pj.get("features", {}),
            "color_ratios": pj.get("color_ratios", {}),
        }

        # Extract L1/L7 identity statements for quick reference
        l1 = id_profile.get("l1_identity", [])
        for item in l1:
            stype = item.get("type", "")
            snippet = item.get("snippet", "")
            if stype in ("identity_statement", "belief", "skill"):
                flat.setdefault("identity", []).append(snippet[:120])

        l7 = id_profile.get("l7_preferences", [])
        for item in l7:
            ptype = item.get("type", "")
            snippet = item.get("snippet", "")
            if ptype in ("preference", "recommendation"):
                flat.setdefault("preferences", []).append(snippet[:120])

        # Get recent category distribution (last 7 days)
        # created_at rows come in both now_iso (T-separated, +00:00) and legacy
        # space-separated formats — filter against both lexical formats.
        now = datetime.now(timezone.utc)
        week_ago_t = (now - timedelta(days=7)).isoformat()
        week_ago_s = (now - timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")
        cat_rows = conn.execute(
            "SELECT category, COUNT(*) as cnt FROM memories WHERE agent_name = ? "
            "AND (created_at > ? OR created_at > ?) GROUP BY category ORDER BY cnt DESC LIMIT 5",
            (agent, week_ago_t, week_ago_s),
        ).fetchall()
        flat["recent_categories"] = [{"category": r[0], "count": r[1]} for r in cat_rows]

        return json.dumps({"agent_name": agent, "profile": flat, "cached": True}, ensure_ascii=False)
    finally:
        conn.close()


def handle_profile_search(arguments: dict) -> str:
    """Search profiles by agent_name or identity keywords.
    Enables cross-agent profile discovery.
    """
    query = arguments.get("query", "")
    if not query:
        return json.dumps({"error": "query is required"}, ensure_ascii=False)
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT agent_name, description, agent_type, status, persona_updated_at FROM identities "
            "WHERE agent_name LIKE ? OR description LIKE ? ORDER BY persona_updated_at DESC LIMIT 20",
            (f"%{query}%", f"%{query}%"),
        ).fetchall()
        results = [dict(r) for r in rows]
        return json.dumps({"query": query, "total": len(results), "agents": results}, ensure_ascii=False)
    finally:
        conn.close()


def handle_foresight(arguments: dict) -> str:
    """Foresight memory — predict what information the user is likely to need next
    based on recent activity patterns.
    """
    agent = _resolve_agent(arguments)
    conn = get_conn()
    try:
        # Analyze recent category transitions to predict next likely query
        now = datetime.now(timezone.utc)
        month_ago_t = (now - timedelta(days=30)).isoformat()
        month_ago_s = (now - timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")
        rows = conn.execute(
            "SELECT category, created_at FROM memories WHERE agent_name = ? "
            "AND (created_at > ? OR created_at > ?) ORDER BY created_at ASC",
            (agent, month_ago_t, month_ago_s),
        ).fetchall()

        if len(rows) < 5:
            return json.dumps({"agent_name": agent, "predictions": [], "confidence": "low"})

        # Extract category sequence
        cats = [r[0] for r in rows if r[0]]
        if not cats:
            return json.dumps({"agent_name": agent, "predictions": [], "note": "no categories found"})

        # Simple transition counting
        transitions = {}
        for i in range(len(cats) - 1):
            pair = (cats[i], cats[i + 1])
            transitions[pair] = transitions.get(pair, 0) + 1

        # Top predicted next categories
        last_cat = cats[-1]
        predicted = []
        for (frm, to), count in sorted(transitions.items(), key=lambda x: -x[1]):
            if frm == last_cat:
                predicted.append({"from": frm, "to": to, "frequency": count})

        # Most active categories
        from collections import Counter
        cat_count = Counter(cats)
        top = [{"category": c, "count": n} for c, n in cat_count.most_common(3)]

        return json.dumps({
            "agent_name": agent,
            "predictions": predicted[:5],
            "top_categories": top,
            "total_memories": len(rows),
        }, ensure_ascii=False)
    finally:
        conn.close()
