import json
from memall.pipeline.persona import generate_persona, generate_dual_persona, get_evolution as persona_evolution, generate_profile_3layer
from memall.pipeline.ask import ContextAssembler
from memall.core.db import get_conn


def handle_persona(arguments: dict) -> str:
    agent = arguments.get("agent_name", "")
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
    agent = arguments["agent_name"]
    layer = arguments.get("layer", "all")
    profile = generate_profile_3layer(agent)
    if layer != "all":
        key = f"layer_{layer}"
        profile = {f"layer_{layer}_{'cognitive' if layer=='1' else 'topology' if layer=='2' else 'behavioral'}": profile.get(key, {})}
    return json.dumps(profile, ensure_ascii=False, default=str)


def handle_ask(arguments: dict) -> str:
    subject = arguments.get("subject") or arguments.get("agent_name", "")
    result = ContextAssembler.ask(
        query=arguments["question"],
        subject=subject,
        mode=arguments.get("mode", "stance"),
    )
    return json.dumps(result, ensure_ascii=False, default=str)


def handle_identity(arguments: dict) -> str:
    agent = arguments.get("agent_name", "")
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT identity_profile, profile_json, persona_updated_at FROM identities WHERE LOWER(agent_name) = LOWER(?)",
            (agent,),
        ).fetchone()
        if not row:
            return json.dumps({"agent_name": agent, "error": "not found"})
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
